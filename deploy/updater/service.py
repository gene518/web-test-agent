"""固定仓库、固定服务的 Docker Compose 在线更新控制面。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from common import (
    ALLOWED_CONTAINER_PUBLISH_JOB,
    ALLOWED_CONTAINER_WORKFLOW,
    ALLOWED_COSIGN_IDENTITY,
    ALLOWED_GITHUB_REPOSITORY,
    ALLOWED_IMAGE_PREFIX,
    atomic_write_json,
    image_references,
    read_json,
    require_commit_sha,
    require_image_digest,
    require_image_id,
    run_command,
    verify_signed_image,
)


STATE_TERMINAL = frozenset({"succeeded", "failed", "rolled_back"})
ROLLBACK_FAILURE_PHASES = frozenset({"rollback_failed", "watchdog_rollback_failed"})
CSRF_COOKIE = "web_test_agent_update_csrf"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class UpdaterConfig:
    """从环境读取的固定更新边界。"""

    state_dir: Path
    github_repository: str
    github_workflow: str
    github_branch: str
    image_prefix: str
    current_revision: str
    internal_token: str
    csrf_secret: bytes
    public_origin: str
    agent_url: str
    compose_project_name: str
    internal_network: str
    scheduler_status_path: Path
    drain_timeout_seconds: int
    check_cache_seconds: int
    reconcile_timeout_seconds: int
    rollback_timeout_seconds: int
    cosign_identity: str

    @classmethod
    def from_environment(cls) -> "UpdaterConfig":
        repository = os.environ.get(
            "GITHUB_REPOSITORY", ALLOWED_GITHUB_REPOSITORY
        ).strip()
        workflow = os.environ.get(
            "GITHUB_CONTAINER_WORKFLOW", ALLOWED_CONTAINER_WORKFLOW
        ).strip()
        image_prefix = (
            os.environ.get("GHCR_IMAGE_PREFIX", ALLOWED_IMAGE_PREFIX)
            .strip()
            .rstrip("/")
        )
        cosign_identity = os.environ.get(
            "COSIGN_CERTIFICATE_IDENTITY", ALLOWED_COSIGN_IDENTITY
        ).strip()
        if repository != ALLOWED_GITHUB_REPOSITORY:
            raise RuntimeError("GITHUB_REPOSITORY is not in the updater allowlist")
        if workflow != ALLOWED_CONTAINER_WORKFLOW:
            raise RuntimeError(
                "GITHUB_CONTAINER_WORKFLOW is not in the updater allowlist"
            )
        if image_prefix != ALLOWED_IMAGE_PREFIX:
            raise RuntimeError("GHCR_IMAGE_PREFIX is not in the updater allowlist")
        if cosign_identity != ALLOWED_COSIGN_IDENTITY:
            raise RuntimeError(
                "COSIGN_CERTIFICATE_IDENTITY is not in the updater allowlist"
            )
        internal_token = os.environ.get("UPDATE_INTERNAL_TOKEN", "")
        csrf_secret = os.environ.get("UPDATE_CSRF_SECRET", "")
        if len(internal_token) < 32 or len(csrf_secret) < 32:
            raise RuntimeError(
                "UPDATE_INTERNAL_TOKEN and UPDATE_CSRF_SECRET must contain at least 32 characters"
            )
        if hmac.compare_digest(internal_token, csrf_secret):
            raise RuntimeError(
                "UPDATE_INTERNAL_TOKEN and UPDATE_CSRF_SECRET must be independent"
            )
        public_origin = os.environ.get("PUBLIC_ORIGIN", "").rstrip("/")
        parsed_origin = urllib.parse.urlsplit(public_origin)
        if (
            parsed_origin.scheme not in {"http", "https"}
            or not parsed_origin.netloc
            or parsed_origin.username is not None
            or parsed_origin.password is not None
            or parsed_origin.path
            or parsed_origin.query
            or parsed_origin.fragment
        ):
            raise RuntimeError("PUBLIC_ORIGIN must be one absolute HTTP(S) origin")
        drain_timeout_seconds = int(
            os.environ.get("UPDATE_DRAIN_TIMEOUT_SECONDS", "3600")
        )
        check_cache_seconds = int(os.environ.get("UPDATE_CHECK_CACHE_SECONDS", "1200"))
        reconcile_timeout_seconds = int(
            os.environ.get("UPDATE_RECONCILE_TIMEOUT_SECONDS", "3600")
        )
        rollback_timeout_seconds = int(
            os.environ.get("UPDATE_ROLLBACK_TIMEOUT_SECONDS", "1800")
        )
        if (
            min(
                drain_timeout_seconds,
                check_cache_seconds,
                reconcile_timeout_seconds,
                rollback_timeout_seconds,
            )
            <= 0
        ):
            raise RuntimeError("updater timeout and cache values must be positive")
        current = os.environ.get("BUILD_SHA", "0" * 40)
        return cls(
            state_dir=Path(os.environ.get("UPDATE_STATE_DIR", "/state")),
            github_repository=repository,
            github_workflow=workflow,
            github_branch="main",
            image_prefix=image_prefix,
            current_revision=require_commit_sha(current),
            internal_token=internal_token,
            csrf_secret=csrf_secret.encode("utf-8"),
            public_origin=public_origin,
            agent_url=os.environ.get("AGENT_INTERNAL_URL", "http://agent:2024").rstrip(
                "/"
            ),
            compose_project_name=os.environ.get(
                "COMPOSE_PROJECT_NAME", "web-test-agent"
            ),
            internal_network=os.environ.get(
                "INTERNAL_NETWORK", "web-test-agent-internal"
            ),
            scheduler_status_path=Path(
                os.environ.get("SCHEDULER_STATUS_FILE", "/state/scheduler-status.json")
            ),
            drain_timeout_seconds=drain_timeout_seconds,
            check_cache_seconds=check_cache_seconds,
            reconcile_timeout_seconds=reconcile_timeout_seconds,
            rollback_timeout_seconds=rollback_timeout_seconds,
            cosign_identity=cosign_identity,
        )


class UpdateController:
    """管理版本检查、维护闸门和异步更新操作。"""

    def __init__(self, config: UpdaterConfig) -> None:
        self.config = config
        self.operations_dir = config.state_dir / "operations"
        self.cache_path = config.state_dir / "latest.json"
        self.deployment_path = config.state_dir / "deployment.json"
        self.maintenance_path = config.state_dir / "maintenance.json"
        self._operation_lock = threading.Lock()
        self._active_operation_id: str | None = None
        self._recover_incomplete_operation()

    def _recover_incomplete_operation(self) -> None:
        """服务重启后恢复共享卷中最新的非终态操作。"""

        candidates: list[dict[str, Any]] = []
        if self.operations_dir.is_dir():
            for path in self.operations_dir.glob("*.json"):
                value = read_json(path, None)
                if (
                    isinstance(value, dict)
                    and value.get("status") not in STATE_TERMINAL
                    and re_safe_operation_id(str(value.get("operation_id", "")))
                ):
                    candidates.append(value)
        if not candidates:
            maintenance = read_json(self.maintenance_path, {})
            maintenance_operation_id = (
                str(maintenance.get("operation_id", ""))
                if isinstance(maintenance, dict)
                else ""
            )
            terminal = self.operation(maintenance_operation_id)
            if not terminal or terminal.get("phase") not in ROLLBACK_FAILURE_PHASES:
                self.maintenance_path.unlink(missing_ok=True)
            return
        operation = max(candidates, key=lambda value: str(value.get("updated_at", "")))
        operation_id = str(operation["operation_id"])
        self._active_operation_id = operation_id
        atomic_write_json(
            self.maintenance_path,
            {"operation_id": operation_id, "started_at": utc_now()},
        )
        threading.Thread(
            target=self._resume_operation,
            args=(operation_id,),
            daemon=True,
            name=f"recover-update-{operation_id[:8]}",
        ).start()

    def _resume_operation(self, operation_id: str) -> None:
        operation = self.operation(operation_id)
        if not operation:
            return
        phase = str(operation.get("phase", ""))
        monitored_phases = {
            "starting_reconciler",
            "reconciling",
            "recreating_services",
            "rolling_back",
            "watchdog_rollback",
            "watchdog_rolling_back",
        }
        if phase not in monitored_phases:
            self._prepare_update(operation_id)
            return
        if phase == "starting_reconciler":
            container_name = str(operation.get("reconciler_container", ""))
            if (
                not container_name
                or self._container_running(container_name) is not True
            ):
                self._prepare_update(operation_id)
                return
        self._monitor_operation(operation_id)

    def current_revision(self) -> str:
        deployment = read_json(self.deployment_path, {})
        candidate = deployment.get("revision") if isinstance(deployment, dict) else None
        try:
            return require_commit_sha(str(candidate or self.config.current_revision))
        except ValueError:
            return self.config.current_revision

    def check_latest(self, *, force: bool = False) -> dict[str, Any]:
        cached = read_json(self.cache_path, {})
        now = time.time()
        if (
            not force
            and isinstance(cached, dict)
            and now - float(cached.get("checked_epoch", 0))
            < self.config.check_cache_seconds
        ):
            return self._decorate_update(cached)

        url = (
            "https://api.github.com/repos/"
            f"{self.config.github_repository}/actions/workflows/"
            f"{self.config.github_workflow}/runs?branch={self.config.github_branch}"
            "&event=push&status=success&per_page=20"
        )
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Web-Test-Agent-Updater",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        github_token = os.environ.get("GITHUB_TOKEN", "").strip()
        if github_token:
            request.add_header("Authorization", f"Bearer {github_token}")
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
        runs = payload.get("workflow_runs", [])
        if not runs:
            raise RuntimeError("no successful container build is available on main")
        if not isinstance(runs, list):
            raise RuntimeError("GitHub returned an invalid workflow runs response")

        for run in runs:
            if not isinstance(run, dict):
                continue
            try:
                revision = require_commit_sha(str(run.get("head_sha", "")))
            except ValueError:
                continue
            head_repository = run.get("head_repository")
            head_repository_name = (
                head_repository.get("full_name")
                if isinstance(head_repository, dict)
                else None
            )
            expected_run = (
                run.get("status") == "completed"
                and run.get("conclusion") == "success"
                and run.get("event") == "push"
                and run.get("head_branch") == self.config.github_branch
                and head_repository_name == self.config.github_repository
            )
            if not expected_run or not self._has_successful_publish_job(run.get("id")):
                continue
            result = {
                "revision": revision,
                "run_id": run.get("id"),
                "run_url": run.get("html_url"),
                "updated_at": run.get("updated_at"),
                "checked_at": utc_now(),
                "checked_epoch": now,
            }
            atomic_write_json(self.cache_path, result)
            return self._decorate_update(result)

        raise RuntimeError("no successful main CI container publish job is available")

    def _has_successful_publish_job(self, run_id: object) -> bool:
        """只接受实际完成容器发布的同一次 CI run，排除迁移前历史记录。"""

        if type(run_id) is not int or run_id <= 0:
            return False
        url = (
            "https://api.github.com/repos/"
            f"{self.config.github_repository}/actions/runs/{run_id}/jobs"
            "?filter=latest&per_page=100"
        )
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Web-Test-Agent-Updater",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        github_token = os.environ.get("GITHUB_TOKEN", "").strip()
        if github_token:
            request.add_header("Authorization", f"Bearer {github_token}")
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
        jobs = payload.get("jobs", [])
        return isinstance(jobs, list) and any(
            isinstance(job, dict)
            and job.get("name") == ALLOWED_CONTAINER_PUBLISH_JOB
            and job.get("status") == "completed"
            and job.get("conclusion") == "success"
            for job in jobs
        )

    def _decorate_update(self, result: dict[str, Any]) -> dict[str, Any]:
        latest = require_commit_sha(str(result["revision"]))
        current = self.current_revision()
        active_operation_id = self._active_operation_id
        if active_operation_id:
            active = self.operation(active_operation_id)
            if active and active.get("status") in STATE_TERMINAL:
                self._active_operation_id = None
                active_operation_id = None
        return {
            **result,
            "current_revision": current,
            "latest_revision": latest,
            "has_update": current != latest,
            "operation_id": active_operation_id,
            "maintenance": self.maintenance_path.exists(),
        }

    def operation(self, operation_id: str) -> dict[str, Any] | None:
        if not re_safe_operation_id(operation_id):
            return None
        value = read_json(self.operations_dir / f"{operation_id}.json", None)
        return value if isinstance(value, dict) else None

    def begin_update(self) -> dict[str, Any]:
        with self._operation_lock:
            if self._active_operation_id:
                current = self.operation(self._active_operation_id)
                if current and current.get("status") not in STATE_TERMINAL:
                    return current
            latest = self.check_latest(force=True)
            if not latest["has_update"]:
                raise RuntimeError("already up to date")
            operation_id = uuid.uuid4().hex
            operation = {
                "operation_id": operation_id,
                "status": "queued",
                "phase": "waiting_for_idle",
                "current_revision": latest["current_revision"],
                "target_revision": latest["latest_revision"],
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
            self._active_operation_id = operation_id
            self._write_operation(operation)
            threading.Thread(
                target=self._prepare_update,
                args=(operation_id,),
                daemon=True,
                name=f"update-{operation_id[:8]}",
            ).start()
            return operation

    def _write_operation(self, operation: dict[str, Any]) -> None:
        operation["updated_at"] = utc_now()
        atomic_write_json(
            self.operations_dir / f"{operation['operation_id']}.json", operation
        )

    def _update_operation(self, operation_id: str, **values: Any) -> dict[str, Any]:
        operation = self.operation(operation_id) or {"operation_id": operation_id}
        operation.update(values)
        self._write_operation(operation)
        return operation

    def _prepare_update(self, operation_id: str) -> None:
        operation = self.operation(operation_id)
        if not operation:
            return
        try:
            atomic_write_json(
                self.maintenance_path,
                {"operation_id": operation_id, "started_at": utc_now()},
            )
            self._wait_until_idle(operation_id)
            target = require_commit_sha(str(operation["target_revision"]))
            references = image_references(self.config.image_prefix, target)
            previous_images = self._current_image_ids()
            self._update_operation(
                operation_id,
                status="running",
                phase="pulling_images",
                previous_images=previous_images,
            )
            self._docker_login()
            resolved_images: dict[str, str] = {}
            for kind, reference in references.items():
                run_command(["docker", "pull", reference])
                digest_reference = self._image_digest(reference)
                self._verify_image(digest_reference, target, kind)
                resolved_images[kind] = digest_reference
            container_name = self._reconciler_name(operation_id, "apply")
            self._update_operation(
                operation_id,
                phase="starting_reconciler",
                images=resolved_images,
                reconciler_container=container_name,
                watchdog_deadline_epoch=(
                    time.time() + self.config.reconcile_timeout_seconds
                ),
            )
            self._start_reconciler(
                operation_id,
                resolved_images["updater"],
                container_name=container_name,
                mode="apply",
            )
            self._update_operation(operation_id, phase="reconciling")
            self._monitor_operation(operation_id)
        except Exception as exc:  # noqa: BLE001
            current = self.operation(operation_id) or {}
            container_name = str(current.get("reconciler_container", ""))
            if (
                current.get("phase") == "starting_reconciler"
                and self._container_running(container_name) is True
            ):
                self._monitor_operation(operation_id)
                return
            self.maintenance_path.unlink(missing_ok=True)
            self._update_operation(
                operation_id,
                status="failed",
                phase="prepare_failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            with self._operation_lock:
                if self._active_operation_id == operation_id:
                    self._active_operation_id = None

    def _wait_until_idle(self, operation_id: str) -> None:
        deadline = time.monotonic() + self.config.drain_timeout_seconds
        while time.monotonic() < deadline:
            busy_threads = self._busy_thread_count()
            scheduler_status = read_json(self.config.scheduler_status_path, {})
            scheduler_active = bool(
                isinstance(scheduler_status, dict)
                and scheduler_status.get("active_run")
            )
            self._update_operation(
                operation_id,
                status="running",
                phase="waiting_for_idle",
                busy_threads=busy_threads,
                scheduler_active=scheduler_active,
            )
            if busy_threads == 0 and not scheduler_active:
                return
            time.sleep(3)
        raise TimeoutError("active tasks did not drain before update timeout")

    def _busy_thread_count(self) -> int:
        body = json.dumps(
            {"status": "busy", "limit": 1000, "select": ["thread_id"]}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.config.agent_url}/threads/search",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            value = json.load(response)
        return len(value) if isinstance(value, list) else 0

    def _docker_login(self) -> None:
        username = os.environ.get("GHCR_USERNAME", "").strip()
        token = os.environ.get("GHCR_TOKEN", "").strip()
        if username and token:
            run_command(
                ["docker", "login", "ghcr.io", "-u", username, "--password-stdin"],
                input_text=token,
                timeout_seconds=60,
            )

    def _current_image_ids(self) -> dict[str, str]:
        """读取 Compose 当前运行容器的内容地址，作为精确回滚基线。"""

        images = {
            kind: self._service_image_id(kind) for kind in ("agent", "web", "updater")
        }
        scheduler_image = self._service_image_id("scheduler")
        if scheduler_image != images["agent"]:
            raise RuntimeError("agent and scheduler are not running the same image")
        return images

    def _service_image_id(self, service: str) -> str:
        output = run_command(
            [
                "docker",
                "ps",
                "--filter",
                f"label=com.docker.compose.project={self.config.compose_project_name}",
                "--filter",
                f"label=com.docker.compose.service={service}",
                "--format",
                "{{.ID}}",
            ],
            timeout_seconds=30,
        )
        container_ids = [line.strip() for line in output.splitlines() if line.strip()]
        if len(container_ids) != 1:
            raise RuntimeError(
                f"expected one running Compose container for service {service}"
            )
        image_id = run_command(
            [
                "docker",
                "inspect",
                "--format",
                "{{.Image}}",
                container_ids[0],
            ],
            timeout_seconds=30,
        )
        return require_image_id(image_id)

    def _image_digest(self, reference: str) -> str:
        output = run_command(
            [
                "docker",
                "image",
                "inspect",
                reference,
                "--format",
                '{{ join .RepoDigests "\\n" }}',
            ]
        )
        expected_repository = reference.split(":sha-", 1)[0] + "@sha256:"
        candidates = [
            line.strip()
            for line in output.splitlines()
            if line.strip().startswith(expected_repository)
        ]
        if len(candidates) != 1:
            raise RuntimeError(
                f"unable to resolve one immutable digest for {reference}"
            )
        return candidates[0]

    def _verify_image(self, reference: str, revision: str, kind: str) -> None:
        require_image_digest(reference, kind, prefix=self.config.image_prefix)
        verify_signed_image(reference, revision, kind)

    @staticmethod
    def _reconciler_name(operation_id: str, mode: str) -> str:
        suffix = "" if mode == "apply" else "-rollback"
        return f"web-test-agent-reconcile-{operation_id[:12]}{suffix}"

    def _start_reconciler(
        self,
        operation_id: str,
        updater_image: str,
        *,
        container_name: str,
        mode: str,
    ) -> None:
        if mode not in {"apply", "rollback"}:
            raise ValueError(f"unknown reconcile mode: {mode}")
        if mode == "apply":
            require_image_digest(updater_image, "updater")
        else:
            require_image_id(updater_image)
        run_command(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                container_name,
                "--label",
                f"web-test-agent.update.operation={operation_id}",
                "--network",
                self.config.internal_network,
                "--volumes-from",
                os.environ.get("HOSTNAME", "web-test-agent-updater"),
                "-v",
                "/var/run/docker.sock:/var/run/docker.sock",
                "--read-only",
                "--tmpfs",
                "/tmp:size=64m,mode=1777",
                "-e",
                f"COMPOSE_PROJECT_NAME={self.config.compose_project_name}",
                "-e",
                f"UPDATE_OPERATION_ID={operation_id}",
                "-e",
                f"GHCR_IMAGE_PREFIX={self.config.image_prefix}",
                "-e",
                f"UPDATE_RECONCILE_MODE={mode}",
                "--entrypoint",
                "python",
                updater_image,
                "/app/reconcile.py",
            ],
            timeout_seconds=60,
        )

    def _monitor_operation(self, operation_id: str) -> None:
        """跨 updater 重建持续观察 helper，避免永久维护态。"""

        missing_checks = 0
        while True:
            operation = self.operation(operation_id)
            if not operation:
                break
            if operation.get("status") in STATE_TERMINAL:
                self._finish_operation(operation_id, operation)
                return
            watchdog_rollback = str(operation.get("phase", "")).startswith("watchdog_")
            container_key = (
                "rollback_container" if watchdog_rollback else "reconciler_container"
            )
            deadline_key = (
                "rollback_deadline_epoch"
                if watchdog_rollback
                else "watchdog_deadline_epoch"
            )
            container_name = str(operation.get(container_key, ""))
            try:
                deadline = float(operation.get(deadline_key, 0))
            except (TypeError, ValueError):
                deadline = 0
            running = (
                self._container_running(container_name) if container_name else None
            )
            missing_checks = 0 if running is True else missing_checks + 1
            timed_out = deadline <= 0 or time.time() >= deadline
            if watchdog_rollback and (timed_out or missing_checks >= 3):
                if not timed_out and self._restart_watchdog_rollback(
                    operation_id,
                    operation,
                ):
                    missing_checks = 0
                    time.sleep(3)
                    continue
                self._stop_container(container_name)
                self._fail_watchdog_rollback(
                    operation_id,
                    "rollback helper timed out"
                    if timed_out
                    else "rollback helper stopped before reporting completion",
                )
                return
            if not watchdog_rollback and (timed_out or missing_checks >= 3):
                self._start_watchdog_rollback(
                    operation_id,
                    operation,
                    "reconciler timed out"
                    if timed_out
                    else "reconciler stopped before reporting completion",
                )
                missing_checks = 0
            time.sleep(3)

        self._fail_watchdog_rollback(operation_id, "update operation state disappeared")

    def _container_running(self, container_name: str) -> bool | None:
        if not container_name:
            return None
        try:
            result = run_command(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.State.Running}}",
                    container_name,
                ],
                timeout_seconds=15,
            )
        except (RuntimeError, OSError, TimeoutError):
            return None
        return result.lower() == "true"

    def _start_watchdog_rollback(
        self,
        operation_id: str,
        operation: dict[str, Any],
        reason: str,
    ) -> None:
        main_container = str(operation.get("reconciler_container", ""))
        if not self._stop_container(main_container):
            self._fail_watchdog_rollback(
                operation_id,
                "could not stop the failed reconciler safely",
            )
            return
        previous_images = operation.get("previous_images")
        rollback_container = ""
        try:
            if not isinstance(previous_images, dict):
                raise RuntimeError("immutable rollback images are missing")
            for kind in ("agent", "web", "updater"):
                require_image_id(str(previous_images[kind]))
            updater_image = str(previous_images["updater"])
            rollback_container = self._reconciler_name(operation_id, "rollback")
            self._update_operation(
                operation_id,
                status="running",
                phase="watchdog_rollback",
                error=reason,
                rollback_container=rollback_container,
                rollback_deadline_epoch=(
                    time.time() + self.config.rollback_timeout_seconds
                ),
                rollback_attempt=1,
            )
            self._start_reconciler(
                operation_id,
                updater_image,
                container_name=rollback_container,
                mode="rollback",
            )
        except Exception as exc:  # noqa: BLE001
            if self._container_running(rollback_container) is True:
                return
            self._fail_watchdog_rollback(
                operation_id,
                f"could not start rollback helper: {type(exc).__name__}: {exc}",
            )

    def _restart_watchdog_rollback(
        self,
        operation_id: str,
        operation: dict[str, Any],
    ) -> bool:
        try:
            attempt = int(operation.get("rollback_attempt", 1))
        except (TypeError, ValueError):
            attempt = 1
        if attempt >= 2:
            return False
        container_name = str(operation.get("rollback_container", ""))
        if not self._stop_container(container_name):
            return False
        previous_images = operation.get("previous_images")
        if not isinstance(previous_images, dict):
            return False
        try:
            updater_image = require_image_id(str(previous_images["updater"]))
            self._update_operation(operation_id, rollback_attempt=attempt + 1)
            self._start_reconciler(
                operation_id,
                updater_image,
                container_name=container_name,
                mode="rollback",
            )
        except Exception:  # noqa: BLE001
            return self._container_running(container_name) is True
        return True

    def _stop_container(self, container_name: str) -> bool:
        if not container_name:
            return True
        try:
            run_command(
                ["docker", "rm", "--force", container_name],
                timeout_seconds=30,
            )
        except (RuntimeError, OSError, TimeoutError):
            return self._container_running(container_name) is not True
        return True

    def _fail_watchdog_rollback(self, operation_id: str, error: str) -> None:
        self._update_operation(
            operation_id,
            status="failed",
            phase="watchdog_rollback_failed",
            rollback_error=error,
        )
        operation = self.operation(operation_id) or {"operation_id": operation_id}
        self._finish_operation(operation_id, operation)

    def _finish_operation(
        self,
        operation_id: str,
        operation: dict[str, Any],
    ) -> None:
        if operation.get("phase") not in ROLLBACK_FAILURE_PHASES:
            self.maintenance_path.unlink(missing_ok=True)
        for key in ("reconciler_container", "rollback_container"):
            self._stop_container(str(operation.get(key, "")))
        with self._operation_lock:
            if self._active_operation_id == operation_id:
                self._active_operation_id = None

    def gate_open(self) -> bool:
        return not self.maintenance_path.exists()


def re_safe_operation_id(value: str) -> bool:
    return len(value) == 32 and all(
        character in "0123456789abcdef" for character in value
    )


def csrf_token(
    secret: bytes, *, now: int | None = None, nonce: str | None = None
) -> str:
    timestamp = int(time.time()) if now is None else now
    random_value = nonce or secrets.token_urlsafe(18)
    payload = f"{timestamp}.{random_value}"
    signature = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).digest()
    return (
        f"{payload}.{base64.urlsafe_b64encode(signature).decode('ascii').rstrip('=')}"
    )


def verify_csrf_token(secret: bytes, value: str, *, max_age: int = 3600) -> bool:
    if len(value) > 256:
        return False
    try:
        timestamp_text, nonce, signature_text = value.split(".", 2)
        timestamp = int(timestamp_text)
        if not nonce or len(nonce) > 64 or abs(int(time.time()) - timestamp) > max_age:
            return False
        payload = f"{timestamp}.{nonce}"
        padding = "=" * (-len(signature_text) % 4)
        signature = base64.b64decode(
            signature_text + padding,
            altchars=b"-_",
            validate=True,
        )
        if len(signature) != hashlib.sha256().digest_size:
            return False
        expected = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).digest()
        return hmac.compare_digest(signature, expected)
    except (ValueError, TypeError, base64.binascii.Error):
        return False


class UpdateRequestHandler(BaseHTTPRequestHandler):
    controller: UpdateController

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        if not self._internal_authorized():
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        if self.path == "/gate":
            status = (
                HTTPStatus.NO_CONTENT
                if self.controller.gate_open()
                else HTTPStatus.SERVICE_UNAVAILABLE
            )
            self.send_response(status)
            self.end_headers()
            return
        if self.path == "/csrf":
            token = csrf_token(self.controller.config.csrf_secret)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header(
                "Set-Cookie",
                f"{CSRF_COOKIE}={token}; Path=/; Max-Age=3600; "
                "HttpOnly; SameSite=Strict",
            )
            self.send_header("Cache-Control", "no-store")
            payload = json.dumps({"token": token}).encode("utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path in {"/status", "/check"}:
            try:
                result = self.controller.check_latest(force=self.path == "/check")
                self._json(HTTPStatus.OK, result)
            except Exception as exc:  # noqa: BLE001
                self._json(
                    HTTPStatus.BAD_GATEWAY,
                    {"error": f"{type(exc).__name__}: {exc}"},
                )
            return
        if self.path.startswith("/operations/"):
            operation = self.controller.operation(self.path.rsplit("/", 1)[-1])
            self._json(
                HTTPStatus.OK if operation else HTTPStatus.NOT_FOUND,
                operation or {"error": "operation not found"},
            )
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._internal_authorized():
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        if not self._browser_authorized():
            self._json(HTTPStatus.FORBIDDEN, {"error": "origin or CSRF check failed"})
            return
        if self.path != "/apply":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            operation = self.controller.begin_update()
            self._json(HTTPStatus.ACCEPTED, operation)
        except RuntimeError as exc:
            self._json(HTTPStatus.CONFLICT, {"error": str(exc)})
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            self._json(
                HTTPStatus.BAD_GATEWAY,
                {"error": f"{type(exc).__name__}: {exc}"},
            )
        except Exception as exc:  # noqa: BLE001
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"{type(exc).__name__}: {exc}"},
            )

    def _internal_authorized(self) -> bool:
        provided = self.headers.get("X-Internal-Token", "")
        return hmac.compare_digest(provided, self.controller.config.internal_token)

    def _browser_authorized(self) -> bool:
        expected_origin = self.controller.config.public_origin
        if (
            not expected_origin
            or self.headers.get("Origin", "").rstrip("/") != expected_origin
        ):
            return False
        header_token = self.headers.get("X-CSRF-Token", "")
        cookies = SimpleCookie(self.headers.get("Cookie", ""))
        cookie = cookies.get(CSRF_COOKIE)
        cookie_token = cookie.value if cookie else ""
        return hmac.compare_digest(header_token, cookie_token) and verify_csrf_token(
            self.controller.config.csrf_secret, header_token
        )

    def _json(self, status: HTTPStatus, payload: Any) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"updater_http {self.address_string()} {format % args}", flush=True)


def main() -> None:
    config = UpdaterConfig.from_environment()
    config.state_dir.mkdir(parents=True, exist_ok=True)
    UpdateRequestHandler.controller = UpdateController(config)
    server = ThreadingHTTPServer(("0.0.0.0", 8090), UpdateRequestHandler)
    print("updater listening on 0.0.0.0:8090", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

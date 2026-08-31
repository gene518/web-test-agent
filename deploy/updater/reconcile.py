"""在独立 helper 容器中重建 Compose 服务并执行健康回滚。"""

from __future__ import annotations

import os
import time
import urllib.request
from pathlib import Path
from typing import Any

from common import (
    ALLOWED_IMAGE_PREFIX,
    atomic_write_json,
    read_json,
    require_commit_sha,
    require_image_digest,
    require_image_id,
    run_command,
    verify_signed_image,
)


STATE_DIR = Path(os.environ.get("UPDATE_STATE_DIR", "/state"))
OPERATION_ID = os.environ["UPDATE_OPERATION_ID"]
OPERATION_PATH = STATE_DIR / "operations" / f"{OPERATION_ID}.json"
DEPLOYMENT_ENV_PATH = STATE_DIR / "deployment.env"
DEPLOYMENT_PATH = STATE_DIR / "deployment.json"
MAINTENANCE_PATH = STATE_DIR / "maintenance.json"
COMPOSE_FILE = os.environ.get("COMPOSE_FILE", "/deployment/compose.yaml")
BASE_ENV_FILE = os.environ.get("BASE_ENV_FILE", "/deployment/.env")
PROJECT_NAME = os.environ.get("COMPOSE_PROJECT_NAME", "web-test-agent")
IMAGE_PREFIX = os.environ.get("GHCR_IMAGE_PREFIX", ALLOWED_IMAGE_PREFIX)
if IMAGE_PREFIX != ALLOWED_IMAGE_PREFIX:
    raise RuntimeError("GHCR_IMAGE_PREFIX is not in the updater allowlist")
RECONCILE_MODE = os.environ.get("UPDATE_RECONCILE_MODE", "apply")


def update_operation(**values: Any) -> dict[str, Any]:
    operation = read_json(OPERATION_PATH, {"operation_id": OPERATION_ID})
    operation.update(values)
    operation["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    atomic_write_json(OPERATION_PATH, operation)
    return operation


def compose_arguments() -> list[str]:
    arguments = ["docker", "compose", "-p", PROJECT_NAME, "-f", COMPOSE_FILE]
    if Path(BASE_ENV_FILE).is_file():
        arguments.extend(["--env-file", BASE_ENV_FILE])
    arguments.extend(["--env-file", str(DEPLOYMENT_ENV_PATH)])
    return arguments


def deployment_env(revision: str, images: dict[str, str]) -> str:
    normalized_revision = require_commit_sha(revision)
    if set(images) != {"agent", "web", "updater"}:
        raise ValueError("deployment images must contain exactly three services")
    refs: dict[str, str] = {}
    for kind in ("agent", "web", "updater"):
        reference = str(images[kind])
        refs[kind] = (
            require_image_id(reference)
            if reference.startswith("sha256:")
            else require_image_digest(reference, kind, prefix=IMAGE_PREFIX)
        )
    return "\n".join(
        [
            f"DEPLOYED_SHA={normalized_revision}",
            f"AGENT_IMAGE={refs['agent']}",
            f"WEB_IMAGE={refs['web']}",
            f"UPDATER_IMAGE={refs['updater']}",
            "",
        ]
    )


def write_text_atomic(path: Path, value: str) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def wait_for_url(url: str, timeout_seconds: float = 180) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if 200 <= response.status < 300:
                    return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(2)
    raise TimeoutError(f"health check failed for {url}: {last_error}")


def apply_compose() -> None:
    run_command(
        [
            *compose_arguments(),
            "up",
            "-d",
            "--remove-orphans",
            "--wait",
        ],
        timeout_seconds=600,
    )
    wait_for_url("http://agent:2024/info")
    wait_for_url("http://web:8080/health")
    wait_for_url("http://updater:8090/health")
    wait_for_url("http://web:8080/health/agent")
    wait_for_url("http://web:8080/health/update")


def _operation() -> dict[str, Any]:
    operation = read_json(OPERATION_PATH, None)
    if not isinstance(operation, dict):
        raise RuntimeError("update operation state is missing")
    if operation.get("operation_id") != OPERATION_ID:
        raise RuntimeError("update operation identity does not match")
    return operation


def _previous_deployment(operation: dict[str, Any]) -> tuple[str, dict[str, str]]:
    revision = require_commit_sha(str(operation["current_revision"]))
    images = operation.get("previous_images")
    if not isinstance(images, dict) or set(images) != {"agent", "web", "updater"}:
        raise RuntimeError("immutable rollback image IDs are missing")
    return revision, {
        kind: require_image_id(str(images[kind]))
        for kind in ("agent", "web", "updater")
    }


def _record_deployment(revision: str, previous_revision: str) -> None:
    atomic_write_json(
        DEPLOYMENT_PATH,
        {
            "revision": require_commit_sha(revision),
            "previous_revision": require_commit_sha(previous_revision),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )


def _restore_previous(operation: dict[str, Any]) -> None:
    previous_revision, previous_images = _previous_deployment(operation)
    write_text_atomic(
        DEPLOYMENT_ENV_PATH,
        deployment_env(previous_revision, previous_images),
    )
    apply_compose()
    _record_deployment(previous_revision, str(operation["target_revision"]))


def _reconcile() -> None:
    operation = _operation()
    target = require_commit_sha(str(operation["target_revision"]))
    _previous_deployment(operation)
    images = operation.get("images")
    if not isinstance(images, dict) or set(images) != {"agent", "web", "updater"}:
        raise RuntimeError("resolved immutable image digests are missing")
    resolved_images = {
        kind: require_image_digest(str(images[kind]), kind, prefix=IMAGE_PREFIX)
        for kind in ("agent", "web", "updater")
    }
    for kind, reference in resolved_images.items():
        verify_signed_image(reference, target, kind)
    try:
        update_operation(status="running", phase="recreating_services")
        write_text_atomic(DEPLOYMENT_ENV_PATH, deployment_env(target, resolved_images))
        apply_compose()
        _record_deployment(target, str(operation["current_revision"]))
        update_operation(status="succeeded", phase="completed")
        MAINTENANCE_PATH.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        update_operation(
            status="running",
            phase="rolling_back",
            error=f"{type(exc).__name__}: {exc}",
        )
        try:
            _restore_previous(operation)
        except Exception as rollback_exc:  # noqa: BLE001
            update_operation(
                status="failed",
                phase="rollback_failed",
                rollback_error=f"{type(rollback_exc).__name__}: {rollback_exc}",
            )
        else:
            update_operation(status="rolled_back", phase="rollback_completed")
            MAINTENANCE_PATH.unlink(missing_ok=True)


def _watchdog_rollback() -> None:
    operation = _operation()
    try:
        update_operation(status="running", phase="watchdog_rolling_back")
        _restore_previous(operation)
    except Exception as exc:  # noqa: BLE001
        update_operation(
            status="failed",
            phase="watchdog_rollback_failed",
            rollback_error=f"{type(exc).__name__}: {exc}",
        )
        raise
    else:
        update_operation(status="rolled_back", phase="watchdog_rollback_completed")
        MAINTENANCE_PATH.unlink(missing_ok=True)


def main() -> None:
    try:
        if RECONCILE_MODE == "apply":
            _reconcile()
        elif RECONCILE_MODE == "rollback":
            _watchdog_rollback()
        else:
            raise RuntimeError(f"unknown reconcile mode: {RECONCILE_MODE}")
    except Exception as exc:  # noqa: BLE001
        try:
            operation = read_json(OPERATION_PATH, {})
            if not isinstance(operation, dict) or operation.get("status") not in {
                "failed",
                "rolled_back",
                "succeeded",
            }:
                update_operation(
                    status="failed",
                    phase="reconciler_crashed",
                    error=f"{type(exc).__name__}: {exc}",
                )
                MAINTENANCE_PATH.unlink(missing_ok=True)
        finally:
            raise


if __name__ == "__main__":
    main()

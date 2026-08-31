"""更新控制面的纯逻辑回归测试。"""

from __future__ import annotations

import io
import json
import os
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from common import (
    ALLOWED_CONTAINER_PUBLISH_JOB,
    ALLOWED_CONTAINER_WORKFLOW,
    ALLOWED_COSIGN_IDENTITY,
    atomic_write_json,
    image_references,
    read_json,
    require_commit_sha,
    require_image_digest,
    require_image_id,
    verify_signed_image,
)
from service import (
    UpdateController,
    UpdaterConfig,
    csrf_token,
    re_safe_operation_id,
    verify_csrf_token,
)


SHA = "a" * 40
OTHER_SHA = "b" * 40


def updater_config(state_dir: Path, *, current_revision: str = SHA) -> UpdaterConfig:
    return UpdaterConfig(
        state_dir=state_dir,
        github_repository="gene518/web-test-agent",
        github_workflow="ci.yml",
        github_branch="main",
        image_prefix="ghcr.io/gene518/web-test-agent",
        current_revision=current_revision,
        internal_token="i" * 32,
        csrf_secret=b"c" * 32,
        public_origin="http://example.test",
        agent_url="http://agent:2024",
        compose_project_name="web-test-agent",
        internal_network="web-test-agent-internal",
        scheduler_status_path=state_dir / "scheduler-status.json",
        drain_timeout_seconds=30,
        check_cache_seconds=1200,
        reconcile_timeout_seconds=3600,
        rollback_timeout_seconds=1800,
        cosign_identity=ALLOWED_COSIGN_IDENTITY,
    )


class UpdaterLogicTestCase(unittest.TestCase):
    def test_only_full_commit_sha_is_accepted(self) -> None:
        self.assertEqual(require_commit_sha(SHA.upper()), SHA)
        for invalid in ("main", "abc123", "g" * 40, "a" * 39):
            with self.assertRaises(ValueError):
                require_commit_sha(invalid)

    def test_image_references_are_fixed_to_known_kinds(self) -> None:
        self.assertEqual(
            image_references("ghcr.io/gene518/web-test-agent", SHA),
            {
                "agent": f"ghcr.io/gene518/web-test-agent-agent:sha-{SHA}",
                "web": f"ghcr.io/gene518/web-test-agent-web:sha-{SHA}",
                "updater": f"ghcr.io/gene518/web-test-agent-updater:sha-{SHA}",
            },
        )
        with self.assertRaises(ValueError):
            image_references("docker.io/other/repo", SHA)
        with self.assertRaises(ValueError):
            image_references("ghcr.io/other/repo", SHA)

    def test_digest_references_are_exactly_allowlisted(self) -> None:
        digest = "ghcr.io/gene518/web-test-agent-agent@sha256:" + "b" * 64
        self.assertEqual(require_image_digest(digest, "agent"), digest)
        for invalid in (
            "ghcr.io/other/repo-agent@sha256:" + "b" * 64,
            "ghcr.io/gene518/web-test-agent-web@sha256:" + "b" * 64,
            "ghcr.io/gene518/web-test-agent-agent@sha256:" + "b" * 63,
            digest + "\nAGENT_IMAGE=ghcr.io/other/repo:latest",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    require_image_digest(invalid, "agent")

    def test_docker_image_ids_are_full_content_addresses(self) -> None:
        image_id = "sha256:" + "c" * 64
        self.assertEqual(require_image_id(image_id), image_id)
        for invalid in ("c" * 64, "sha256:" + "c" * 63, image_id + "\nWEB_IMAGE=x"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    require_image_id(invalid)

    def test_signed_image_verification_checks_labels_and_cosign_identity(self) -> None:
        self.assertEqual(ALLOWED_CONTAINER_WORKFLOW, "ci.yml")
        self.assertEqual(
            ALLOWED_COSIGN_IDENTITY,
            "https://github.com/gene518/web-test-agent/.github/workflows/"
            "ci.yml@refs/heads/main",
        )
        digest = "ghcr.io/gene518/web-test-agent-agent@sha256:" + "b" * 64
        with patch(
            "common.run_command",
            side_effect=[
                "https://github.com/gene518/web-test-agent",
                SHA,
                "verified",
            ],
        ) as command:
            verify_signed_image(digest, SHA, "agent")
        cosign_arguments = command.call_args_list[-1].args[0]
        self.assertIn(ALLOWED_COSIGN_IDENTITY, cosign_arguments)
        self.assertEqual(cosign_arguments[-1], digest)

        with patch(
            "common.run_command",
            side_effect=["https://github.com/other/repo", SHA],
        ):
            with self.assertRaises(RuntimeError):
                verify_signed_image(digest, SHA, "agent")

    def test_csrf_token_is_signed_and_expires(self) -> None:
        secret = b"s" * 32
        token = csrf_token(secret, now=int(time.time()), nonce="fixed")
        self.assertTrue(verify_csrf_token(secret, token))
        self.assertFalse(verify_csrf_token(b"x" * 32, token))
        expired = csrf_token(secret, now=int(time.time()) - 7200, nonce="old")
        self.assertFalse(verify_csrf_token(secret, expired))
        self.assertFalse(verify_csrf_token(secret, "x" * 257))
        self.assertFalse(verify_csrf_token(secret, "0.nonce.%%%"))

    def test_operation_ids_cannot_escape_state_directory(self) -> None:
        self.assertTrue(re_safe_operation_id("f" * 32))
        self.assertFalse(re_safe_operation_id("../operation"))

    def test_json_state_is_replaced_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            atomic_write_json(path, {"revision": SHA})
            self.assertEqual(read_json(path, {}), {"revision": SHA})

    def test_mutable_tag_is_resolved_to_one_repository_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = UpdateController(updater_config(Path(directory)))
            tag = f"ghcr.io/gene518/web-test-agent-agent:sha-{SHA}"
            digest = "ghcr.io/gene518/web-test-agent-agent@sha256:" + "b" * 64
            with patch("service.run_command", return_value=digest):
                self.assertEqual(controller._image_digest(tag), digest)

    def test_environment_rejects_security_allowlist_overrides(self) -> None:
        environment = {
            "BUILD_SHA": SHA,
            "PUBLIC_ORIGIN": "http://example.test",
            "UPDATE_CSRF_SECRET": "c" * 32,
            "UPDATE_INTERNAL_TOKEN": "i" * 32,
        }
        invalid_values = {
            "GITHUB_REPOSITORY": "other/repository",
            "GITHUB_CONTAINER_WORKFLOW": "container.yml",
            "GHCR_IMAGE_PREFIX": "ghcr.io/other/repository",
            "COSIGN_CERTIFICATE_IDENTITY": "https://example.test/workflow",
        }
        for name, value in invalid_values.items():
            with self.subTest(name=name):
                with patch.dict(os.environ, {**environment, name: value}, clear=True):
                    with self.assertRaises(RuntimeError):
                        UpdaterConfig.from_environment()

    def test_environment_requires_distinct_secrets_and_valid_origin(self) -> None:
        environment = {
            "BUILD_SHA": SHA,
            "PUBLIC_ORIGIN": "http://example.test",
            "UPDATE_CSRF_SECRET": "s" * 32,
            "UPDATE_INTERNAL_TOKEN": "s" * 32,
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(RuntimeError):
                UpdaterConfig.from_environment()
        environment["UPDATE_INTERNAL_TOKEN"] = "i" * 32
        for origin in (
            "",
            "ftp://example.test",
            "http://user@example.test",
            "http://example.test/path",
        ):
            with self.subTest(origin=origin):
                environment["PUBLIC_ORIGIN"] = origin
                with patch.dict(os.environ, environment, clear=True):
                    with self.assertRaises(RuntimeError):
                        UpdaterConfig.from_environment()

    def test_latest_revision_must_be_verified_main_ci_push_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = UpdateController(updater_config(Path(directory)))
            run = {
                "id": 123,
                "html_url": "https://github.com/gene518/web-test-agent/actions/runs/123",
                "updated_at": "2026-08-30T00:00:00Z",
                "head_sha": OTHER_SHA,
                "head_branch": "main",
                "head_repository": {"full_name": "gene518/web-test-agent"},
                "status": "completed",
                "conclusion": "success",
                "event": "push",
            }

            def response_for(value: dict) -> io.BytesIO:
                return io.BytesIO(json.dumps(value).encode("utf-8"))

            published_job = {
                "name": ALLOWED_CONTAINER_PUBLISH_JOB,
                "status": "completed",
                "conclusion": "success",
            }

            with patch(
                "service.urllib.request.urlopen",
                side_effect=[
                    response_for({"workflow_runs": [run]}),
                    response_for({"jobs": [published_job]}),
                ],
            ) as urlopen:
                latest = controller.check_latest(force=True)
            self.assertEqual(latest["latest_revision"], OTHER_SHA)
            self.assertIn(
                "/actions/workflows/ci.yml/runs?branch=main&event=push",
                urlopen.call_args_list[0].args[0].full_url,
            )

            invalid_runs = (
                {**run, "event": "workflow_dispatch"},
                {**run, "head_branch": "feature"},
                {**run, "head_repository": {"full_name": "other/repository"}},
                {**run, "conclusion": "failure"},
            )
            for invalid in invalid_runs:
                with self.subTest(invalid=invalid):
                    with patch(
                        "service.urllib.request.urlopen",
                        return_value=response_for({"workflow_runs": [invalid]}),
                    ):
                        with self.assertRaises(RuntimeError):
                            controller.check_latest(force=True)

            with patch(
                "service.urllib.request.urlopen",
                side_effect=[
                    response_for({"workflow_runs": [run]}),
                    response_for({"jobs": []}),
                ],
            ):
                with self.assertRaises(RuntimeError):
                    controller.check_latest(force=True)

    def test_restart_clears_stale_maintenance_without_active_operation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            maintenance = state_dir / "maintenance.json"
            atomic_write_json(
                maintenance,
                {"operation_id": "f" * 32, "started_at": "stale"},
            )
            UpdateController(updater_config(state_dir))
            self.assertFalse(maintenance.exists())

    def test_running_images_capture_requires_agent_scheduler_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = UpdateController(updater_config(Path(directory)))
            image_ids = {
                "agent": "sha256:" + "1" * 64,
                "web": "sha256:" + "2" * 64,
                "updater": "sha256:" + "3" * 64,
                "scheduler": "sha256:" + "1" * 64,
            }
            with patch.object(
                controller,
                "_service_image_id",
                side_effect=lambda service: image_ids[service],
            ):
                self.assertEqual(
                    controller._current_image_ids(),
                    {kind: image_ids[kind] for kind in ("agent", "web", "updater")},
                )
            image_ids["scheduler"] = "sha256:" + "4" * 64
            with patch.object(
                controller,
                "_service_image_id",
                side_effect=lambda service: image_ids[service],
            ):
                with self.assertRaises(RuntimeError):
                    controller._current_image_ids()

    def test_drain_reads_scheduler_state_from_its_separate_read_only_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory) / "updater"
            scheduler_status_path = Path(directory) / "scheduler" / "status.json"
            atomic_write_json(scheduler_status_path, {"active_run": "nightly"})
            config = replace(
                updater_config(state_dir),
                scheduler_status_path=scheduler_status_path,
                drain_timeout_seconds=1,
            )
            controller = UpdateController(config)
            operation_id = "d" * 32
            controller._write_operation({"operation_id": operation_id})
            with (
                patch.object(controller, "_busy_thread_count", return_value=0),
                patch("service.time.monotonic", side_effect=[0, 0, 2]),
                patch("service.time.sleep"),
                self.assertRaises(TimeoutError),
            ):
                controller._wait_until_idle(operation_id)
            operation = controller.operation(operation_id)
            self.assertTrue(operation["scheduler_active"])

    def test_watchdog_starts_bounded_rollback_helper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = UpdateController(updater_config(Path(directory)))
            operation_id = "f" * 32
            previous_updater = "sha256:" + "3" * 64
            operation = {
                "operation_id": operation_id,
                "status": "running",
                "phase": "reconciling",
                "images": {
                    "agent": "ghcr.io/gene518/web-test-agent-agent@sha256:" + "7" * 64,
                    "web": "ghcr.io/gene518/web-test-agent-web@sha256:" + "8" * 64,
                    "updater": "ghcr.io/gene518/web-test-agent-updater@sha256:"
                    + "9" * 64,
                },
                "previous_images": {
                    "agent": "sha256:" + "1" * 64,
                    "web": "sha256:" + "2" * 64,
                    "updater": previous_updater,
                },
                "reconciler_container": "apply-helper",
            }
            controller._write_operation(operation)
            with (
                patch.object(controller, "_stop_container", return_value=True) as stop,
                patch.object(controller, "_start_reconciler") as start,
            ):
                controller._start_watchdog_rollback(
                    operation_id,
                    operation,
                    "reconciler timed out",
                )
            stop.assert_called_once_with("apply-helper")
            start.assert_called_once_with(
                operation_id,
                previous_updater,
                container_name=controller._reconciler_name(operation_id, "rollback"),
                mode="rollback",
            )
            updated = controller.operation(operation_id)
            self.assertEqual(updated["phase"], "watchdog_rollback")
            self.assertGreater(updated["rollback_deadline_epoch"], time.time())

    def test_restart_keeps_maintenance_after_failed_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            operation_id = "e" * 32
            atomic_write_json(
                state_dir / "operations" / f"{operation_id}.json",
                {
                    "operation_id": operation_id,
                    "status": "failed",
                    "phase": "watchdog_rollback_failed",
                },
            )
            maintenance = state_dir / "maintenance.json"
            atomic_write_json(maintenance, {"operation_id": operation_id})
            controller = UpdateController(updater_config(state_dir))
            self.assertTrue(maintenance.exists())
            self.assertFalse(controller.gate_open())


if __name__ == "__main__":
    unittest.main()

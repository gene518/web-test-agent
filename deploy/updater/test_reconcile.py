"""独立 Reconciler 的签名复核、部署与回滚测试。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from common import atomic_write_json, read_json


os.environ.setdefault("UPDATE_OPERATION_ID", "0" * 32)
import reconcile  # noqa: E402


CURRENT_SHA = "a" * 40
TARGET_SHA = "b" * 40


def digest(kind: str, character: str) -> str:
    return f"ghcr.io/gene518/web-test-agent-{kind}@sha256:" + character * 64


def operation(operation_id: str) -> dict[str, object]:
    return {
        "operation_id": operation_id,
        "status": "running",
        "phase": "reconciling",
        "current_revision": CURRENT_SHA,
        "target_revision": TARGET_SHA,
        "images": {
            "agent": digest("agent", "1"),
            "web": digest("web", "2"),
            "updater": digest("updater", "3"),
        },
        "previous_images": {
            "agent": "sha256:" + "4" * 64,
            "web": "sha256:" + "5" * 64,
            "updater": "sha256:" + "6" * 64,
        },
    }


class ReconcileTestCase(unittest.TestCase):
    def paths(self, directory: str, operation_id: str) -> dict[str, object]:
        state = Path(directory)
        return {
            "OPERATION_ID": operation_id,
            "OPERATION_PATH": state / "operations" / f"{operation_id}.json",
            "DEPLOYMENT_ENV_PATH": state / "deployment.env",
            "DEPLOYMENT_PATH": state / "deployment.json",
            "MAINTENANCE_PATH": state / "maintenance.json",
        }

    def test_apply_rechecks_every_signature_before_compose(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            operation_id = "1" * 32
            paths = self.paths(directory, operation_id)
            atomic_write_json(paths["OPERATION_PATH"], operation(operation_id))
            with (
                patch.multiple(reconcile, **paths),
                patch.object(reconcile, "verify_signed_image") as verify,
                patch.object(reconcile, "apply_compose") as apply,
            ):
                reconcile._reconcile()
            self.assertEqual(
                verify.call_args_list,
                [
                    call(digest("agent", "1"), TARGET_SHA, "agent"),
                    call(digest("web", "2"), TARGET_SHA, "web"),
                    call(digest("updater", "3"), TARGET_SHA, "updater"),
                ],
            )
            apply.assert_called_once_with()
            result = read_json(paths["OPERATION_PATH"], {})
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(
                read_json(paths["DEPLOYMENT_PATH"], {})["revision"], TARGET_SHA
            )
            deployment = paths["DEPLOYMENT_ENV_PATH"].read_text(encoding="utf-8")
            self.assertIn(f"AGENT_IMAGE={digest('agent', '1')}", deployment)

    def test_apply_failure_restores_exact_previous_image_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            operation_id = "2" * 32
            paths = self.paths(directory, operation_id)
            atomic_write_json(paths["OPERATION_PATH"], operation(operation_id))
            with (
                patch.multiple(reconcile, **paths),
                patch.object(reconcile, "verify_signed_image"),
                patch.object(
                    reconcile,
                    "apply_compose",
                    side_effect=[RuntimeError("target unhealthy"), None],
                ) as apply,
            ):
                reconcile._reconcile()
            self.assertEqual(apply.call_count, 2)
            result = read_json(paths["OPERATION_PATH"], {})
            self.assertEqual(result["status"], "rolled_back")
            self.assertEqual(
                read_json(paths["DEPLOYMENT_PATH"], {})["revision"], CURRENT_SHA
            )
            deployment = paths["DEPLOYMENT_ENV_PATH"].read_text(encoding="utf-8")
            self.assertIn("AGENT_IMAGE=sha256:" + "4" * 64, deployment)
            self.assertIn("WEB_IMAGE=sha256:" + "5" * 64, deployment)
            self.assertIn("UPDATER_IMAGE=sha256:" + "6" * 64, deployment)

    def test_watchdog_rollback_uses_same_immutable_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            operation_id = "3" * 32
            paths = self.paths(directory, operation_id)
            atomic_write_json(paths["OPERATION_PATH"], operation(operation_id))
            with (
                patch.multiple(reconcile, **paths),
                patch.object(reconcile, "apply_compose") as apply,
            ):
                reconcile._watchdog_rollback()
            apply.assert_called_once_with()
            result = read_json(paths["OPERATION_PATH"], {})
            self.assertEqual(result["phase"], "watchdog_rollback_completed")
            self.assertEqual(result["status"], "rolled_back")
            self.assertEqual(
                read_json(paths["DEPLOYMENT_PATH"], {})["revision"], CURRENT_SHA
            )

    def test_tampered_image_reference_is_rejected_before_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            operation_id = "4" * 32
            paths = self.paths(directory, operation_id)
            value = operation(operation_id)
            value["images"]["agent"] = (
                "ghcr.io/other/repository-agent@sha256:" + "1" * 64
            )
            atomic_write_json(paths["OPERATION_PATH"], value)
            with (
                patch.multiple(reconcile, **paths),
                patch.object(reconcile, "verify_signed_image") as verify,
                patch.object(reconcile, "apply_compose") as apply,
            ):
                with self.assertRaises(ValueError):
                    reconcile._reconcile()
            verify.assert_not_called()
            apply.assert_not_called()
            self.assertFalse(paths["DEPLOYMENT_ENV_PATH"].exists())


if __name__ == "__main__":
    unittest.main()

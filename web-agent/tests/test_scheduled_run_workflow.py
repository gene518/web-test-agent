from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any

from deep_agent.agent.scheduled_run.scheduled_run_agent import (
    ScheduledProgressMonitor,
    ScheduledRunAgent,
    TaskHealerPolicy,
    read_task_healer_policy,
)
from deep_agent.core.config import AppSettings
from deep_agent.scheduler.report_models import (
    ScheduledFailureDiagnosis,
    ScheduledRunReport,
)
from deep_agent.scheduler.runner import (
    LangGraphScheduledTaskRunner,
    PendingScheduledRun,
    ScheduledRunResult,
    serialize_run_request,
    serialize_run_result,
    scheduled_run_thread_id,
)
from deep_agent.scheduler.summary import ScheduledRunSummaryNode
from deep_agent.scheduled_run_workflow import build_scheduled_run_workflow


FAILED_OUTPUT = (
    "  1) [chromium] › test_case/checkout/a_checkout.spec.ts:12:3 › checkout › submits order ─────",
    "    Error: expect(received).toBe(expected)",
    "    Expected: 200",
    "    Received: 500",
    "  1 failed",
    "    [chromium] › test_case/checkout/a_checkout.spec.ts:12:3 › checkout › submits order",
)


class FakeDiagnosisAnalyzer:
    def __init__(
        self,
        *,
        owner: str = "test_automation",
        confidence: float = 0.95,
        repair_allowed: bool = True,
    ) -> None:
        self.owner = owner
        self.confidence = confidence
        self.repair_allowed = repair_allowed
        self.policies: list[TaskHealerPolicy] = []

    async def diagnose(self, report, policy, *, config=None):  # noqa: ANN001
        self.policies.append(policy)
        return [
            ScheduledFailureDiagnosis(
                test_id=case.test_id,
                owner=self.owner,
                confidence=self.confidence,
                repair_allowed=self.repair_allowed,
                reason="定位器已失效，属于测试自动化维护问题。",
            )
            for case in report.failed_cases
        ]


class FakePlaywrightRunner:
    def __init__(self, observer, calls: list[str]):  # noqa: ANN001
        self.observer = observer
        self.calls = calls

    async def run(self, run_request) -> ScheduledRunResult:  # noqa: ANN001
        self.calls.append(run_request.run_key)
        for line in FAILED_OUTPUT:
            await self.observer(line)
        return ScheduledRunResult(
            exit_code=1,
            duration_seconds=1.2,
            output_lines=FAILED_OUTPUT,
            output_line_count=len(FAILED_OUTPUT),
        )


class FakeHealer:
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self.calls = calls

    async def execute(self, state, config=None):  # noqa: ANN001
        self.calls.append(state)
        script = state["extracted_params"]["test_scripts"][0]
        return {
            "stage_result": {
                "agent_type": "healer",
                "status": "success",
                "artifact": {
                    "output_files": [script],
                    "validation_runs": [script],
                },
                "raw_result": {},
            }
        }


class PassthroughStageFinalizer:
    async def execute(self, state, config=None):  # noqa: ANN001
        return state


class FakeScheduledFinalizer:
    async def finalize(self, report, *, config=None):  # noqa: ANN001
        return f"报告完成：{report.execution.status}"


class FakeThreads:
    def __init__(self, final_values: dict[str, Any] | None = None) -> None:
        self.final_values = final_values
        self.created: list[dict[str, Any]] = []

    async def create(self, **kwargs):  # noqa: ANN003
        self.created.append(kwargs)
        return {"thread_id": kwargs["thread_id"]}

    async def get_state(self, thread_id):  # noqa: ANN001
        return {"values": self.final_values or {}}


class FakeRuns:
    def __init__(self, final_values: dict[str, Any] | None = None) -> None:
        self.final_values = final_values
        self.created: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.cancelled: list[tuple[str, str]] = []

    async def create(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.created.append((args, kwargs))
        return {"run_id": "remote-run"}

    async def join(self, thread_id, run_id):  # noqa: ANN001
        return self.final_values or {}

    async def cancel(self, thread_id, run_id, wait=False):  # noqa: ANN001
        self.cancelled.append((thread_id, run_id))


class FakeClient:
    def __init__(self, final_values: dict[str, Any] | None = None) -> None:
        self.threads = FakeThreads(final_values)
        self.runs = FakeRuns(final_values)


class UnavailableThreads:
    async def create(self, **kwargs):  # noqa: ANN003
        raise ConnectionError("connection refused")


class UnavailableClient:
    def __init__(self) -> None:
        self.threads = UnavailableThreads()
        self.runs = FakeRuns()


class ScheduledRunWorkflowTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.project_dir = self.root / "demo"
        self.test_root = self.project_dir / "test_case"
        self.spec_dir = self.test_root / "checkout"
        self.spec_dir.mkdir(parents=True)
        self.plan_path = self.spec_dir / "aaa_checkout.md"
        self.plan_path.write_text("# Checkout plan\n", encoding="utf-8")
        self.spec_path = self.spec_dir / "a_checkout.spec.ts"
        self.spec_path.write_text(
            "// spec: test_case/checkout/aaa_checkout.md\n"
            "import { test } from '@playwright/test';\n",
            encoding="utf-8",
        )
        (self.project_dir / "task-healer.md").write_text(
            "定位器过期属于测试自动化问题；产品返回 500 属于产品问题。\n",
            encoding="utf-8",
        )
        self.request = PendingScheduledRun(
            project_name="demo",
            project_dir=self.project_dir,
            test_root_dir=self.test_root,
            task_id="daily-checkout",
            schedule="0 9 * * *",
            locations=("test_case/checkout/a_checkout.spec.ts",),
            headed=False,
            timezone="Asia/Shanghai",
            scheduled_minute=datetime.fromisoformat("2026-08-30T09:00:00+08:00"),
            log_file_path=self.test_root / "scheduler-service.log",
        )
        self.settings = AppSettings(
            default_automation_project_root=str(self.root),
            scheduler_monitor_heartbeat_seconds=30,
            scheduler_auto_heal_enabled=True,
            scheduler_auto_heal_confidence_threshold=0.8,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_graph_runs_analysis_and_one_scoped_healer_then_is_idempotent(
        self,
    ) -> None:
        runner_calls: list[str] = []
        healer_calls: list[dict[str, Any]] = []
        analyzer = FakeDiagnosisAnalyzer()
        agent = ScheduledRunAgent(
            self.settings,
            diagnosis_analyzer=analyzer,
            task_runner_factory=lambda observer: FakePlaywrightRunner(
                observer, runner_calls
            ),
            healer_factory=lambda allowed, shared: FakeHealer(healer_calls),
            finalizer=FakeScheduledFinalizer(),
            healer_stage_finalizer=PassthroughStageFinalizer(),
        )
        graph = build_scheduled_run_workflow(scheduled_run_agent=agent)
        graph_input = {
            "run_request": serialize_run_request(self.request),
            "conversation_thread_id": scheduled_run_thread_id(self.request),
        }

        result = await graph.ainvoke(
            graph_input,
            config={"configurable": {"thread_id": scheduled_run_thread_id(self.request)}},
        )

        report = ScheduledRunReport.model_validate(result["report"])
        self.assertEqual(report.schema_version, 2)
        self.assertEqual(report.diagnoses[0].owner, "test_automation")
        self.assertEqual(report.healing.status, "succeeded")
        self.assertEqual(report.healing.validation_status, "passed")
        self.assertEqual(
            report.conversation.thread_id, scheduled_run_thread_id(self.request)
        )
        self.assertEqual(report.conversation.status, "completed")
        self.assertEqual(len(runner_calls), 1)
        self.assertEqual(len(healer_calls), 1)
        self.assertEqual(
            healer_calls[0]["extracted_params"]["test_plan_files"],
            ["test_case/checkout/aaa_checkout.md"],
        )
        self.assertEqual(
            analyzer.policies[0].content,
            "定位器过期属于测试自动化问题；产品返回 500 属于产品问题。\n",
        )
        self.assertTrue(Path(report.artifacts.analysis_report).is_file())

        replay = await graph.ainvoke(
            {
                **result,
                "run_request": serialize_run_request(self.request),
                "conversation_thread_id": scheduled_run_thread_id(self.request),
            },
            config={"configurable": {"thread_id": scheduled_run_thread_id(self.request)}},
        )
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(len(runner_calls), 1)
        self.assertEqual(len(healer_calls), 1)

    async def test_product_diagnosis_never_invokes_healer(self) -> None:
        healer_calls: list[dict[str, Any]] = []
        runner_calls: list[str] = []
        agent = ScheduledRunAgent(
            self.settings,
            diagnosis_analyzer=FakeDiagnosisAnalyzer(
                owner="product", confidence=0.99, repair_allowed=True
            ),
            task_runner_factory=lambda observer: FakePlaywrightRunner(
                observer, runner_calls
            ),
            healer_factory=lambda allowed, shared: FakeHealer(healer_calls),
            finalizer=FakeScheduledFinalizer(),
            healer_stage_finalizer=PassthroughStageFinalizer(),
        )

        result = await build_scheduled_run_workflow(
            scheduled_run_agent=agent
        ).ainvoke(
            {
                "run_request": serialize_run_request(self.request),
                "conversation_thread_id": scheduled_run_thread_id(self.request),
            },
            config={"configurable": {"thread_id": scheduled_run_thread_id(self.request)}},
        )

        report = ScheduledRunReport.model_validate(result["report"])
        self.assertFalse(report.diagnoses[0].repair_allowed)
        self.assertEqual(report.healing.status, "not_eligible")
        self.assertEqual(healer_calls, [])

    async def test_sdk_runner_creates_deterministic_readonly_thread(self) -> None:
        report_payload = {"artifacts": {"analysis_report": "/tmp/report.json"}}
        final_values = {
            "execution_result": serialize_run_result(
                ScheduledRunResult(exit_code=0, duration_seconds=0.2)
            ),
            "report": report_payload,
        }
        client = FakeClient(final_values)
        runner = LangGraphScheduledTaskRunner(
            api_url="http://127.0.0.1:2024",
            client_factory=lambda: client,
        )

        first = await runner.run(self.request)
        second = await runner.run(self.request)

        self.assertTrue(first.report_generated)
        self.assertEqual(first.report_path, "/tmp/report.json")
        self.assertEqual(first.conversation_thread_id, second.conversation_thread_id)
        metadata = client.threads.created[0]["metadata"]
        self.assertEqual(metadata["graph_id"], "web-autotest-agent")
        self.assertEqual(metadata["run_graph_id"], "web-autotest-scheduled-run")
        self.assertTrue(metadata["readonly"])
        self.assertEqual(metadata["thread_type"], "scheduled_run")
        self.assertEqual(client.threads.created[0]["if_exists"], "do_nothing")
        _, run_kwargs = client.runs.created[0]
        self.assertEqual(run_kwargs["multitask_strategy"], "reject")
        self.assertEqual(run_kwargs["stream_mode"], ["values", "custom"])

    async def test_unavailable_api_produces_schema_v2_error_report(self) -> None:
        runner = LangGraphScheduledTaskRunner(
            api_url="http://127.0.0.1:2024",
            client_factory=UnavailableClient,
        )

        result = await runner.run(self.request)
        summary = await ScheduledRunSummaryNode().execute(self.request, result)

        self.assertEqual(result.exit_code, 1)
        self.assertIn("connection refused", result.error_message or "")
        self.assertEqual(summary.report.schema_version, 2)
        self.assertEqual(summary.report.conversation.status, "error")
        self.assertIn(
            "connection refused", summary.report.conversation.error_message or ""
        )

    async def test_progress_monitor_emits_only_changed_heartbeat(self) -> None:
        monitor = ScheduledProgressMonitor(run_id="run", heartbeat_seconds=0.01)
        heartbeat = __import__("asyncio").create_task(monitor.heartbeat())
        await monitor.observe_line(
            "  ✓ [chromium] › test_case/a.spec.ts:1:1 › suite › case"
        )
        await __import__("asyncio").sleep(0.025)
        first_count = len(monitor.messages)
        await __import__("asyncio").sleep(0.02)
        monitor.stop()
        await heartbeat

        self.assertGreaterEqual(first_count, 2)
        self.assertEqual(len(monitor.messages), first_count)

    def test_task_healer_policy_is_exact_utf8_bounded_and_never_follows_symlink(
        self,
    ) -> None:
        policy = read_task_healer_policy(self.project_dir)
        self.assertIn("定位器过期", policy.content or "")

        policy_path = self.project_dir / "task-healer.md"
        policy_path.unlink()
        self.assertIsNone(read_task_healer_policy(self.project_dir).content)

        external = self.root / "external.md"
        external.write_text("do not read", encoding="utf-8")
        try:
            policy_path.symlink_to(external)
        except OSError:
            self.skipTest("当前文件系统不支持 symlink")
        linked = read_task_healer_policy(self.project_dir)
        self.assertIsNone(linked.content)
        self.assertIn("符号链接", linked.warning or "")

        policy_path.unlink()
        policy_path.write_bytes(b"x" * (32 * 1024 + 1))
        oversized = read_task_healer_policy(self.project_dir)
        self.assertIsNone(oversized.content)
        self.assertIn("32 KiB", oversized.warning or "")

    async def test_schema_v1_payload_remains_readable(self) -> None:
        summary = await ScheduledRunSummaryNode().execute(
            self.request,
            ScheduledRunResult(exit_code=0, duration_seconds=0.1),
        )
        legacy_payload = summary.report.model_dump(mode="json")
        legacy_payload["schema_version"] = 1
        legacy_payload.pop("diagnoses")
        legacy_payload.pop("healing")
        legacy_payload.pop("conversation")

        restored = ScheduledRunReport.model_validate(legacy_payload)

        self.assertEqual(restored.schema_version, 1)
        self.assertEqual(restored.diagnoses, [])
        self.assertEqual(restored.healing.status, "not_needed")
        self.assertEqual(restored.conversation.status, "unavailable")

    async def test_cancelled_remote_run_report_keeps_monitor_thread(self) -> None:
        thread_id = scheduled_run_thread_id(self.request)
        summary = await ScheduledRunSummaryNode().execute(
            self.request,
            ScheduledRunResult(
                exit_code=130,
                duration_seconds=0.1,
                cancelled=True,
                conversation_thread_id=thread_id,
            ),
        )

        self.assertEqual(summary.report.conversation.thread_id, thread_id)
        self.assertEqual(summary.report.conversation.status, "cancelled")


if __name__ == "__main__":
    unittest.main()

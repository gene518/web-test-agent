from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from deep_agent.core.config import AppSettings
from deep_agent.scheduler.analysis import parse_playwright_output
from deep_agent.scheduler.runner import PendingScheduledRun, ScheduledRunResult
from deep_agent.scheduler.service import SchedulerService
from deep_agent.scheduler.summary import ScheduledRunSummaryNode


FLAKY_OUTPUT = (
    "  ✘  1 [chromium] › test_case/checkout.spec.ts:12:3 › checkout › submits order (1.0s)",
    "    Error: expect(received).toBe(expected)",
    "    Expected: 200",
    "    Received: 500",
    "    Retry #1",
    "  ✓  2 [chromium] › test_case/checkout.spec.ts:12:3 › checkout › submits order (retry #1) (0.5s)",
    "  1 flaky",
    "    [chromium] › test_case/checkout.spec.ts:12:3 › checkout › submits order",
    "  1 passed (2.0s)",
)

FAILED_OUTPUT = (
    "  1) [chromium] › test_case/checkout.spec.ts:12:3 › checkout › submits order ─────",
    "    Error: expect(received).toBe(expected)",
    "    Expected: 200",
    "    Received: 500",
    "  1 failed",
    "    [chromium] › test_case/checkout.spec.ts:12:3 › checkout › submits order",
)


class RaisingRunner:
    async def run(self, run_request) -> ScheduledRunResult:  # noqa: ANN001
        raise RuntimeError("Playwright executable is missing")


class RaisingSummaryNode:
    async def execute(self, run_request, result):  # noqa: ANN001
        raise RuntimeError("optional summary model unavailable")


class SchedulerSummaryTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)
        self.project_dir = self.root_path / "demo"
        self.test_root_dir = self.project_dir / "test_case"
        self.test_root_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _run_request(
        self,
        *,
        scheduled_minute: datetime | None = None,
    ) -> PendingScheduledRun:
        return PendingScheduledRun(
            project_name="demo",
            project_dir=self.project_dir,
            test_root_dir=self.test_root_dir,
            task_id="daily-checkout",
            schedule="0 9 * * *",
            locations=("test_case/checkout.spec.ts",),
            headed=False,
            timezone="Asia/Shanghai",
            scheduled_minute=scheduled_minute
            or datetime.fromisoformat("2026-08-29T09:00:00+08:00"),
            log_file_path=self.test_root_dir / "scheduler-service.log",
        )

    def test_parser_identifies_failed_and_retried_cases_with_reasons(self) -> None:
        parsed = parse_playwright_output(
            (
                *FLAKY_OUTPUT,
                "  1 failed",
                "    [chromium] › test_case/payment.spec.ts:8:2 › payment › pays",
            )
        )

        self.assertEqual(parsed.counts.passed, 1)
        self.assertEqual(parsed.counts.failed, 1)
        self.assertEqual(parsed.counts.flaky, 1)
        self.assertEqual(len(parsed.failed_cases), 1)
        self.assertEqual(parsed.failed_cases[0].title, "payment › pays")
        self.assertEqual(len(parsed.retried_cases), 1)
        retried_case = parsed.retried_cases[0]
        self.assertEqual(retried_case.retry_count, 1)
        self.assertEqual(retried_case.final_status, "flaky")
        self.assertTrue(
            any("Expected: 200" in reason for reason in retried_case.retry_reasons)
        )

    async def test_summary_node_persists_report_and_aggregates_task_history(
        self,
    ) -> None:
        summary_node = ScheduledRunSummaryNode()
        first_run = self._run_request()

        first_summary = await summary_node.execute(
            first_run,
            ScheduledRunResult(
                exit_code=1,
                duration_seconds=3.2,
                output_lines=FAILED_OUTPUT,
                output_line_count=len(FAILED_OUTPUT),
            ),
        )

        self.assertTrue(first_summary.report_path.is_file())
        self.assertTrue(first_summary.latest_report_path.is_file())
        self.assertTrue(first_summary.markdown_report_path.is_file())
        self.assertTrue(first_summary.latest_markdown_report_path.is_file())
        self.assertEqual(first_summary.report.execution.status, "failed")
        self.assertEqual(len(first_summary.report.failed_cases), 1)
        self.assertEqual(first_summary.report.history.analyzed_runs, 1)
        self.assertEqual(first_summary.report.common_issues[0].category, "assertion")
        self.assertEqual(
            sum(
                issue.current_occurrences
                for issue in first_summary.report.common_issues
            ),
            3,
        )
        markdown_report = first_summary.markdown_report_path.read_text(encoding="utf-8")
        self.assertIn("# Scheduler 执行分析报告", markdown_report)
        self.assertIn("## 失败用例", markdown_report)
        self.assertIn("## 共性问题", markdown_report)

        legacy_payload = json.loads(
            first_summary.report_path.read_text(encoding="utf-8")
        )
        legacy_payload["artifacts"].pop("analysis_report_markdown")
        legacy_payload["artifacts"].pop("latest_analysis_report_markdown")
        first_summary.report_path.write_text(
            json.dumps(legacy_payload, ensure_ascii=False),
            encoding="utf-8",
        )

        second_run = self._run_request(
            scheduled_minute=first_run.scheduled_minute + timedelta(days=1)
        )
        second_summary = await summary_node.execute(
            second_run,
            ScheduledRunResult(
                exit_code=0,
                duration_seconds=2.0,
                output_lines=FLAKY_OUTPUT,
                output_line_count=len(FLAKY_OUTPUT),
            ),
        )

        report = second_summary.report
        self.assertEqual(report.execution.status, "passed_with_retries")
        self.assertEqual(len(report.retried_cases), 1)
        self.assertEqual(report.history.analyzed_runs, 2)
        self.assertEqual(report.history.successful_runs, 1)
        self.assertEqual(report.history.failed_runs, 1)
        self.assertEqual(report.history.runs_with_retries, 1)
        assertion_issue = next(
            issue for issue in report.common_issues if issue.category == "assertion"
        )
        self.assertTrue(assertion_issue.recurring)
        self.assertEqual(assertion_issue.affected_run_count, 2)
        persisted_report = json.loads(
            second_summary.latest_report_path.read_text(encoding="utf-8")
        )
        self.assertEqual(persisted_report["run"]["run_id"], report.run.run_id)
        self.assertIn("成功率 50.0%", persisted_report["conclusion"])

    async def test_service_summarizes_runner_exception(self) -> None:
        settings = AppSettings(
            default_automation_project_root=str(self.root_path),
            scheduler_config_path=str(self.root_path / "scheduler_tasks.json"),
        )
        service = SchedulerService(
            settings=settings,
            config_path=self.root_path / "scheduler_tasks.json",
            task_runner=RaisingRunner(),
        )
        run_request = self._run_request()

        await service._enqueue_run(run_request)
        await service._start_next_run_if_idle()
        await service.drain()

        report_paths = list(self.test_root_dir.glob("scheduler-reports/*/[0-9]*.json"))
        self.assertEqual(len(report_paths), 1)
        report = json.loads(report_paths[0].read_text(encoding="utf-8"))
        self.assertEqual(report["execution"]["status"], "error")
        self.assertIn(
            "Playwright executable is missing", report["execution"]["error_message"]
        )
        log_text = run_request.log_file_path.read_text(encoding="utf-8")
        self.assertIn("总结阶段开始", log_text)
        self.assertIn("总结阶段完成", log_text)

    async def test_history_retry_rate_counts_summary_only_flaky_runs(self) -> None:
        summary_node = ScheduledRunSummaryNode()
        first_run = self._run_request()
        first_summary = await summary_node.execute(
            first_run,
            ScheduledRunResult(
                exit_code=0,
                duration_seconds=1.0,
                output_lines=("  1 flaky",),
                output_line_count=1,
            ),
        )
        self.assertEqual(first_summary.report.execution.status, "passed_with_retries")
        self.assertEqual(first_summary.report.retried_cases, [])
        self.assertEqual(first_summary.report.history.runs_with_retries, 1)

        second_summary = await summary_node.execute(
            self._run_request(
                scheduled_minute=first_run.scheduled_minute + timedelta(days=1)
            ),
            ScheduledRunResult(
                exit_code=0,
                duration_seconds=1.0,
                output_lines=("  1 passed",),
                output_line_count=1,
            ),
        )

        self.assertEqual(second_summary.report.history.analyzed_runs, 2)
        self.assertEqual(second_summary.report.history.runs_with_retries, 1)
        self.assertEqual(second_summary.report.history.retry_rate, 0.5)

    async def test_service_falls_back_when_injected_summary_stage_fails(self) -> None:
        settings = AppSettings(
            default_automation_project_root=str(self.root_path),
            scheduler_config_path=str(self.root_path / "scheduler_tasks.json"),
        )
        service = SchedulerService(
            settings=settings,
            config_path=self.root_path / "scheduler_tasks.json",
            summary_node=RaisingSummaryNode(),
        )
        run_request = self._run_request()

        await service._record_run_result(
            run_request,
            ScheduledRunResult(exit_code=0, duration_seconds=0.1),
        )

        latest_reports = list(
            self.test_root_dir.glob("scheduler-reports/*/latest.json")
        )
        self.assertEqual(len(latest_reports), 1)
        report = json.loads(latest_reports[0].read_text(encoding="utf-8"))
        self.assertEqual(report["execution"]["status"], "passed")
        log_text = run_request.log_file_path.read_text(encoding="utf-8")
        self.assertIn("总结阶段异常", log_text)
        self.assertIn("总结阶段完成", log_text)


if __name__ == "__main__":
    unittest.main()

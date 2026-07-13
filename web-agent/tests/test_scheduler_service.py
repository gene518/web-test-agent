from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from deep_agent.agent.scheduler import SchedulerAgent
from deep_agent.core.config import AppSettings
from deep_agent.scheduler.service import PendingScheduledRun, PlaywrightTaskRunner, ScheduledRunResult, SchedulerService
from deep_agent.scheduler.store import (
    generate_scheduled_task_id,
    load_scheduler_config,
    update_existing_task_config,
    upsert_auto_scheduled_task_config,
)


class FakeTaskRunner:
    def __init__(self) -> None:
        self.run_order: list[str] = []

    async def run(self, run_request) -> ScheduledRunResult:  # noqa: ANN001
        self.run_order.append(run_request.display_name)
        await asyncio.sleep(0)
        return ScheduledRunResult(exit_code=0, duration_seconds=0.01)


class FakeMasterAgent:
    async def summarize_final_response(self, *, state, stage_name, raw_result, config=None):  # noqa: ANN001
        return f"{stage_name}: {raw_result}"


class FakeProcessOutput:
    async def readline(self) -> bytes:
        return b""


class FakeProcess:
    stdout = FakeProcessOutput()

    async def wait(self) -> int:
        return 0


class SchedulerServiceTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)
        self.projects_root = self.root_path / "projects"
        self.project_dir = self.projects_root / "demo"
        (self.project_dir / "test_case").mkdir(parents=True, exist_ok=True)
        self.config_path = self.root_path / "scheduler_tasks.json"
        self.settings = AppSettings(
            default_automation_project_root=str(self.projects_root),
            scheduler_config_path=str(self.config_path),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_config(self) -> None:
        self.config_path.write_text(
            json.dumps(
                {
                    "scheduler": {"poll_interval_seconds": 30},
                    "projects": [
                        {
                            "project_name": "demo",
                            "headed": False,
                            "tasks": [
                                {
                                    "task_id": "daily_smoke",
                                    "schedule": "0 9 * * *",
                                    "locations": ["test_case/demo/a_case.spec.ts"],
                                    "enabled": True,
                                },
                                {
                                    "task_id": "daily_regression",
                                    "schedule": "0 9 * * *",
                                    "locations": ["test_case/demo/b_case.spec.ts"],
                                    "enabled": True,
                                },
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def test_default_scheduler_config_path_uses_server_root(self) -> None:
        settings = AppSettings(scheduler_config_path=None)

        self.assertEqual(
            settings.resolved_scheduler_config_path,
            Path(__file__).resolve().parents[1] / "scheduler_tasks.json",
        )

    def test_relative_scheduler_config_path_uses_server_root(self) -> None:
        settings = AppSettings(scheduler_config_path="runtime/scheduler_tasks.json")

        self.assertEqual(
            settings.resolved_scheduler_config_path,
            Path(__file__).resolve().parents[1] / "runtime" / "scheduler_tasks.json",
        )

    def test_update_existing_task_config_updates_only_existing_task(self) -> None:
        self._write_config()

        result = update_existing_task_config(
            settings=self.settings,
            config_path=self.config_path,
            project_name="demo",
            project_dir=None,
            task_id="daily_smoke",
            schedule="15 10 * * *",
            headed=True,
            enabled=False,
            locations=["test_case/demo/updated.spec.ts"],
        )

        self.assertEqual(result["status"], "success")
        config_model = load_scheduler_config(self.config_path)
        first_task = config_model.projects[0].tasks[0]
        self.assertEqual(first_task.schedule, "15 10 * * *")
        self.assertTrue(first_task.headed)
        self.assertFalse(first_task.enabled)
        self.assertEqual(first_task.locations, ["test_case/demo/updated.spec.ts"])

    def test_upsert_auto_task_creates_config_and_stable_system_id(self) -> None:
        result = upsert_auto_scheduled_task_config(
            settings=self.settings,
            config_path=self.config_path,
            project_name=None,
            project_dir=str(self.project_dir),
            schedule="15 10 * * *",
        )

        expected_task_id = generate_scheduled_task_id(self.project_dir)
        self.assertEqual(result["operation"], "created")
        self.assertEqual(result["task_id"], expected_task_id)
        config_model = load_scheduler_config(self.config_path)
        self.assertEqual(len(config_model.projects), 1)
        self.assertEqual(config_model.projects[0].project_dir, str(self.project_dir.resolve()))
        self.assertEqual(config_model.projects[0].tasks[0].task_id, expected_task_id)
        self.assertEqual(config_model.projects[0].tasks[0].locations, [])

        updated_result = upsert_auto_scheduled_task_config(
            settings=self.settings,
            config_path=self.config_path,
            project_name=None,
            project_dir=str(self.project_dir),
            schedule="20 11 * * *",
        )

        self.assertEqual(updated_result["operation"], "updated")
        updated_config = load_scheduler_config(self.config_path)
        self.assertEqual(len(updated_config.projects), 1)
        self.assertEqual(len(updated_config.projects[0].tasks), 1)
        self.assertEqual(updated_config.projects[0].tasks[0].schedule, "20 11 * * *")

    async def test_scheduler_service_logs_startup_and_runs_tasks_serially(self) -> None:
        self._write_config()
        runner = FakeTaskRunner()
        service = SchedulerService(
            settings=self.settings,
            config_path=self.config_path,
            task_runner=runner,
            current_time_factory=lambda: datetime.fromisoformat("2026-05-02T09:00:00+08:00"),
        )

        await service.poll_once()
        await service.drain()

        self.assertEqual(
            runner.run_order,
            ["demo/daily_smoke", "demo/daily_regression"],
        )
        log_text = (self.project_dir / "test_case" / "scheduler-service.log").read_text(encoding="utf-8")
        self.assertIn("调度服务已加载项目", log_text)
        self.assertIn("任务冲突", log_text)
        self.assertIn("任务开始", log_text)
        self.assertIn("任务结束", log_text)

    async def test_playwright_runner_disables_auto_opening_html_report(self) -> None:
        run_request = PendingScheduledRun(
            project_name="demo",
            project_dir=self.project_dir,
            test_root_dir=self.project_dir / "test_case",
            task_id="system-task",
            schedule="0 9 * * *",
            locations=(),
            headed=False,
            timezone=None,
            scheduled_minute=datetime.fromisoformat("2026-07-13T03:45:00-04:00"),
            log_file_path=self.project_dir / "test_case" / "scheduler-service.log",
        )

        with patch(
            "deep_agent.scheduler.service.asyncio.create_subprocess_exec",
            return_value=FakeProcess(),
        ) as create_process:
            result = await PlaywrightTaskRunner().run(run_request)

        self.assertEqual(result.exit_code, 0)
        process_env = create_process.call_args.kwargs["env"]
        self.assertEqual(process_env["PLAYWRIGHT_HTML_OPEN"], "never")

    async def test_scheduler_agent_creates_config_without_task_id(self) -> None:
        scheduler_agent = SchedulerAgent(FakeMasterAgent(), self.settings)

        result = await scheduler_agent.execute(
            {
                "messages": [],
                "extracted_params": {
                    "project_dir": str(self.project_dir),
                    "schedule_cron": "30 11 * * *",
                    "schedule_headed": True,
                },
            }
        )

        updated_task = load_scheduler_config(self.config_path).projects[0].tasks[0]
        self.assertEqual(updated_task.task_id, generate_scheduled_task_id(self.project_dir))
        self.assertEqual(updated_task.schedule, "30 11 * * *")
        self.assertTrue(updated_task.headed)
        self.assertEqual(result["stage_result"]["agent_type"], "scheduler")
        self.assertIn("Scheduler Agent", result["messages"][0].content)
        self.assertEqual(len(result["display_messages"]), 1)
        self.assertIn("Scheduler Agent", result["display_messages"][0].content)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from deep_agent.agent.scheduler import SchedulerAgent
from deep_agent.core.config import AppSettings
from deep_agent.scheduler.service import ScheduledRunResult, SchedulerService
from deep_agent.scheduler.store import load_scheduler_config, update_existing_task_config


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

    async def test_scheduler_agent_updates_config_file(self) -> None:
        self._write_config()
        scheduler_agent = SchedulerAgent(FakeMasterAgent(), self.settings)

        result = await scheduler_agent.execute(
            {
                "messages": [],
                "extracted_params": {
                    "project_name": "demo",
                    "schedule_task_id": "daily_smoke",
                    "schedule_cron": "30 11 * * *",
                    "schedule_headed": True,
                },
            }
        )

        updated_task = load_scheduler_config(self.config_path).projects[0].tasks[0]
        self.assertEqual(updated_task.schedule, "30 11 * * *")
        self.assertTrue(updated_task.headed)
        self.assertEqual(result["stage_result"]["agent_type"], "scheduler")
        self.assertIn("Scheduler Agent", result["messages"][0].content)


if __name__ == "__main__":
    unittest.main()

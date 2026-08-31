from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from deep_agent.agent.finalizer import SCHEDULER_FINALIZE_CONFIG, FinalizeStageNode
from deep_agent.agent.scheduler import SchedulerAgent
from deep_agent.core.config import AppSettings
from deep_agent.scheduler.cron import CronExpression
from deep_agent.scheduler.paths import resolve_scheduler_locations
from deep_agent.scheduler.service import (
    PendingScheduledRun,
    PlaywrightTaskRunner,
    ScheduledRunResult,
    SchedulerService,
)
from deep_agent.scheduler.store import (
    generate_scheduled_task_id,
    load_scheduler_config,
    resolve_scheduler_log_path,
    resolve_scheduler_project_dir,
    save_scheduler_config,
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


class CanonicalFinalizerAgent:
    async def finalize_stage(self, *, canonical_summary, **kwargs):  # noqa: ANN003
        return canonical_summary


class FakeProcessOutput:
    async def readline(self) -> bytes:
        return b""


class FakeProcess:
    stdout = FakeProcessOutput()

    async def wait(self) -> int:
        return 0


class HangingFakeProcess:
    stdout = None
    pid = 12345
    returncode = None

    async def wait(self) -> int:
        await asyncio.Future()
        return 0


class BlockingTaskRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def run(self, run_request) -> ScheduledRunResult:  # noqa: ANN001
        self.started.set()
        try:
            await asyncio.Future()
        finally:
            self.cancelled.set()


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

    def _run_request(
        self,
        *,
        task_id: str = "system-task",
        scheduled_minute: datetime | None = None,
        timeout_seconds: float = 1800,
    ) -> PendingScheduledRun:
        return PendingScheduledRun(
            project_name="demo",
            project_dir=self.project_dir,
            test_root_dir=self.project_dir / "test_case",
            task_id=task_id,
            schedule="* * * * *",
            locations=(),
            headed=False,
            timezone=None,
            scheduled_minute=scheduled_minute
            or datetime.fromisoformat("2026-07-13T03:45:00-04:00"),
            log_file_path=self.project_dir / "test_case" / "scheduler-service.log",
            timeout_seconds=timeout_seconds,
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

    def test_cron_scalar_step_expands_until_field_maximum(self) -> None:
        expression = CronExpression.parse("5/10 * * * *")

        self.assertEqual(expression.minute.values, frozenset({5, 15, 25, 35, 45, 55}))

    def test_scheduler_relative_project_paths_cannot_escape_automation_root(
        self,
    ) -> None:
        for project_name, project_dir in (
            ("../../outside", None),
            (None, "../../outside"),
        ):
            with self.subTest(project_name=project_name, project_dir=project_dir):
                with self.assertRaisesRegex(RuntimeError, "不能逃逸|安全目录名"):
                    resolve_scheduler_project_dir(
                        settings=self.settings,
                        project_name=project_name,
                        project_dir=project_dir,
                    )

    def test_scheduler_explicit_absolute_project_dir_can_be_external(self) -> None:
        external_project_dir = self.root_path / "external" / "demo"

        self.assertEqual(
            resolve_scheduler_project_dir(
                settings=self.settings,
                project_name=None,
                project_dir=str(external_project_dir),
            ),
            external_project_dir.resolve(),
        )

    def test_scheduler_test_root_cannot_escape_project(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "test_root_dir.*不能逃逸"):
            resolve_scheduler_log_path(
                settings=self.settings,
                project_name="demo",
                project_dir=None,
                test_root_dir="../../logs",
            )

    def test_scheduler_locations_must_be_project_relative_files_or_directories(
        self,
    ) -> None:
        outside_path = self.root_path / "outside.spec.ts"
        for location in (
            "../../outside.spec.ts",
            str(outside_path.resolve()),
            "test_case/**/*.spec.ts",
        ):
            with (
                self.subTest(location=location),
                self.assertRaisesRegex(RuntimeError, "location|locations"),
            ):
                resolve_scheduler_locations(
                    project_dir=self.project_dir,
                    locations=[location],
                )

    def test_scheduler_locations_reject_symlink_escape(self) -> None:
        external_dir = self.root_path / "external-tests"
        external_dir.mkdir()
        linked_dir = self.project_dir / "linked-tests"
        linked_dir.symlink_to(external_dir, target_is_directory=True)

        with self.assertRaisesRegex(RuntimeError, "locations.*不能逃逸"):
            resolve_scheduler_locations(
                project_dir=self.project_dir,
                locations=["linked-tests/case.spec.ts"],
            )

    def test_scheduler_locations_are_normalized_relative_to_project(self) -> None:
        self.assertEqual(
            resolve_scheduler_locations(
                project_dir=self.project_dir,
                locations=["test_case/demo/../smoke.spec.ts"],
            ),
            ("test_case/smoke.spec.ts",),
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

    def test_update_existing_task_rejects_location_outside_project(self) -> None:
        self._write_config()

        with self.assertRaisesRegex(RuntimeError, "locations.*不能逃逸"):
            update_existing_task_config(
                settings=self.settings,
                config_path=self.config_path,
                project_name="demo",
                project_dir=None,
                task_id="daily_smoke",
                locations=["../../outside.spec.ts"],
            )

        unchanged_task = load_scheduler_config(self.config_path).projects[0].tasks[0]
        self.assertEqual(
            unchanged_task.locations,
            ["test_case/demo/a_case.spec.ts"],
        )

    def test_scheduler_config_save_uses_atomic_replace(self) -> None:
        self._write_config()
        config_model = load_scheduler_config(self.config_path)

        with patch(
            "deep_agent.scheduler.store.os.replace", wraps=os.replace
        ) as replace_file:
            save_scheduler_config(self.config_path, config_model)

        replace_file.assert_called_once()
        self.assertEqual(load_scheduler_config(self.config_path), config_model)
        self.assertEqual(
            list(self.config_path.parent.glob(f".{self.config_path.name}.*.tmp")), []
        )

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
        self.assertEqual(
            config_model.projects[0].project_dir, str(self.project_dir.resolve())
        )
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
            current_time_factory=lambda: datetime.fromisoformat(
                "2026-05-02T09:00:00+08:00"
            ),
        )

        await service.poll_once()
        await service.drain()

        self.assertEqual(
            runner.run_order,
            ["demo/daily_smoke", "demo/daily_regression"],
        )
        log_text = (self.project_dir / "test_case" / "scheduler-service.log").read_text(
            encoding="utf-8"
        )
        self.assertIn("调度服务已加载项目", log_text)
        self.assertIn("任务冲突", log_text)
        self.assertIn("任务开始", log_text)
        self.assertIn("任务结束", log_text)

    async def test_scheduler_service_compensates_for_a_missed_poll_minute(self) -> None:
        self.config_path.write_text(
            json.dumps(
                {
                    "scheduler": {
                        "poll_interval_seconds": 30,
                        "misfire_grace_seconds": 300,
                    },
                    "projects": [
                        {
                            "project_name": "demo",
                            "tasks": [
                                {
                                    "task_id": "missed-run",
                                    "schedule": "1 9 * * *",
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        current_time = [datetime.fromisoformat("2026-05-02T09:00:00+08:00")]
        runner = FakeTaskRunner()
        service = SchedulerService(
            settings=self.settings,
            config_path=self.config_path,
            task_runner=runner,
            current_time_factory=lambda: current_time[0],
        )

        await service.poll_once()
        current_time[0] = datetime.fromisoformat("2026-05-02T09:02:00+08:00")
        await service.poll_once()
        await service.drain()

        self.assertEqual(runner.run_order, ["demo/missed-run"])

    async def test_scheduler_service_skips_invalid_project_path_without_stopping_poll(
        self,
    ) -> None:
        self.config_path.write_text(
            json.dumps(
                {
                    "projects": [
                        {
                            "project_name": "../../outside",
                            "tasks": [{"task_id": "invalid", "schedule": "* * * * *"}],
                        },
                        {
                            "project_name": "demo",
                            "tasks": [{"task_id": "valid", "schedule": "* * * * *"}],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        runner = FakeTaskRunner()
        service = SchedulerService(
            settings=self.settings,
            config_path=self.config_path,
            task_runner=runner,
            current_time_factory=lambda: datetime.fromisoformat(
                "2026-05-02T09:00:00+08:00"
            ),
        )

        await service.poll_once()
        await service.drain()

        self.assertEqual(runner.run_order, ["demo/valid"])

    async def test_scheduler_service_skips_invalid_location_and_runs_valid_task(
        self,
    ) -> None:
        self.config_path.write_text(
            json.dumps(
                {
                    "projects": [
                        {
                            "project_name": "demo",
                            "tasks": [
                                {
                                    "task_id": "invalid",
                                    "schedule": "* * * * *",
                                    "locations": ["../../outside.spec.ts"],
                                },
                                {
                                    "task_id": "valid",
                                    "schedule": "* * * * *",
                                    "locations": ["test_case/smoke.spec.ts"],
                                },
                            ],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        runner = FakeTaskRunner()
        service = SchedulerService(
            settings=self.settings,
            config_path=self.config_path,
            task_runner=runner,
            current_time_factory=lambda: datetime.fromisoformat(
                "2026-05-02T09:00:00+08:00"
            ),
        )

        await service.poll_once()
        await service.drain()

        self.assertEqual(runner.run_order, ["demo/valid"])

    async def test_scheduler_coalesces_same_task_and_bounds_pending_queue(self) -> None:
        service = SchedulerService(
            settings=self.settings,
            config_path=self.config_path,
            task_runner=FakeTaskRunner(),
        )
        service._max_pending_runs = 1
        first_minute = datetime.fromisoformat("2026-05-02T09:00:00+08:00")

        await service._enqueue_run(self._run_request(scheduled_minute=first_minute))
        await service._enqueue_run(
            self._run_request(scheduled_minute=first_minute + timedelta(minutes=1))
        )
        await service._enqueue_run(
            self._run_request(task_id="another-task", scheduled_minute=first_minute)
        )

        self.assertEqual(len(service._pending_runs), 1)
        self.assertEqual(
            service._pending_runs[0].scheduled_minute,
            first_minute + timedelta(minutes=1),
        )
        log_text = (self.project_dir / "test_case" / "scheduler-service.log").read_text(
            encoding="utf-8"
        )
        self.assertIn("任务合并", log_text)
        self.assertIn("reason=queue_full", log_text)

    async def test_scheduler_stop_cancels_active_run_and_discards_pending_runs(
        self,
    ) -> None:
        self._write_config()
        runner = BlockingTaskRunner()
        service = SchedulerService(
            settings=self.settings,
            config_path=self.config_path,
            task_runner=runner,
            current_time_factory=lambda: datetime.fromisoformat(
                "2026-05-02T09:00:00+08:00"
            ),
        )

        await service.poll_once()
        await runner.started.wait()
        await service.stop()

        self.assertTrue(runner.cancelled.is_set())
        self.assertIsNone(service._active_run_task)
        self.assertEqual(len(service._pending_runs), 0)
        log_text = (self.project_dir / "test_case" / "scheduler-service.log").read_text(
            encoding="utf-8"
        )
        self.assertIn("reason=scheduler_shutdown state=running", log_text)
        self.assertIn("reason=scheduler_shutdown state=pending", log_text)
        latest_reports = list(
            (self.project_dir / "test_case").glob("scheduler-reports/*/latest.json")
        )
        self.assertEqual(len(latest_reports), 1)
        cancelled_report = json.loads(latest_reports[0].read_text(encoding="utf-8"))
        self.assertEqual(cancelled_report["execution"]["status"], "cancelled")
        self.assertTrue(latest_reports[0].with_suffix(".md").is_file())

    async def test_scheduler_stop_during_poll_cannot_leave_pending_runs(self) -> None:
        self._write_config()
        service = SchedulerService(
            settings=self.settings,
            config_path=self.config_path,
            task_runner=FakeTaskRunner(),
            current_time_factory=lambda: datetime.fromisoformat(
                "2026-05-02T09:00:00+08:00"
            ),
        )
        poll_blocked = asyncio.Event()
        release_poll = asyncio.Event()

        async def block_startup_logs(projects) -> None:  # noqa: ANN001
            poll_blocked.set()
            await release_poll.wait()

        with patch.object(
            service,
            "_ensure_project_startup_logs",
            side_effect=block_startup_logs,
        ):
            poll_task = asyncio.create_task(service.poll_once())
            await poll_blocked.wait()
            stop_task = asyncio.create_task(service.stop())
            await asyncio.sleep(0)
            release_poll.set()
            await asyncio.gather(poll_task, stop_task)

        self.assertTrue(service._shutdown_complete)
        self.assertIsNone(service._active_run_task)
        self.assertEqual(len(service._pending_runs), 0)

    async def test_playwright_runner_disables_auto_opening_html_report(self) -> None:
        run_request = self._run_request()

        with patch(
            "deep_agent.scheduler.runner.asyncio.create_subprocess_exec",
            return_value=FakeProcess(),
        ) as create_process:
            result = await PlaywrightTaskRunner().run(run_request)

        self.assertEqual(result.exit_code, 0)
        process_env = create_process.call_args.kwargs["env"]
        self.assertEqual(process_env["PLAYWRIGHT_HTML_OPEN"], "never")
        if os.name == "posix":
            self.assertTrue(create_process.call_args.kwargs["start_new_session"])

    async def test_playwright_runner_times_out_and_terminates_process_tree(
        self,
    ) -> None:
        run_request = self._run_request(timeout_seconds=0.01)
        terminate_process = AsyncMock()

        with (
            patch(
                "deep_agent.scheduler.runner.asyncio.create_subprocess_exec",
                return_value=HangingFakeProcess(),
            ),
            patch(
                "deep_agent.scheduler.runner._terminate_process_tree",
                terminate_process,
            ),
        ):
            result = await PlaywrightTaskRunner().run(run_request)

        self.assertEqual(result.exit_code, 124)
        self.assertTrue(result.timed_out)
        terminate_process.assert_awaited_once()

    async def test_playwright_runner_cancellation_terminates_process_tree(self) -> None:
        run_request = self._run_request()
        terminate_process = AsyncMock()

        with (
            patch(
                "deep_agent.scheduler.runner.asyncio.create_subprocess_exec",
                return_value=HangingFakeProcess(),
            ),
            patch(
                "deep_agent.scheduler.runner._terminate_process_tree",
                terminate_process,
            ),
        ):
            run_task = asyncio.create_task(PlaywrightTaskRunner().run(run_request))
            await asyncio.sleep(0)
            run_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await run_task

        terminate_process.assert_awaited_once()

    async def test_scheduler_agent_creates_config_without_task_id(self) -> None:
        scheduler_agent = SchedulerAgent(self.settings)

        state = {
            "messages": [],
            "extracted_params": {
                "project_dir": str(self.project_dir),
                "schedule_cron": "30 11 * * *",
                "schedule_headed": True,
            },
        }
        stage_result = await scheduler_agent.execute(state)
        result = await FinalizeStageNode(
            CanonicalFinalizerAgent(), SCHEDULER_FINALIZE_CONFIG
        ).execute({**state, **stage_result})

        updated_task = load_scheduler_config(self.config_path).projects[0].tasks[0]
        self.assertEqual(
            updated_task.task_id, generate_scheduled_task_id(self.project_dir)
        )
        self.assertEqual(updated_task.schedule, "30 11 * * *")
        self.assertTrue(updated_task.headed)
        self.assertEqual(result["stage_result"]["agent_type"], "scheduler")
        self.assertIn("**Scheduler 阶段**", result["messages"][0].content)
        self.assertIn(
            f"- 项目目录：`{self.project_dir.resolve()}`",
            result["messages"][0].content,
        )
        self.assertIn(
            f"- 配置文件：`{self.config_path.resolve()}`",
            result["messages"][0].content,
        )
        self.assertIn("- Cron：`30 11 * * *`", result["messages"][0].content)
        self.assertEqual(len(result["display_messages"]), 1)
        self.assertEqual(
            result["stage_result"]["stage_summary"]["text"],
            result["display_messages"][0].content,
        )

    async def test_scheduler_agent_failure_uses_a_stable_stage_summary(self) -> None:
        scheduler_agent = SchedulerAgent(self.settings)

        state = {
            "messages": [],
            "extracted_params": {
                "project_dir": str(self.project_dir),
                "schedule_cron": "invalid cron",
            },
        }
        stage_result = await scheduler_agent.execute(state)
        result = await FinalizeStageNode(
            CanonicalFinalizerAgent(), SCHEDULER_FINALIZE_CONFIG
        ).execute({**state, **stage_result})

        summary = result["messages"][0].content
        self.assertIn("**Scheduler 阶段**", summary)
        self.assertIn("- 状态：失败", summary)
        self.assertIn(f"- 配置文件：`{self.config_path.resolve()}`", summary)


if __name__ == "__main__":
    unittest.main()

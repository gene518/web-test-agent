"""独立于 Agent 的定时任务扫描与执行服务。"""

from __future__ import annotations

import asyncio
import os
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

from deep_agent.core.config import AppSettings
from deep_agent.core.runtime_logging import get_logger, log_title
from deep_agent.scheduler.cron import CronExpression
from deep_agent.scheduler.models import ScheduledProjectConfig
from deep_agent.scheduler.store import load_scheduler_config, resolve_scheduler_log_path, resolve_scheduler_project_dir


logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PendingScheduledRun:
    """一次等待执行的任务请求。"""

    project_name: str
    project_dir: Path
    test_root_dir: Path
    task_id: str
    schedule: str
    locations: tuple[str, ...]
    headed: bool
    timezone: str | None
    scheduled_minute: datetime
    log_file_path: Path

    @property
    def run_key(self) -> str:
        """返回本次调度实例的去重键。"""

        return (
            f"{self.project_dir}::"
            f"{self.task_id}::"
            f"{self.scheduled_minute.isoformat(timespec='minutes')}"
        )

    @property
    def display_name(self) -> str:
        """返回便于日志打印的任务展示名。"""

        return f"{self.project_name}/{self.task_id}"


@dataclass(frozen=True, slots=True)
class ScheduledRunResult:
    """一次任务执行完成后的结果。"""

    exit_code: int
    duration_seconds: float


class ScheduledTaskRunner(Protocol):
    """定时任务执行器协议，便于测试注入假实现。"""

    async def run(self, run_request: PendingScheduledRun) -> ScheduledRunResult:
        """执行单个排队任务。"""


class PlaywrightTaskRunner:
    """基于 `npx playwright test` 的默认任务执行器。"""

    async def run(self, run_request: PendingScheduledRun) -> ScheduledRunResult:
        """在目标项目目录串行执行 Playwright 测试。"""

        report_name = (
            f"scheduled-{run_request.task_id}-"
            f"{run_request.scheduled_minute.strftime('%Y%m%d-%H%M')}"
        )
        command = ["npx", "playwright", "test", *run_request.locations]
        env = os.environ.copy()
        env["PWTEST_HEADED"] = "1" if run_request.headed else "0"
        env["PW_TEST_REPORT_NAME"] = report_name
        env["PW_SCHEDULE_TASK_ID"] = run_request.task_id
        env["PW_SCHEDULE_PROJECT_NAME"] = run_request.project_name
        env["PW_SCHEDULED_FOR"] = run_request.scheduled_minute.isoformat(timespec="minutes")

        started_at = monotonic()
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(run_request.project_dir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        if process.stdout is not None:
            while True:
                raw_line = await process.stdout.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue
                await _append_project_log(
                    run_request.log_file_path,
                    f"{_log_timestamp()} INFO [{run_request.task_id}] {line}",
                )

        exit_code = await process.wait()
        duration_seconds = round(monotonic() - started_at, 3)
        return ScheduledRunResult(exit_code=exit_code, duration_seconds=duration_seconds)


class SchedulerService:
    """扫描配置文件并按 Cron 串行执行测试任务。"""

    def __init__(
        self,
        *,
        settings: AppSettings,
        config_path: Path,
        task_runner: ScheduledTaskRunner | None = None,
        current_time_factory: Callable[[], datetime] | None = None,
    ) -> None:
        """初始化调度服务。"""

        self._settings = settings
        self._config_path = config_path.expanduser().resolve()
        self._task_runner = task_runner or PlaywrightTaskRunner()
        self._current_time_factory = current_time_factory or (lambda: datetime.now().astimezone())
        self._pending_runs: deque[PendingScheduledRun] = deque()
        self._last_scheduled_minutes: dict[tuple[str, str], str] = {}
        self._active_run: PendingScheduledRun | None = None
        self._active_run_task: asyncio.Task[ScheduledRunResult] | None = None
        self._poll_interval_seconds = settings.scheduler_poll_interval_seconds
        self._startup_logged_projects: set[str] = set()

    async def run_forever(self) -> None:
        """持续扫描配置并执行到点任务。"""

        logger.info(
            "%s 定时执行服务启动 config_path=%s",
            log_title("初始化", "调度服务"),
            self._config_path,
        )
        while True:
            await self.poll_once()
            await asyncio.sleep(self._poll_interval_seconds)

    async def poll_once(self) -> None:
        """执行一次扫描周期。"""

        await self._harvest_finished_run()

        try:
            config_model = load_scheduler_config(self._config_path)
        except RuntimeError as exc:
            logger.warning("%s 加载定时任务配置失败：%s", log_title("执行", "调度服务"), exc)
            return

        self._poll_interval_seconds = config_model.scheduler.poll_interval_seconds
        await self._ensure_project_startup_logs(config_model.projects)
        due_runs = self._collect_due_runs(config_model.projects)
        for run_request in due_runs:
            await self._enqueue_run(run_request)

        await self._start_next_run_if_idle()

    async def drain(self) -> None:
        """等待当前活动任务和已排队任务执行完毕，便于测试验证。"""

        while self._active_run_task is not None or self._pending_runs:
            if self._active_run_task is not None:
                await self._active_run_task
            await self._harvest_finished_run()
            await self._start_next_run_if_idle()

    def _collect_due_runs(self, projects: list[ScheduledProjectConfig]) -> list[PendingScheduledRun]:
        """根据当前时间计算所有到点任务。"""

        due_runs: list[PendingScheduledRun] = []
        for project_model in projects:
            resolved_project_dir = resolve_scheduler_project_dir(
                settings=self._settings,
                project_name=project_model.project_name,
                project_dir=project_model.project_dir,
            )
            resolved_test_root_dir = (resolved_project_dir / project_model.test_root_dir).resolve()
            project_timezone = ZoneInfo(project_model.timezone) if project_model.timezone else None
            current_time = self._current_time_factory()
            if project_timezone is not None:
                current_time = current_time.astimezone(project_timezone)
            current_minute = current_time.replace(second=0, microsecond=0)
            log_file_path = resolve_scheduler_log_path(
                settings=self._settings,
                project_name=project_model.project_name,
                project_dir=project_model.project_dir,
                test_root_dir=project_model.test_root_dir,
            )

            for task_model in project_model.tasks:
                if not task_model.enabled:
                    continue
                cron_expression = CronExpression.parse(task_model.schedule)
                if not cron_expression.matches(current_minute):
                    continue
                run_request = PendingScheduledRun(
                    project_name=project_model.project_name or resolved_project_dir.name,
                    project_dir=resolved_project_dir,
                    test_root_dir=resolved_test_root_dir,
                    task_id=task_model.task_id,
                    schedule=task_model.schedule,
                    locations=tuple(task_model.locations),
                    headed=task_model.headed if task_model.headed is not None else project_model.headed,
                    timezone=project_model.timezone,
                    scheduled_minute=current_minute,
                    log_file_path=log_file_path,
                )
                task_key = (str(run_request.project_dir), run_request.task_id)
                scheduled_minute_text = run_request.scheduled_minute.isoformat(timespec="minutes")
                if self._last_scheduled_minutes.get(task_key) == scheduled_minute_text:
                    continue
                self._last_scheduled_minutes[task_key] = scheduled_minute_text
                due_runs.append(run_request)
        return due_runs

    async def _enqueue_run(self, run_request: PendingScheduledRun) -> None:
        """把到点任务加入串行队列，并在冲突时落日志。"""

        if self._active_run is not None or self._pending_runs:
            active_display_name = self._active_run.display_name if self._active_run is not None else self._pending_runs[0].display_name
            conflict_message = (
                f"{_log_timestamp()} WARNING 任务冲突 display_name={run_request.display_name} "
                f"scheduled_for={run_request.scheduled_minute.isoformat(timespec='minutes')} "
                f"blocked_by={active_display_name} policy=serial_queue"
            )
            await _append_project_log(run_request.log_file_path, conflict_message)
            logger.warning(
                "%s 任务冲突 display_name=%s blocked_by=%s",
                log_title("执行", "定时任务"),
                run_request.display_name,
                active_display_name,
            )
        else:
            await _append_project_log(
                run_request.log_file_path,
                (
                    f"{_log_timestamp()} INFO 任务命中执行窗口 display_name={run_request.display_name} "
                    f"scheduled_for={run_request.scheduled_minute.isoformat(timespec='minutes')}"
                ),
            )

        self._pending_runs.append(run_request)

    async def _start_next_run_if_idle(self) -> None:
        """在空闲时启动下一个排队任务。"""

        if self._active_run_task is not None or not self._pending_runs:
            return

        self._active_run = self._pending_runs.popleft()
        await _append_project_log(
            self._active_run.log_file_path,
            (
                f"{_log_timestamp()} INFO 任务开始 display_name={self._active_run.display_name} "
                f"headed={self._active_run.headed} schedule=\"{self._active_run.schedule}\" "
                f"locations={list(self._active_run.locations) or ['<all>']}"
            ),
        )
        logger.info(
            "%s 定时任务开始 display_name=%s headed=%s schedule=%s locations=%s",
            log_title("执行", "定时任务"),
            self._active_run.display_name,
            self._active_run.headed,
            self._active_run.schedule,
            list(self._active_run.locations) or ["<all>"],
        )
        self._active_run_task = asyncio.create_task(self._task_runner.run(self._active_run))

    async def _harvest_finished_run(self) -> None:
        """收割已经结束的活动任务，释放串行执行锁。"""

        if self._active_run_task is None or self._active_run is None:
            return
        if not self._active_run_task.done():
            return

        try:
            result = await self._active_run_task
            await _append_project_log(
                self._active_run.log_file_path,
                (
                    f"{_log_timestamp()} INFO 任务结束 display_name={self._active_run.display_name} "
                    f"exit_code={result.exit_code} duration_seconds={result.duration_seconds}"
                ),
            )
            logger.info(
                "%s 定时任务结束 display_name=%s exit_code=%s duration_seconds=%s",
                log_title("执行", "定时任务"),
                self._active_run.display_name,
                result.exit_code,
                result.duration_seconds,
            )
            if result.exit_code != 0:
                await _append_project_log(
                    self._active_run.log_file_path,
                    (
                        f"{_log_timestamp()} ERROR 任务执行失败 display_name={self._active_run.display_name} "
                        f"exit_code={result.exit_code}"
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            await _append_project_log(
                self._active_run.log_file_path,
                f"{_log_timestamp()} ERROR 任务执行异常 display_name={self._active_run.display_name} error={exc}",
            )
            logger.exception("%s 定时任务执行异常 display_name=%s", log_title("执行", "定时任务"), self._active_run.display_name)
        finally:
            self._active_run = None
            self._active_run_task = None

    async def _ensure_project_startup_logs(self, projects: list[ScheduledProjectConfig]) -> None:
        """确保配置中的每个项目在服务启动后都有一条启动日志。"""

        for project_model in projects:
            resolved_log_path = resolve_scheduler_log_path(
                settings=self._settings,
                project_name=project_model.project_name,
                project_dir=project_model.project_dir,
                test_root_dir=project_model.test_root_dir,
            )
            project_key = str(resolved_log_path)
            if project_key in self._startup_logged_projects:
                continue
            await _append_project_log(
                resolved_log_path,
                (
                    f"{_log_timestamp()} INFO 调度服务已加载项目 project_key={project_model.project_key()} "
                    f"poll_interval_seconds={self._poll_interval_seconds}"
                ),
            )
            self._startup_logged_projects.add(project_key)


async def _append_project_log(log_file_path: Path, line: str) -> None:
    """把一行日志追加到项目测试根目录下的日志文件。"""

    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    with log_file_path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"{line}\n")


def _log_timestamp() -> str:
    """返回统一的本地时间戳文本。"""

    return datetime.now().astimezone().isoformat(timespec="seconds")


__all__ = ["PendingScheduledRun", "PlaywrightTaskRunner", "ScheduledRunResult", "SchedulerService"]

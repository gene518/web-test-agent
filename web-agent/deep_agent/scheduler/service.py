"""独立于 Agent 的定时任务扫描与执行服务。"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from deep_agent.core.config import AppSettings
from deep_agent.core.runtime_logging import get_logger, log_title
from deep_agent.scheduler.cron import CronExpression
from deep_agent.scheduler.deployment_state import SchedulerDeploymentState
from deep_agent.scheduler.logs import append_project_log as _append_project_log
from deep_agent.scheduler.logs import log_timestamp as _log_timestamp
from deep_agent.scheduler.models import ScheduledProjectConfig
from deep_agent.scheduler.paths import (
    resolve_scheduler_locations,
    resolve_scheduler_log_path,
    resolve_scheduler_project_dir,
)
from deep_agent.scheduler.runner import (
    LangGraphScheduledTaskRunner,
    PendingScheduledRun,
    PlaywrightTaskRunner,
    ScheduledRunResult,
    ScheduledTaskRunner,
    scheduled_run_thread_id,
)
from deep_agent.scheduler.store import load_scheduler_config
from deep_agent.scheduler.summary import (
    ScheduledRunSummaryNode,
    ScheduledRunSummaryStage,
)

logger = get_logger(__name__)


class SchedulerService:
    """扫描配置文件并按 Cron 串行执行测试任务。"""

    def __init__(
        self,
        *,
        settings: AppSettings,
        config_path: Path,
        task_runner: ScheduledTaskRunner | None = None,
        summary_node: ScheduledRunSummaryStage | None = None,
        current_time_factory: Callable[[], datetime] | None = None,
        deployment_state: SchedulerDeploymentState | None = None,
    ) -> None:
        """初始化调度服务。"""

        self._settings = settings
        self._config_path = config_path.expanduser().resolve()
        self._task_runner = task_runner or LangGraphScheduledTaskRunner(
            api_url=settings.scheduler_langgraph_url,
            graph_id=settings.scheduler_scheduled_run_graph_id,
            api_key=settings.scheduler_langgraph_api_key,
            api_timeout_seconds=settings.scheduler_langgraph_timeout_seconds,
        )
        self._fallback_summary_node = ScheduledRunSummaryNode()
        self._summary_node = summary_node or self._fallback_summary_node
        self._current_time_factory = current_time_factory or (
            lambda: datetime.now().astimezone()
        )
        self._deployment_state = (
            deployment_state or SchedulerDeploymentState.from_environment()
        )
        self._pending_runs: deque[PendingScheduledRun] = deque()
        self._last_scheduled_minutes: dict[tuple[str, str], str] = {}
        self._active_run: PendingScheduledRun | None = None
        self._active_run_task: asyncio.Task[ScheduledRunResult] | None = None
        self._active_run_started_at: datetime | None = None
        self._poll_interval_seconds = settings.scheduler_poll_interval_seconds
        self._task_timeout_seconds = 1800
        self._max_pending_runs = 100
        self._misfire_grace_seconds = 300
        self._startup_logged_projects: set[str] = set()
        self._last_checked_minutes: dict[str, datetime] = {}
        self._stop_event = asyncio.Event()
        self._lifecycle_lock = asyncio.Lock()
        self._shutdown_complete = False

    async def run_forever(self) -> None:
        """持续扫描配置并执行到点任务。"""

        logger.info(
            "%s 定时执行服务启动 config_path=%s",
            log_title("初始化", "调度服务"),
            self._config_path,
        )
        self._publish_deployment_state()
        try:
            while not self._stop_event.is_set():
                await self.poll_once()
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._poll_interval_seconds,
                    )
                except TimeoutError:
                    continue
        finally:
            await self.shutdown()

    async def poll_once(self) -> None:
        """执行一次扫描周期。"""

        async with self._lifecycle_lock:
            await self._poll_once_locked()

    async def _poll_once_locked(self) -> None:
        """在生命周期锁内完成一次不可与 shutdown 交错的扫描。"""

        if self._stop_event.is_set():
            return
        await self._harvest_finished_run()

        if self._deployment_state.maintenance_active():
            self._publish_deployment_state()
            return

        try:
            config_model = load_scheduler_config(self._config_path)
        except RuntimeError as exc:
            logger.warning(
                "%s 加载定时任务配置失败：%s", log_title("执行", "调度服务"), exc
            )
            return

        self._poll_interval_seconds = config_model.scheduler.poll_interval_seconds
        self._task_timeout_seconds = config_model.scheduler.task_timeout_seconds
        self._max_pending_runs = config_model.scheduler.max_pending_runs
        self._misfire_grace_seconds = config_model.scheduler.misfire_grace_seconds
        await self._ensure_project_startup_logs(config_model.projects)
        due_runs = self._collect_due_runs(config_model.projects)
        for run_request in due_runs:
            await self._enqueue_run(run_request)

        await self._start_next_run_if_idle()

    async def stop(self) -> None:
        """停止接收新任务，并清理当前执行进程和待执行队列。"""

        self._stop_event.set()
        await self.shutdown()

    async def shutdown(self) -> None:
        """幂等释放调度服务持有的活动任务。"""

        async with self._lifecycle_lock:
            if self._shutdown_complete:
                return
            self._stop_event.set()
            await self._harvest_finished_run()

            while self._pending_runs:
                pending_run = self._pending_runs.popleft()
                await _append_project_log(
                    pending_run.log_file_path,
                    (
                        f"{_log_timestamp()} WARNING 任务取消 display_name={pending_run.display_name} "
                        "reason=scheduler_shutdown state=pending"
                    ),
                )

            if self._active_run_task is not None and self._active_run is not None:
                active_run = self._active_run
                self._active_run_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await self._active_run_task
                await _append_project_log(
                    active_run.log_file_path,
                    (
                        f"{_log_timestamp()} WARNING 任务取消 display_name={active_run.display_name} "
                        "reason=scheduler_shutdown state=running"
                    ),
                )
                await self._harvest_finished_run()

            self._shutdown_complete = True
            self._publish_deployment_state(online=False)

    async def drain(self) -> None:
        """等待当前活动任务和已排队任务执行完毕，便于测试验证。"""

        while self._active_run_task is not None or self._pending_runs:
            if self._active_run_task is not None:
                with suppress(asyncio.CancelledError, Exception):
                    await self._active_run_task
            await self._harvest_finished_run()
            await self._start_next_run_if_idle()

    def _collect_due_runs(
        self, projects: list[ScheduledProjectConfig]
    ) -> list[PendingScheduledRun]:
        """根据当前时间计算所有到点任务。"""

        due_runs: list[PendingScheduledRun] = []
        for project_model in projects:
            try:
                resolved_project_dir = resolve_scheduler_project_dir(
                    settings=self._settings,
                    project_name=project_model.project_name,
                    project_dir=project_model.project_dir,
                )
                log_file_path = resolve_scheduler_log_path(
                    settings=self._settings,
                    project_name=project_model.project_name,
                    project_dir=project_model.project_dir,
                    test_root_dir=project_model.test_root_dir,
                )
            except RuntimeError as exc:
                logger.warning(
                    "%s 跳过路径非法的调度项目 project_key=%s error=%s",
                    log_title("执行", "调度服务"),
                    project_model.project_key(),
                    exc,
                )
                continue

            project_timezone = (
                ZoneInfo(project_model.timezone) if project_model.timezone else None
            )
            current_time = self._current_time_factory()
            if project_timezone is not None:
                current_time = current_time.astimezone(project_timezone)
            current_minute = current_time.replace(second=0, microsecond=0)
            resolved_test_root_dir = log_file_path.parent
            scheduled_minutes = self._minutes_to_check(
                project_key=str(resolved_project_dir),
                current_minute=current_minute,
            )
            enabled_tasks = []
            for task_model in project_model.tasks:
                if not task_model.enabled:
                    continue
                try:
                    resolved_locations = resolve_scheduler_locations(
                        project_dir=resolved_project_dir,
                        locations=task_model.locations,
                    )
                except RuntimeError as exc:
                    logger.warning(
                        "%s 跳过路径非法的调度任务 display_name=%s/%s error=%s",
                        log_title("执行", "定时任务"),
                        project_model.project_name or resolved_project_dir.name,
                        task_model.task_id,
                        exc,
                    )
                    continue
                enabled_tasks.append(
                    (
                        task_model,
                        CronExpression.parse(task_model.schedule),
                        resolved_locations,
                    )
                )

            for scheduled_minute in scheduled_minutes:
                for task_model, cron_expression, resolved_locations in enabled_tasks:
                    if not cron_expression.matches(scheduled_minute):
                        continue
                    run_request = PendingScheduledRun(
                        project_name=project_model.project_name
                        or resolved_project_dir.name,
                        project_dir=resolved_project_dir,
                        test_root_dir=resolved_test_root_dir,
                        task_id=task_model.task_id,
                        schedule=task_model.schedule,
                        locations=resolved_locations,
                        headed=task_model.headed
                        if task_model.headed is not None
                        else project_model.headed,
                        timezone=project_model.timezone,
                        scheduled_minute=scheduled_minute,
                        log_file_path=log_file_path,
                        timeout_seconds=self._task_timeout_seconds,
                    )
                    scheduled_minute_text = run_request.scheduled_minute.isoformat(
                        timespec="minutes"
                    )
                    if (
                        self._last_scheduled_minutes.get(run_request.task_key)
                        == scheduled_minute_text
                    ):
                        continue
                    self._last_scheduled_minutes[run_request.task_key] = (
                        scheduled_minute_text
                    )
                    due_runs.append(run_request)
        return due_runs

    def _minutes_to_check(
        self, *, project_key: str, current_minute: datetime
    ) -> list[datetime]:
        """返回本轮要检查的分钟，并补偿服务运行期间错过的短暂窗口。"""

        current_timeline_minute = _to_timeline_minute(current_minute)
        previous_timeline_minute = self._last_checked_minutes.get(project_key)
        self._last_checked_minutes[project_key] = current_timeline_minute
        if (
            previous_timeline_minute is None
            or previous_timeline_minute >= current_timeline_minute
        ):
            return [current_minute]

        first_unchecked_minute = previous_timeline_minute + timedelta(minutes=1)
        grace_boundary = current_timeline_minute - timedelta(
            seconds=self._misfire_grace_seconds
        )
        cursor = max(first_unchecked_minute, grace_boundary).replace(
            second=0, microsecond=0
        )
        result: list[datetime] = []
        while cursor <= current_timeline_minute:
            result.append(_from_timeline_minute(cursor, reference=current_minute))
            cursor += timedelta(minutes=1)
        return result or [current_minute]

    async def _enqueue_run(self, run_request: PendingScheduledRun) -> None:
        """把到点任务加入串行队列，并在冲突时落日志。"""

        if self._stop_event.is_set() or self._shutdown_complete:
            return

        for index, pending_run in enumerate(self._pending_runs):
            if pending_run.task_key != run_request.task_key:
                continue
            self._pending_runs[index] = run_request
            await _append_project_log(
                run_request.log_file_path,
                (
                    f"{_log_timestamp()} WARNING 任务合并 display_name={run_request.display_name} "
                    f"replaced_scheduled_for={pending_run.scheduled_minute.isoformat(timespec='minutes')} "
                    f"scheduled_for={run_request.scheduled_minute.isoformat(timespec='minutes')} "
                    "policy=latest_only"
                ),
            )
            return

        if len(self._pending_runs) >= self._max_pending_runs:
            await _append_project_log(
                run_request.log_file_path,
                (
                    f"{_log_timestamp()} ERROR 任务丢弃 display_name={run_request.display_name} "
                    f"scheduled_for={run_request.scheduled_minute.isoformat(timespec='minutes')} "
                    f"reason=queue_full max_pending_runs={self._max_pending_runs}"
                ),
            )
            logger.error(
                "%s 任务队列已满 display_name=%s max_pending_runs=%s",
                log_title("执行", "定时任务"),
                run_request.display_name,
                self._max_pending_runs,
            )
            return

        if self._active_run is not None or self._pending_runs:
            active_display_name = (
                self._active_run.display_name
                if self._active_run is not None
                else self._pending_runs[0].display_name
            )
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

        if (
            self._stop_event.is_set()
            or self._active_run_task is not None
            or not self._pending_runs
            or self._deployment_state.maintenance_active()
        ):
            self._publish_deployment_state()
            return

        self._active_run = self._pending_runs.popleft()
        self._active_run_started_at = datetime.now().astimezone()
        await _append_project_log(
            self._active_run.log_file_path,
            (
                f"{_log_timestamp()} INFO 任务开始 display_name={self._active_run.display_name} "
                f'headed={self._active_run.headed} schedule="{self._active_run.schedule}" '
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
        self._active_run_task = asyncio.create_task(
            self._task_runner.run(self._active_run)
        )
        self._publish_deployment_state()

    async def _harvest_finished_run(self) -> None:
        """收割活动任务，并在释放串行锁前完成总结阶段。"""

        if self._active_run_task is None or self._active_run is None:
            return
        if not self._active_run_task.done():
            return

        active_run = self._active_run
        finished_at = datetime.now().astimezone()
        try:
            if self._active_run_task.cancelled():
                result = ScheduledRunResult(
                    exit_code=130,
                    duration_seconds=self._active_duration_seconds(finished_at),
                    cancelled=True,
                    error_message="Scheduler task was cancelled before completion.",
                    started_at=self._active_run_started_at,
                    finished_at=finished_at,
                    conversation_thread_id=self._conversation_thread_id(active_run),
                )
            else:
                try:
                    result = await self._active_run_task
                except asyncio.CancelledError:
                    result = ScheduledRunResult(
                        exit_code=130,
                        duration_seconds=self._active_duration_seconds(finished_at),
                        cancelled=True,
                        error_message="Scheduler task was cancelled before completion.",
                        started_at=self._active_run_started_at,
                        finished_at=finished_at,
                        conversation_thread_id=self._conversation_thread_id(active_run),
                    )
                except Exception as exc:  # noqa: BLE001
                    result = ScheduledRunResult(
                        exit_code=1,
                        duration_seconds=self._active_duration_seconds(finished_at),
                        error_message=f"{type(exc).__name__}: {exc}",
                        started_at=self._active_run_started_at,
                        finished_at=finished_at,
                    )
                    logger.exception(
                        "%s 定时任务执行异常 display_name=%s",
                        log_title("执行", "定时任务"),
                        active_run.display_name,
                    )
                else:
                    result = replace(
                        result,
                        started_at=result.started_at or self._active_run_started_at,
                        finished_at=result.finished_at or finished_at,
                    )

            await self._record_run_result(active_run, result)
        finally:
            self._active_run = None
            self._active_run_task = None
            self._active_run_started_at = None
            self._publish_deployment_state()

    def _publish_deployment_state(self, *, online: bool = True) -> None:
        """把 updater 需要的最小状态写入共享卷。"""

        self._deployment_state.publish(
            active_run=self._active_run.display_name if self._active_run else None,
            pending_runs=len(self._pending_runs),
            online=online,
        )

    async def _record_run_result(
        self,
        run_request: PendingScheduledRun,
        result: ScheduledRunResult,
    ) -> None:
        """记录进程结果，并强制进入可降级的总结阶段。"""

        log_level = "WARNING" if result.cancelled else "INFO"
        await _append_project_log(
            run_request.log_file_path,
            (
                f"{_log_timestamp()} {log_level} 任务结束 display_name={run_request.display_name} "
                f"exit_code={result.exit_code} duration_seconds={result.duration_seconds} "
                f"timed_out={result.timed_out} cancelled={result.cancelled}"
            ),
        )
        logger.info(
            "%s 定时任务结束 display_name=%s exit_code=%s duration_seconds=%s",
            log_title("执行", "定时任务"),
            run_request.display_name,
            result.exit_code,
            result.duration_seconds,
        )
        if result.exit_code != 0:
            await _append_project_log(
                run_request.log_file_path,
                (
                    f"{_log_timestamp()} ERROR 任务执行失败 display_name={run_request.display_name} "
                    f"exit_code={result.exit_code} error={result.error_message or '<none>'}"
                ),
            )

        if result.report_generated:
            await _append_project_log(
                run_request.log_file_path,
                (
                    f"{_log_timestamp()} INFO 总结阶段完成 display_name={run_request.display_name} "
                    f"source=scheduled_run_graph conversation_thread_id="
                    f"{result.conversation_thread_id or '<none>'} "
                    f"report_path={result.report_path or '<unknown>'}"
                ),
            )
            return

        await _append_project_log(
            run_request.log_file_path,
            f"{_log_timestamp()} INFO 总结阶段开始 display_name={run_request.display_name}",
        )
        try:
            summary_result = await self._summary_node.execute(run_request, result)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "%s 总结阶段异常 display_name=%s",
                log_title("执行", "定时任务总结"),
                run_request.display_name,
            )
            await _append_project_log(
                run_request.log_file_path,
                (
                    f"{_log_timestamp()} ERROR 总结阶段异常 display_name={run_request.display_name} "
                    f"error={type(exc).__name__}: {exc}"
                ),
            )
            if self._summary_node is self._fallback_summary_node:
                return
            try:
                summary_result = await self._fallback_summary_node.execute(
                    run_request,
                    result,
                )
            except Exception as fallback_exc:  # noqa: BLE001
                logger.exception(
                    "%s 确定性总结兜底失败 display_name=%s",
                    log_title("执行", "定时任务总结"),
                    run_request.display_name,
                )
                await _append_project_log(
                    run_request.log_file_path,
                    (
                        f"{_log_timestamp()} ERROR 总结阶段兜底失败 display_name={run_request.display_name} "
                        f"error={type(fallback_exc).__name__}: {fallback_exc}"
                    ),
                )
                return

        report = summary_result.report
        await _append_project_log(
            run_request.log_file_path,
            (
                f"{_log_timestamp()} INFO 总结阶段完成 display_name={run_request.display_name} "
                f"status={report.execution.status} failed_cases={len(report.failed_cases)} "
                f"retried_cases={len(report.retried_cases)} "
                f"report_path={summary_result.markdown_report_path}"
            ),
        )

    def _active_duration_seconds(self, finished_at: datetime) -> float:
        """为执行器异常或取消结果补充服务观测到的耗时。"""

        if self._active_run_started_at is None:
            return 0
        return round(
            max(0, (finished_at - self._active_run_started_at).total_seconds()), 3
        )

    def _conversation_thread_id(
        self, run_request: PendingScheduledRun
    ) -> str | None:
        """仅默认 SDK runner 实际创建了监控对话时返回确定性 thread ID。"""

        if not isinstance(self._task_runner, LangGraphScheduledTaskRunner):
            return None
        return scheduled_run_thread_id(run_request)

    async def _ensure_project_startup_logs(
        self, projects: list[ScheduledProjectConfig]
    ) -> None:
        """确保配置中的每个项目在服务启动后都有一条启动日志。"""

        for project_model in projects:
            try:
                resolved_log_path = resolve_scheduler_log_path(
                    settings=self._settings,
                    project_name=project_model.project_name,
                    project_dir=project_model.project_dir,
                    test_root_dir=project_model.test_root_dir,
                )
            except RuntimeError as exc:
                logger.warning(
                    "%s 跳过路径非法的调度项目 project_key=%s error=%s",
                    log_title("执行", "调度服务"),
                    project_model.project_key(),
                    exc,
                )
                continue
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


def _to_timeline_minute(value: datetime) -> datetime:
    """把带时区时间转成 UTC 时间轴，避免夏令时切换破坏补偿顺序。"""

    if value.tzinfo is None:
        return value
    return value.astimezone(UTC)


def _from_timeline_minute(value: datetime, *, reference: datetime) -> datetime:
    """把 UTC 时间轴上的分钟还原为项目本地时间。"""

    if reference.tzinfo is None:
        return value.replace(tzinfo=None)
    return value.astimezone(reference.tzinfo)


__all__ = [
    "PendingScheduledRun",
    "PlaywrightTaskRunner",
    "ScheduledRunResult",
    "SchedulerService",
]

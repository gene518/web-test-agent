"""Scheduler 的 Playwright 子进程执行与清理。"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import subprocess
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from deep_agent.scheduler.logs import append_project_log, log_timestamp


MAX_CAPTURED_OUTPUT_LINES = 5000
MAX_CAPTURED_LINE_CHARS = 4000


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
    timeout_seconds: float = 1800

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

    @property
    def task_key(self) -> tuple[str, str]:
        """返回不包含调度分钟的任务身份，用于合并积压执行。"""

        return (str(self.project_dir), self.task_id)


@dataclass(frozen=True, slots=True)
class ScheduledRunResult:
    """一次任务执行完成后的结果。"""

    exit_code: int
    duration_seconds: float
    timed_out: bool = False
    cancelled: bool = False
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    output_lines: tuple[str, ...] = ()
    output_line_count: int = 0
    output_truncated: bool = False
    report_name: str | None = None
    report_generated: bool = False
    report_path: str | None = None
    conversation_thread_id: str | None = None
    conversation_error: str | None = None


@dataclass(slots=True)
class _OutputCapture:
    """限制内存占用的控制台输出尾部缓冲区。"""

    lines: deque[str] = field(
        default_factory=lambda: deque(maxlen=MAX_CAPTURED_OUTPUT_LINES)
    )
    total_line_count: int = 0
    truncated: bool = False

    def append(self, line: str) -> None:
        self.total_line_count += 1
        if len(self.lines) == MAX_CAPTURED_OUTPUT_LINES:
            self.truncated = True
        if len(line) > MAX_CAPTURED_LINE_CHARS:
            line = f"{line[:MAX_CAPTURED_LINE_CHARS]}...<truncated>"
            self.truncated = True
        self.lines.append(line)


class ScheduledTaskRunner(Protocol):
    """定时任务执行器协议，便于测试注入假实现。"""

    async def run(self, run_request: PendingScheduledRun) -> ScheduledRunResult:
        """执行单个排队任务。"""


class PlaywrightTaskRunner:
    """基于 `npx playwright test` 的默认任务执行器。"""

    def __init__(
        self,
        *,
        output_observer: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._output_observer = output_observer

    async def run(self, run_request: PendingScheduledRun) -> ScheduledRunResult:
        """在目标项目目录串行执行 Playwright 测试。"""

        report_name = (
            f"scheduled-{_safe_report_component(run_request.task_id)}-"
            f"{run_request.scheduled_minute.strftime('%Y%m%d-%H%M')}"
        )
        portable_node = os.environ.get("WEB_TEST_AGENT_NODE_EXECUTABLE")
        portable_cli = os.environ.get("WEB_TEST_AGENT_PLAYWRIGHT_CLI")
        if portable_node and portable_cli:
            workspace_cli = (
                run_request.project_dir / "node_modules" / "playwright" / "cli.js"
            )
            command = [
                portable_node,
                str(workspace_cli if workspace_cli.is_file() else portable_cli),
                "test",
                *run_request.locations,
            ]
        else:
            command = ["npx", "playwright", "test", *run_request.locations]

        env = os.environ.copy()
        env["PWTEST_HEADED"] = "1" if run_request.headed else "0"
        env["PW_TEST_REPORT_NAME"] = report_name
        env["PLAYWRIGHT_HTML_OPEN"] = "never"
        env["PW_SCHEDULE_TASK_ID"] = run_request.task_id
        env["PW_SCHEDULE_PROJECT_NAME"] = run_request.project_name
        env["PW_SCHEDULED_FOR"] = run_request.scheduled_minute.isoformat(
            timespec="minutes"
        )

        started_at_clock = monotonic()
        started_at = datetime.now().astimezone()
        output_capture = _OutputCapture()
        process_options: dict[str, object] = {}
        if os.name == "posix":
            process_options["start_new_session"] = True
        elif os.name == "nt":
            process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(run_request.project_dir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **process_options,
        )
        try:
            async with asyncio.timeout(run_request.timeout_seconds):
                await _stream_process_output(
                    process,
                    run_request,
                    output_capture,
                    output_observer=self._output_observer,
                )
                exit_code = await process.wait()
        except TimeoutError:
            await append_project_log(
                run_request.log_file_path,
                (
                    f"{log_timestamp()} ERROR 任务执行超时 display_name={run_request.display_name} "
                    f"timeout_seconds={run_request.timeout_seconds}"
                ),
            )
            await asyncio.shield(_terminate_process_tree(process))
            return ScheduledRunResult(
                exit_code=124,
                duration_seconds=round(monotonic() - started_at_clock, 3),
                timed_out=True,
                started_at=started_at,
                finished_at=datetime.now().astimezone(),
                output_lines=tuple(output_capture.lines),
                output_line_count=output_capture.total_line_count,
                output_truncated=output_capture.truncated,
                report_name=report_name,
            )
        except asyncio.CancelledError:
            await asyncio.shield(_terminate_process_tree(process))
            raise
        except Exception:
            await asyncio.shield(_terminate_process_tree(process))
            raise

        return ScheduledRunResult(
            exit_code=exit_code,
            duration_seconds=round(monotonic() - started_at_clock, 3),
            started_at=started_at,
            finished_at=datetime.now().astimezone(),
            output_lines=tuple(output_capture.lines),
            output_line_count=output_capture.total_line_count,
            output_truncated=output_capture.truncated,
            report_name=report_name,
        )


async def _stream_process_output(
    process: asyncio.subprocess.Process,
    run_request: PendingScheduledRun,
    output_capture: _OutputCapture,
    *,
    output_observer: Callable[[str], Awaitable[None]] | None = None,
) -> None:
    """持续读取测试输出，避免子进程因管道写满而阻塞。"""

    if process.stdout is None:
        return
    while True:
        raw_line = await process.stdout.readline()
        if not raw_line:
            return
        line = raw_line.decode("utf-8", errors="replace").rstrip()
        if not line:
            continue
        output_capture.append(line)
        if output_observer is not None:
            try:
                await output_observer(line)
            except Exception:  # noqa: BLE001
                # 监控消息不能反向中断 Playwright；完整输出仍会进入 Scheduler 日志。
                pass
        await append_project_log(
            run_request.log_file_path,
            f"{log_timestamp()} INFO [{run_request.task_id}] {line}",
        )


def serialize_run_request(run_request: PendingScheduledRun) -> dict[str, Any]:
    """把排队请求转换为 LangGraph 可持久化的 JSON 数据。"""

    return {
        "project_name": run_request.project_name,
        "project_dir": str(run_request.project_dir),
        "test_root_dir": str(run_request.test_root_dir),
        "task_id": run_request.task_id,
        "schedule": run_request.schedule,
        "locations": list(run_request.locations),
        "headed": run_request.headed,
        "timezone": run_request.timezone,
        "scheduled_minute": run_request.scheduled_minute.isoformat(),
        "log_file_path": str(run_request.log_file_path),
        "timeout_seconds": run_request.timeout_seconds,
    }


def deserialize_run_request(payload: Mapping[str, Any]) -> PendingScheduledRun:
    """从图输入恢复请求，并重新校验所有持久化路径边界。"""

    project_dir = Path(str(payload["project_dir"])).expanduser().resolve()
    test_root_dir = Path(str(payload["test_root_dir"])).expanduser().resolve()
    log_file_path = Path(str(payload["log_file_path"])).expanduser().resolve()
    if not test_root_dir.is_relative_to(project_dir):
        raise ValueError("scheduled-run 的 test_root_dir 不能逃逸 project_dir。")
    if not log_file_path.is_relative_to(test_root_dir):
        raise ValueError("scheduled-run 的 log_file_path 必须位于 test_root_dir。")
    if not project_dir.is_dir():
        raise ValueError("scheduled-run 的 project_dir 不存在或不是目录。")

    scheduled_minute = datetime.fromisoformat(str(payload["scheduled_minute"]))
    if scheduled_minute.tzinfo is None:
        raise ValueError("scheduled-run 的 scheduled_minute 必须包含时区。")
    raw_locations = payload.get("locations", [])
    if not isinstance(raw_locations, (list, tuple)):
        raise ValueError("scheduled-run 的 locations 必须是数组。")
    normalized_locations: list[str] = []
    for raw_location in raw_locations:
        location = str(raw_location).strip().replace("\\", "/")
        candidate = Path(location)
        if (
            not location
            or candidate.is_absolute()
            or any(character in location for character in "*?[]{}")
        ):
            raise ValueError("scheduled-run 的 locations 只能包含项目内相对文件或目录。")
        resolved_location = (project_dir / candidate).resolve()
        if not resolved_location.is_relative_to(project_dir) or not resolved_location.exists():
            raise ValueError(f"scheduled-run location 不存在或逃逸项目目录：{location}")
        normalized_locations.append(resolved_location.relative_to(project_dir).as_posix())

    timeout_seconds = float(payload.get("timeout_seconds", 1800))
    if timeout_seconds <= 0:
        raise ValueError("scheduled-run 的 timeout_seconds 必须大于 0。")
    return PendingScheduledRun(
        project_name=str(payload["project_name"]),
        project_dir=project_dir,
        test_root_dir=test_root_dir,
        task_id=str(payload["task_id"]),
        schedule=str(payload["schedule"]),
        locations=tuple(normalized_locations),
        headed=bool(payload.get("headed", False)),
        timezone=str(payload["timezone"]) if payload.get("timezone") else None,
        scheduled_minute=scheduled_minute,
        log_file_path=log_file_path,
        timeout_seconds=timeout_seconds,
    )


def serialize_run_result(result: ScheduledRunResult) -> dict[str, Any]:
    """把执行结果转换成图状态可保存的数据。"""

    return {
        "exit_code": result.exit_code,
        "duration_seconds": result.duration_seconds,
        "timed_out": result.timed_out,
        "cancelled": result.cancelled,
        "error_message": result.error_message,
        "started_at": result.started_at.isoformat() if result.started_at else None,
        "finished_at": result.finished_at.isoformat() if result.finished_at else None,
        "output_lines": list(result.output_lines),
        "output_line_count": result.output_line_count,
        "output_truncated": result.output_truncated,
        "report_name": result.report_name,
    }


def deserialize_run_result(payload: Mapping[str, Any]) -> ScheduledRunResult:
    """从 scheduled-run 图输出恢复进程结果。"""

    raw_lines = payload.get("output_lines", [])
    return ScheduledRunResult(
        exit_code=int(payload.get("exit_code", 1)),
        duration_seconds=max(0, float(payload.get("duration_seconds", 0))),
        timed_out=bool(payload.get("timed_out", False)),
        cancelled=bool(payload.get("cancelled", False)),
        error_message=(
            str(payload["error_message"]) if payload.get("error_message") else None
        ),
        started_at=_parse_optional_datetime(payload.get("started_at")),
        finished_at=_parse_optional_datetime(payload.get("finished_at")),
        output_lines=tuple(str(item) for item in raw_lines)
        if isinstance(raw_lines, (list, tuple))
        else (),
        output_line_count=int(payload.get("output_line_count", 0)),
        output_truncated=bool(payload.get("output_truncated", False)),
        report_name=str(payload["report_name"]) if payload.get("report_name") else None,
    )


def scheduled_run_thread_id(run_request: PendingScheduledRun) -> str:
    """为同一调度实例生成稳定 UUID，服务重启不会重复执行。"""

    canonical_project_dir = run_request.project_dir.expanduser().resolve()
    canonical_run_key = (
        f"{canonical_project_dir}::{run_request.task_id}::"
        f"{run_request.scheduled_minute.isoformat(timespec='minutes')}"
    )
    return str(
        uuid5(NAMESPACE_URL, f"web-test-agent:scheduled-run:{canonical_run_key}")
    )


class LangGraphScheduledTaskRunner:
    """通过 LangGraph SDK 在独立只读线程中启动 scheduled-run 图。"""

    def __init__(
        self,
        *,
        api_url: str,
        graph_id: str = "web-autotest-scheduled-run",
        api_key: str | None = None,
        api_timeout_seconds: float = 3600,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._graph_id = graph_id
        self._api_key = api_key
        self._api_timeout_seconds = api_timeout_seconds
        self._client_factory = client_factory

    async def run(self, run_request: PendingScheduledRun) -> ScheduledRunResult:
        """创建确定性监控线程并等待图完成；API 故障转换为可报告结果。"""

        started_at = datetime.now().astimezone()
        started_clock = monotonic()
        thread_id = scheduled_run_thread_id(run_request)
        client = self._build_client()
        remote_run_id: str | None = None
        try:
            await client.threads.create(
                thread_id=thread_id,
                graph_id=self._graph_id,
                if_exists="do_nothing",
                metadata={
                    "thread_title": (
                        f"定时测试：{run_request.display_name} "
                        f"{run_request.scheduled_minute.isoformat(timespec='minutes')}"
                    ),
                    "thread_title_source": "scheduler-v1",
                    "readonly": True,
                    "thread_type": "scheduled_run",
                    "scheduled_run_key": run_request.run_key,
                    "task_id": run_request.task_id,
                },
            )
            remote_run = await client.runs.create(
                thread_id,
                self._graph_id,
                input={
                    "run_request": serialize_run_request(run_request),
                    "conversation_thread_id": thread_id,
                },
                metadata={"scheduled_run_key": run_request.run_key},
                stream_mode=["values", "custom"],
                multitask_strategy="reject",
            )
            remote_run_id = str(remote_run["run_id"])
            joined = await client.runs.join(thread_id, remote_run_id)
            values = await self._resolve_values(client, thread_id, joined)
            raw_result = values.get("execution_result")
            if not isinstance(raw_result, Mapping):
                raise RuntimeError("scheduled-run 图未返回 execution_result。")
            result = deserialize_run_result(raw_result)
            raw_report = values.get("report")
            report_path: str | None = None
            if isinstance(raw_report, Mapping):
                artifacts = raw_report.get("artifacts")
                if isinstance(artifacts, Mapping) and artifacts.get("analysis_report"):
                    report_path = str(artifacts["analysis_report"])
            return replace(
                result,
                report_generated=isinstance(raw_report, Mapping),
                report_path=report_path,
                conversation_thread_id=thread_id,
            )
        except asyncio.CancelledError:
            if remote_run_id is not None:
                with suppress(Exception):
                    await client.runs.cancel(thread_id, remote_run_id, wait=False)
            raise
        except Exception as exc:  # noqa: BLE001
            finished_at = datetime.now().astimezone()
            return ScheduledRunResult(
                exit_code=1,
                duration_seconds=round(monotonic() - started_clock, 3),
                error_message=(
                    "LangGraph scheduled-run API unavailable or failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
                started_at=started_at,
                finished_at=finished_at,
                conversation_thread_id=thread_id,
                conversation_error=f"{type(exc).__name__}: {exc}",
            )

    def _build_client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        from langgraph_sdk import get_client

        kwargs: dict[str, Any] = {
            "url": self._api_url,
            "timeout": self._api_timeout_seconds,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
        return get_client(**kwargs)

    async def _resolve_values(
        self,
        client: Any,
        thread_id: str,
        joined: Any,
    ) -> Mapping[str, Any]:
        if isinstance(joined, Mapping) and "execution_result" in joined:
            return joined
        state = await client.threads.get_state(thread_id)
        values = state.get("values") if isinstance(state, Mapping) else None
        if not isinstance(values, Mapping):
            raise RuntimeError("scheduled-run 线程没有可读取的最终 state。")
        return values


def _parse_optional_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value))


def _safe_report_component(value: str) -> str:
    """避免任务 ID 被目标项目配置解释为报告目录路径。"""

    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-")
    return (normalized or "task")[:64]


async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    """结束 Playwright 进程及其派生进程，并保证子进程被回收。"""

    if process.returncode is not None:
        return

    process_id = process.pid
    if os.name == "nt":
        await _terminate_windows_process_tree(process, process_id)
        return

    with suppress(ProcessLookupError, PermissionError):
        os.killpg(process_id, signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
        return
    except TimeoutError:
        pass

    with suppress(ProcessLookupError, PermissionError):
        os.killpg(process_id, signal.SIGKILL)
    with suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=5)


async def _terminate_windows_process_tree(
    process: asyncio.subprocess.Process,
    process_id: int,
) -> None:
    """在 Windows 上使用 taskkill 清理完整派生进程树。"""

    try:
        tree_killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process_id),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await tree_killer.wait()
    except OSError:
        with suppress(ProcessLookupError):
            process.kill()
    with suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=5)


__all__ = [
    "LangGraphScheduledTaskRunner",
    "PendingScheduledRun",
    "PlaywrightTaskRunner",
    "ScheduledRunResult",
    "ScheduledTaskRunner",
    "deserialize_run_request",
    "deserialize_run_result",
    "scheduled_run_thread_id",
    "serialize_run_request",
    "serialize_run_result",
]

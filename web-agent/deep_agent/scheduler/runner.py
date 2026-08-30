"""Scheduler 的 Playwright 子进程执行与清理。"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import subprocess
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Protocol

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
                await _stream_process_output(process, run_request, output_capture)
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
        await append_project_log(
            run_request.log_file_path,
            f"{log_timestamp()} INFO [{run_request.task_id}] {line}",
        )


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
    "PendingScheduledRun",
    "PlaywrightTaskRunner",
    "ScheduledRunResult",
    "ScheduledTaskRunner",
]

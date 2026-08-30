"""定时任务扫描服务的命令行入口。"""

from __future__ import annotations

import argparse
import asyncio
import signal
from contextlib import suppress
from pathlib import Path

from deep_agent.core.config import get_settings, load_project_env_file
from deep_agent.core.runtime_logging import configure_logging_from_env
from deep_agent.scheduler.service import SchedulerService


def build_argument_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""

    parser = argparse.ArgumentParser(
        description="扫描配置文件并串行执行 Web AutoTest 定时任务。"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="定时任务配置文件路径；未传时使用环境变量或默认配置路径。",
    )
    return parser


async def _run() -> None:
    """启动调度服务。"""

    load_project_env_file()
    configure_logging_from_env()
    argument_parser = build_argument_parser()
    args = argument_parser.parse_args()
    settings = get_settings()
    config_path = args.config or settings.resolved_scheduler_config_path
    scheduler_service = SchedulerService(
        settings=settings,
        config_path=config_path,
    )
    event_loop = asyncio.get_running_loop()

    def request_stop() -> None:
        event_loop.create_task(scheduler_service.stop())

    registered_signals: list[signal.Signals] = []
    for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            event_loop.add_signal_handler(shutdown_signal, request_stop)
            registered_signals.append(shutdown_signal)

    try:
        await scheduler_service.run_forever()
    finally:
        for shutdown_signal in registered_signals:
            event_loop.remove_signal_handler(shutdown_signal)


def main() -> None:
    """CLI 同步入口。"""

    asyncio.run(_run())


__all__ = ["main"]

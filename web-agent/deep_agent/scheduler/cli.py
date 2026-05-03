"""定时任务扫描服务的命令行入口。"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from deep_agent.core.config import get_settings
from deep_agent.core.runtime_logging import configure_logging_from_env
from deep_agent.scheduler.service import SchedulerService


def build_argument_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""

    parser = argparse.ArgumentParser(description="扫描配置文件并串行执行 Web AutoTest 定时任务。")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="定时任务配置文件路径；未传时使用环境变量或默认配置路径。",
    )
    return parser


async def _run() -> None:
    """启动调度服务。"""

    configure_logging_from_env()
    argument_parser = build_argument_parser()
    args = argument_parser.parse_args()
    settings = get_settings()
    config_path = args.config or settings.resolved_scheduler_config_path
    scheduler_service = SchedulerService(
        settings=settings,
        config_path=config_path,
    )
    await scheduler_service.run_forever()


def main() -> None:
    """CLI 同步入口。"""

    asyncio.run(_run())


__all__ = ["main"]

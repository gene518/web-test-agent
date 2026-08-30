"""Scheduler 项目日志的轻量写入辅助方法。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


async def append_project_log(log_file_path: Path, line: str) -> None:
    """把一行日志追加到项目测试根目录下的日志文件。"""

    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    with log_file_path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"{line}\n")


def log_timestamp() -> str:
    """返回统一的本地时间戳文本。"""

    return datetime.now().astimezone().isoformat(timespec="seconds")


__all__ = ["append_project_log", "log_timestamp"]

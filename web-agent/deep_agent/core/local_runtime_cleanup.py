"""本地 LangGraph dev runtime 启动清理。"""

from __future__ import annotations

import os
from collections.abc import MutableMapping, Sequence
from datetime import UTC, datetime
from typing import Any

from deep_agent.core.runtime_logging import get_logger


logger = get_logger(__name__)

ACTIVE_RUN_STATUSES = {"pending", "running"}
DEFAULT_GRAPH_ID = "web-autotest-agent"


def mark_stale_runs_interrupted(
    store: MutableMapping[str, Any],
    *,
    graph_id: str = DEFAULT_GRAPH_ID,
) -> int:
    """把本地持久化里遗留的执行中 run 标记为已中断，并保留历史记录。"""

    assistants = store.get("assistants", [])
    runs = store.get("runs", [])
    threads = store.get("threads", [])
    if not isinstance(assistants, Sequence) or not isinstance(runs, Sequence):
        return 0

    assistants_by_id = {
        assistant.get("assistant_id"): assistant
        for assistant in assistants
        if isinstance(assistant, MutableMapping)
    }
    now = datetime.now(tz=UTC)
    interrupted_count = 0

    for run in runs:
        if not isinstance(run, MutableMapping):
            continue
        if run.get("status") not in ACTIVE_RUN_STATUSES:
            continue
        assistant = assistants_by_id.get(run.get("assistant_id"))
        if isinstance(assistant, MutableMapping) and assistant.get("graph_id") != graph_id:
            continue

        run["status"] = "interrupted"
        run["updated_at"] = now
        interrupted_count += 1

    if isinstance(threads, Sequence):
        _mark_idle_threads_without_active_runs(threads=threads, runs=runs, now=now)

    return interrupted_count


def cancel_stale_inmemory_runs_on_start(*, graph_id: str = DEFAULT_GRAPH_ID) -> int:
    """在本地 in-memory runtime 启动时终止上次遗留的 active run。"""

    if os.getenv("WEB_AUTOTEST_CANCEL_STALE_RUNS_ON_START", "1") == "0":
        return 0

    try:
        from langgraph_runtime_inmem.database import GLOBAL_STORE
    except Exception:  # noqa: BLE001
        return 0

    interrupted_count = mark_stale_runs_interrupted(GLOBAL_STORE, graph_id=graph_id)
    if interrupted_count <= 0:
        return 0

    sync = getattr(GLOBAL_STORE, "sync", None)
    if callable(sync):
        sync()

    logger.info("本地启动清理已中断 %s 个遗留执行。", interrupted_count)
    return interrupted_count


def _mark_idle_threads_without_active_runs(
    *,
    threads: Sequence[Any],
    runs: Sequence[Any],
    now: datetime,
) -> None:
    active_thread_ids = {
        run.get("thread_id")
        for run in runs
        if isinstance(run, MutableMapping) and run.get("status") in ACTIVE_RUN_STATUSES
    }

    for thread in threads:
        if not isinstance(thread, MutableMapping):
            continue
        if thread.get("status") != "busy":
            continue
        if thread.get("thread_id") in active_thread_ids:
            continue
        thread["status"] = "idle"
        thread["updated_at"] = now

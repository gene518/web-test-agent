"""面向 Portal 会话的进程内 SSE 事件分发。"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator

from deep_agent.portal.models import PortalEvent


class PortalEventHub:
    """把事件分发给某个会话的全部 EventSource 订阅者。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._subscribers: dict[str, set[asyncio.Queue[PortalEvent]]] = defaultdict(set)

    async def publish(self, event: PortalEvent) -> None:
        async with self._lock:
            subscribers = list(self._subscribers.get(event.session_id, set()))
        for queue in subscribers:
            queue.put_nowait(event)

    async def subscribe(self, session_id: str) -> AsyncIterator[PortalEvent]:
        queue: asyncio.Queue[PortalEvent] = asyncio.Queue()
        async with self._lock:
            self._subscribers[session_id].add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            async with self._lock:
                subscribers = self._subscribers.get(session_id)
                if subscribers is not None:
                    subscribers.discard(queue)
                    if not subscribers:
                        self._subscribers.pop(session_id, None)

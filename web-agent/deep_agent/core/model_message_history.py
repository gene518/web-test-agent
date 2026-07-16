"""模型调用前的历史消息链规范化。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage


def normalize_tool_message_history(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """过滤孤立工具结果，并为未闭合的工具调用补充结果消息。"""

    normalized: list[BaseMessage] = []
    pending_tool_calls: dict[str, str | None] = {}

    def close_pending_tool_calls() -> None:
        for tool_call_id, tool_name in pending_tool_calls.items():
            normalized.append(
                ToolMessage(
                    content="工具调用已处理。",
                    tool_call_id=tool_call_id,
                    name=tool_name,
                )
            )
        pending_tool_calls.clear()

    for message in messages:
        if isinstance(message, AIMessage):
            close_pending_tool_calls()
            normalized.append(message)
            pending_tool_calls.update(_tool_call_ids(message.tool_calls))
            continue

        if isinstance(message, ToolMessage):
            tool_call_id = str(message.tool_call_id or "").strip()
            if tool_call_id and tool_call_id in pending_tool_calls:
                normalized.append(message)
                pending_tool_calls.pop(tool_call_id, None)
            continue

        close_pending_tool_calls()
        normalized.append(message)

    close_pending_tool_calls()
    return normalized


def _tool_call_ids(tool_calls: Sequence[Any]) -> dict[str, str | None]:
    """提取 LangChain 标准工具调用的 ID 与名称。"""

    result: dict[str, str | None] = {}
    for tool_call in tool_calls:
        if not isinstance(tool_call, Mapping):
            continue
        tool_call_id = str(tool_call.get("id") or "").strip()
        if not tool_call_id:
            continue
        raw_name = tool_call.get("name")
        result[tool_call_id] = str(raw_name) if raw_name else None
    return result

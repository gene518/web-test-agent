"""UI 可见消息时间线辅助方法。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, convert_to_messages


def build_display_summary_message(content: str, *, prefix: str) -> AIMessage:
    """构造一条仅用于 UI 展示去重的总结消息。"""

    return AIMessage(content=content, id=f"display-{prefix}-{uuid4()}")


def extract_missing_display_messages(state: dict[str, Any]) -> list[BaseMessage]:
    """从主消息列表中提取尚未写入 display 时间线的消息。"""

    state_messages = _normalize_base_messages(state.get("messages", []))
    display_messages = _normalize_base_messages(state.get("display_messages", []))
    if not state_messages:
        return []
    if not display_messages:
        return state_messages

    display_counts = _message_occurrence_counts(display_messages)
    current_counts: defaultdict[str, int] = defaultdict(int)
    missing_messages: list[BaseMessage] = []
    for message in state_messages:
        fingerprint = _message_fingerprint(message)
        occurrence_index = current_counts[fingerprint]
        current_counts[fingerprint] += 1
        if occurrence_index < display_counts.get(fingerprint, 0):
            continue
        missing_messages.append(message)
    return missing_messages


def normalize_display_delta(messages: Any) -> list[BaseMessage]:
    """过滤出可写入 display 时间线的消息列表。"""

    return _normalize_base_messages(messages)


def _normalize_base_messages(messages: Any) -> list[BaseMessage]:
    if isinstance(messages, BaseMessage):
        candidate_messages: list[Any] = [messages]
    elif isinstance(messages, Mapping):
        candidate_messages = [messages]
    elif isinstance(messages, Sequence) and not isinstance(messages, (str, bytes, bytearray)):
        candidate_messages = list(messages)
    else:
        return []

    normalized_messages: list[BaseMessage] = []
    for message in candidate_messages:
        if isinstance(message, BaseMessage):
            normalized_messages.append(message)
            continue

        try:
            normalized_messages.extend(
                candidate
                for candidate in convert_to_messages([message])
                if isinstance(candidate, BaseMessage)
            )
        except Exception:  # noqa: BLE001
            continue

    return normalized_messages


def _message_occurrence_counts(messages: Sequence[BaseMessage]) -> dict[str, int]:
    counts: defaultdict[str, int] = defaultdict(int)
    for message in messages:
        counts[_message_fingerprint(message)] += 1
    return dict(counts)


def _message_fingerprint(message: BaseMessage) -> str:
    if message.id:
        return f"id:{message.id}"

    tool_call_id = getattr(message, "tool_call_id", "") or ""
    name = getattr(message, "name", "") or ""
    content = message.content
    if isinstance(content, str):
        content_text = content
    else:
        content_text = repr(content)
    return (
        f"type:{message.__class__.__name__}"
        f"|tool_call_id:{tool_call_id}"
        f"|name:{name}"
        f"|content:{content_text}"
    )

"""UI 可见消息时间线辅助方法。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, convert_to_messages


DISPLAY_TEXT_CHAR_LIMIT = 12000
DISPLAY_TOOL_ARG_CHAR_LIMIT = 4000
DISPLAY_COLLECTION_ITEM_LIMIT = 40


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


def sanitize_display_messages(messages: Any) -> list[BaseMessage]:
    """把 UI 时间线消息裁剪到适合前端保存和渲染的体量。"""

    return [_sanitize_display_message(message) for message in _normalize_base_messages(messages)]


def _sanitize_display_message(message: BaseMessage) -> BaseMessage:
    updates: dict[str, Any] = {
        "content": _truncate_display_value(
            message.content,
            max_string_chars=DISPLAY_TEXT_CHAR_LIMIT,
        )
    }

    tool_calls = getattr(message, "tool_calls", None)
    if isinstance(tool_calls, list):
        updates["tool_calls"] = _truncate_display_value(
            tool_calls,
            max_string_chars=DISPLAY_TOOL_ARG_CHAR_LIMIT,
        )

    additional_kwargs = getattr(message, "additional_kwargs", None)
    if isinstance(additional_kwargs, Mapping) and additional_kwargs:
        updates["additional_kwargs"] = _truncate_display_value(
            dict(additional_kwargs),
            max_string_chars=DISPLAY_TOOL_ARG_CHAR_LIMIT,
        )

    artifact = getattr(message, "artifact", None)
    if artifact is not None:
        updates["artifact"] = _truncate_display_value(
            artifact,
            max_string_chars=DISPLAY_TEXT_CHAR_LIMIT,
        )

    return message.model_copy(update=updates)


def _truncate_display_value(
    value: Any,
    *,
    max_string_chars: int,
    depth: int = 0,
) -> Any:
    if isinstance(value, str):
        return _truncate_display_text(value, max_string_chars=max_string_chars)

    if isinstance(value, Mapping):
        if depth >= 6:
            return _truncate_display_text(str(dict(value)), max_string_chars=max_string_chars)
        truncated: dict[str, Any] = {}
        items = list(value.items())
        for key, item_value in items[:DISPLAY_COLLECTION_ITEM_LIMIT]:
            truncated[str(key)] = _truncate_display_value(
                item_value,
                max_string_chars=max_string_chars,
                depth=depth + 1,
            )
        if len(items) > DISPLAY_COLLECTION_ITEM_LIMIT:
            truncated["__truncated_items__"] = len(items) - DISPLAY_COLLECTION_ITEM_LIMIT
        return truncated

    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        if depth >= 6:
            return _truncate_display_text(str(list(value)), max_string_chars=max_string_chars)
        items = list(value)
        truncated_items = [
            _truncate_display_value(
                item,
                max_string_chars=max_string_chars,
                depth=depth + 1,
            )
            for item in items[:DISPLAY_COLLECTION_ITEM_LIMIT]
        ]
        if len(items) > DISPLAY_COLLECTION_ITEM_LIMIT:
            truncated_items.append({"type": "text", "text": f"[UI 展示已省略 {len(items) - DISPLAY_COLLECTION_ITEM_LIMIT} 个条目]"})
        return truncated_items

    return value


def _truncate_display_text(value: str, *, max_string_chars: int) -> str:
    if len(value) <= max_string_chars:
        return value

    omitted_chars = len(value) - max_string_chars
    return f"{value[:max_string_chars]}\n\n[UI 展示已截断，省略 {omitted_chars} 个字符]"


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

"""运行时可见消息时间线收集 core。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langgraph.config import get_stream_writer

from .display_messages import normalize_display_delta, sanitize_display_messages


@dataclass(slots=True)
class VisibleTranscriptCollector:
    """收集 Specialist 运行过程里用户需要看到的轻量消息。"""

    messages: list[BaseMessage] = field(default_factory=list)
    final_output: dict[str, Any] | None = None

    def consume_event(self, event: Mapping[str, Any]) -> list[BaseMessage]:
        """消费一条事件流记录，并更新最终输出与可见消息。"""

        self.final_output = capture_final_output(self.final_output, event)
        previous_count = len(self.messages)
        self.messages = append_stream_messages(self.messages, event)
        return self.messages[previous_count:]


def emit_display_message_delta(messages: Sequence[BaseMessage]) -> None:
    """通过 LangGraph custom stream 立即推送 UI 可见消息增量。"""

    if not messages:
        return

    try:
        writer = get_stream_writer()
    except RuntimeError:
        return

    writer(
        {
            "type": "display_messages",
            "messages": [message.model_dump(mode="json") for message in sanitize_display_messages(messages)],
        }
    )


def build_runtime_message_result(
    *,
    collector: VisibleTranscriptCollector,
    existing_messages: Sequence[Any],
    fallback_message: str,
) -> dict[str, list[BaseMessage]]:
    """把运行时可见消息和最终输出统一转换成工作流增量消息。"""

    merged_messages = filter_display_worthy_messages(list(collector.messages))
    final_output = collector.final_output
    if final_output is not None:
        all_messages = final_output.get("messages", [])
        if isinstance(all_messages, list):
            merged_messages = merge_unique_messages(
                merged_messages,
                filter_display_worthy_messages(
                    normalize_display_delta(all_messages[len(existing_messages) :])
                ),
            )

    if merged_messages:
        return {"messages": merged_messages}

    return {"messages": [AIMessage(content=fallback_message)]}


def capture_final_output(
    current_output: dict[str, Any] | None,
    event: Mapping[str, Any],
) -> dict[str, Any] | None:
    """只从根链路的结束事件提取最终输出。"""

    if event.get("event") != "on_chain_end" or event.get("parent_ids"):
        return current_output

    data = event.get("data")
    if not isinstance(data, Mapping):
        return current_output

    output = data.get("output")
    if isinstance(output, dict):
        return output

    return current_output


def append_stream_messages(
    current_messages: Sequence[BaseMessage],
    event: Mapping[str, Any],
) -> list[BaseMessage]:
    """按事件顺序收集本轮需要保留到 UI 时间线的消息。"""

    extracted_messages = extract_stream_messages(event)
    if not extracted_messages:
        return list(current_messages)

    return merge_unique_messages(current_messages, extracted_messages)


def merge_unique_messages(
    current_messages: Sequence[BaseMessage],
    new_messages: Sequence[BaseMessage],
) -> list[BaseMessage]:
    """按指纹合并消息，保留原有顺序并跳过重复项。"""

    seen = {message_fingerprint(message) for message in current_messages}
    merged_messages = list(current_messages)
    for message in new_messages:
        fingerprint = message_fingerprint(message)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        merged_messages.append(message)
    return merged_messages


def extract_stream_messages(event: Mapping[str, Any]) -> list[BaseMessage]:
    """从模型/工具事件里提取本轮新增的轻量可见消息。"""

    event_name = event.get("event")
    if event_name not in {"on_chat_model_end", "on_tool_end", "on_tool_error"}:
        return []

    data = event.get("data")
    if not isinstance(data, Mapping):
        return []

    value_key = "output" if event_name != "on_tool_error" else "error"
    extracted = extract_messages_from_event_value(data.get(value_key))
    return filter_display_worthy_messages(extracted)


def extract_messages_from_event_value(value: Any) -> list[BaseMessage]:
    """递归展开事件输出中的消息对象和 `Command(update={messages})` 包装。"""

    if isinstance(value, BaseMessage):
        return [value]

    if isinstance(value, Mapping):
        if "messages" in value:
            return extract_messages_from_event_value(value["messages"])
        if "update" in value:
            return extract_messages_from_event_value(value["update"])
        return []

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        messages: list[BaseMessage] = []
        for item in value:
            messages.extend(extract_messages_from_event_value(item))
        return messages

    nested_update = getattr(value, "update", None)
    if nested_update is not None:
        return extract_messages_from_event_value(nested_update)

    nested_messages = getattr(value, "messages", None)
    if nested_messages is not None:
        return extract_messages_from_event_value(nested_messages)

    return []


def is_display_worthy_message(message: BaseMessage) -> bool:
    """只保留适合进入主时间线的轻量消息。"""

    if not isinstance(message, AIMessage):
        return False
    return content_has_visible_text(message.content)


def filter_display_worthy_messages(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """过滤出允许进入主时间线的轻量消息列表。"""

    return [message for message in messages if is_display_worthy_message(message)]


def content_has_visible_text(content: Any) -> bool:
    """判断消息内容是否包含用户真正需要看到的文本。"""

    if isinstance(content, str):
        return bool(content.strip())

    if isinstance(content, Sequence) and not isinstance(content, (str, bytes, bytearray)):
        for block in content:
            if isinstance(block, str) and block.strip():
                return True
            if isinstance(block, Mapping):
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    return True
        return False

    return False


def message_fingerprint(message: BaseMessage) -> str:
    """为流式事件里的消息生成稳定指纹，用于去重。"""

    if message.id:
        return f"id:{message.id}"

    tool_call_id = getattr(message, "tool_call_id", "") or ""
    name = getattr(message, "name", "") or ""
    content = message.content if isinstance(message.content, str) else repr(message.content)
    return (
        f"type:{message.__class__.__name__}"
        f"|tool_call_id:{tool_call_id}"
        f"|name:{name}"
        f"|content:{content}"
    )

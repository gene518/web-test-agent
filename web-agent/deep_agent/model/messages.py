"""不同模型供应商共用的消息规范化。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import BaseMessage, SystemMessage

from deep_agent.model.capabilities import ModelCapabilities


def normalize_messages(
    messages: Sequence[BaseMessage],
    capabilities: ModelCapabilities,
) -> list[BaseMessage]:
    """按供应商要求把所有 System 内容合并为唯一首条消息。"""

    normalized = list(messages)
    if capabilities.system_message_policy != "single_first":
        return normalized

    system_messages = [message for message in normalized if isinstance(message, SystemMessage)]
    if not system_messages:
        return normalized

    merged_content = "\n\n".join(_message_content_to_text(message.content) for message in system_messages if message.content)
    first_system = system_messages[0].model_copy(update={"content": merged_content})
    return [first_system, *(message for message in normalized if not isinstance(message, SystemMessage))]


def append_system_instruction(
    messages: Sequence[BaseMessage],
    instruction: str,
    capabilities: ModelCapabilities,
) -> list[BaseMessage]:
    """追加系统约束后再次执行 System 归一化。"""

    return normalize_messages([*messages, SystemMessage(content=instruction)], capabilities)


def _message_content_to_text(content: str | list[str | dict[str, Any]]) -> str:
    if isinstance(content, str):
        return content
    text_parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            text_parts.append(block)
        elif isinstance(block, dict):
            text = block.get("text")
            if isinstance(text, str):
                text_parts.append(text)
            else:
                text_parts.append(str(block))
    return "\n".join(text_parts)

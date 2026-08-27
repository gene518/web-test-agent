"""推理内容的内部保留和 UI 脱敏辅助。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any


_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>\s*", flags=re.IGNORECASE | re.DOTALL)
_REASONING_BLOCK_TYPES = frozenset({"thinking", "reasoning", "reasoning_content", "reasoning_details"})
_REASONING_KEYS = frozenset({"thinking", "reasoning_content", "reasoning_details"})


def sanitize_reasoning_for_display(content: Any) -> Any:
    """从 UI 副本中移除推理块，内部模型消息保持不变。"""

    if isinstance(content, str):
        cleaned = _THINK_BLOCK_RE.sub("", content)
        unclosed_index = cleaned.lower().find("<think>")
        if unclosed_index >= 0:
            cleaned = cleaned[:unclosed_index]
        return cleaned.strip()

    if isinstance(content, Sequence) and not isinstance(content, (str, bytes, bytearray)):
        return [
            sanitize_reasoning_for_display(item)
            for item in content
            if not (isinstance(item, Mapping) and str(item.get("type", "")).lower() in _REASONING_BLOCK_TYPES)
        ]
    return content


def sanitize_reasoning_metadata_for_display(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """移除 additional_kwargs 中不应进入前端时间线的推理字段。"""

    return {key: value for key, value in metadata.items() if key not in _REASONING_KEYS}

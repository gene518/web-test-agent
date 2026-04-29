"""MCP 工具错误包装的通用能力。"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol


_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_MAX_TOOL_ERROR_MESSAGE_CHARS = 4000


class MCPToolErrorPolicy(Protocol):
    """描述某类 MCP 工具错误的分类与恢复策略。"""

    def classify_tool_error(self, error_message: str) -> str:
        """根据错误文本返回结构化错误类型。"""

    def recovery_instruction_for(self, error_type: str) -> str:
        """返回当前错误类型对应的恢复建议。"""

    def is_retryable(self, error_type: str) -> bool:
        """判断当前错误类型是否允许模型继续重试。"""


class GenericMCPToolErrorPolicy:
    """默认的 MCP 工具错误策略。"""

    _recovery_instructions = {
        "TOOL_ARGS_INVALID": "先根据工具定义修正参数名和参数值，再重试。不要原样重复同一次错误调用。",
        "UNKNOWN_TOOL_ERROR": "先分析错误信息，不要机械重复相同调用。可尝试重新观察、修正参数、换工具，或在必要时跳过该步骤并说明原因。",
    }

    def classify_tool_error(self, error_message: str) -> str:
        del error_message
        return "UNKNOWN_TOOL_ERROR"

    def recovery_instruction_for(self, error_type: str) -> str:
        return self._recovery_instructions.get(error_type, self._recovery_instructions["UNKNOWN_TOOL_ERROR"])

    def is_retryable(self, error_type: str) -> bool:
        del error_type
        return True


DEFAULT_MCP_TOOL_ERROR_POLICY = GenericMCPToolErrorPolicy()


def normalize_tool_error_message(error: Any) -> str:
    """把工具异常统一整理成适合暴露给模型的文本。"""

    raw_message = str(error).strip() or error.__class__.__name__
    normalized_message = _ANSI_ESCAPE_RE.sub("", raw_message).replace("\r\n", "\n").replace("\r", "\n")
    normalized_message = re.sub(r"\n{3,}", "\n\n", normalized_message).strip()
    if len(normalized_message) <= _MAX_TOOL_ERROR_MESSAGE_CHARS:
        return normalized_message
    return f"{normalized_message[:_MAX_TOOL_ERROR_MESSAGE_CHARS]}... [truncated]"


def build_structured_tool_error(
    *,
    tool_name: str,
    error_type: str,
    error_message: str,
    tool_error_policy: MCPToolErrorPolicy,
) -> str:
    """把工具错误包装成模型可见的 JSON 结果。"""

    payload = {
        "ok": False,
        "type": "tool_error",
        "tool_name": tool_name,
        "error_type": error_type,
        "error_message": error_message,
        "retryable": tool_error_policy.is_retryable(error_type),
        "recovery_instruction": tool_error_policy.recovery_instruction_for(error_type),
    }
    return json.dumps(payload, ensure_ascii=False)

"""工具 Schema 预检与供应商工具参数适配。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import json
import re
from typing import Any

from langchain_core.tools import BaseTool

from deep_agent.model.capabilities import ModelCapabilities
from deep_agent.model.errors import ToolProtocolError
from deep_agent.model.settings import ResolvedModelConnection


_TOOL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")


@dataclass(frozen=True, slots=True)
class ToolSetDiagnostics:
    count: int
    names: tuple[str, ...]


def validate_tool_set(
    tools: Sequence[BaseTool],
    capabilities: ModelCapabilities,
    connection: ResolvedModelConnection,
) -> ToolSetDiagnostics:
    """在请求模型前检查工具名、重复项、Schema 和数量限制。"""

    names = [tool.name for tool in tools]
    duplicate_names = sorted({name for name in names if names.count(name) > 1})
    if duplicate_names:
        raise ToolProtocolError(
            f"模型工具列表包含重复名称：{', '.join(duplicate_names)}。",
            context=_tool_error_context(connection),
        )
    invalid_names = [name for name in names if not _TOOL_NAME_RE.fullmatch(name)]
    if invalid_names:
        raise ToolProtocolError(
            f"模型工具名称不符合兼容接口约束：{', '.join(invalid_names)}。",
            context=_tool_error_context(connection),
        )
    if capabilities.max_tools is not None and len(tools) > capabilities.max_tools:
        raise ToolProtocolError(
            f"当前模型最多支持 {capabilities.max_tools} 个工具，实际收到 {len(tools)} 个。",
            context=_tool_error_context(connection),
        )

    for tool in tools:
        schema = tool.args_schema
        if schema is None:
            continue
        try:
            schema_value = schema if isinstance(schema, dict) else schema.model_json_schema()
            json.dumps(schema_value)
        except Exception as exc:  # noqa: BLE001
            raise ToolProtocolError(
                f"工具 `{tool.name}` 的参数 Schema 无法序列化。",
                context=_tool_error_context(connection),
                cause=exc,
            ) from exc
    return ToolSetDiagnostics(count=len(tools), names=tuple(names))


def adapt_tool_binding(
    *,
    tool_choice: Any,
    kwargs: dict[str, Any],
    capabilities: ModelCapabilities,
    connection: ResolvedModelConnection,
) -> tuple[Any, dict[str, Any]]:
    """移除供应商不支持的工具参数，并对 GLM 强制使用 auto。"""

    adapted_kwargs = dict(kwargs)
    if capabilities.supports_parallel_tool_calls:
        adapted_kwargs["parallel_tool_calls"] = False
    else:
        adapted_kwargs.pop("parallel_tool_calls", None)

    adapted_choice = tool_choice
    if connection.family == "minimax" and connection.channel != "minimax_anthropic":
        adapted_choice = None
    elif connection.family == "glm" and adapted_choice not in (None, "auto"):
        adapted_choice = "auto"
    elif connection.family == "qwen" and connection.thinking == "enabled" and adapted_choice not in (None, "auto"):
        adapted_choice = "auto"
    return adapted_choice, adapted_kwargs


def _tool_error_context(connection: ResolvedModelConnection) -> dict[str, str]:
    return {
        "role": connection.role,
        "family": connection.family,
        "channel": connection.channel,
        "model": connection.api_model_name,
    }

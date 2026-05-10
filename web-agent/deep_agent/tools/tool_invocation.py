"""工具调用与输出结构的通用辅助。

本模块的作用是把"如何解读 MCP/LangChain 工具的原始输出、如何绕过 BaseTool 的
外层校验直接调用底层实现"这类跨 provider 的通用能力集中在一处，便于 `MCPToolsManager`
和各个 provider 的包装逻辑复用。这些函数对外是纯函数，不依赖项目状态。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool


def is_tool_error_output(output: Any) -> bool:
    """判断工具输出是否表示错误结果。

    用于在包装层决定"要不要把这次调用视作失败并走统一错误路径"。
    它兼容三种来源：LangChain `ToolMessage`、字典形态的返回值、以及已经被序列化成
    JSON 字符串的工具结果。
    """

    status = getattr(output, "status", None)
    if status == "error":
        return True
    if isinstance(output, dict):
        if output.get("status") == "error":
            return True
        if output.get("ok") is False or output.get("type") == "tool_error":
            return True

    content = getattr(output, "content", output)
    if isinstance(content, str):
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return False
        return isinstance(payload, dict) and (
            payload.get("ok") is False or payload.get("type") == "tool_error"
        )
    return False


def tool_output_text(output: Any) -> str:
    """把工具输出压成一段可以用 `str.lower()` / 关键字检测的文本。

    该函数不保证可读美观，只保证"一段稳定的字符串"，便于 provider 的包装层基于
    错误关键字做分支判断。
    """

    content = getattr(output, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        try:
            return json.dumps(content, ensure_ascii=False, default=str)
        except TypeError:
            return str(content)
    if isinstance(output, str):
        return output
    try:
        return json.dumps(output, ensure_ascii=False, default=str)
    except TypeError:
        return str(output)


def tool_output_content(output: Any) -> Any:
    """提取工具输出的 content 部分。

    该函数用于在 provider 的包装层把底层工具的原始返回转回"给外层 LangChain 工具最终
    呈现的内容"，避免 ToolMessage 被二次嵌套。
    """

    if isinstance(output, ToolMessage):
        return output.content
    if isinstance(output, dict) and "content" in output:
        return output["content"]
    return output


async def invoke_tool_raw_result(tool: BaseTool, payload: dict[str, Any]) -> Any:
    """直接执行工具的原始实现，绕过 BaseTool 的外层校验与事件包装。

    这样做的目的，是在 provider 的包装层先看到"工具真正返回了什么"，据此决定是否
    要做业务校验、重试、路径纠正等后续动作，而不是先被 BaseTool 按 response_format
    校验失败。
    """

    coroutine = getattr(tool, "coroutine", None)
    if callable(coroutine):
        return await coroutine(**payload)

    arun_impl = getattr(tool, "_arun", None)
    if arun_impl is not None and getattr(arun_impl, "__func__", None) is not BaseTool._arun:
        return await arun_impl(**payload)

    run_impl = getattr(tool, "_run", None)
    if run_impl is not None and getattr(run_impl, "__func__", None) is not BaseTool._run:
        return await asyncio.to_thread(run_impl, **payload)

    tool_call = {
        "type": "tool_call",
        "name": tool.name,
        "args": payload,
        "id": f"tool-invoke-{uuid4()}",
    }
    return await tool.ainvoke(tool_call)


__all__ = [
    "invoke_tool_raw_result",
    "is_tool_error_output",
    "tool_output_content",
    "tool_output_text",
]

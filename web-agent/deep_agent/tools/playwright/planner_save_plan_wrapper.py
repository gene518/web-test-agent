"""`planner_save_plan` 工具的业务规则包装。

这个模块的作用是把 Plan 阶段特有的三类规则从 `MCPToolsManager` 移出来，集中放到
Playwright provider 自己的领域内：

1. `planner_save_plan.fileName` 必须符合 `test_case/aaaplanning_{name}/aaa_{name}.md` 规范；
2. 工具首次返回"父目录不存在"错误时，本地自动创建父目录并按原参数重试；
3. 最终执行结果仍要按常规 MCP 工具错误包装为 `ToolException`，以便进入统一错误处理。

模块只对外暴露 `wrap_planner_save_plan_tool`，由 provider 在 `post_process_tool`
钩子里调用，`MCPToolsManager` 不再需要关心 Plan 阶段的任何业务细节。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from langchain_core.tools.base import ToolException

from deep_agent.core.runtime_logging import get_logger, log_title
from deep_agent.tools.tool_error_handling import (
    DEFAULT_MCP_TOOL_ERROR_POLICY,
    MCPToolErrorPolicy,
    build_structured_tool_error,
    normalize_tool_error_message,
)
from deep_agent.tools.tool_invocation import (
    invoke_tool_raw_result,
    is_tool_error_output,
    tool_output_content,
    tool_output_text,
)


PLANNER_SAVE_PLAN_TOOL_NAME = "planner_save_plan"
PLANNING_DIR_PREFIX = "aaaplanning_"
PLAN_FILE_PREFIX = "aaa_"

# Playwright MCP 在父目录缺失时返回的若干典型错误片段；大小写不敏感匹配。
_PARENT_DIR_MISSING_ERROR_MARKERS = (
    "enoent",
    "resource_not_found",
    "no such file or directory",
    "parent directory",
    "directory does not exist",
    "cannot find path",
)

logger = get_logger(__name__)


def wrap_planner_save_plan_tool(
    tool: BaseTool,
    *,
    workspace_dir: Path | None,
    tool_error_policy: MCPToolErrorPolicy | None = None,
) -> BaseTool:
    """把 `planner_save_plan` 工具包一层业务规则守卫。

    - 入参 `fileName` 不符合规范时直接抛 `ToolException`，由上层 MCPToolsManager
      的 `handle_tool_error` 转成结构化 JSON。
    - 首次保存失败且命中"父目录缺失"时，在 workspace 内创建父目录并原参重试一次。
    - 最终仍失败的结果一律走 `ToolException` 分支，保证和其他工具错误路径一致。

    非 `planner_save_plan` 工具会被原样返回，不受影响。
    """

    if getattr(tool, "name", None) != PLANNER_SAVE_PLAN_TOOL_NAME:
        return tool

    effective_policy = tool_error_policy or DEFAULT_MCP_TOOL_ERROR_POLICY

    async def guarded_planner_save_plan(**payload: Any) -> Any:
        relative_file = _validate_file_name(payload)
        first_output = await _invoke_with_error_preserved(tool, payload, effective_policy)
        final_output = first_output
        if _is_parent_dir_missing_tool_output(first_output) and workspace_dir is not None:
            plan_dir = workspace_dir / relative_file.parent
            await asyncio.to_thread(plan_dir.mkdir, parents=True, exist_ok=True)
            logger.info(
                "%s planner_save_plan 首次保存缺少父目录，已创建后原参重试 workspace_dir=%s fileName=%s",
                log_title("工具", "Planner保存"),
                workspace_dir,
                relative_file.as_posix(),
            )
            final_output = await _invoke_with_error_preserved(tool, payload, effective_policy)

        _raise_if_tool_error_output(final_output)
        return tool_output_content(final_output)

    wrapped_tool = StructuredTool.from_function(
        coroutine=guarded_planner_save_plan,
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
        return_direct=tool.return_direct,
        response_format="content",
    )
    wrapped_tool.callbacks = tool.callbacks
    wrapped_tool.tags = tool.tags
    wrapped_tool.metadata = tool.metadata
    wrapped_tool.verbose = tool.verbose
    return wrapped_tool


async def _invoke_with_error_preserved(
    tool: BaseTool,
    payload: dict[str, Any],
    tool_error_policy: MCPToolErrorPolicy,
) -> Any:
    """调用底层 `planner_save_plan`，保留第一次失败结果供重试逻辑判断。

    不直接走 `tool.ainvoke`，因为底层 MCP 适配器声明 `response_format='content_and_artifact'`，
    而真实返回可能只给 content list，直接调会在格式校验阶段抛 `ValueError`。这里改为
    调用底层实现拿到原始返回；如果首次抛出"父目录缺失"异常，把它转成结构化错误结果
    由外层判断是否需要自动建目录后重试，其他错误类型则原样继续抛出。
    """

    try:
        raw_output = await invoke_tool_raw_result(tool, payload)
        if isinstance(raw_output, tuple):
            # `content_and_artifact` 形式的 (content, artifact) 元组，这里只取 content。
            try:
                content, _artifact = raw_output
            except ValueError:
                return raw_output
            return content
        return raw_output
    except ToolException as exc:
        if _is_parent_dir_missing_tool_output(exc):
            return build_structured_tool_error(
                tool_name=tool.name,
                error_type=tool_error_policy.classify_tool_error(normalize_tool_error_message(exc)),
                error_message=normalize_tool_error_message(exc),
                tool_error_policy=tool_error_policy,
            )
        raise


def _validate_file_name(payload: dict[str, Any]) -> Path:
    """校验 `fileName` 是否遵循 aaaplanning 规范，返回相对 Path 以便后续建目录。"""

    raw_file_name = payload.get("fileName")
    if not isinstance(raw_file_name, str) or not raw_file_name.strip():
        raise ToolException(
            "`planner_save_plan.fileName` 不能为空，必须保存到 "
            "`test_case/aaaplanning_{plan-name}/aaa_{plan-name}.md`。"
        )

    relative_file = Path(raw_file_name.strip())
    expected_path = _expected_file_path(payload, relative_file)
    if (
        relative_file.is_absolute()
        or ".." in relative_file.parts
        or len(relative_file.parts) != 3
        or relative_file.parts[0] != "test_case"
        or not relative_file.parts[1].startswith(PLANNING_DIR_PREFIX)
    ):
        raise _invalid_file_name_error(raw_file_name, expected_path)

    plan_identifier = relative_file.parts[1].removeprefix(PLANNING_DIR_PREFIX)
    expected_file_name = f"{PLAN_FILE_PREFIX}{plan_identifier}.md"
    if not plan_identifier or relative_file.name != expected_file_name:
        raise _invalid_file_name_error(raw_file_name, expected_path)
    return relative_file


def _invalid_file_name_error(received_path: str, expected_path: str | None) -> ToolException:
    """构造一致的非法 fileName 错误消息。"""

    expected_suffix = f" 请改用 `{expected_path}`。" if expected_path else ""
    return ToolException(
        "`planner_save_plan.fileName` 必须保存到 "
        "`test_case/aaaplanning_{plan-name}/aaa_{plan-name}.md`，"
        f"当前收到：`{received_path}`。{expected_suffix}"
    )


def _expected_file_path(payload: dict[str, Any], relative_file: Path) -> str | None:
    """基于现有输入推断用户应该使用的规范路径，便于提示词里给出纠正建议。"""

    plan_identifier = _infer_plan_identifier(payload, relative_file)
    if not plan_identifier:
        return None
    return f"test_case/{PLANNING_DIR_PREFIX}{plan_identifier}/{PLAN_FILE_PREFIX}{plan_identifier}.md"


def _infer_plan_identifier(payload: dict[str, Any], relative_file: Path) -> str | None:
    """优先使用 payload 中的 name，否则从现有 fileName 中推断出计划名。"""

    raw_name = payload.get("name")
    if isinstance(raw_name, str):
        plan_identifier = raw_name.strip()
        if plan_identifier and "/" not in plan_identifier and "\\" not in plan_identifier:
            return plan_identifier

    file_name = relative_file.name
    if file_name.startswith(PLAN_FILE_PREFIX) and file_name.endswith(".md"):
        plan_identifier = file_name[len(PLAN_FILE_PREFIX) : -len(".md")]
        if plan_identifier:
            return plan_identifier
    return None


def _is_parent_dir_missing_tool_output(output: Any) -> bool:
    """判断输出文本是否命中"父目录缺失"的一类错误关键字。"""

    text = tool_output_text(output).lower()
    return any(marker in text for marker in _PARENT_DIR_MISSING_ERROR_MARKERS)


def _raise_if_tool_error_output(output: Any) -> None:
    """把底层工具的错误 `ToolMessage` 转回 `ToolException`，交给外层统一错误处理。"""

    if is_tool_error_output(output):
        raise ToolException(tool_output_text(output))


__all__ = ["wrap_planner_save_plan_tool"]

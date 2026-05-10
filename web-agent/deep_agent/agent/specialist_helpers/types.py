"""Specialist 共享的运行时数据结构。

本模块只承担"类型定义"职责：描述 Plan / Generator / Healer 在单次执行过程中会反复
使用的上下文与静态配置。调用方是 `BaseSpecialistAgent` 与其子类；把这些结构抽出来是
为了让运行期流程和业务字段解耦，后续新增配置字段只改这里即可。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool

from deep_agent.config.specialist_file_filter import SpecialistFileFilter


@dataclass(slots=True)
class SpecialistExecutionContext:
    """承接单次 Specialist 执行所需的完整运行上下文。"""

    workspace_dir: Path | None = field(metadata={"description": "当前 Specialist 执行所在的项目目录；没有工作目录约束时为 None。"})
    system_prompt: str = field(
        metadata={"description": "本次执行最终拼装后的 system prompt，已经包含运行时上下文和规范补充。"}
    )
    tools: list[BaseTool] | tuple[BaseTool, ...] = field(
        metadata={"description": "当前 Specialist 允许调用的工具集合，通常由 MCP 管理器按白名单过滤后返回。"}
    )
    trace_context: dict[str, Any] = field(
        metadata={"description": "本次 Specialist 执行对应的 session/thread/run 调试标识。"}
    )


@dataclass(frozen=True, slots=True)
class SpecialistRuntimeConfig:
    """描述单个 Specialist 的静态变化点。"""

    system_prompt_parts: tuple[str, ...] = field(default_factory=tuple)
    allowed_playwright_test_mcp_tools: tuple[str, ...] = field(default=())
    load_project_standard: bool = True
    project_standard_file_name: str = "web_standard.md"
    query_filter_config: SpecialistFileFilter = field(default_factory=SpecialistFileFilter)

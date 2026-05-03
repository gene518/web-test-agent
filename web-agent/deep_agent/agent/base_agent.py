"""所有 Agent 的抽象基类与 Specialist 公共逻辑。

本文件的目标不是单纯“放一个父类”，而是把 Specialist 共享的执行骨架收敛到一处，
让 Plan / Generator / Healer 只保留各自真正不同的业务规则，避免重复维护
workspace 解析、MCP 工具准备、prompt 拼装和 Deep Agent 调用细节。
"""

from __future__ import annotations
import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from deepagents.middleware import FilesystemPermission
from langchain.chat_models import init_chat_model
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from deep_agent.core.config import AppSettings
from deep_agent.agent.artifacts import (
    append_artifact_history,
    append_stage_summary,
    build_stage_summary,
)
from deep_agent.core.display_message import (
    build_display_summary_message,
    build_runtime_message_result,
    extract_missing_display_messages,
    normalize_display_delta,
)
from deep_agent.core.runtime_logging import (
    build_trace_context,
    debug_full_messages_enabled,
    debug_max_chars,
    format_messages_for_log,
    format_state_for_log,
    format_value_for_log,
    get_logger,
    log_debug_event,
    log_title,
    serialize_tools_for_log,
    summarize_model_kwargs,
    with_trace_context,
)
from deep_agent.agent.state import WorkflowState
from deep_agent.config.specialist_file_filter import SpecialistFileFilter
from deep_agent.tools import MCPToolsManager, get_mcp_tools_manager
from deep_agent.tools.playwright import PLAYWRIGHT_TEST_MCP_SERVER_NAME


logger = get_logger(__name__)


@dataclass(slots=True)
class SpecialistExecutionContext:
    """承接单次 Specialist 执行所需的完整运行上下文。

    把 workspace、system prompt 和工具集合提前整理成一个对象，是为了让后续
    “创建 Agent”和“执行 Agent”两个阶段只关心消费结果，不再重复关心准备过程。
    """

    workspace_dir: Path | None = field(metadata={"description": "当前 Specialist 执行所在的项目目录；没有工作目录约束时为 None。"})
    system_prompt: str = field(
        metadata={"description": "本次执行最终拼装后的 system prompt，已经包含运行时上下文和规范补充。"}
    )
    tools: Sequence[BaseTool] = field(
        metadata={"description": "当前 Specialist 允许调用的工具集合，通常由 MCP 管理器按白名单过滤后返回。"}
    )
    trace_context: dict[str, Any] = field(
        metadata={"description": "本次 Specialist 执行对应的 session/thread/run 调试标识。"}
    )


@dataclass(frozen=True, slots=True)
class SpecialistRuntimeConfig:
    """描述单个 Specialist 的静态变化点。

    这个配置对象的目的，是把每种 Specialist 的差异收敛成“system prompt 片段、工具白名单、项目规范策略”
    这样的静态参数，进而复用同一条执行主链路，减少子类里散落的条件分支。
    """

    system_prompt_parts: tuple[str, ...] = field(
        default_factory=tuple,
        metadata={"description": "当前 Specialist 的 system prompt 片段列表，会按顺序拼装成最终提示词。"},
    )
    allowed_playwright_test_mcp_tools: tuple[str, ...] = field(
        default=(),
        metadata={"description": "允许暴露给当前 Specialist 的 Playwright Test MCP 工具白名单。"},
    )
    load_project_standard: bool = field(
        default=True,
        metadata={"description": "是否尝试从项目目录加载额外的项目规范文件。"},
    )
    project_standard_file_name: str = field(
        default="web_standard.md",
        metadata={"description": "项目规范文件名，默认会在 workspace 下查找该文件并附加到 prompt。"},
    )
    query_filter_config: SpecialistFileFilter = field(
        default_factory=SpecialistFileFilter,
        metadata={"description": "当前 Specialist 的文件查询过滤配置，会转成内置文件工具的读权限规则。"},
    )


class BaseAgent(ABC):
    """定义所有工作流 Agent 的统一执行契约。

    统一抽象接口的目的，是让 LangGraph 节点无论接入 Master 还是 Specialist，
    都遵循同一种 `execute(state) -> state_delta` 约定，降低图编排和测试的心智负担。
    """

    @abstractmethod
    async def execute(self, state: WorkflowState, config: RunnableConfig | None = None) -> WorkflowState:
        """执行当前 Agent 的核心逻辑。

        Args:
            state: 当前 LangGraph 工作流状态。

        Returns:
            WorkflowState: 需要合并回图状态中的增量字段。

        Raises:
            NotImplementedError: 子类未实现时抛出。
        """


class BaseSpecialistAgent(BaseAgent, ABC):
    """为 Plan / Generator / Healer 提供统一的 Deep Agents 执行骨架。

    它存在的目的，是把“准备运行上下文 -> 创建 Deep Agent -> 执行并提取新增消息”
    这条通用链路固定下来，让子类只覆写必要的业务差异，例如参数校验、工作目录策略和
    特殊收尾逻辑。
    """

    agent_type: str = "specialist"
    display_name: str = "Specialist Agent"
    runtime_config = SpecialistRuntimeConfig()

    def __init__(
        self,
        settings: AppSettings,
        mcp_manager: MCPToolsManager | None = None,
    ) -> None:
        """初始化 Specialist Agent。

        Args:
            settings: 应用运行配置。
            mcp_manager: 可选的 MCP 工具管理器，用于测试或自定义注入。

        Returns:
            None.

        Raises:
            None.
        """

        self._settings = settings
        # 这里优先允许测试注入自定义 MCP 管理器；生产场景下则复用全局单例，
        # 目的是避免每个 Specialist 都重复拉起一套 MCP 子进程。
        self._mcp_manager = mcp_manager or get_mcp_tools_manager(settings)
        logger.info("%s Agent 初始化完成 display_name=%s",
            log_title("初始化", "Agent初始化"), self.display_name,)

    async def execute(self, state: WorkflowState, config: RunnableConfig | None = None) -> WorkflowState:
        """执行 Specialist Agent。

        Args:
            state: 当前工作流状态。

        Returns:
            WorkflowState: 只追加本节点新增的消息。
        """

        node_name = f"{self.agent_type}_node"
        trace_context = build_trace_context(config, node_name=node_name, event_name="node_enter")
        logger.info("%s event=node_enter trace=%s display_name=%s state=%s",
            log_title("执行", "节点入参", node_name=node_name), trace_context, self.display_name, format_state_for_log(state, self._settings),)

        # 执行前先做业务侧必填校验，避免把明显缺参的请求直接交给大模型“猜”。
        validation_error = self._validate_extracted_params(state)
        if validation_error:
            result = await self._build_final_summary_result(
                state=state,
                raw_result={"status": "validation_error", "message": validation_error},
                config=config,
            )
            logger.info("%s event=node_exit trace=%s display_name=%s messages=%s",
                log_title("执行", "节点出参", node_name=node_name), build_trace_context(config, node_name=node_name, event_name="node_exit"), self.display_name, format_messages_for_log(result["messages"], self._settings),)
            return result

        try:
            # 这里把“准备上下文”、“创建 Agent”、“执行 Agent”明确拆开，
            # 目的是让每一步职责稳定，后续子类要覆写某一步时不必复制整段流程。
            execution_context = await self._prepare_execution(state, config=config)
            specialist_agent = self._create_specialist_agent(execution_context)
            raw_result = await self._run_deep_agent(specialist_agent, state, execution_context, config=config)
            result = await self._build_final_summary_result(state=state, raw_result=raw_result, config=config)
            logger.info("%s event=node_exit trace=%s display_name=%s messages=%s",
                log_title("执行", "节点出参", node_name=node_name), build_trace_context(config, node_name=node_name, event_name="node_exit"), self.display_name, format_messages_for_log(result.get("messages", []), self._settings),)
            return result
        except Exception as exc:  # noqa: BLE001
            logger.exception("%s event=node_error trace=%s %s 执行失败。",
                log_title("执行", "节点异常", node_name=node_name), build_trace_context(config, node_name=node_name, event_name="node_error"), self.display_name,)
            result = await self._build_final_summary_result(
                state=state,
                raw_result={"status": "exception", "message": self._build_unhandled_exception_message(exc)},
                config=config,
            )
            logger.info("%s event=node_exit trace=%s display_name=%s messages=%s",
                log_title("执行", "节点出参", node_name=node_name), build_trace_context(config, node_name=node_name, event_name="node_exit"), self.display_name, format_messages_for_log(result["messages"], self._settings),)
            return result

    def _validate_extracted_params(self, state: WorkflowState) -> str | None:
        """在真实执行前校验关键信息。"""

        return None

    async def _prepare_execution(
        self,
        state: WorkflowState,
        config: RunnableConfig | None = None,
    ) -> SpecialistExecutionContext:
        """准备单次执行所需的 prompt、工具和 workspace。

        这个方法的目的，是把所有“会影响一次执行结果的外部依赖”提前固化下来，
        这样真正进入模型调用阶段时，数据来源已经稳定，排查问题也更聚焦。
        """

        # TODO(重点流程): 这里先取出当前 Specialist 的静态配置，后续工具白名单、
        # prompt 结构和项目规范加载策略都以它为准。
        runtime_config = self._get_runtime_config()
        node_name = f"{self.agent_type}_node"
        trace_context = build_trace_context(config, node_name=node_name, event_name="specialist_context")
        if not any(section.strip() for section in runtime_config.system_prompt_parts):
            raise RuntimeError(f"{self.display_name} 缺少 system prompt 配置，无法创建 Deep Agent。")

        # 先确定工作目录，再按目录维度请求工具，是为了让 MCP server 能拿到正确的项目上下文。
        workspace_dir = await asyncio.to_thread(self._resolve_workspace_dir, state)
        # TODO(重点流程): 这里真正向 MCP 管理器申请当前 Specialist 可见的工具集合，
        # 工具白名单是否合理会直接决定模型后续能做什么、不能做什么。
        tools = await self._mcp_manager.get_tools(
            PLAYWRIGHT_TEST_MCP_SERVER_NAME,
            workspace_dir=workspace_dir,
            allowed_tool_ids=runtime_config.allowed_playwright_test_mcp_tools,
        )
        # system prompt 放在工具之后再组装，是为了把 workspace / extracted_params 等运行时上下文
        # 一次性拼进去，避免 prompt 和实际执行环境脱节。
        system_prompt = await asyncio.to_thread(self._compose_system_prompt, state=state, workspace_dir=workspace_dir, runtime_config=runtime_config)

        allowed_tool_names = sorted(tool.name for tool in tools)
        logger.info("%s event=specialist_context trace=%s display_name=%s workspace_dir=%s allowed_tool_names=%s",
            log_title("初始化", "DeepAgent", node_name=node_name), trace_context, self.display_name, workspace_dir, allowed_tool_names,)
        debug_payload: dict[str, Any] = {
            "display_name": self.display_name,
            "workspace_dir": str(workspace_dir) if workspace_dir is not None else None,
            "allowed_tool_ids": list(runtime_config.allowed_playwright_test_mcp_tools),
            "loaded_tools": serialize_tools_for_log(tools, max_text_length=debug_max_chars(self._settings)),
            "system_prompt_length": len(system_prompt),
        }
        if debug_full_messages_enabled(self._settings):
            debug_payload["system_prompt"] = system_prompt

        log_debug_event(logger, self._settings, log_title("初始化", "DeepAgent"), "specialist_context", trace_context, **debug_payload)
        return SpecialistExecutionContext(
            workspace_dir=workspace_dir,
            system_prompt=system_prompt,
            tools=tools,
            trace_context=trace_context,
        )

    def _create_specialist_agent(self, execution_context: SpecialistExecutionContext) -> Any:
        """创建单次执行使用的 Deep Agent。

        把 Agent 创建单独收敛成一个方法，是为了让子类在需要更换 middleware、
        memory 或执行模式时，只改这一处，不影响前后的上下文准备和结果提取逻辑。
        """

        model_kwargs = self._settings.build_model_kwargs(self._settings.specialist_model)
        model = init_chat_model(**model_kwargs)
        logger.info("%s %s 模型初始化完成 model_kwargs=%s",
            log_title("初始化", "模型初始化", node_name=f"{self.agent_type}_node"), self.display_name, summarize_model_kwargs(model_kwargs),)

        backend = self._build_deep_agent_backend(execution_context.workspace_dir)
        permissions = self._build_deep_agent_permissions(execution_context.workspace_dir)

        # TODO(重点流程): 这里完成 Deep Agent 实例化，后续所有工具调用和模型推理
        # 都会沿着这个 agent 的编排能力执行。
        # `create_deep_agent` 会在这里把我们传入的 system prompt 前置，再自动追加
        # Deep Agents 自带的 BASE_AGENT_PROMPT，并注入内置工具（如 `read_file` / `ls`）。
        # 因此这里还要显式绑定真实 workspace backend，避免内置文件工具只看到空的 StateBackend。
        return create_deep_agent(
            model=model,
            tools=execution_context.tools,
            system_prompt=execution_context.system_prompt,
            backend=backend,
            permissions=permissions,
            name=f"{self.agent_type}-specialist",
        )

    def _build_deep_agent_backend(self, workspace_dir: Path | None) -> FilesystemBackend | None:
        """为 Deep Agent 的内置文件工具绑定真实 workspace。"""

        if workspace_dir is None:
            return None

        return FilesystemBackend(root_dir=str(workspace_dir), virtual_mode=False)

    def _build_deep_agent_permissions(self, workspace_dir: Path | None) -> list[FilesystemPermission] | None:
        """约束 Deep Agent 内置文件工具只读当前项目目录。"""

        if workspace_dir is None:
            return None

        return self._build_workspace_permissions(workspace_dir, allow_workspace_writes=False)

    async def _run_deep_agent(
        self,
        specialist_agent: Any,
        state: WorkflowState,
        execution_context: SpecialistExecutionContext,
        config: RunnableConfig | None = None,
    ) -> WorkflowState:
        """默认使用 `ainvoke` 执行 Specialist。

        默认实现只关心“把现有消息交给 Agent，再截取新增消息返回”，目的是让大多数
        Specialist 直接复用；只有像 Plan 这种需要事件流和强约束收尾的场景才需要覆写。
        """

        existing_messages = state.get("messages", [])
        # TODO(重点流程): 这里开始真正调用 Specialist 背后的大模型/工具编排链路。
        result = await specialist_agent.ainvoke(
            {"messages": existing_messages},
            config=with_trace_context(
                config,
                execution_context.trace_context,
                recursion_limit=self._settings.specialist_recursion_limit,
            ),
        )
        return {"messages": self._extract_new_messages(result, len(existing_messages))}

    def _resolve_workspace_dir(self, state: WorkflowState) -> Path | None:
        """根据当前状态决定 Specialist 的工作目录。

        基类默认只解析用户显式传入的 `project_dir`，目的是为子类保留覆写空间；
        例如 Plan 可以在未传目录时自动创建目录，而其他 Specialist 可以继续保持保守策略。
        """

        raw_project_dir = state.get("extracted_params", {}).get("project_dir")
        if not raw_project_dir:
            return None

        # 统一在这里做 `expanduser + resolve`，是为了让后续 MCP 和文件读取逻辑始终面对同一种绝对路径。
        project_dir = Path(str(raw_project_dir)).expanduser()
        return project_dir.resolve()

    def _get_runtime_config(self) -> SpecialistRuntimeConfig:
        """返回当前 Specialist 的运行时配置。"""

        return self.runtime_config

    def _compose_system_prompt(
        self,
        *,
        state: WorkflowState,
        workspace_dir: Path | None,
        runtime_config: SpecialistRuntimeConfig,
    ) -> str:
        """拼装单次运行使用的完整 system prompt。

        这里采用分段拼装，而不是在子类里手写长字符串，目的是让“静态 system prompt 片段、
        项目规范、运行时上下文”三类信息来源清晰分层，后续新增一段上下文时也更容易定位。
        """

        prompt_sections = [section.strip() for section in runtime_config.system_prompt_parts if section.strip()]

        # 项目规范是可选层：只有当前 Specialist 明确声明要加载，并且 workspace 下真的存在规范文件时才追加。
        project_standard_prompt = self._load_project_standard_prompt(workspace_dir, runtime_config)
        if project_standard_prompt:
            prompt_sections.append(project_standard_prompt)

        query_guard_prompt = self._build_query_guard_prompt(runtime_config)
        if query_guard_prompt:
            prompt_sections.append(query_guard_prompt)

        # 运行时上下文放在最后追加，是为了保证前面的角色与规范先稳定，再补充本次调用的动态参数。
        runtime_context_prompt = self._build_runtime_context_prompt(state=state, workspace_dir=workspace_dir)
        if runtime_context_prompt:
            prompt_sections.append(runtime_context_prompt)

        return "\n\n".join(section for section in prompt_sections if section.strip())

    def _load_project_standard_prompt(
        self,
        workspace_dir: Path | None,
        runtime_config: SpecialistRuntimeConfig,
    ) -> str:
        """按配置读取项目规范文件。

        这一步的目的，是让模型在通用规范之外还能感知项目自己的落地约束，例如目录结构、
        文件命名或测试资产保存规则。
        """

        if not runtime_config.load_project_standard or workspace_dir is None:
            return ""

        # 规范文件名交给运行时配置控制，而不是写死在子类里，目的是让不同 Specialist 或项目约定可平滑调整。
        standard_file = workspace_dir / runtime_config.project_standard_file_name
        if not standard_file.is_file():
            return ""

        return standard_file.read_text(encoding="utf-8").strip()

    def _build_runtime_context_prompt(self, *, state: WorkflowState, workspace_dir: Path | None) -> str:
        """构建与单次运行相关的额外上下文。

        这部分上下文承载的是“本次调用才知道的动态信息”，目的是让同一份基础 prompt
        可以复用于不同请求，而不把瞬时参数硬编码进静态 prompt 模板。
        """

        prompt_lines: list[str] = []
        extracted_params = state.get("extracted_params", {})

        # workspace 信息优先放进去，是为了让模型在生成相对路径、保存产物时先建立目录感知。
        if workspace_dir is not None:
            prompt_lines.append(f"- workspace_dir: `{workspace_dir}`")

        # extracted_params 统一展开成 key-value 文本，目的是保持 prompt 可读，同时避免为每个字段单独维护模板。
        for key, value in extracted_params.items():
            prompt_lines.append(f"- {key}: {self._format_prompt_value(value)}")

        if not prompt_lines:
            return ""

        return "## 本次运行上下文\n" + "\n".join(prompt_lines)

    def _build_workspace_permissions(
        self,
        workspace_dir: Path,
        *,
        allow_workspace_writes: bool,
    ) -> list[FilesystemPermission]:
        """根据当前 Specialist 配置构建 workspace 级别的文件权限。"""

        workspace_path = workspace_dir.resolve().as_posix()
        permissions: list[FilesystemPermission] = []
        denied_read_paths = self._build_query_filter_read_paths(
            workspace_dir=workspace_dir,
            query_filter_config=self._get_runtime_config().query_filter_config,
        )
        for denied_path in denied_read_paths:
            permissions.append(FilesystemPermission(operations=["read"], paths=[denied_path], mode="deny"))

        permissions.append(
            FilesystemPermission(
                operations=["read"],
                paths=[workspace_path, f"{workspace_path}/**"],
                mode="allow",
            )
        )
        permissions.append(FilesystemPermission(operations=["read"], paths=["/**"], mode="deny"))

        if allow_workspace_writes:
            permissions.append(
                FilesystemPermission(
                    operations=["write"],
                    paths=[workspace_path, f"{workspace_path}/**"],
                    mode="allow",
                )
            )
        permissions.append(FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"))
        return permissions

    def _build_query_filter_read_paths(
        self,
        *,
        workspace_dir: Path,
        query_filter_config: SpecialistFileFilter,
    ) -> list[str]:
        """把查询过滤配置展开成 workspace 作用域下的绝对 deny 路径列表。"""

        blocked_paths: list[str] = []
        for pattern in query_filter_config.blocked_path_globs:
            blocked_paths.append(self._resolve_workspace_query_glob(workspace_dir, pattern))

        for extension in query_filter_config.blocked_file_extensions:
            normalized_extension = extension if extension.startswith(".") else f".{extension}"
            blocked_paths.append(self._resolve_workspace_query_glob(workspace_dir, f"*{normalized_extension}"))
            blocked_paths.append(self._resolve_workspace_query_glob(workspace_dir, f"**/*{normalized_extension}"))

        deduplicated_paths: list[str] = []
        seen: set[str] = set()
        for path in blocked_paths:
            if path in seen:
                continue
            seen.add(path)
            deduplicated_paths.append(path)
        return deduplicated_paths

    def _resolve_workspace_query_glob(self, workspace_dir: Path, pattern: str) -> str:
        """把相对 workspace 的查询 glob 转成绝对权限路径。"""

        normalized_pattern = pattern.strip().replace("\\", "/")
        if not normalized_pattern:
            raise ValueError("查询过滤规则不允许空路径模式。")
        if normalized_pattern.startswith("/"):
            return normalized_pattern

        normalized_pattern = normalized_pattern[2:] if normalized_pattern.startswith("./") else normalized_pattern
        workspace_path = workspace_dir.resolve().as_posix()
        if normalized_pattern in {".", ""}:
            return workspace_path
        return f"{workspace_path}/{normalized_pattern}"

    def _build_query_guard_prompt(self, runtime_config: SpecialistRuntimeConfig) -> str:
        """构建所有 Specialist 共用的文件查询约束提示词。"""

        guidance_lines = [
            "- 如果需要查询文件，先使用 `ls` 观察候选目录结构，再把范围缩小到最小必要的单个子目录或单个文件。",
            "- 不要直接对 `project_dir`、`workspace_dir` 或其他大目录执行 `glob=\"**/*\"`、递归 `grep` 或无范围全量搜索。",
            "- `grep` 首次检索优先使用默认的 `files_with_matches`；只有缩小到少量候选文件后，才使用 `output_mode=\"content\"` 查看正文。",
        ]
        query_filter_config = runtime_config.query_filter_config
        if query_filter_config.blocked_path_globs:
            blocked_paths = ", ".join(f"`{pattern}`" for pattern in query_filter_config.blocked_path_globs)
            guidance_lines.append(f"- 禁止查询这些路径模式：{blocked_paths}")
        if query_filter_config.blocked_file_extensions:
            blocked_types = ", ".join(f"`{suffix}`" for suffix in query_filter_config.blocked_file_extensions)
            guidance_lines.append(f"- 禁止查询这些文件类型：{blocked_types}")
        return "## 文件查询约束\n" + "\n".join(guidance_lines)

    def _extract_new_messages(self, result: dict[str, Any], existing_message_count: int) -> list[Any]:
        """从 Agent 输出中截取新增消息。

        这里只返回新增消息，目的是让 LangGraph 合并状态时保持增量语义，避免把历史消息重复写回，
        进而造成状态膨胀和后续节点误判。
        """

        all_messages = result.get("messages", [])
        if not isinstance(all_messages, list):
            raise RuntimeError(f"{self.display_name} 返回的 messages 结构非法。")

        # 这里按调用前的消息数量截断，是因为 Deep Agent 返回的是“完整消息历史”，
        # 而工作流节点真正需要写回的只有这次新增的部分。
        new_messages = all_messages[existing_message_count:]
        if not new_messages:
            raise RuntimeError(f"{self.display_name} 未返回新的消息结果。")

        return new_messages

    async def _build_final_summary_result(
        self,
        *,
        state: WorkflowState,
        raw_result: dict[str, Any],
        config: RunnableConfig | None = None,
    ) -> WorkflowState:
        """把 Specialist 原始结果整理成统一的结构化阶段结果。"""

        stage_status = self._resolve_stage_status(raw_result)
        artifact = self._extract_stage_artifact(raw_result)
        fallback_message = self._fallback_final_summary(raw_result)
        stage_summary = build_stage_summary(
            stage=self.agent_type,
            status=stage_status,
            artifact=artifact,
            fallback_message=fallback_message,
        )
        artifact_history, latest_artifacts, current_turn_artifact_ids = append_artifact_history(dict(state), artifact)
        pending_stage_summaries = append_stage_summary(dict(state), stage_summary)
        result: WorkflowState = {
            "stage_result": self._build_stage_result(raw_result, stage_status=stage_status, artifact=artifact, stage_summary=stage_summary),
            "final_summary": stage_summary["text"],
            "artifact_history": artifact_history,
            "latest_artifacts": latest_artifacts,
            "current_turn_artifact_ids": current_turn_artifact_ids,
            "pending_stage_summaries": pending_stage_summaries,
            "pipeline_handoff": True,
            "return_to_master": False,
            "missing_params": [],
            "pending_missing_params": [],
        }
        display_messages = [
            *extract_missing_display_messages(dict(state)),
            *normalize_display_delta(raw_result.get("messages", [])),
        ]
        if display_messages:
            result["display_messages"] = display_messages
        if self._workflow_managed_pipeline(state):
            result["messages"] = []
        else:
            final_message = build_display_summary_message(
                stage_summary["text"],
                prefix=f"{self.agent_type}-summary",
            )
            result["messages"] = [final_message]
            result["display_messages"] = [*display_messages, final_message]
        return result

    def _resolve_stage_status(self, raw_result: dict[str, Any]) -> str:
        """解析当前阶段状态，默认把无显式错误视为成功。"""

        status = raw_result.get("status")
        if isinstance(status, str) and status:
            return status
        return "success"

    def _extract_stage_artifact(self, raw_result: dict[str, Any]) -> dict[str, Any] | None:
        """从阶段原始结果中取出结构化产物。"""

        artifact = raw_result.get("artifact")
        if isinstance(artifact, dict):
            return artifact
        return None

    def _workflow_managed_pipeline(self, state: WorkflowState) -> bool:
        """判断当前阶段是否由统一工作流 finalizer 管理用户回复。"""

        requested_pipeline = state.get("requested_pipeline")
        return isinstance(requested_pipeline, list) and bool(requested_pipeline)

    def _fallback_final_summary(self, raw_result: dict[str, Any]) -> str:
        """总结模型不可用时，退回到阶段结果中最接近最终回复的文本。"""

        status = raw_result.get("status")
        message = raw_result.get("message")
        if status and status != "success" and message:
            return str(message)

        messages = raw_result.get("messages", [])
        if isinstance(messages, list):
            for message in reversed(messages):
                if isinstance(message, BaseMessage):
                    text = self._message_to_text(message).strip()
                    if text:
                        return text

        if message:
            return str(message)

        status = raw_result.get("status")
        if status:
            return str(status)

        return "阶段已结束，但总结模型暂时不可用，未能生成最终总结。"

    def _build_runtime_exception_result(
        self,
        *,
        collector: Any,
        existing_messages: Sequence[Any],
        exc: Exception,
    ) -> WorkflowState:
        """保留已流出的运行时消息，同时把当前阶段标记为异常。"""

        message = self._build_unhandled_exception_message(exc)
        result: WorkflowState = build_runtime_message_result(
            collector=collector,
            existing_messages=existing_messages,
            fallback_message=message,
        )
        result["status"] = "exception"
        result["message"] = message
        return result

    def _build_stage_result(
        self,
        raw_result: dict[str, Any],
        *,
        stage_status: str,
        artifact: dict[str, Any] | None,
        stage_summary: dict[str, Any],
    ) -> dict[str, Any]:
        """构造写入 state 的内部阶段结果，不直接暴露给用户。"""

        return {
            "agent_type": self.agent_type,
            "display_name": self.display_name,
            "status": stage_status,
            "artifact": artifact,
            "stage_summary": stage_summary,
            "raw_messages": [self._message_to_text(message) for message in raw_result.get("messages", []) if isinstance(message, BaseMessage)],
            "raw_result": {
                key: value
                for key, value in raw_result.items()
                if key != "messages"
            },
        }

    def _format_stage_result_for_prompt(self, raw_result: dict[str, Any]) -> str:
        """把阶段原始结果压成总结提示词文本。"""

        lines: list[str] = []
        for key, value in raw_result.items():
            if key == "messages" and isinstance(value, list):
                message_lines = [f"{message.__class__.__name__}: {self._message_to_text(message)}" for message in value if isinstance(message, BaseMessage)]
                lines.append(f"messages:\n" + "\n".join(message_lines))
                continue
            lines.append(f"{key}: {value}")
        text = "\n".join(lines)
        max_chars = debug_max_chars(self._settings)
        if len(text) <= max_chars:
            return text
        return f"{text[:max_chars]}... [truncated]"

    def _latest_human_message_text(self, messages: Sequence[Any]) -> str:
        """返回最近一条用户消息文本。"""

        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                return self._message_to_text(message)
        return ""

    def _message_to_text(self, message: BaseMessage) -> str:
        """把消息内容转换成字符串。"""

        content = message.content
        return content if isinstance(content, str) else str(content)

    def log_get_logger(self) -> logging.Logger:
        """返回当前 Agent 模块对应的日志对象。"""

        return get_logger(type(self).__module__)

    def log_stream_event(self, event: dict[str, Any], trace_context: dict[str, Any] | None = None) -> None:
        """打印 Specialist 事件流中的关键模型与工具事件。"""

        event_name = event.get("event", "")
        name = event.get("name", "")
        base_trace_context = trace_context or {}
        node_name = base_trace_context.get("node_name") or f"{self.agent_type}_node"
        agent_logger = self.log_get_logger()

        if event_name == "on_chat_model_start":
            agent_logger.info("%s event=model_start trace=%s name=%s input=%s",
                log_title("执行", "事件流", node_name=node_name), self.log_event_trace_context(base_trace_context, "model_start"), name, format_value_for_log(event.get("data", {}).get("input"), self._settings),)
            return

        if event_name == "on_chat_model_end":
            agent_logger.info("%s event=model_end trace=%s name=%s output=%s",
                log_title("执行", "事件流", node_name=node_name), self.log_event_trace_context(base_trace_context, "model_end"), name, format_value_for_log(event.get("data", {}).get("output"), self._settings),)
            return

        if event_name == "on_tool_start":
            agent_logger.info("%s event=tool_start trace=%s name=%s input=%s",
                log_title("执行", "事件流", node_name=node_name), self.log_event_trace_context(base_trace_context, "tool_start"), name, format_value_for_log(event.get("data", {}).get("input"), self._settings),)
            return

        if event_name == "on_tool_end":
            agent_logger.info("%s event=tool_end trace=%s name=%s output=%s",
                log_title("执行", "事件流", node_name=node_name), self.log_event_trace_context(base_trace_context, "tool_end"), name, format_value_for_log(event.get("data", {}).get("output"), self._settings),)
            return

        if event_name == "on_tool_error":
            agent_logger.warning("%s event=tool_error trace=%s name=%s error=%s",
                log_title("执行", "事件流", node_name=node_name), self.log_event_trace_context(base_trace_context, "tool_error"), name, format_value_for_log(event.get("data", {}).get("error"), self._settings),)
            return

        if event_name == "on_chain_end" and not event.get("parent_ids"):
            agent_logger.info("%s event=deep_agent_end trace=%s name=%s output=%s",
                log_title("执行", "事件流", node_name=node_name), self.log_event_trace_context(base_trace_context, "deep_agent_end"), name, format_value_for_log(event.get("data", {}).get("output"), self._settings),)

    def log_browser_close_expected(self, trace_context: dict[str, Any], exc: Exception) -> None:
        """记录浏览器在收尾阶段按预期关闭的异常。"""

        node_name = trace_context.get("node_name") or f"{self.agent_type}_node"
        self.log_get_logger().info("%s event=browser_close_expected trace=%s error=%s",
            log_title("执行", "事件流", node_name=node_name), self.log_event_trace_context(trace_context, "browser_close_expected"), self.log_truncate(str(exc)),)

    def log_tool_state(
        self,
        *,
        trace_context: dict[str, Any],
        event_name: str,
        status: str,
        error: str | None,
    ) -> None:
        """记录关键工具执行状态，便于按 session grep。"""

        node_name = trace_context.get("node_name") or f"{self.agent_type}_node"
        self.log_get_logger().info("%s event=%s trace=%s status=%s error=%s",
            log_title("执行", "事件流", node_name=node_name), event_name, self.log_event_trace_context(trace_context, event_name), status, error,)

    def log_event_trace_context(self, trace_context: dict[str, Any], event_name: str) -> dict[str, Any]:
        """复用节点 trace 标识，只替换当前日志事件名。"""

        event_trace_context = dict(trace_context)
        event_trace_context["event_name"] = event_name
        return event_trace_context

    def log_truncate(self, value: Any, max_length: int | None = None) -> str:
        """压缩日志输出长度。"""

        resolved_max_length = max_length if max_length is not None else debug_max_chars(self._settings)
        text = value if isinstance(value, str) else repr(value)
        if len(text) <= resolved_max_length:
            return text
        return f"{text[:resolved_max_length]}..."

    def _build_unhandled_exception_message(self, exc: Exception) -> str:
        """把漏网异常压缩成一条用户可读、不会打爆 graph 的消息。"""

        error_message = str(exc).strip() or exc.__class__.__name__
        if len(error_message) > 1200:
            error_message = f"{error_message[:1200]}... [truncated]"
        return (
            f"{self.display_name} 执行过程中遇到未处理异常，已停止当前阶段但不会中断整个工作流。"
            f"此前已完成的操作历史仍然保留。"
            f"错误类型：`{exc.__class__.__name__}`。"
            f"错误信息：{error_message}"
        )

    def _format_prompt_value(self, value: Any) -> str:
        """把运行时参数格式化成适合拼接进 prompt 的文本。"""

        if isinstance(value, list):
            if not value:
                return "[]"
            # 列表在 prompt 里统一转成扁平字符串，目的是减少模型对 Python 原始结构的依赖。
            return ", ".join(str(item) for item in value)

        return str(value)

"""所有 Agent 的抽象基类与 Specialist 公共逻辑。

本文件的目标不是单纯“放一个父类”，而是把 Specialist 共享的执行骨架收敛到一处，
让 Plan / Generator / Healer 只保留各自真正不同的业务规则，避免重复维护
workspace 解析、MCP 工具准备、prompt 拼装和 Deep Agent 调用细节。
"""

from __future__ import annotations
import asyncio
from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from deepagents.middleware import FilesystemPermission
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableConfig
from deep_agent.core.config import AppSettings
from deep_agent.model import adapt_chat_model, resolve_model_capabilities, validate_tool_set
from deep_agent.core.cancellation import is_langgraph_user_cancellation
from deep_agent.helpers.artifacts import (
    append_artifact_history,
    append_stage_summary,
    build_stage_summary,
)
from deep_agent.core.display_message import (
    build_display_summary_message,
    emit_display_message_delta,
    extract_missing_display_messages,
    normalize_display_delta,
    sanitize_display_messages,
)
from deep_agent.core.runtime_logging import (
    build_trace_context,
    debug_full_messages_enabled,
    debug_max_chars,
    format_messages_for_log,
    format_state_for_log,
    get_logger,
    log_debug_event,
    log_title,
    serialize_tools_for_log,
    summarize_model_kwargs,
    with_trace_context,
)
from deep_agent.agent.state import WorkflowState
from deep_agent.helpers.specialist_helpers import (
    SpecialistDisplayMixin,
    SpecialistExecutionContext,
    SpecialistLoggingMixin,
    SpecialistRuntimeConfig,
    SpecialistWorkspaceMixin,
    is_windows_platform,
)
from deep_agent.tools import MCPToolsManager, get_mcp_tools_manager
from deep_agent.tools.playwright import PLAYWRIGHT_TEST_MCP_SERVER_NAME


logger = get_logger(__name__)


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


class BaseSpecialistAgent(
    SpecialistDisplayMixin,
    SpecialistWorkspaceMixin,
    SpecialistLoggingMixin,
    BaseAgent,
    ABC,
):
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

        stage_start_message: AIMessage | None = None
        try:
            # 这里把“准备上下文”、“创建 Agent”、“执行 Agent”明确拆开，
            # 目的是让每一步职责稳定，后续子类要覆写某一步时不必复制整段流程。
            execution_context = await self._prepare_execution(state, config=config)
            stage_start_message = self._build_stage_start_display_message(
                state=state,
                execution_context=execution_context,
            )
            emit_display_message_delta([stage_start_message])
            specialist_agent = self._create_specialist_agent(execution_context)
            raw_result = await self._run_deep_agent(specialist_agent, state, execution_context, config=config)
            result = await self._build_final_summary_result(
                state=state,
                raw_result=raw_result,
                config=config,
                preface_messages=[stage_start_message],
            )
            logger.info("%s event=node_exit trace=%s display_name=%s messages=%s",
                log_title("执行", "节点出参", node_name=node_name), build_trace_context(config, node_name=node_name, event_name="node_exit"), self.display_name, format_messages_for_log(result.get("messages", []), self._settings),)
            return result
        except Exception as exc:  # noqa: BLE001
            if is_langgraph_user_cancellation(exc):
                raise
            logger.exception("%s event=node_error trace=%s %s 执行失败。",
                log_title("执行", "节点异常", node_name=node_name), build_trace_context(config, node_name=node_name, event_name="node_error"), self.display_name,)
            result = await self._build_final_summary_result(
                state=state,
                raw_result={"status": "exception", "message": self._build_unhandled_exception_message(exc)},
                config=config,
                preface_messages=[stage_start_message] if stage_start_message is not None else (),
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

        # 主链路：这里先取出当前 Specialist 的静态配置，后续工具白名单、
        # prompt 结构和项目规范加载策略都以它为准。
        runtime_config = self._get_runtime_config()
        node_name = f"{self.agent_type}_node"
        trace_context = build_trace_context(config, node_name=node_name, event_name="specialist_context")
        if not any(section.strip() for section in runtime_config.system_prompt_parts):
            raise RuntimeError(f"{self.display_name} 缺少 system prompt 配置，无法创建 Deep Agent。")

        # 先确定工作目录，再按目录维度请求工具，是为了让 MCP server 能拿到正确的项目上下文。
        workspace_dir = await asyncio.to_thread(self._resolve_workspace_dir, state)
        # 主链路：这里真正向 MCP 管理器申请当前 Specialist 可见的工具集合，
        # 工具白名单是否合理会直接决定模型后续能做什么、不能做什么。
        tools = await self._mcp_manager.get_tools(
            PLAYWRIGHT_TEST_MCP_SERVER_NAME,
            workspace_dir=workspace_dir,
            allowed_tool_ids=runtime_config.allowed_playwright_test_mcp_tools,
        )
        if self._settings.model_adapter_v2_enabled:
            model_connection = self._settings.resolve_model_connection("specialist")
            model_capabilities = resolve_model_capabilities(model_connection)
            tool_diagnostics = validate_tool_set(
                tools,
                model_capabilities,
                model_connection,
            )
            model_family = model_connection.family
            model_channel = model_connection.channel
        else:
            tool_diagnostics = None
            model_family = "legacy"
            model_channel = "legacy"
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
            "model_family": model_family,
            "model_channel": model_channel,
            "tool_count": tool_diagnostics.count if tool_diagnostics is not None else len(tools),
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

        # 主链路：这里开始初始化当前 Specialist 的模型实例；后续写用例、写脚本、
        # 调试修复等阶段都会基于这一个模型对象继续进入 Deep Agent 编排。
        model_kwargs = self._settings.build_model_kwargs(role="specialist")
        raw_model = init_chat_model(**model_kwargs)
        if self._settings.model_adapter_v2_enabled:
            model_connection = self._settings.resolve_model_connection("specialist")
            model_capabilities = resolve_model_capabilities(model_connection)
            model = adapt_chat_model(
                raw_model,
                connection=model_connection,
                capabilities=model_capabilities,
            )
        else:
            model = raw_model
        logger.info("%s %s 模型初始化完成 model_kwargs=%s",
            log_title("初始化", "模型初始化", node_name=f"{self.agent_type}_node"), self.display_name, summarize_model_kwargs(model_kwargs),)

        backend = self._build_deep_agent_backend(execution_context.workspace_dir)
        permissions = self._build_deep_agent_permissions(execution_context.workspace_dir)

        # 主链路：这里完成 Deep Agent 实例化，后续所有工具调用和模型推理
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
        """为 Deep Agent 的内置文件工具绑定真实 workspace。

        按平台区分 `virtual_mode`：
        - Windows：启用 `virtual_mode=True`。LLM 在虚拟路径命名空间里写 `/foo.md`，
          backend 自动映射到 `root_dir/foo.md`。这样才能绕开 deepagents 的
          `FilesystemPermission.__post_init__` 对 Windows 绝对路径的字符串校验。
        - mac / Linux：保持原行为 `virtual_mode=False`，真实绝对路径直接落盘，
          不改变既有运行时表现，避免对 mac 用户引入不必要的回归风险。
        """

        if workspace_dir is None:
            return None

        return FilesystemBackend(
            root_dir=str(workspace_dir),
            virtual_mode=is_windows_platform(),
        )

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
        # 主链路：这里开始真正调用 Specialist 背后的大模型/工具编排链路。
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

    async def _close_playwright_mcp_session(
        self,
        *,
        workspace_dir: Path | None,
        trace_context: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> None:
        """在阶段结束时关闭当前 workspace 对应的 Playwright MCP 会话。

        调用方：Plan / Generator / Healer 的 runtime helper 在每条收尾路径上调用，
        无论阶段成功、异常退出还是产物校验失败，都会尝试关闭该会话。
        目的：避免 Playwright MCP 子进程（Chromium）在阶段结束后驻留，释放端口、
        浏览器上下文和内存；提示词也要求模型主动关闭，这里是兜底机制，任一路径都能释放。
        """

        node_name = f"{self.agent_type}_node"
        resolved_trace = trace_context or build_trace_context(
            None,
            node_name=node_name,
            event_name="playwright_mcp_close",
        )
        try:
            closed = await self._mcp_manager.close_session(
                PLAYWRIGHT_TEST_MCP_SERVER_NAME,
                workspace_dir=workspace_dir,
            )
        except Exception:  # noqa: BLE001
            # 关闭失败不应影响阶段已经确认的最终结果；只记录日志方便线上排查。
            logger.exception(
                "%s event=playwright_mcp_close_failed trace=%s workspace_dir=%s reason=%s",
                log_title("关闭", "MCP关闭", node_name=node_name),
                resolved_trace,
                workspace_dir,
                reason,
            )
            return

        logger.info(
            "%s event=playwright_mcp_close trace=%s workspace_dir=%s closed=%s reason=%s",
            log_title("关闭", "MCP关闭", node_name=node_name),
            resolved_trace,
            workspace_dir,
            closed,
            reason,
        )

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

    async def _build_final_summary_result(
        self,
        *,
        state: WorkflowState,
        raw_result: dict[str, Any],
        config: RunnableConfig | None = None,
        preface_messages: Sequence[BaseMessage] = (),
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
        stage_summary_message = build_display_summary_message(
            stage_summary["text"],
            prefix=f"{self.agent_type}-summary",
        )
        display_messages = sanitize_display_messages(
            [
                *extract_missing_display_messages(dict(state)),
                *normalize_display_delta(preface_messages),
                *normalize_display_delta(raw_result.get("messages", [])),
                stage_summary_message,
            ]
        )
        if display_messages:
            result["display_messages"] = display_messages
        if self._workflow_managed_pipeline(state):
            result["messages"] = []
        else:
            result["messages"] = [stage_summary_message]
            result["display_messages"] = display_messages
        return result

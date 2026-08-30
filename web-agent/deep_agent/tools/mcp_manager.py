"""统一管理所有 MCP server 的持久会话和工具缓存。

这个模块的核心目的，是把"如何连接 MCP、如何按 workspace 复用会话、如何把工具定义转换成
LangChain Tool"这些底层细节收口，避免上层 Agent 自己管理连接生命周期和缓存一致性。
本模块只负责通用编排；工具级别的业务规则（例如 Playwright 的 `planner_save_plan` 路径
校验和缺父目录重试）由具体 provider 的 `post_process_tool` 钩子承担。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from langchain_core.tools import BaseTool, ToolException
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool
from pydantic import ValidationError
from pydantic.v1 import ValidationError as ValidationErrorV1

from deep_agent.core.cancellation import is_langgraph_user_cancellation
from deep_agent.core.config import AppSettings
from deep_agent.core.runtime_logging import get_logger, log_title, summarize_settings
from deep_agent.tools.tool_error_handling import (
    DEFAULT_MCP_TOOL_ERROR_POLICY,
    MCPToolErrorPolicy,
    build_structured_tool_error,
    normalize_tool_error_message,
)


logger = get_logger(__name__)


class MCPServerProvider(Protocol):
    """描述单个 MCP server 的专属接入规则。

    把 server 差异抽成 provider 协议的目的，是让 `MCPToolsManager` 只负责统一编排，
    而把"路径归一化、连接参数构造、错误包装、工具级业务规则"交给各个 server 自己定义。
    """

    server_name: str
    tool_error_policy: MCPToolErrorPolicy | None

    def normalize_workspace_dir(self, workspace_dir: str | Path | None) -> str | None:
        """归一化当前 server 使用的工作目录。"""

    def build_connection_config(
        self,
        settings: AppSettings,
        workspace_dir: str | None,
    ) -> dict[str, object]:
        """构建当前 server 的连接配置。"""

    def build_connection_error(
        self,
        exc: Exception,
        *,
        workspace_dir: str | None,
    ) -> RuntimeError:
        """构建当前 server 的连接失败异常。"""

    def post_process_tool(
        self,
        tool: BaseTool,
        *,
        workspace_dir: str | None,
    ) -> BaseTool:
        """对转换后的 LangChain Tool 做 provider 专属的二次包装，可选实现。"""


@dataclass(slots=True)
class _CachedToolsSession:
    """缓存单个 server + workspace 的 MCP 会话。

    这里把 session、工具定义和已转换过的 LangChain Tool 放在一起，是为了保证同一组
    server/workspace 请求能稳定复用连接和工具对象，减少重复握手与重复转换。
    """

    client: MultiServerMCPClient
    stack: AsyncExitStack
    session: Any
    provider: MCPServerProvider
    workspace_dir: str | None
    session_id: str | None
    tool_names: tuple[str, ...]
    tool_specs_by_name: dict[str, Any]
    loaded_tools_by_name: dict[str, BaseTool] = field(default_factory=dict)


class MCPToolsManager:
    """统一维护所有 MCP server 的长连接与工具缓存。

    它存在的目的，是让上层 Agent 只表达“我要哪个 server、哪个 workspace、哪些工具”，
    而不需要关心连接建立、工具分页、缓存命中和工具对象转换这些基础设施细节。
    """

    def __init__(
        self,
        settings: AppSettings,
        providers: Sequence[MCPServerProvider] | None = None,
    ) -> None:
        """初始化 MCP 管理器。"""

        self._settings = settings
        self._providers = self._build_provider_registry(
            () if providers is None else providers
        )
        self._sessions: dict[
            tuple[str, str | None, str | None], _CachedToolsSession
        ] = {}
        self._closing_sessions: dict[
            tuple[str, str | None, str | None],
            tuple[_CachedToolsSession, asyncio.Future[bool]],
        ] = {}
        # 这个锁的目的，是避免并发请求同一个 server/workspace 时重复初始化 session，
        # 进而造成多条长连接、重复工具拉取或同一会话被并发关闭。
        self._lock = asyncio.Lock()
        logger.info(
            "%s MCPToolsManager 初始化完成 settings=%s",
            log_title("初始化", "MCP初始化"),
            summarize_settings(settings),
        )

    async def get_tools(
        self,
        server_name: str,
        workspace_dir: str | Path | None = None,
        allowed_tool_ids: Sequence[str] | None = None,
        session_id: str | None = None,
    ) -> Sequence[BaseTool]:
        """获取指定 MCP server 的工具列表。

        对外暴露这个方法的目的，是让调用方用统一入口拿到“已经可直接执行的 LangChain Tool”，
        而不是自己处理 provider、session、allowlist 和工具转换。
        """

        # 先解析 provider，再做目录归一化，目的是把不同 server 的接入差异消化在管理器内部。
        provider = self._get_provider(server_name)
        normalized_workspace = await asyncio.to_thread(
            provider.normalize_workspace_dir, workspace_dir
        )
        logger.info(
            "%s 开始获取 MCP 工具 server=%s, workspace_dir=%s, session_id=%s, allowed_tool_ids=%s",
            log_title("工具", "MCP工具"),
            server_name,
            normalized_workspace,
            session_id,
            list(allowed_tool_ids or ()),
        )
        prepare_workspace = getattr(provider, "prepare_workspace", None)
        if prepare_workspace is not None:
            await asyncio.to_thread(
                prepare_workspace, self._settings, normalized_workspace
            )

        # 会话准备和工具筛选分成两步，目的是先确保连接稳定，再按当前 Agent 的白名单裁剪可见工具。
        cached_session = await self._ensure_session(
            provider, normalized_workspace, session_id
        )
        try:
            return self._build_allowed_tools(
                cached_session,
                server_name=server_name,
                allowed_tool_ids=allowed_tool_ids,
            )
        except Exception:
            # 工具白名单校验或转换失败时，本次调用拿不到可用工具；立即释放刚申请的
            # 执行级会话，避免 prepare 阶段失败后遗留 MCP 子进程。
            await self.close_session(
                server_name,
                workspace_dir=normalized_workspace,
                session_id=session_id,
            )
            raise

    async def close(self) -> None:
        """主动关闭持有的 MCP 会话。

        显式暴露关闭能力的目的，是让进程退出或测试结束时能主动回收长连接和子进程，
        而不是依赖解释器回收时机。
        """

        async with self._lock:
            cached_sessions = list(self._sessions.items())

        cancelled_error: asyncio.CancelledError | None = None
        for cache_key, cached_session in cached_sessions:
            try:
                await self._close_cached_session(cache_key, cached_session)
            except asyncio.CancelledError as exc:
                # 先记住取消，再继续尝试关闭快照中的其余会话。被取消的会话仍留在
                # 缓存中，调用方可在后续清理阶段重试，不会丢失资源句柄。
                if cancelled_error is None:
                    cancelled_error = exc
                logger.warning(
                    "%s 关闭 MCP 会话被取消，继续清理其余会话 server=%s workspace_dir=%s session_id=%s",
                    log_title("关闭", "MCP关闭"),
                    *cache_key,
                )

        if cancelled_error is not None:
            raise cancelled_error

        logger.info(
            "%s MCP 会话清理完成 remaining_session_count=%s",
            log_title("关闭", "MCP关闭"),
            len(self._sessions),
        )

    async def close_session(
        self,
        server_name: str,
        workspace_dir: str | Path | None = None,
        session_id: str | None = None,
    ) -> bool:
        """关闭指定 server + workspace + execution scope 的 MCP 会话。

        调用方：Plan / Generator / Healer 的 runtime helper 在每次阶段结束（含预期关闭、
        失败、异常）时调用，用于在不影响其他 workspace 的情况下精准释放当前会话。
        目的：避免 Playwright MCP 子进程（Chromium）在阶段结束后继续驻留，减少端口占用、
        会话串扰和本地资源泄漏。

        Returns:
            bool: `True` 表示命中并成功关闭；`False` 表示会话不存在或关闭失败。
        """

        try:
            provider = self._get_provider(server_name)
        except RuntimeError:
            return False

        normalized_workspace = await asyncio.to_thread(
            provider.normalize_workspace_dir, workspace_dir
        )
        cache_key = self._make_cache_key(server_name, normalized_workspace, session_id)

        # 先快照本次调用要关闭的具体会话对象；最终关闭前仍会在锁内做 identity
        # 校验，因此即使并发关闭后建立了同 key 的新会话，也不会误关新一代对象。
        cached_session = self._sessions.get(cache_key)

        if cached_session is None:
            return False

        return await self._close_cached_session(cache_key, cached_session)

    async def _close_cached_session(
        self,
        cache_key: tuple[str, str | None, str | None],
        cached_session: _CachedToolsSession,
    ) -> bool:
        """在原调用 task 中关闭会话，并让并发调用共享本次关闭结果。"""

        server_name, workspace_dir, session_id = cache_key
        async with self._lock:
            closing_session = self._closing_sessions.get(cache_key)
            if closing_session is not None:
                if closing_session[0] is not cached_session:
                    return False
                close_result = closing_session[1]
                owns_close = False
            elif self._sessions.get(cache_key) is not cached_session:
                return False
            else:
                close_result = asyncio.get_running_loop().create_future()
                self._closing_sessions[cache_key] = (cached_session, close_result)
                owns_close = True

        if not owns_close:
            # 某个等待者被取消时不能取消共享 Future，owner 仍需完成底层关闭并通知
            # 其他等待者。
            return await asyncio.shield(close_result)

        try:
            # AsyncExitStack 可能持有 AnyIO cancel scope，必须由当前调用 task 直接
            # 退出，不能把 aclose 放进额外 create_task。
            await cached_session.stack.aclose()
        except asyncio.CancelledError:
            await self._complete_cached_session_close(
                cache_key,
                cached_session,
                close_result,
                succeeded=False,
            )
            logger.warning(
                "%s 关闭 MCP 会话被取消 server=%s workspace_dir=%s session_id=%s",
                log_title("关闭", "MCP关闭"),
                server_name,
                workspace_dir,
                session_id,
            )
            raise
        except Exception:  # noqa: BLE001
            await self._complete_cached_session_close(
                cache_key,
                cached_session,
                close_result,
                succeeded=False,
            )
            # 关闭异常时保留缓存引用，既避免误报成功，也允许后续清理再次尝试。
            logger.exception(
                "%s 关闭 MCP 会话时出现异常 server=%s workspace_dir=%s session_id=%s",
                log_title("关闭", "MCP关闭"),
                server_name,
                workspace_dir,
                session_id,
            )
            return False

        await self._complete_cached_session_close(
            cache_key,
            cached_session,
            close_result,
            succeeded=True,
        )

        logger.info(
            "%s MCP 会话已关闭 server=%s, workspace_dir=%s, session_id=%s",
            log_title("关闭", "MCP关闭"),
            server_name,
            workspace_dir,
            session_id,
        )
        return True

    async def _complete_cached_session_close(
        self,
        cache_key: tuple[str, str | None, str | None],
        cached_session: _CachedToolsSession,
        close_result: asyncio.Future[bool],
        *,
        succeeded: bool,
    ) -> None:
        """原子发布关闭结果；只有成功时才移除对应会话。"""

        async with self._lock:
            closing_session = self._closing_sessions.get(cache_key)
            if (
                closing_session is not None
                and closing_session[0] is cached_session
                and closing_session[1] is close_result
            ):
                if succeeded and self._sessions.get(cache_key) is cached_session:
                    del self._sessions[cache_key]
                del self._closing_sessions[cache_key]
            if not close_result.done():
                close_result.set_result(succeeded)

    async def _prefetch_workspace_access(self, workspace_dir: str | None) -> None:
        """在建立 MCP 会话之前，主动在线程池里完成对 workspace 目录的同步 IO 预检。

        作用：`langchain_mcp_adapters` 建立 stdio 会话时底层会对 cwd 调用同步
        `os.access` / `os.stat`，这在 LangGraph dev 的 ASGI 事件循环里会被
        `BlockingCallDetector` 识别为阻塞调用并中断整个会话建立。本方法把
        等价的同步 IO 提前放到 `asyncio.to_thread`，让真正的阻塞发生在工作
        线程，事件循环不会被检测器标记。

        调用方：`_ensure_session` 在调用 `client.session(...)` 前调用一次。
        目的：规避 Blocking call 检测，同时在 workspace 路径有权限或缺失问题
        时更早给出清晰错误。
        """

        if workspace_dir is None:
            return

        def _probe() -> None:
            # `os.access` 在不存在的路径上直接返回 False，不会抛异常；配合
            # `os.path.isdir` 能给出更清晰的错误语义。这里只关心预热触发同步 IO
            # 的时机，并不把访问失败当作硬错误——`client.session()` 后续会用自己的
            # 启动失败异常上报，这里的预热不替代真正的连通性检查。
            os.path.isdir(workspace_dir)
            os.access(workspace_dir, os.R_OK | os.X_OK)

        await asyncio.to_thread(_probe)

    async def _ensure_session(
        self,
        provider: MCPServerProvider,
        workspace_dir: str | None,
        session_id: str | None,
    ) -> _CachedToolsSession:
        """确保指定 MCP server + workspace + execution scope 只有一个持久会话。

        同一次执行内复用长连接和工具缓存；不同执行即使使用同一 workspace，也保持
        独立会话，避免并发 Specialist 共享浏览器上下文或互相关闭底层子进程。
        """

        server_name = provider.server_name
        cache_key = self._make_cache_key(server_name, workspace_dir, session_id)
        while True:
            async with self._lock:
                closing_session = self._closing_sessions.get(cache_key)
                if closing_session is None:
                    cached_session = self._sessions.get(cache_key)
                    if cached_session is not None:
                        logger.info(
                            "%s 命中 MCP 工具缓存 server=%s, workspace_dir=%s, session_id=%s",
                            log_title("工具", "MCP缓存"),
                            server_name,
                            workspace_dir,
                            session_id,
                        )
                        return cached_session
                    return await self._create_session_locked(
                        provider,
                        workspace_dir,
                        session_id,
                        cache_key,
                    )
                close_result = closing_session[1]

            close_succeeded = await asyncio.shield(close_result)
            if not close_succeeded:
                raise RuntimeError(
                    f"MCP server `{server_name}` 的既有会话关闭失败，无法安全复用。"
                )

    async def _create_session_locked(
        self,
        provider: MCPServerProvider,
        workspace_dir: str | None,
        session_id: str | None,
        cache_key: tuple[str, str | None, str | None],
    ) -> _CachedToolsSession:
        """在持有管理器锁时创建并缓存一个 MCP 会话。"""

        server_name = provider.server_name
        stack: AsyncExitStack | None = None
        try:
            logger.info(
                "%s 开始建立 MCP 会话 server=%s, workspace_dir=%s, session_id=%s",
                log_title("工具", "MCP连接"),
                server_name,
                workspace_dir,
                session_id,
            )

            # 说明：
            # - `build_connection_config` 内部可能触发 `shutil.which` 等同步 PATH 扫描；
            #   把它丢到线程池执行，避免在 LangGraph dev 的 ASGI 事件循环里被
            #   `BlockingCallDetector` 捕获并中断会话建立。
            connection_config = await asyncio.to_thread(
                provider.build_connection_config,
                self._settings,
                workspace_dir,
            )

            # 主链路：这里正式创建 MCP 客户端，后续所有工具发现和调用都依赖这条连接。
            client = MultiServerMCPClient({server_name: connection_config})
            stack = AsyncExitStack()

            # 说明：
            # - `client.session()` 底层会通过 anyio 拉起 stdio 子进程，期间对 cwd
            #   做同步 `os.access(cwd, os.X_OK)` 一类的存在性校验。这在 LangGraph
            #   dev 的 ASGI 事件循环里会被 `BlockingCallDetector` 检测为阻塞调用，
            #   导致整个 MCP 连接直接抛异常中断。
            # - 这里提前在线程池内做一次等价的 `os.access` 预热，一方面把可能的
            #   同步 IO 合法化（调用发生在 work thread，不在事件循环里），一方面
            #   遇到权限/路径问题时可以更早给出明确错误。
            await self._prefetch_workspace_access(workspace_dir)

            # 这里把 session 放进 `AsyncExitStack`，目的是让关闭逻辑统一交给 manager 托管。
            session = await stack.enter_async_context(client.session(server_name))
            tool_specs = await self._list_mcp_tools(session)

            # 先把工具定义按名字建索引，目的是后续 allowlist 可以 O(1) 校验和取用。
            tool_names: list[str] = []
            tool_specs_by_name: dict[str, Any] = {}
            for tool in tool_specs:
                if tool.name in tool_specs_by_name:
                    raise RuntimeError(
                        f"MCP server `{server_name}` 返回了重复工具名：`{tool.name}`。"
                    )
                tool_names.append(tool.name)
                tool_specs_by_name[tool.name] = tool
        except BaseException as exc:  # noqa: BLE001
            if stack is not None:
                try:
                    await stack.aclose()
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "%s MCP 会话建立失败后的资源回收也失败 server=%s workspace_dir=%s session_id=%s",
                        log_title("关闭", "MCP关闭"),
                        server_name,
                        workspace_dir,
                        session_id,
                    )
            if not isinstance(exc, Exception) or is_langgraph_user_cancellation(exc):
                raise
            logger.exception(
                "%s MCP 会话建立失败：server=%s，workspace_dir=%s，session_id=%s",
                log_title("工具", "MCP异常"),
                server_name,
                workspace_dir,
                session_id,
            )
            raise provider.build_connection_error(
                exc, workspace_dir=workspace_dir
            ) from exc

        cached_session = _CachedToolsSession(
            client=client,
            stack=stack,
            session=session,
            provider=provider,
            workspace_dir=workspace_dir,
            session_id=session_id,
            tool_names=tuple(tool_names),
            tool_specs_by_name=tool_specs_by_name,
        )
        self._sessions[cache_key] = cached_session
        logger.info(
            "%s MCP 工具加载完成 server=%s, workspace_dir=%s, session_id=%s, tool_count=%s",
            log_title("工具", "MCP连接"),
            server_name,
            workspace_dir,
            session_id,
            len(tool_names),
        )
        return cached_session

    def _build_allowed_tools(
        self,
        cached_session: _CachedToolsSession,
        *,
        server_name: str,
        allowed_tool_ids: Sequence[str] | None,
    ) -> list[BaseTool]:
        """按精确工具标识返回当前 Agent 可见的 MCP 工具。

        这里之所以单独做一层 allowlist 过滤，是为了把"server 全量暴露了什么工具"和
        "当前 Agent 实际允许看到什么工具"这两个概念分开，降低越权调用风险。
        """

        if allowed_tool_ids is None:
            requested_tool_names = cached_session.tool_names
            missing_tool_ids: list[str] = []
        else:
            if not allowed_tool_ids:
                return []
            # 先把 `server/tool_name` 解析成原始工具名，目的是在进入真正转换前先把白名单合法性校验掉。
            requested_tool_names = tuple(
                self._parse_tool_id(server_name=server_name, tool_id=tool_id)
                for tool_id in allowed_tool_ids
            )
            missing_tool_ids = [
                tool_id
                for tool_id, tool_name in zip(
                    allowed_tool_ids, requested_tool_names, strict=True
                )
                if tool_name not in cached_session.tool_specs_by_name
            ]

        if missing_tool_ids:
            raise RuntimeError(
                f"MCP server `{server_name}` 缺少以下工具："
                f"{', '.join(missing_tool_ids)}。请检查当前 MCP 工具白名单配置。"
            )

        allowed_tools: list[BaseTool] = []
        for tool_name in requested_tool_names:
            tool = cached_session.loaded_tools_by_name.get(tool_name)
            if tool is None:
                # 主链路：这里把 MCP 原始工具定义转换成 LangChain Tool，
                # 这样上层 Agent 才能直接把它们交给 Deep Agent 使用。
                tool = convert_mcp_tool_to_langchain_tool(
                    cached_session.session,
                    cached_session.tool_specs_by_name[tool_name],
                    server_name=server_name,
                    tool_name_prefix=False,
                )
                self._patch_tool_error_handlers(tool, provider=cached_session.provider)
                tool = self._apply_provider_post_process(
                    tool,
                    provider=cached_session.provider,
                    workspace_dir=cached_session.workspace_dir,
                )
                # provider 的 post_process 可能包成新的 StructuredTool，
                # 这里对最终对外暴露的 tool 再做一次错误处理器补齐，保证统一。
                self._patch_tool_error_handlers(tool, provider=cached_session.provider)
                cached_session.loaded_tools_by_name[tool_name] = tool
            allowed_tools.append(tool)

        return allowed_tools

    def _apply_provider_post_process(
        self,
        tool: BaseTool,
        *,
        provider: MCPServerProvider,
        workspace_dir: str | None,
    ) -> BaseTool:
        """调用 provider 的 `post_process_tool` 钩子；provider 未实现时原样返回。"""

        post_process = getattr(provider, "post_process_tool", None)
        if post_process is None:
            return tool
        return post_process(tool, workspace_dir=workspace_dir)

    def _patch_tool_error_handlers(
        self, tool: BaseTool, *, provider: MCPServerProvider
    ) -> None:
        """为 MCP 工具统一补齐结构化错误包装。

        第一阶段只在工具对象级别补 `handle_tool_error / handle_validation_error`，
        目的是把工具调用失败从“直接抛异常打断图执行”改成“返回模型可见的错误结果”，
        而不需要重写 LangGraph 的 `ToolNode`。
        """

        tool_error_policy = self._resolve_tool_error_policy(provider)
        tool.handle_tool_error = (
            lambda exc, *, tool_name=tool.name: self._wrap_tool_exception(  # type: ignore[assignment]
                exc,
                tool_name=tool_name,
                tool_error_policy=tool_error_policy,
            )
        )
        tool.handle_validation_error = (
            lambda exc, *, tool_name=tool.name: self._wrap_validation_error(  # type: ignore[assignment]
                exc,
                tool_name=tool_name,
                tool_error_policy=tool_error_policy,
            )
        )

    def _wrap_tool_exception(
        self,
        exc: ToolException,
        *,
        tool_name: str,
        tool_error_policy: MCPToolErrorPolicy,
    ) -> str:
        """把工具执行错误变成结构化 JSON 字符串。"""

        error_message = normalize_tool_error_message(exc)
        return self._wrap_tool_failure(
            tool_name=tool_name,
            tool_error_policy=tool_error_policy,
            error_type=tool_error_policy.classify_tool_error(error_message),
            error_message=error_message,
        )

    def _wrap_validation_error(
        self,
        exc: ValidationError | ValidationErrorV1,
        *,
        tool_name: str,
        tool_error_policy: MCPToolErrorPolicy,
    ) -> str:
        """把工具参数错误变成结构化 JSON 字符串。"""

        return self._wrap_tool_failure(
            tool_name=tool_name,
            tool_error_policy=tool_error_policy,
            error_type="TOOL_ARGS_INVALID",
            error_message=normalize_tool_error_message(exc),
        )

    def _wrap_tool_failure(
        self,
        *,
        tool_name: str,
        tool_error_policy: MCPToolErrorPolicy,
        error_type: str,
        error_message: str,
    ) -> str:
        """统一生成结构化工具失败结果，并记录包装日志。"""

        try:
            wrapped_error = build_structured_tool_error(
                tool_name=tool_name,
                error_type=error_type,
                error_message=error_message,
                tool_error_policy=tool_error_policy,
            )
        except Exception as wrap_exc:  # noqa: BLE001
            logger.exception(
                "%s 结构化工具错误包装失败 tool_name=%s",
                log_title("工具", "MCP错误"),
                tool_name,
            )
            fallback_message = normalize_tool_error_message(
                f"{error_type}: {error_message}. Wrapper failure: {wrap_exc}"
            )
            return build_structured_tool_error(
                tool_name=tool_name,
                error_type="UNKNOWN_TOOL_ERROR",
                error_message=fallback_message,
                tool_error_policy=DEFAULT_MCP_TOOL_ERROR_POLICY,
            )

        logger.warning(
            "%s MCP 工具错误已包装为模型可见结果 tool_name=%s error_type=%s payload=%s",
            log_title("工具", "MCP错误"),
            tool_name,
            error_type,
            wrapped_error,
        )
        return wrapped_error

    def _resolve_tool_error_policy(
        self, provider: MCPServerProvider
    ) -> MCPToolErrorPolicy:
        """返回 provider 对应的工具错误策略。"""

        policy = getattr(provider, "tool_error_policy", None)
        if policy is None:
            return DEFAULT_MCP_TOOL_ERROR_POLICY
        return policy

    def _parse_tool_id(self, *, server_name: str, tool_id: str) -> str:
        """把带 server 前缀的工具标识解析成 MCP 原始工具名。

        强制校验前缀的目的，是避免不同 server 的工具标识混用，导致 Agent 误拿到错误来源的工具。
        """

        expected_prefix = f"{server_name}/"
        if not tool_id.startswith(expected_prefix):
            raise RuntimeError(
                f"MCP 工具标识 `{tool_id}` 非法，必须使用 `{expected_prefix}` 前缀。"
            )
        return tool_id[len(expected_prefix) :]

    async def _list_mcp_tools(self, session: Any) -> list[Any]:
        """列出指定 MCP session 暴露的全部工具定义。

        这里自己处理分页而不是假设一次拉全，目的是兼容工具较多或 server 采用分页返回的场景。
        """

        current_cursor: str | None = None
        all_tools: list[Any] = []

        # 设置一个足够高的分页上限，目的是在异常 server 行为下避免无限循环。
        for _ in range(1000):
            page = await session.list_tools(cursor=current_cursor)
            if page.tools:
                all_tools.extend(page.tools)
            if not page.nextCursor:
                return all_tools
            current_cursor = page.nextCursor

        raise RuntimeError("列举 MCP 工具时超过最大分页次数 1000。")

    def _build_provider_registry(
        self,
        providers: Sequence[MCPServerProvider],
    ) -> dict[str, MCPServerProvider]:
        """构建 provider 注册表。

        提前建好注册表的目的，是把 provider 查找从“遍历列表”变成“按名字直接命中”，
        同时在启动阶段就尽早发现重复注册问题。
        """

        registry: dict[str, MCPServerProvider] = {}
        for provider in providers:
            if provider.server_name in registry:
                raise RuntimeError(f"MCP provider `{provider.server_name}` 重复注册。")
            registry[provider.server_name] = provider
        return registry

    def _get_provider(self, server_name: str) -> MCPServerProvider:
        """返回指定 server 对应的 provider。

        通过统一入口解析 provider，目的是让后续调用链不必关心 provider 来自默认配置还是测试注入。
        """

        provider = self._providers.get(server_name)
        if provider is None:
            raise RuntimeError(f"MCP server `{server_name}` 未注册对应的 provider。")
        return provider

    def _make_cache_key(
        self,
        server_name: str,
        workspace_dir: str | None,
        session_id: str | None,
    ) -> tuple[str, str | None, str | None]:
        """构建工具缓存键。

        把 `server_name + workspace_dir + session_id` 组合成缓存键，既隔离不同项目，
        也隔离同项目内并发的 Specialist 执行。
        """

        return server_name, workspace_dir, session_id

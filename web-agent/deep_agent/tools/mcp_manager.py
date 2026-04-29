"""统一管理所有 MCP server 的持久会话和工具缓存。

这个模块的核心目的，是把“如何连接 MCP、如何按 workspace 复用会话、如何把工具定义转换成
LangChain Tool”这些底层细节收口，避免上层 Agent 自己管理连接生命周期和缓存一致性。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from langchain_core.tools import BaseTool
from langchain_core.tools.base import ToolException
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool
from pydantic import ValidationError
from pydantic.v1 import ValidationError as ValidationErrorV1

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
    而把“路径归一化、连接参数构造、错误包装”交给各个 server 自己定义。
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
        self._providers = self._build_provider_registry(() if providers is None else providers)
        self._sessions: dict[tuple[str, str | None], _CachedToolsSession] = {}
        # 这个锁的目的，是避免并发请求同一个 server/workspace 时重复初始化 session，
        # 进而造成多条长连接和重复工具拉取。
        self._lock = asyncio.Lock()
        logger.info("%s MCPToolsManager 初始化完成 settings=%s",
            log_title("初始化", "MCP初始化"), summarize_settings(settings),)

    async def get_tools(
        self,
        server_name: str,
        workspace_dir: str | Path | None = None,
        allowed_tool_ids: Sequence[str] | None = None,
    ) -> Sequence[BaseTool]:
        """获取指定 MCP server 的工具列表。

        对外暴露这个方法的目的，是让调用方用统一入口拿到“已经可直接执行的 LangChain Tool”，
        而不是自己处理 provider、session、allowlist 和工具转换。
        """

        # 先解析 provider，再做目录归一化，目的是把不同 server 的接入差异消化在管理器内部。
        provider = self._get_provider(server_name)
        normalized_workspace = await asyncio.to_thread(provider.normalize_workspace_dir, workspace_dir)
        logger.info("%s 开始获取 MCP 工具 server=%s, workspace_dir=%s, allowed_tool_ids=%s",
            log_title("工具", "MCP工具"), server_name, normalized_workspace, list(allowed_tool_ids or ()),)
        prepare_workspace = getattr(provider, "prepare_workspace", None)
        if prepare_workspace is not None:
            await asyncio.to_thread(prepare_workspace, self._settings, normalized_workspace)

        # 会话准备和工具筛选分成两步，目的是先确保连接稳定，再按当前 Agent 的白名单裁剪可见工具。
        cached_session = await self._ensure_session(provider, normalized_workspace)
        return self._build_allowed_tools(
            cached_session,
            server_name=server_name,
            allowed_tool_ids=allowed_tool_ids,
        )

    async def close(self) -> None:
        """主动关闭持有的 MCP 会话。

        显式暴露关闭能力的目的，是让进程退出或测试结束时能主动回收长连接和子进程，
        而不是依赖解释器回收时机。
        """

        for cached_session in self._sessions.values():
            await cached_session.stack.aclose()
        self._sessions.clear()
        logger.info("%s 所有 MCP 会话已关闭。",
            log_title("关闭", "MCP关闭"),)

    async def _ensure_session(
        self,
        provider: MCPServerProvider,
        workspace_dir: str | None,
    ) -> _CachedToolsSession:
        """确保指定 MCP server 只有一个持久会话。

        这里的核心目标，是把相同 `server + workspace` 的请求复用到同一条长连接上，
        从而减少连接开销并保持工具缓存一致。
        """

        server_name = provider.server_name
        cache_key = self._make_cache_key(server_name, workspace_dir)
        cached_session = self._sessions.get(cache_key)
        if cached_session is not None:
            logger.info("%s 命中 MCP 工具缓存 server=%s, workspace_dir=%s",
                log_title("工具", "MCP缓存"), server_name, workspace_dir,)
            return cached_session

        # 首次未命中后再进锁做二次检查，目的是兼顾并发安全和常见命中路径的性能。
        async with self._lock:
            cached_session = self._sessions.get(cache_key)
            if cached_session is not None:
                logger.info("%s 命中 MCP 工具缓存 server=%s, workspace_dir=%s",
                    log_title("工具", "MCP缓存"), server_name, workspace_dir,)
                return cached_session

            try:
                logger.info("%s 开始建立 MCP 会话 server=%s, workspace_dir=%s",
                    log_title("工具", "MCP连接"), server_name, workspace_dir,)
                # TODO(重点流程): 这里正式创建 MCP 客户端，后续所有工具发现和调用都依赖这条连接。
                client = MultiServerMCPClient(
                    {server_name: provider.build_connection_config(self._settings, workspace_dir)}
                )
                stack = AsyncExitStack()
                # 这里把 session 放进 `AsyncExitStack`，目的是让关闭逻辑统一交给 manager 托管。
                session = await stack.enter_async_context(client.session(server_name))
                tool_specs = await self._list_mcp_tools(session)
            except Exception as exc:  # noqa: BLE001
                logger.exception("%s MCP 会话建立失败：server=%s，workspace_dir=%s",
                    log_title("工具", "MCP异常"), server_name, workspace_dir,)
                raise provider.build_connection_error(exc, workspace_dir=workspace_dir) from exc

            # 先把工具定义按名字建索引，目的是后续 allowlist 可以 O(1) 校验和取用。
            tool_names: list[str] = []
            tool_specs_by_name: dict[str, Any] = {}
            for tool in tool_specs:
                if tool.name in tool_specs_by_name:
                    raise RuntimeError(f"MCP server `{server_name}` 返回了重复工具名：`{tool.name}`。")
                tool_names.append(tool.name)
                tool_specs_by_name[tool.name] = tool

            cached_session = _CachedToolsSession(
                client=client,
                stack=stack,
                session=session,
                provider=provider,
                tool_names=tuple(tool_names),
                tool_specs_by_name=tool_specs_by_name,
            )
            self._sessions[cache_key] = cached_session
            logger.info("%s MCP 工具加载完成 server=%s, workspace_dir=%s, tool_count=%s",
                log_title("工具", "MCP连接"), server_name, workspace_dir, len(tool_names),)
            return cached_session

    def _build_allowed_tools(
        self,
        cached_session: _CachedToolsSession,
        *,
        server_name: str,
        allowed_tool_ids: Sequence[str] | None,
    ) -> list[BaseTool]:
        """按精确工具标识返回当前 Agent 可见的 MCP 工具。

        这里之所以单独做一层 allowlist 过滤，是为了把“server 全量暴露了什么工具”和
        “当前 Agent 实际允许看到什么工具”这两个概念分开，降低越权调用风险。
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
                for tool_id, tool_name in zip(allowed_tool_ids, requested_tool_names, strict=True)
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
                # TODO(重点流程): 这里把 MCP 原始工具定义转换成 LangChain Tool，
                # 这样上层 Agent 才能直接把它们交给 Deep Agent 使用。
                tool = convert_mcp_tool_to_langchain_tool(
                    cached_session.session,
                    cached_session.tool_specs_by_name[tool_name],
                    server_name=server_name,
                    tool_name_prefix=False,
                )
                self._patch_tool_error_handlers(tool, provider=cached_session.provider)
                cached_session.loaded_tools_by_name[tool_name] = tool
            allowed_tools.append(tool)

        return allowed_tools

    def _patch_tool_error_handlers(self, tool: BaseTool, *, provider: MCPServerProvider) -> None:
        """为 MCP 工具统一补齐结构化错误包装。

        第一阶段只在工具对象级别补 `handle_tool_error / handle_validation_error`，
        目的是把工具调用失败从“直接抛异常打断图执行”改成“返回模型可见的错误结果”，
        而不需要重写 LangGraph 的 `ToolNode`。
        """

        tool_error_policy = self._resolve_tool_error_policy(provider)
        tool.handle_tool_error = lambda exc, *, tool_name=tool.name: self._wrap_tool_exception(  # type: ignore[assignment]
            exc,
            tool_name=tool_name,
            tool_error_policy=tool_error_policy,
        )
        tool.handle_validation_error = lambda exc, *, tool_name=tool.name: self._wrap_validation_error(  # type: ignore[assignment]
            exc,
            tool_name=tool_name,
            tool_error_policy=tool_error_policy,
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
            logger.exception("%s 结构化工具错误包装失败 tool_name=%s",
                log_title("工具", "MCP错误"), tool_name,)
            fallback_message = normalize_tool_error_message(
                f"{error_type}: {error_message}. Wrapper failure: {wrap_exc}"
            )
            return build_structured_tool_error(
                tool_name=tool_name,
                error_type="UNKNOWN_TOOL_ERROR",
                error_message=fallback_message,
                tool_error_policy=DEFAULT_MCP_TOOL_ERROR_POLICY,
            )

        logger.warning("%s MCP 工具错误已包装为模型可见结果 tool_name=%s error_type=%s payload=%s",
            log_title("工具", "MCP错误"), tool_name, error_type, wrapped_error,)
        return wrapped_error

    def _resolve_tool_error_policy(self, provider: MCPServerProvider) -> MCPToolErrorPolicy:
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

    def _make_cache_key(self, server_name: str, workspace_dir: str | None) -> tuple[str, str | None]:
        """构建工具缓存键。

        把 `server_name + workspace_dir` 组合成缓存键的目的，是保证“同一个 server 不同项目目录”
        不会错误复用同一条会话。
        """

        return server_name, workspace_dir

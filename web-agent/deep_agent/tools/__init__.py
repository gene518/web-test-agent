"""工具层导出。"""

from __future__ import annotations

from deep_agent.core.config import AppSettings, get_settings
from deep_agent.core.runtime_logging import get_logger, log_title
from deep_agent.tools.mcp_manager import MCPToolsManager
from deep_agent.tools.playwright import PLAYWRIGHT_TEST_MCP_PROVIDER


logger = get_logger(__name__)
_DEFAULT_MCP_PROVIDERS = (PLAYWRIGHT_TEST_MCP_PROVIDER,)
_MCP_MANAGER: MCPToolsManager | None = None


def get_mcp_tools_manager(settings: AppSettings | None = None) -> MCPToolsManager:
    """返回带默认 provider 的全局单例 MCP 管理器。"""

    global _MCP_MANAGER

    if _MCP_MANAGER is None:
        _MCP_MANAGER = MCPToolsManager(
            settings or get_settings(),
            providers=_DEFAULT_MCP_PROVIDERS,
        )
        logger.info("%s 已创建全局 MCP 管理器。",
            log_title("初始化", "MCP单例"),)
    else:
        logger.info("%s 复用全局 MCP 管理器。",
            log_title("初始化", "MCP单例"),)

    return _MCP_MANAGER


__all__ = ["MCPToolsManager", "get_mcp_tools_manager"]

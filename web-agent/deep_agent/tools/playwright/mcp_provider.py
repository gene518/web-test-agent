"""Playwright Test MCP 的专属 provider 定义。"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from json import JSONDecodeError
from pathlib import Path

from langchain_core.tools import BaseTool

from deep_agent.core.config import AppSettings
from deep_agent.core.runtime_logging import get_logger, log_title
from deep_agent.tools.playwright.planner_save_plan_wrapper import wrap_planner_save_plan_tool
from deep_agent.tools.playwright.tool_error_policy import PLAYWRIGHT_MCP_TOOL_ERROR_POLICY


PLAYWRIGHT_TEST_MCP_SERVER_NAME = "playwright-test"
PLAYWRIGHT_TEST_PACKAGE_NAME = "@playwright/test"

logger = get_logger(__name__)


def _resolve_node_executable(name: str) -> str:
    """
    把 Node.js 生态的可执行文件名（如 `npm`、`npx`）解析成绝对路径。

    作用：在 Windows 上这些命令实际是 `npm.cmd`、`npx.cmd` 这样的批处理脚本，
    Python 的 `subprocess.run` 默认使用 `CreateProcess` 直接执行，不会像 shell
    一样按 `PATHEXT` 查找 `.cmd`/`.bat`，因此裸写 `"npm"` 会抛 `FileNotFoundError`。
    这里用 `shutil.which` 做跨平台查找（Windows 上它会遍历 `PATHEXT`），
    找到真实路径后再交给 subprocess，避免依赖 `shell=True` 引入注入风险。

    主要消费方：`PlaywrightTestMCPProvider._run_npm`、`build_connection_config`，
    目的是让 Windows 上也能稳定拉起 npm/npx。

    缓存：结果按 name 缓存在 `_NODE_EXECUTABLE_CACHE`，避免每次建立 MCP 会话都要
    遍历 PATH。遍历 PATH 属于同步 IO，在 LangGraph dev 的 ASGI 事件循环里可能触发
    `BlockingCallDetector`；缓存可以把真正的 IO 限制在进程生命周期里的首次调用。
    首次解析如果发生在事件循环线程，调用方仍然应该用 `asyncio.to_thread` 包裹。
    """

    cached = _NODE_EXECUTABLE_CACHE.get(name)
    if cached is not None:
        return cached

    resolved = shutil.which(name)
    if resolved is None and sys.platform.startswith("win"):
        # 兜底：Windows 上再尝试一次常见扩展名，兼容 PATH 中只登记命令名未登记扩展名的环境。
        for ext in (".cmd", ".exe", ".bat"):
            resolved = shutil.which(name + ext)
            if resolved:
                break

    # 未找到时仍然把原名存进缓存并返回，由调用方在 FileNotFoundError 时给出友好提示。
    final_value = resolved if resolved else name
    _NODE_EXECUTABLE_CACHE[name] = final_value
    return final_value


# 模块级缓存：存放已解析的 Node 可执行文件绝对路径，见 `_resolve_node_executable` docstring。
_NODE_EXECUTABLE_CACHE: dict[str, str] = {}


@dataclass(frozen=True, slots=True)
class PlaywrightTestMCPProvider:
    """定义 Playwright Test MCP 的连接与目录规则。"""

    server_name: str = PLAYWRIGHT_TEST_MCP_SERVER_NAME
    tool_error_policy = PLAYWRIGHT_MCP_TOOL_ERROR_POLICY

    def normalize_workspace_dir(self, workspace_dir: str | Path | None) -> str | None:
        """归一化 Playwright Test MCP 的工作目录。"""

        if workspace_dir is None:
            return None

        return str(Path(workspace_dir).expanduser().resolve())

    def build_connection_config(
        self,
        settings: AppSettings,
        workspace_dir: str | None,
    ) -> dict[str, object]:
        """构建 Playwright Test MCP 的 stdio 连接配置。"""

        return {
            "transport": "stdio",
            "command": _resolve_node_executable("npx"),
            "args": list(settings.playwright_mcp_args),
            "env": settings.playwright_mcp_env,
            "cwd": workspace_dir,
        }

    def prepare_workspace(self, settings: AppSettings, workspace_dir: str | None) -> None:
        """确保 Playwright Test MCP 的项目目录具备可运行测试的 npm 依赖。"""

        if workspace_dir is None or not settings.playwright_bootstrap_workspace:
            return

        workspace_path = Path(workspace_dir).expanduser().resolve()
        workspace_path.mkdir(parents=True, exist_ok=True)

        package_json = workspace_path / "package.json"
        if not package_json.exists():
            package_json.write_text(
                json.dumps(
                    {
                        "name": self._workspace_package_name(workspace_path),
                        "private": True,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

        package_data = self._read_package_json(package_json)
        dependency_declared = self._declares_playwright_test(package_data)
        dependency_installed = (workspace_path / "node_modules" / PLAYWRIGHT_TEST_PACKAGE_NAME / "package.json").is_file()

        if dependency_declared and dependency_installed:
            return

        if dependency_declared:
            command = ("npm", "install")
        else:
            command = ("npm", "install", "--save-dev", settings.playwright_test_package)

        logger.info("%s 初始化 Playwright Test 项目依赖 workspace_dir=%s command=%s",
            log_title("工具", "Playwright依赖"), workspace_path, " ".join(command),)
        self._run_npm(command, workspace_path, settings=settings)

    def build_connection_error(
        self,
        exc: Exception,
        *,
        workspace_dir: str | None,
    ) -> RuntimeError:
        """构建 Playwright Test MCP 的连接失败异常。"""

        error = str(exc).strip()
        suffix = f" 原始错误：{error}" if error else ""
        return RuntimeError(
            "无法连接到 MCP server `playwright-test`。请确认本机可以执行 "
            "`npx playwright run-test-mcp-server`，并且项目目录可执行 npm install。"
            f" workspace_dir={workspace_dir}.{suffix}"
        )

    def post_process_tool(
        self,
        tool: BaseTool,
        *,
        workspace_dir: str | None,
    ) -> BaseTool:
        """供 `MCPToolsManager` 在工具转换完成后调用的扩展点。

        目的：把 `planner_save_plan` 的业务规则（路径校验、缺父目录自动重建、
        最终错误归一化）从 `MCPToolsManager` 的通用流程里剥出来，集中放到
        Playwright provider 自己的领域内。其他工具原样返回。
        """

        workspace_path = Path(workspace_dir) if workspace_dir else None
        return wrap_planner_save_plan_tool(
            tool,
            workspace_dir=workspace_path,
            tool_error_policy=self.tool_error_policy,
        )

    def _read_package_json(self, package_json: Path) -> dict[str, object]:
        """读取 package.json，并把非法 JSON 转成可操作的运行错误。"""

        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except JSONDecodeError as exc:
            raise RuntimeError(f"`{package_json}` 不是合法 JSON，无法初始化 Playwright 依赖。") from exc

        if not isinstance(data, dict):
            raise RuntimeError(f"`{package_json}` 顶层必须是 JSON object，无法初始化 Playwright 依赖。")
        return data

    def _declares_playwright_test(self, package_data: dict[str, object]) -> bool:
        """判断 package.json 是否已经声明 @playwright/test。"""

        for dependency_group in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            dependencies = package_data.get(dependency_group)
            if isinstance(dependencies, dict) and PLAYWRIGHT_TEST_PACKAGE_NAME in dependencies:
                return True
        return False

    def _run_npm(self, command: tuple[str, ...], workspace_path: Path, *, settings: AppSettings) -> None:
        """在指定项目目录执行 npm 命令，并保留失败时最有用的输出。"""

        env = os.environ.copy()
        if settings.playwright_skip_browser_download:
            env["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "1"

        if not command:
            raise RuntimeError("执行 npm 命令失败：命令参数为空。")

        # 把首元素（通常是 `npm`）解析成绝对路径，兼容 Windows 上 `npm.cmd` 的场景。
        resolved_command = (_resolve_node_executable(command[0]), *command[1:])

        try:
            subprocess.run(
                resolved_command,
                cwd=workspace_path,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("未找到 npm，请先安装 Node.js/npm 后再启动 Playwright Test MCP。") from exc
        except subprocess.CalledProcessError as exc:
            output = "\n".join(part for part in (exc.stdout, exc.stderr) if part).strip()
            if len(output) > 2000:
                output = f"{output[:2000]}..."
            raise RuntimeError(
                f"执行 `{' '.join(command)}` 失败，无法初始化 Playwright Test 项目依赖。{output}"
            ) from exc

    def _workspace_package_name(self, workspace_path: Path) -> str:
        """根据目录名生成 npm 可接受的私有包名。"""

        package_name = re.sub(r"[^a-z0-9._-]+", "-", workspace_path.name.lower()).strip("._-")
        return package_name[:214] or "web-autotest-workspace"


PLAYWRIGHT_TEST_MCP_PROVIDER = PlaywrightTestMCPProvider()


__all__ = [
    "PLAYWRIGHT_TEST_MCP_PROVIDER",
    "PLAYWRIGHT_TEST_MCP_SERVER_NAME",
    "PlaywrightTestMCPProvider",
]

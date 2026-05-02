"""Playwright Test MCP 的专属 provider 定义。"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from json import JSONDecodeError
from pathlib import Path

from deep_agent.core.config import AppSettings
from deep_agent.core.runtime_logging import get_logger, log_title
from deep_agent.tools.playwright.tool_error_policy import PLAYWRIGHT_MCP_TOOL_ERROR_POLICY


PLAYWRIGHT_TEST_MCP_SERVER_NAME = "playwright-test"
PLAYWRIGHT_TEST_PACKAGE_NAME = "@playwright/test"

logger = get_logger(__name__)


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
            "command": "npx",
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

        try:
            subprocess.run(
                command,
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

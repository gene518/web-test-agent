from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Literal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langchain_core.tools.base import ToolException
from pydantic import BaseModel, Field

from deep_agent.core.config import AppSettings
from deep_agent.tools.mcp_manager import MCPToolsManager
from deep_agent.tools.playwright import (
    PLAYWRIGHT_TEST_MCP_PROVIDER,
    PLAYWRIGHT_TEST_MCP_SERVER_NAME,
)


class FakeSessionContext:
    def __init__(self, server_name: str) -> None:
        self.server_name = server_name
        self.tools_pages: list[SimpleNamespace] = []

    async def __aenter__(self):
        return f"session:{self.server_name}"

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
        return False


class FakeClient:
    instances: list["FakeClient"] = []

    def __init__(self, connections) -> None:  # noqa: ANN001
        self.connections = connections
        FakeClient.instances.append(self)

    def session(self, server_name: str) -> FakeSessionContext:
        return FakeSessionContext(server_name)


class FakeCustomProvider:
    server_name = "custom-mcp"

    def __init__(self) -> None:
        self.prepared_workspaces: list[str | None] = []

    def normalize_workspace_dir(self, workspace_dir):  # noqa: ANN001
        if workspace_dir is None:
            return None
        return f"custom::{Path(workspace_dir).expanduser().resolve()}"

    def prepare_workspace(self, settings, workspace_dir):  # noqa: ANN001
        del settings
        self.prepared_workspaces.append(workspace_dir)

    def build_connection_config(self, settings, workspace_dir):  # noqa: ANN001
        return {
            "transport": "stdio",
            "command": "custom-command",
            "args": ["custom-server"],
            "env": {"CUSTOM": "1", "PWTEST_HEADED": "1" if settings.pwtest_headed else "0"},
            "cwd": workspace_dir,
        }

    def build_connection_error(self, exc, *, workspace_dir):  # noqa: ANN001
        del exc
        return RuntimeError(f"custom-mcp 连接失败：workspace_dir={workspace_dir}")


class ClickArgs(BaseModel):
    ref: str
    intent: str


class PlannerSavePlanArgs(BaseModel):
    name: str
    fileName: str
    overview: str = "overview"
    suites: list[dict] = Field(default_factory=list)


class PointerInterceptedTool(BaseTool):
    name: str = "browser_click"
    description: str = "browser click tool"
    args_schema: type[BaseModel] = ClickArgs

    def _run(self, ref: str, intent: str) -> str:  # noqa: ARG002
        raise ToolException(
            "### Error\nTimeoutError: browserBackend.callTool: Timeout 30000ms exceeded.\n"
            "Call log:\n  - <label for='index-kw'></label> intercepts pointer events"
        )

    async def _arun(self, ref: str, intent: str) -> str:  # noqa: ARG002
        return self._run(ref, intent)


class ValidatingClickTool(BaseTool):
    name: str = "browser_click"
    description: str = "browser click tool"
    args_schema: type[BaseModel] = ClickArgs

    def _run(self, ref: str, intent: str) -> str:
        return f"{ref}:{intent}"

    async def _arun(self, ref: str, intent: str) -> str:
        return self._run(ref, intent)


class ParentMissingPlannerSaveTool(BaseTool):
    name: str = "planner_save_plan"
    description: str = "planner save plan tool"
    args_schema: type[BaseModel] = PlannerSavePlanArgs
    response_format: Literal["content", "content_and_artifact"] = "content_and_artifact"
    workspace_dir: Path
    calls: int = 0

    def _run(self, name: str, fileName: str, overview: str = "overview", suites: list[dict] | None = None):  # noqa: ANN201, ARG002, N803
        self.calls += 1
        if self.calls == 1:
            raise ToolException(
                "RESOURCE_NOT_FOUND: ENOENT: no such file or directory, open "
                f"'{self.workspace_dir / fileName}'"
        )
        if not (self.workspace_dir / Path(fileName).parent).is_dir():
            raise ToolException(f"ENOENT: parent directory does not exist: {Path(fileName).parent}")
        return ([{"type": "text", "text": "saved"}], {"raw": "saved"})

    async def _arun(self, name: str, fileName: str, overview: str = "overview", suites: list[dict] | None = None):  # noqa: ANN201, N803
        return self._run(name, fileName, overview, suites)


class FailingPlannerSaveTool(BaseTool):
    name: str = "planner_save_plan"
    description: str = "planner save plan tool"
    args_schema: type[BaseModel] = PlannerSavePlanArgs
    response_format: Literal["content", "content_and_artifact"] = "content_and_artifact"
    calls: int = 0

    def _run(self, name: str, fileName: str, overview: str = "overview", suites: list[dict] | None = None):  # noqa: ANN201, ARG002, N803
        self.calls += 1
        raise ToolException("PERMISSION_DENIED: cannot write file")

    async def _arun(self, name: str, fileName: str, overview: str = "overview", suites: list[dict] | None = None):  # noqa: ANN201, N803
        return self._run(name, fileName, overview, suites)


class ListReturningPlannerSaveTool(BaseTool):
    name: str = "planner_save_plan"
    description: str = "planner save plan tool"
    args_schema: type[BaseModel] = PlannerSavePlanArgs
    response_format: Literal["content", "content_and_artifact"] = "content_and_artifact"
    calls: int = 0

    def _run(self, name: str, fileName: str, overview: str = "overview", suites: list[dict] | None = None):  # noqa: ANN201, ARG002, N803
        self.calls += 1
        return [{"type": "text", "text": f"saved:{fileName}"}]

    async def _arun(self, name: str, fileName: str, overview: str = "overview", suites: list[dict] | None = None):  # noqa: ANN201, N803
        return self._run(name, fileName, overview, suites)


class MCPManagerTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)
        FakeClient.instances.clear()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _build_settings(self) -> AppSettings:
        return AppSettings(
            default_automation_project_root=str(self.root_path / "projects"),
            playwright_bootstrap_workspace=False,
        )

    async def test_get_tools_caches_by_server_and_workspace_dir(self) -> None:
        settings = self._build_settings()
        manager = MCPToolsManager(settings, providers=(PLAYWRIGHT_TEST_MCP_PROVIDER,))
        project_a = (self.root_path / "a").resolve()
        project_b = (self.root_path / "b").resolve()
        project_a.mkdir(parents=True, exist_ok=True)
        project_b.mkdir(parents=True, exist_ok=True)

        list_tools = AsyncMock(
            side_effect=[
                [
                    SimpleNamespace(name="browser_navigate"),
                    SimpleNamespace(name="planner_save_plan"),
                ],
                [
                    SimpleNamespace(name="browser_navigate"),
                    SimpleNamespace(name="planner_save_plan"),
                ],
            ]
        )
        converter = Mock(side_effect=["tool-a", "tool-b"])

        with (
            patch("deep_agent.tools.mcp_manager.MultiServerMCPClient", FakeClient),
            patch.object(MCPToolsManager, "_list_mcp_tools", list_tools),
            patch("deep_agent.tools.mcp_manager.convert_mcp_tool_to_langchain_tool", converter),
            patch.object(MCPToolsManager, "_patch_tool_error_handlers"),
        ):
            tools_a_first = await manager.get_tools(
                PLAYWRIGHT_TEST_MCP_SERVER_NAME,
                project_a,
                (f"{PLAYWRIGHT_TEST_MCP_SERVER_NAME}/browser_navigate",),
            )
            tools_a_second = await manager.get_tools(
                PLAYWRIGHT_TEST_MCP_SERVER_NAME,
                project_a,
                (f"{PLAYWRIGHT_TEST_MCP_SERVER_NAME}/browser_navigate",),
            )
            tools_b_first = await manager.get_tools(
                PLAYWRIGHT_TEST_MCP_SERVER_NAME,
                project_b,
                (f"{PLAYWRIGHT_TEST_MCP_SERVER_NAME}/browser_navigate",),
            )

        self.assertEqual(tools_a_first, ["tool-a"])
        self.assertEqual(tools_a_second, ["tool-a"])
        self.assertEqual(tools_b_first, ["tool-b"])
        self.assertEqual(list_tools.await_count, 2)
        self.assertEqual(converter.call_count, 2)
        self.assertEqual(
            FakeClient.instances[0].connections[PLAYWRIGHT_TEST_MCP_SERVER_NAME]["cwd"],
            str(project_a),
        )
        self.assertEqual(
            FakeClient.instances[1].connections[PLAYWRIGHT_TEST_MCP_SERVER_NAME]["cwd"],
            str(project_b),
        )

        await manager.close()

    async def test_get_tools_returns_exact_allowlist_order(self) -> None:
        settings = self._build_settings()
        manager = MCPToolsManager(settings, providers=(PLAYWRIGHT_TEST_MCP_PROVIDER,))
        project_dir = (self.root_path / "project").resolve()
        project_dir.mkdir(parents=True, exist_ok=True)

        list_tools = AsyncMock(
            return_value=[
                SimpleNamespace(name="browser_navigate"),
                SimpleNamespace(name="planner_save_plan"),
                SimpleNamespace(name="browser_snapshot"),
            ]
        )

        def fake_converter(session, tool, **kwargs):  # noqa: ANN001
            return f"{session}:{tool.name}:{kwargs['server_name']}"

        with (
            patch("deep_agent.tools.mcp_manager.MultiServerMCPClient", FakeClient),
            patch.object(MCPToolsManager, "_list_mcp_tools", list_tools),
            patch("deep_agent.tools.mcp_manager.convert_mcp_tool_to_langchain_tool", side_effect=fake_converter),
            patch.object(MCPToolsManager, "_patch_tool_error_handlers"),
        ):
            tools = await manager.get_tools(
                PLAYWRIGHT_TEST_MCP_SERVER_NAME,
                project_dir,
                (
                    f"{PLAYWRIGHT_TEST_MCP_SERVER_NAME}/planner_save_plan",
                    f"{PLAYWRIGHT_TEST_MCP_SERVER_NAME}/browser_navigate",
                ),
            )

        self.assertEqual(
            tools,
            [
                f"session:{PLAYWRIGHT_TEST_MCP_SERVER_NAME}:planner_save_plan:{PLAYWRIGHT_TEST_MCP_SERVER_NAME}",
                f"session:{PLAYWRIGHT_TEST_MCP_SERVER_NAME}:browser_navigate:{PLAYWRIGHT_TEST_MCP_SERVER_NAME}",
            ],
        )

        await manager.close()

    async def test_get_tools_raises_when_allowlist_tool_missing(self) -> None:
        settings = self._build_settings()
        manager = MCPToolsManager(settings, providers=(PLAYWRIGHT_TEST_MCP_PROVIDER,))
        project_dir = (self.root_path / "project-missing").resolve()
        project_dir.mkdir(parents=True, exist_ok=True)

        list_tools = AsyncMock(return_value=[SimpleNamespace(name="browser_navigate")])

        with (
            patch("deep_agent.tools.mcp_manager.MultiServerMCPClient", FakeClient),
            patch.object(MCPToolsManager, "_list_mcp_tools", list_tools),
        ):
            with self.assertRaisesRegex(RuntimeError, "playwright-test/planner_save_plan"):
                await manager.get_tools(
                    PLAYWRIGHT_TEST_MCP_SERVER_NAME,
                    project_dir,
                    (f"{PLAYWRIGHT_TEST_MCP_SERVER_NAME}/planner_save_plan",),
                )

        await manager.close()

    async def test_get_tools_raises_when_server_is_not_registered(self) -> None:
        settings = self._build_settings()
        manager = MCPToolsManager(settings, providers=())

        with self.assertRaisesRegex(RuntimeError, "unknown-mcp"):
            await manager.get_tools("unknown-mcp")

    async def test_get_tools_supports_custom_provider_injection(self) -> None:
        settings = self._build_settings()
        provider = FakeCustomProvider()
        manager = MCPToolsManager(settings, providers=(provider,))
        project_dir = self.root_path / "custom-project"
        project_dir.mkdir(parents=True, exist_ok=True)

        list_tools = AsyncMock(return_value=[SimpleNamespace(name="custom_tool")])

        with (
            patch("deep_agent.tools.mcp_manager.MultiServerMCPClient", FakeClient),
            patch.object(MCPToolsManager, "_list_mcp_tools", list_tools),
            patch("deep_agent.tools.mcp_manager.convert_mcp_tool_to_langchain_tool", return_value="custom-tool"),
            patch.object(MCPToolsManager, "_patch_tool_error_handlers"),
        ):
            tools = await manager.get_tools(
                "custom-mcp",
                project_dir,
                ("custom-mcp/custom_tool",),
            )

        self.assertEqual(tools, ["custom-tool"])
        self.assertEqual(FakeClient.instances[0].connections["custom-mcp"]["command"], "custom-command")
        self.assertEqual(
            FakeClient.instances[0].connections["custom-mcp"]["cwd"],
            f"custom::{project_dir.resolve()}",
        )
        self.assertEqual(provider.prepared_workspaces, [f"custom::{project_dir.resolve()}"])

        await manager.close()

    async def test_get_tools_wraps_tool_exception_as_structured_tool_message(self) -> None:
        settings = self._build_settings()
        manager = MCPToolsManager(settings, providers=(PLAYWRIGHT_TEST_MCP_PROVIDER,))
        project_dir = (self.root_path / "structured-error").resolve()
        project_dir.mkdir(parents=True, exist_ok=True)
        list_tools = AsyncMock(return_value=[SimpleNamespace(name="browser_click")])
        tool = PointerInterceptedTool()

        with (
            patch("deep_agent.tools.mcp_manager.MultiServerMCPClient", FakeClient),
            patch.object(MCPToolsManager, "_list_mcp_tools", list_tools),
            patch("deep_agent.tools.mcp_manager.convert_mcp_tool_to_langchain_tool", return_value=tool),
        ):
            [wrapped_tool] = await manager.get_tools(
                PLAYWRIGHT_TEST_MCP_SERVER_NAME,
                project_dir,
                (f"{PLAYWRIGHT_TEST_MCP_SERVER_NAME}/browser_click",),
            )

        result = await wrapped_tool.arun(
            {"ref": "e55", "intent": "Click the search input"},
            tool_call_id="call-1",
        )

        self.assertIsInstance(result, ToolMessage)
        self.assertEqual(result.status, "error")
        payload = json.loads(result.content)
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["type"], "tool_error")
        self.assertEqual(payload["tool_name"], "browser_click")
        self.assertEqual(payload["error_type"], "POINTER_INTERCEPTED")
        self.assertTrue(payload["retryable"])
        self.assertIn("browser_snapshot", payload["recovery_instruction"])
        self.assertIn("browser_type", payload["recovery_instruction"])
        self.assertIn("intercepts pointer events", payload["error_message"])

        await manager.close()

    async def test_get_tools_wraps_validation_error_as_structured_tool_message(self) -> None:
        settings = self._build_settings()
        manager = MCPToolsManager(settings, providers=(PLAYWRIGHT_TEST_MCP_PROVIDER,))
        project_dir = (self.root_path / "structured-validation-error").resolve()
        project_dir.mkdir(parents=True, exist_ok=True)
        list_tools = AsyncMock(return_value=[SimpleNamespace(name="browser_click")])
        tool = ValidatingClickTool()

        with (
            patch("deep_agent.tools.mcp_manager.MultiServerMCPClient", FakeClient),
            patch.object(MCPToolsManager, "_list_mcp_tools", list_tools),
            patch("deep_agent.tools.mcp_manager.convert_mcp_tool_to_langchain_tool", return_value=tool),
        ):
            [wrapped_tool] = await manager.get_tools(
                PLAYWRIGHT_TEST_MCP_SERVER_NAME,
                project_dir,
                (f"{PLAYWRIGHT_TEST_MCP_SERVER_NAME}/browser_click",),
            )

        result = await wrapped_tool.arun({}, tool_call_id="call-2")

        self.assertIsInstance(result, ToolMessage)
        self.assertEqual(result.status, "error")
        payload = json.loads(result.content)
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["type"], "tool_error")
        self.assertEqual(payload["tool_name"], "browser_click")
        self.assertEqual(payload["error_type"], "TOOL_ARGS_INVALID")
        self.assertTrue(payload["retryable"])
        self.assertIn("参数", payload["recovery_instruction"])
        self.assertIn("ref", payload["error_message"])
        self.assertIn("intent", payload["error_message"])

        await manager.close()

    async def test_planner_save_plan_creates_parent_dir_after_missing_parent_error_and_retries(self) -> None:
        settings = self._build_settings()
        manager = MCPToolsManager(settings, providers=(PLAYWRIGHT_TEST_MCP_PROVIDER,))
        project_dir = (self.root_path / "planner-save-retry").resolve()
        project_dir.mkdir(parents=True, exist_ok=True)
        list_tools = AsyncMock(return_value=[SimpleNamespace(name="planner_save_plan")])
        tool = ParentMissingPlannerSaveTool(workspace_dir=project_dir)

        with (
            patch("deep_agent.tools.mcp_manager.MultiServerMCPClient", FakeClient),
            patch.object(MCPToolsManager, "_list_mcp_tools", list_tools),
            patch("deep_agent.tools.mcp_manager.convert_mcp_tool_to_langchain_tool", return_value=tool),
        ):
            [wrapped_tool] = await manager.get_tools(
                PLAYWRIGHT_TEST_MCP_SERVER_NAME,
                project_dir,
                (f"{PLAYWRIGHT_TEST_MCP_SERVER_NAME}/planner_save_plan",),
            )

        result = await wrapped_tool.arun(
            {
                "name": "demo",
                "fileName": "test_case/aaaplanning_demo/aaa_demo.md",
                "overview": "overview",
                "suites": [],
            },
            tool_call_id="call-plan",
        )

        self.assertIsInstance(result, ToolMessage)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.content, [{"type": "text", "text": "saved"}])
        self.assertEqual(tool.calls, 2)
        self.assertTrue((project_dir / "test_case" / "aaaplanning_demo").is_dir())

        await manager.close()

    async def test_planner_save_plan_rejects_legacy_path_before_tool_call(self) -> None:
        settings = self._build_settings()
        manager = MCPToolsManager(settings, providers=(PLAYWRIGHT_TEST_MCP_PROVIDER,))
        project_dir = (self.root_path / "planner-save-invalid-path").resolve()
        project_dir.mkdir(parents=True, exist_ok=True)
        list_tools = AsyncMock(return_value=[SimpleNamespace(name="planner_save_plan")])
        tool = ParentMissingPlannerSaveTool(workspace_dir=project_dir)

        with (
            patch("deep_agent.tools.mcp_manager.MultiServerMCPClient", FakeClient),
            patch.object(MCPToolsManager, "_list_mcp_tools", list_tools),
            patch("deep_agent.tools.mcp_manager.convert_mcp_tool_to_langchain_tool", return_value=tool),
        ):
            [wrapped_tool] = await manager.get_tools(
                PLAYWRIGHT_TEST_MCP_SERVER_NAME,
                project_dir,
                (f"{PLAYWRIGHT_TEST_MCP_SERVER_NAME}/planner_save_plan",),
            )

        result = await wrapped_tool.arun(
            {
                "name": "demo",
                "fileName": "test_case/aaa_demo.md",
                "overview": "overview",
                "suites": [],
            },
            tool_call_id="call-plan",
        )

        self.assertIsInstance(result, ToolMessage)
        self.assertEqual(result.status, "error")
        self.assertEqual(tool.calls, 0)
        self.assertFalse((project_dir / "test_case" / "aaaplanning_demo").exists())
        payload = json.loads(result.content)
        self.assertIn("test_case/aaaplanning_demo/aaa_demo.md", payload["error_message"])
        self.assertIn("当前收到：`test_case/aaa_demo.md`", payload["error_message"])

        await manager.close()

    async def test_planner_save_plan_preserves_non_parent_directory_errors(self) -> None:
        settings = self._build_settings()
        manager = MCPToolsManager(settings, providers=(PLAYWRIGHT_TEST_MCP_PROVIDER,))
        project_dir = (self.root_path / "planner-save-permission-denied").resolve()
        project_dir.mkdir(parents=True, exist_ok=True)
        list_tools = AsyncMock(return_value=[SimpleNamespace(name="planner_save_plan")])
        tool = FailingPlannerSaveTool()

        with (
            patch("deep_agent.tools.mcp_manager.MultiServerMCPClient", FakeClient),
            patch.object(MCPToolsManager, "_list_mcp_tools", list_tools),
            patch("deep_agent.tools.mcp_manager.convert_mcp_tool_to_langchain_tool", return_value=tool),
        ):
            [wrapped_tool] = await manager.get_tools(
                PLAYWRIGHT_TEST_MCP_SERVER_NAME,
                project_dir,
                (f"{PLAYWRIGHT_TEST_MCP_SERVER_NAME}/planner_save_plan",),
            )

        result = await wrapped_tool.arun(
            {
                "name": "demo",
                "fileName": "test_case/aaaplanning_demo/aaa_demo.md",
                "overview": "overview",
                "suites": [],
            },
            tool_call_id="call-plan",
        )

        self.assertIsInstance(result, ToolMessage)
        self.assertEqual(result.status, "error")
        self.assertEqual(tool.calls, 1)
        self.assertFalse((project_dir / "test_case" / "aaaplanning_demo").exists())
        payload = json.loads(result.content)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["type"], "tool_error")
        self.assertIn("PERMISSION_DENIED", payload["error_message"])

        await manager.close()

    async def test_planner_save_plan_accepts_content_list_from_raw_tool_implementation(self) -> None:
        settings = self._build_settings()
        manager = MCPToolsManager(settings, providers=(PLAYWRIGHT_TEST_MCP_PROVIDER,))
        project_dir = (self.root_path / "planner-save-list-content").resolve()
        project_dir.mkdir(parents=True, exist_ok=True)
        list_tools = AsyncMock(return_value=[SimpleNamespace(name="planner_save_plan")])
        tool = ListReturningPlannerSaveTool()

        with (
            patch("deep_agent.tools.mcp_manager.MultiServerMCPClient", FakeClient),
            patch.object(MCPToolsManager, "_list_mcp_tools", list_tools),
            patch("deep_agent.tools.mcp_manager.convert_mcp_tool_to_langchain_tool", return_value=tool),
        ):
            [wrapped_tool] = await manager.get_tools(
                PLAYWRIGHT_TEST_MCP_SERVER_NAME,
                project_dir,
                (f"{PLAYWRIGHT_TEST_MCP_SERVER_NAME}/planner_save_plan",),
            )

        result = await wrapped_tool.arun(
            {
                "name": "demo",
                "fileName": "test_case/aaaplanning_demo/aaa_demo.md",
                "overview": "overview",
                "suites": [],
            },
            tool_call_id="call-plan",
        )

        self.assertIsInstance(result, ToolMessage)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.content, [{"type": "text", "text": "saved:test_case/aaaplanning_demo/aaa_demo.md"}])
        self.assertEqual(tool.calls, 1)

        await manager.close()

    def test_playwright_provider_bootstraps_missing_workspace_dependency(self) -> None:
        settings = AppSettings(
            default_automation_project_root=str(self.root_path / "projects"),
            playwright_test_package="@playwright/test@1.59.1",
        )
        project_dir = self.root_path / "playwright-project"

        with patch.object(type(PLAYWRIGHT_TEST_MCP_PROVIDER), "_run_npm", autospec=True) as run_npm:
            PLAYWRIGHT_TEST_MCP_PROVIDER.prepare_workspace(settings, str(project_dir))

        package_json = json.loads((project_dir / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package_json["name"], "playwright-project")
        self.assertTrue(package_json["private"])
        run_npm.assert_called_once_with(
            PLAYWRIGHT_TEST_MCP_PROVIDER,
            ("npm", "install", "--save-dev", "@playwright/test@1.59.1"),
            project_dir.resolve(),
            settings=settings,
        )

    def test_playwright_provider_skips_bootstrap_when_dependency_is_installed(self) -> None:
        settings = AppSettings(default_automation_project_root=str(self.root_path / "projects"))
        project_dir = self.root_path / "installed-project"
        package_dir = project_dir / "node_modules" / "@playwright" / "test"
        package_dir.mkdir(parents=True)
        (project_dir / "package.json").write_text(
            json.dumps({"devDependencies": {"@playwright/test": "^1.59.1"}}),
            encoding="utf-8",
        )
        (package_dir / "package.json").write_text("{}", encoding="utf-8")

        with patch.object(type(PLAYWRIGHT_TEST_MCP_PROVIDER), "_run_npm", autospec=True) as run_npm:
            PLAYWRIGHT_TEST_MCP_PROVIDER.prepare_workspace(settings, str(project_dir))

        run_npm.assert_not_called()

    def test_playwright_provider_sets_skip_browser_download_env_for_npm_install(self) -> None:
        settings = AppSettings(default_automation_project_root=str(self.root_path / "projects"))
        project_dir = self.root_path / "bootstrap-project"
        project_dir.mkdir()

        with (
            patch.dict("deep_agent.tools.playwright.mcp_provider.os.environ", {}, clear=True),
            patch("deep_agent.tools.playwright.mcp_provider.subprocess.run") as run_subprocess,
        ):
            PLAYWRIGHT_TEST_MCP_PROVIDER._run_npm(
                ("npm", "install"),
                project_dir.resolve(),
                settings=settings,
            )

        run_subprocess.assert_called_once()
        _, kwargs = run_subprocess.call_args
        self.assertEqual(kwargs["env"]["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"], "1")

    def test_playwright_provider_can_disable_skip_browser_download_env(self) -> None:
        settings = AppSettings(
            default_automation_project_root=str(self.root_path / "projects"),
            playwright_skip_browser_download=False,
        )
        project_dir = self.root_path / "bootstrap-project-no-skip"
        project_dir.mkdir()

        with (
            patch.dict("deep_agent.tools.playwright.mcp_provider.os.environ", {}, clear=True),
            patch("deep_agent.tools.playwright.mcp_provider.subprocess.run") as run_subprocess,
        ):
            PLAYWRIGHT_TEST_MCP_PROVIDER._run_npm(
                ("npm", "install"),
                project_dir.resolve(),
                settings=settings,
            )

        run_subprocess.assert_called_once()
        _, kwargs = run_subprocess.call_args
        self.assertNotIn("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", kwargs["env"])

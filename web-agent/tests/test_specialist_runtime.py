from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deepagents.backends import FilesystemBackend
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool

from deep_agent.agent.base_agent import BaseSpecialistAgent, SpecialistExecutionContext, SpecialistRuntimeConfig
from deep_agent.core.config import AppSettings
from deep_agent.agent.generator import GENERATOR_RUNTIME_CONFIG, GeneratorAgent
from deep_agent.agent.healer import HEALER_RUNTIME_CONFIG, HealerAgent
from deep_agent.agent.plan import PLAN_RUNTIME_CONFIG, PlanAgent
from deep_agent.tools.playwright import PLAYWRIGHT_TEST_MCP_SERVER_NAME


class DummyTool(BaseTool):
    name: str
    description: str = "dummy"

    def _run(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return "ok"

    async def _arun(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return "ok"


class FakeMCPManager:
    def __init__(self, tools: list[BaseTool]) -> None:
        self.tools = {tool.name: tool for tool in tools}
        self.server_names: list[str] = []
        self.workspace_dirs: list[Path | None] = []
        self.allowed_tool_ids: list[tuple[str, ...] | None] = []

    async def get_tools(self, server_name, workspace_dir=None, allowed_tool_ids=None):  # noqa: ANN001
        self.server_names.append(server_name)
        self.workspace_dirs.append(workspace_dir)
        normalized_ids = None if allowed_tool_ids is None else tuple(allowed_tool_ids)
        self.allowed_tool_ids.append(normalized_ids)
        if not allowed_tool_ids:
            return list(self.tools.values())

        filtered_tools: list[BaseTool] = []
        for tool_id in allowed_tool_ids:
            _, _, tool_name = tool_id.partition("/")
            tool = self.tools.get(tool_name)
            if tool is not None:
                filtered_tools.append(tool)
        return filtered_tools


class TemplateBackedPlanAgent(PlanAgent):
    def __init__(
        self,
        settings: AppSettings,
        template_dir: Path,
        mcp_manager: FakeMCPManager | None = None,
    ) -> None:
        super().__init__(settings, mcp_manager=mcp_manager)
        self._template_dir = template_dir

    def _bundled_demo_template_dir(self) -> Path:
        return self._template_dir


class TemplateBackedGeneratorAgent(GeneratorAgent):
    def __init__(
        self,
        settings: AppSettings,
        template_dir: Path,
        mcp_manager: FakeMCPManager | None = None,
    ) -> None:
        super().__init__(settings, mcp_manager=mcp_manager)
        self._template_dir = template_dir

    def _bundled_demo_template_dir(self) -> Path:
        return self._template_dir


class TemplateBackedHealerAgent(HealerAgent):
    def __init__(
        self,
        settings: AppSettings,
        template_dir: Path,
        mcp_manager: FakeMCPManager | None = None,
    ) -> None:
        super().__init__(settings, mcp_manager=mcp_manager)
        self._template_dir = template_dir

    def _bundled_demo_template_dir(self) -> Path:
        return self._template_dir


class FakeInvokeAgent:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.inputs: list[tuple[dict, dict | None]] = []

    async def ainvoke(self, input_data, config=None):  # noqa: ANN001
        self.inputs.append((input_data, config))
        return self.result


class FakeStreamAgent:
    def __init__(self, result: dict, *, raise_on_stream: Exception | None = None) -> None:
        self.result = result
        self.raise_on_stream = raise_on_stream
        self.inputs: list[tuple[dict, dict | None, str | None]] = []

    async def astream_events(self, input_data, config=None, version=None):  # noqa: ANN001
        self.inputs.append((input_data, config, version))
        yield {
            "event": "on_chat_model_start",
            "name": "generator-specialist",
            "parent_ids": [],
            "data": {"input": input_data},
        }
        if self.raise_on_stream is not None:
            raise self.raise_on_stream
        yield {
            "event": "on_chain_end",
            "name": "generator-specialist",
            "parent_ids": [],
            "data": {"output": self.result},
        }


class DefaultRuntimeAgent(BaseSpecialistAgent):
    agent_type = "default_runtime"
    display_name = "Default Runtime Agent"
    runtime_config = SpecialistRuntimeConfig(system_prompt_parts=("system",), load_project_standard=False)


class SpecialistRuntimeTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _build_settings(self) -> AppSettings:
        return AppSettings(
            default_automation_project_root=str(self.root_path / "projects"),
        )

    def _create_template_dir(self) -> Path:
        template_dir = self.root_path / "bundled-template"
        (template_dir / "test_case" / "shared").mkdir(parents=True, exist_ok=True)
        (template_dir / "package.json").write_text('{"name":"demo"}', encoding="utf-8")
        (template_dir / "playwright.config.ts").write_text("export default {};\n", encoding="utf-8")
        (template_dir / "test_case" / "shared" / "base-test.ts").write_text("export const base = true;\n", encoding="utf-8")
        return template_dir

    def test_openai_compatible_base_url_disables_responses_api(self) -> None:
        settings = AppSettings(
            openai_api_key="test-key",
            openai_base_url=" https://open.bigmodel.cn/api/paas/v4/ ",
        )

        kwargs = settings.build_model_kwargs("openai:gpt-5.4")

        self.assertEqual(kwargs["base_url"], "https://open.bigmodel.cn/api/paas/v4/")
        self.assertFalse(kwargs["use_responses_api"])

    def test_specialist_agent_uses_configured_model_instance(self) -> None:
        settings = AppSettings(
            specialist_model="openai:gpt-5.4",
            openai_api_key="test-key",
            openai_base_url="https://open.bigmodel.cn/api/paas/v4/",
        )
        agent = PlanAgent(settings, mcp_manager=FakeMCPManager([]))
        context = SpecialistExecutionContext(
            workspace_dir=None,
            system_prompt="system",
            tools=[],
            trace_context={},
        )
        fake_model = object()
        fake_deep_agent = object()

        with (
            patch("deep_agent.agent.base_agent.init_chat_model", return_value=fake_model) as init_model_mock,
            patch("deep_agent.agent.base_agent.create_deep_agent", return_value=fake_deep_agent) as create_agent_mock,
        ):
            result = agent._create_specialist_agent(context)

        self.assertIs(result, fake_deep_agent)
        self.assertEqual(init_model_mock.call_args.kwargs["model"], "openai:gpt-5.4")
        self.assertFalse(init_model_mock.call_args.kwargs["use_responses_api"])
        self.assertEqual(create_agent_mock.call_args.kwargs["model"], fake_model)
        self.assertIsNone(create_agent_mock.call_args.kwargs["backend"])
        self.assertIsNone(create_agent_mock.call_args.kwargs["permissions"])

    async def test_base_specialist_runtime_passes_configured_recursion_limit(self) -> None:
        agent = DefaultRuntimeAgent(
            AppSettings(
                default_automation_project_root=str(self.root_path / "projects"),
                specialist_recursion_limit=123,
            ),
            mcp_manager=FakeMCPManager([]),
        )
        fake_agent = FakeInvokeAgent({"messages": [AIMessage(content="runtime-finished")]})
        execution_context = SpecialistExecutionContext(
            workspace_dir=None,
            system_prompt="system",
            tools=[],
            trace_context={},
        )

        result = await agent._run_deep_agent(fake_agent, {"messages": []}, execution_context)

        self.assertEqual(result["messages"][0].content, "runtime-finished")
        self.assertEqual(fake_agent.inputs[0][0], {"messages": []})
        self.assertEqual(fake_agent.inputs[0][1]["recursion_limit"], 123)

    def test_plan_validation_rejects_null_like_project_name(self) -> None:
        agent = PlanAgent(self._build_settings(), mcp_manager=FakeMCPManager([]))

        result = agent._validate_extracted_params({"extracted_params": {"project_name": "null", "url": "https://example.com"}})

        self.assertEqual(result, "Plan 模式缺少自动化工程名字。请补充工程名字后再继续。")

    def test_plan_validation_rejects_null_like_url(self) -> None:
        agent = PlanAgent(self._build_settings(), mcp_manager=FakeMCPManager([]))

        result = agent._validate_extracted_params({"extracted_params": {"project_name": "demo", "url": "undefined"}})

        self.assertEqual(result, "Plan 模式缺少被测页面 URL。请补充完整 URL 后再继续。")

    def _build_plan_tools(self) -> list[BaseTool]:
        return [
            DummyTool(name="browser_navigate"),
            DummyTool(name="browser_run_code"),
            DummyTool(name="planner_setup_page"),
            DummyTool(name="planner_save_plan"),
            DummyTool(name="planner_submit_plan"),
            DummyTool(name="generator_setup_page"),
        ]

    def _build_generator_tools(self) -> list[BaseTool]:
        return [
            DummyTool(name="browser_click"),
            DummyTool(name="browser_navigate"),
            DummyTool(name="browser_run_code"),
            DummyTool(name="browser_snapshot"),
            DummyTool(name="generator_setup_page"),
            DummyTool(name="generator_read_log"),
            DummyTool(name="generator_write_test"),
        ]

    def _build_healer_tools(self) -> list[BaseTool]:
        return [
            DummyTool(name="browser_console_messages"),
            DummyTool(name="browser_evaluate"),
            DummyTool(name="browser_generate_locator"),
            DummyTool(name="browser_network_requests"),
            DummyTool(name="browser_snapshot"),
            DummyTool(name="test_debug"),
            DummyTool(name="test_list"),
            DummyTool(name="test_run"),
        ]

    def _create_generator_plan_file(
        self,
        project_dir: Path,
        relative_path: str,
        *,
        case_names: list[str] | None = None,
    ) -> Path:
        plan_file = project_dir / relative_path
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        resolved_case_names = case_names or ["a_case"]
        plan_identifier = plan_file.stem.removeprefix("aaa_")
        planning_dir = plan_file.parent.name
        scenario_blocks = []
        for index, case_name in enumerate(resolved_case_names, start=1):
            scenario_blocks.append(
                "\n".join(
                    [
                        f"#### 1.{index}. {case_name}",
                        "",
                        f"**File:** `test_case/{planning_dir}/{case_name}.spec.ts`",
                        "",
                        "**Steps:**",
                        "1. 执行步骤",
                        "   - expect:",
                        "     - 断言结果",
                    ]
                )
            )
        plan_file.write_text(
            "\n".join(
                [
                    f"# {plan_identifier} Plan",
                    "",
                    "## Test Scenarios",
                    "",
                    "### 1. Demo Suite",
                    "**Seed:** `test_case/seed.spec.ts`",
                    "",
                    "\n\n".join(scenario_blocks),
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return plan_file

    def _create_healer_script_file(self, project_dir: Path, relative_path: str) -> Path:
        script_file = project_dir / relative_path
        script_file.parent.mkdir(parents=True, exist_ok=True)
        script_file.write_text("import { test } from '@playwright/test';\n", encoding="utf-8")
        return script_file

    async def test_plan_uses_project_name_under_default_root_and_bootstraps_template(self) -> None:
        manager = FakeMCPManager(self._build_plan_tools())
        template_dir = self._create_template_dir()
        agent = TemplateBackedPlanAgent(self._build_settings(), template_dir, mcp_manager=manager)

        context = await agent._prepare_execution(
            {"extracted_params": {"project_name": "baidu-demo", "url": "https://example.com"}}
        )

        expected_dir = (self.root_path / "projects" / "baidu-demo").resolve()
        demo_dir = (self.root_path / "projects" / "demo").resolve()
        self.assertEqual(context.workspace_dir, expected_dir)
        self.assertTrue(expected_dir.is_dir())
        self.assertTrue((expected_dir / "package.json").is_file())
        self.assertTrue((expected_dir / "test_case" / "shared" / "base-test.ts").is_file())
        self.assertTrue(demo_dir.is_dir())
        self.assertEqual(manager.server_names, [PLAYWRIGHT_TEST_MCP_SERVER_NAME])
        self.assertEqual(manager.workspace_dirs, [expected_dir])
        self.assertEqual(manager.allowed_tool_ids, [PLAN_RUNTIME_CONFIG.allowed_playwright_test_mcp_tools])

    async def test_plan_uses_explicit_project_dir_and_loads_project_standard(self) -> None:
        project_dir = self.root_path / "custom-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "web_standard.md").write_text("# 项目规范\n只允许保存到 test_case。", encoding="utf-8")

        manager = FakeMCPManager(self._build_plan_tools())
        template_dir = self._create_template_dir()
        settings = AppSettings(
            default_automation_project_root=str(self.root_path / "projects"),
            agent_debug_trace=True,
            agent_debug_full_messages=True,
            agent_debug_max_chars=12000,
        )
        agent = TemplateBackedPlanAgent(settings, template_dir, mcp_manager=manager)

        with self.assertLogs("deep_agent.agent.base_agent", level="INFO") as log_capture:
            context = await agent._prepare_execution(
                {
                    "extracted_params": {
                        "project_name": "custom-project",
                        "project_dir": str(project_dir),
                        "url": "https://example.com",
                        "feature_points": ["登录", "登出"],
                    }
                }
            )

        self.assertEqual(context.workspace_dir, project_dir.resolve())
        self.assertEqual(manager.server_names, [PLAYWRIGHT_TEST_MCP_SERVER_NAME])
        self.assertIn("# 项目规范", context.system_prompt)
        self.assertIn("https://example.com", context.system_prompt)
        self.assertIn("planner_save_plan", context.system_prompt)
        combined_logs = "\n".join(log_capture.output)
        self.assertIn("event=specialist_context", combined_logs)
        self.assertIn("system_prompt", combined_logs)
        self.assertIn("planner_save_plan", combined_logs)
        self.assertIn("browser_navigate", combined_logs)

    async def test_generator_resolves_relative_project_dir_under_automation_root(self) -> None:
        manager = FakeMCPManager(self._build_generator_tools())
        template_dir = self._create_template_dir()
        settings = AppSettings(default_automation_project_root=str(self.root_path / "projects"))
        agent = TemplateBackedGeneratorAgent(settings, template_dir, mcp_manager=manager)
        relative_plan_path = "test_case/aaaplanning_baidu-demo/aaa_baidu-demo.md"
        expected_dir = (self.root_path / "projects" / "baidu-demo").resolve()
        self._create_generator_plan_file(expected_dir, relative_plan_path)

        context = await agent._prepare_execution(
            {
                "extracted_params": {
                    "project_dir": "baidu-demo",
                    "test_plan_files": [str(expected_dir / relative_plan_path)],
                }
            }
        )

        self.assertEqual(context.workspace_dir, expected_dir)
        self.assertEqual(manager.workspace_dirs, [expected_dir])
        self.assertIn(str(expected_dir / relative_plan_path), context.system_prompt)

    async def test_plan_filters_tools_to_whitelist(self) -> None:
        manager = FakeMCPManager(self._build_plan_tools())
        template_dir = self._create_template_dir()
        agent = TemplateBackedPlanAgent(self._build_settings(), template_dir, mcp_manager=manager)

        context = await agent._prepare_execution(
            {"extracted_params": {"project_name": "baidu-demo", "url": "https://example.com"}}
        )

        self.assertEqual(
            [tool.name for tool in context.tools],
            ["browser_navigate", "browser_run_code", "planner_setup_page", "planner_save_plan"],
        )
        self.assertEqual(manager.server_names, [PLAYWRIGHT_TEST_MCP_SERVER_NAME])

    async def test_plan_prompt_includes_tool_error_recovery_rules(self) -> None:
        manager = FakeMCPManager(self._build_plan_tools())
        template_dir = self._create_template_dir()
        agent = TemplateBackedPlanAgent(self._build_settings(), template_dir, mcp_manager=manager)

        context = await agent._prepare_execution(
            {"extracted_params": {"project_name": "baidu-demo", "url": "https://example.com"}}
        )

        self.assertIn("ok=false", context.system_prompt)
        self.assertIn("type=tool_error", context.system_prompt)
        self.assertIn("禁止重复完全相同的工具调用和参数", context.system_prompt)
        self.assertIn("browser_snapshot", context.system_prompt)
        self.assertIn("browser_type", context.system_prompt)

    async def test_plan_reuses_existing_project_dir_without_recopying(self) -> None:
        existing_project_dir = self.root_path / "projects" / "existing-project"
        existing_project_dir.mkdir(parents=True, exist_ok=True)
        (existing_project_dir / "marker.txt").write_text("keep", encoding="utf-8")
        manager = FakeMCPManager(self._build_plan_tools())
        template_dir = self._create_template_dir()
        agent = TemplateBackedPlanAgent(self._build_settings(), template_dir, mcp_manager=manager)

        context = await agent._prepare_execution(
            {"extracted_params": {"project_name": "existing-project", "url": "https://example.com"}}
        )

        self.assertEqual(context.workspace_dir, existing_project_dir.resolve())
        self.assertEqual((existing_project_dir / "marker.txt").read_text(encoding="utf-8"), "keep")

    def test_generator_validation_requires_project_identifier_and_test_plan_files(self) -> None:
        agent = GeneratorAgent(self._build_settings(), mcp_manager=FakeMCPManager([]))

        self.assertEqual(
            agent._validate_extracted_params({"extracted_params": {"test_plan_files": ["test_case/demo/aaa_demo.md"]}}),
            "Generator 模式缺少自动化工程目录。请提供 `project_dir`，或至少提供 `project_name` 以便按 Plan 规则推导目录。",
        )
        self.assertEqual(
            agent._validate_extracted_params({"extracted_params": {"project_name": "demo-project"}}),
            "Generator 模式缺少待生成脚本的测试计划文件或文件夹。请至少提供 1 个 `test_plan_files` 条目后再继续。",
        )

    def test_healer_validation_requires_project_identifier_and_test_scripts(self) -> None:
        agent = HealerAgent(self._build_settings(), mcp_manager=FakeMCPManager([]))

        self.assertEqual(
            agent._validate_extracted_params({"extracted_params": {"test_scripts": ["test_case/demo/a_case.spec.ts"]}}),
            "Healer 模式缺少自动化工程目录。请提供 `project_dir`，或至少提供 `project_name` 以便按 Generator 规则推导目录。",
        )
        self.assertEqual(
            agent._validate_extracted_params({"extracted_params": {"project_name": "demo-project"}}),
            "Healer 模式缺少待调试脚本文件或文件夹。请至少提供 1 个 `test_scripts` 条目后再继续。",
        )

    async def test_generator_uses_project_name_under_default_root_and_bootstraps_template(self) -> None:
        manager = FakeMCPManager(self._build_generator_tools())
        template_dir = self._create_template_dir()
        agent = TemplateBackedGeneratorAgent(self._build_settings(), template_dir, mcp_manager=manager)
        relative_plan_path = "test_case/aaaplanning_baidu-demo/aaa_baidu-demo.md"

        workspace_dir = agent._resolve_workspace_dir({"extracted_params": {"project_name": "baidu-demo"}})
        self._create_generator_plan_file(workspace_dir, relative_plan_path)

        context = await agent._prepare_execution(
            {
                "extracted_params": {
                    "project_name": "baidu-demo",
                    "test_plan_files": [relative_plan_path],
                }
            }
        )

        expected_dir = (self.root_path / "projects" / "baidu-demo").resolve()
        demo_dir = (self.root_path / "projects" / "demo").resolve()
        self.assertEqual(context.workspace_dir, expected_dir)
        self.assertTrue(expected_dir.is_dir())
        self.assertTrue((expected_dir / "package.json").is_file())
        self.assertTrue((expected_dir / "test_case" / "shared" / "base-test.ts").is_file())
        self.assertTrue(demo_dir.is_dir())
        self.assertIn(relative_plan_path, context.system_prompt)
        self.assertIn("generator_setup_page", context.system_prompt)
        self.assertIn("expected_test_scripts", context.system_prompt)
        self.assertIn("expected_case_count", context.system_prompt)
        self.assertIn("test_case/baidu-demo/a_case.spec.ts", context.system_prompt)
        self.assertNotIn("## 完成条件", context.system_prompt)
        self.assertIn("## 额外运行时约束", context.system_prompt)
        self.assertEqual(manager.server_names, [PLAYWRIGHT_TEST_MCP_SERVER_NAME])
        self.assertEqual(manager.workspace_dirs, [expected_dir])
        self.assertEqual(manager.allowed_tool_ids, [GENERATOR_RUNTIME_CONFIG.allowed_playwright_test_mcp_tools])

    async def test_generator_accepts_directory_input_and_expands_to_plan_files(self) -> None:
        manager = FakeMCPManager(self._build_generator_tools())
        template_dir = self._create_template_dir()
        agent = TemplateBackedGeneratorAgent(self._build_settings(), template_dir, mcp_manager=manager)
        relative_plan_dir = "test_case/aaaplanning_baidu-demo"
        relative_plan_path = f"{relative_plan_dir}/aaa_baidu_demo.md"

        workspace_dir = agent._resolve_workspace_dir({"extracted_params": {"project_name": "baidu-demo"}})
        self._create_generator_plan_file(workspace_dir, relative_plan_path)
        (workspace_dir / relative_plan_dir / "README.md").write_text("# ignored\n", encoding="utf-8")

        context = await agent._prepare_execution(
            {
                "extracted_params": {
                    "project_name": "baidu-demo",
                    "test_plan_files": [relative_plan_dir],
                }
            }
        )

        self.assertIn(relative_plan_path, context.system_prompt)
        self.assertNotIn("README.md", context.system_prompt)
        self.assertIn("test_case/baidu-demo/a_case.spec.ts", context.system_prompt)

    async def test_generator_and_healer_prompts_include_system_prompt_parts(self) -> None:
        project_dir = self.root_path / "generator-project"
        relative_plan_path = "test_case/aaaplanning_demo/aaa_demo.md"
        relative_script_path = "test_case/demo/a_case.spec.ts"
        self._create_generator_plan_file(project_dir, relative_plan_path)
        self._create_healer_script_file(project_dir, relative_script_path)
        tools = self._build_generator_tools() + self._build_healer_tools()
        manager = FakeMCPManager(tools)
        settings = self._build_settings()

        generator_context = await GeneratorAgent(settings, mcp_manager=manager)._prepare_execution(
            {
                "extracted_params": {
                    "project_dir": str(project_dir),
                    "test_plan_files": [relative_plan_path],
                }
            }
        )
        healer_context = await HealerAgent(settings, mcp_manager=manager)._prepare_execution(
            {
                "extracted_params": {
                    "project_dir": str(project_dir),
                    "test_scripts": [relative_script_path],
                }
            }
        )

        self.assertIn("# Generator 阶段移动端业务约束", generator_context.system_prompt)
        self.assertIn("# Healer 阶段移动端修复约定", healer_context.system_prompt)
        self.assertIn("## 文件查询约束", generator_context.system_prompt)
        self.assertIn("## 文件查询约束", healer_context.system_prompt)
        self.assertIn("先使用 `ls` 观察候选目录结构", generator_context.system_prompt)
        self.assertIn("`.playwright-mcp/`", healer_context.system_prompt)
        self.assertIn(relative_plan_path, generator_context.system_prompt)
        self.assertIn(relative_script_path, healer_context.system_prompt)
        self.assertEqual(
            manager.server_names,
            [PLAYWRIGHT_TEST_MCP_SERVER_NAME, PLAYWRIGHT_TEST_MCP_SERVER_NAME],
        )
        self.assertEqual(manager.allowed_tool_ids[0], GENERATOR_RUNTIME_CONFIG.allowed_playwright_test_mcp_tools)
        self.assertEqual(manager.allowed_tool_ids[1], HEALER_RUNTIME_CONFIG.allowed_playwright_test_mcp_tools)

    async def test_plan_prompt_includes_query_constraints(self) -> None:
        manager = FakeMCPManager(self._build_plan_tools())
        template_dir = self._create_template_dir()
        agent = TemplateBackedPlanAgent(self._build_settings(), template_dir, mcp_manager=manager)

        context = await agent._prepare_execution(
            {
                "extracted_params": {
                    "project_name": "plan-query-demo",
                    "url": "https://example.com",
                    "feature_points": ["搜索"],
                }
            }
        )

        self.assertIn("## 文件查询约束", context.system_prompt)
        self.assertIn("先使用 `ls` 观察候选目录结构", context.system_prompt)
        self.assertIn("不要对整个 `project_dir` 做递归搜索", context.system_prompt)

    async def test_healer_uses_project_name_under_default_root_and_bootstraps_template(self) -> None:
        manager = FakeMCPManager(self._build_healer_tools())
        template_dir = self._create_template_dir()
        agent = TemplateBackedHealerAgent(self._build_settings(), template_dir, mcp_manager=manager)
        relative_script_path = "test_case/demo/a_case.spec.ts"

        workspace_dir = agent._resolve_workspace_dir({"extracted_params": {"project_name": "baidu-demo"}})
        self._create_healer_script_file(workspace_dir, relative_script_path)

        context = await agent._prepare_execution(
            {
                "extracted_params": {
                    "project_name": "baidu-demo",
                    "test_scripts": [relative_script_path],
                }
            }
        )

        expected_dir = (self.root_path / "projects" / "baidu-demo").resolve()
        demo_dir = (self.root_path / "projects" / "demo").resolve()
        self.assertEqual(context.workspace_dir, expected_dir)
        self.assertTrue(expected_dir.is_dir())
        self.assertTrue((expected_dir / "package.json").is_file())
        self.assertTrue((expected_dir / "test_case" / "shared" / "base-test.ts").is_file())
        self.assertTrue(demo_dir.is_dir())
        self.assertIn(relative_script_path, context.system_prompt)
        self.assertIn("test.fixme()", context.system_prompt)
        self.assertEqual(manager.server_names, [PLAYWRIGHT_TEST_MCP_SERVER_NAME])
        self.assertEqual(manager.workspace_dirs, [expected_dir])
        self.assertEqual(manager.allowed_tool_ids, [HEALER_RUNTIME_CONFIG.allowed_playwright_test_mcp_tools])

    async def test_healer_accepts_directory_input_and_expands_to_test_scripts(self) -> None:
        manager = FakeMCPManager(self._build_healer_tools())
        template_dir = self._create_template_dir()
        agent = TemplateBackedHealerAgent(self._build_settings(), template_dir, mcp_manager=manager)
        relative_script_dir = "test_case/demo"
        first_script = f"{relative_script_dir}/a_case.spec.ts"
        second_script = f"{relative_script_dir}/b_case.spec.ts"

        workspace_dir = agent._resolve_workspace_dir({"extracted_params": {"project_name": "baidu-demo"}})
        self._create_healer_script_file(workspace_dir, first_script)
        self._create_healer_script_file(workspace_dir, second_script)
        (workspace_dir / relative_script_dir / "note.md").write_text("# ignored\n", encoding="utf-8")

        context = await agent._prepare_execution(
            {
                "extracted_params": {
                    "project_name": "baidu-demo",
                    "test_scripts": [relative_script_dir],
                }
            }
        )

        self.assertIn(first_script, context.system_prompt)
        self.assertIn(second_script, context.system_prompt)
        self.assertNotIn("note.md", context.system_prompt)

    async def test_generator_execute_uses_deep_agent_runtime(self) -> None:
        project_dir = self.root_path / "generator-runtime"
        relative_plan_path = "test_case/aaaplanning_demo/aaa_demo.md"
        self._create_generator_plan_file(project_dir, relative_plan_path)
        manager = FakeMCPManager(self._build_generator_tools())
        agent = GeneratorAgent(self._build_settings(), mcp_manager=manager)

        class FakeGeneratorStreamAgent:
            def __init__(self) -> None:
                self.inputs: list[tuple[dict, dict | None, str | None]] = []

            async def astream_events(self, input_data, config=None, version=None):  # noqa: ANN001
                self.inputs.append((input_data, config, version))
                yield {
                    "event": "on_tool_start",
                    "name": "generator_write_test",
                    "parent_ids": [],
                    "data": {
                        "input": {
                            "fileName": "test_case/demo/a_case.spec.ts",
                            "code": (
                                "// spec: test_case/aaaplanning_demo/aaa_demo.md\n"
                                "test.describe('Demo', () => {\n"
                                "  test('a_case', async () => {});\n"
                                "});\n"
                            ),
                        }
                    },
                }
                yield {
                    "event": "on_tool_end",
                    "name": "generator_write_test",
                    "parent_ids": [],
                    "data": {"output": {"status": "success", "content": "ok"}},
                }
                yield {
                    "event": "on_chain_end",
                    "name": "generator-specialist",
                    "parent_ids": [],
                    "data": {"output": {"messages": [AIMessage(content="existing"), AIMessage(content="generator-finished")] }},
                }

        fake_deep_agent = FakeGeneratorStreamAgent()

        with (
            patch("deep_agent.agent.base_agent.init_chat_model", return_value=object()),
            patch("deep_agent.agent.base_agent.create_deep_agent", return_value=fake_deep_agent),
        ):
            result = await agent.execute(
                {
                    "messages": [AIMessage(content="existing")],
                    "extracted_params": {
                        "project_dir": str(project_dir),
                        "test_plan_files": [relative_plan_path],
                    },
                }
            )

        self.assertIn("Generator 阶段", result["messages"][0].content)
        self.assertIn("a_case.spec.ts", result["messages"][0].content)
        self.assertEqual(fake_deep_agent.inputs[0][0]["messages"][0].content, "existing")
        self.assertEqual(fake_deep_agent.inputs[0][2], "v2")

    async def test_generator_execute_fails_when_only_subset_of_expected_scripts_are_written(self) -> None:
        project_dir = self.root_path / "generator-partial"
        relative_plan_path = "test_case/aaaplanning_demo/aaa_demo.md"
        self._create_generator_plan_file(project_dir, relative_plan_path, case_names=["a_case", "b_case"])
        manager = FakeMCPManager(self._build_generator_tools())
        agent = GeneratorAgent(self._build_settings(), mcp_manager=manager)

        class FakePartialStreamAgent:
            async def astream_events(self, input_data, config=None, version=None):  # noqa: ANN001
                yield {
                    "event": "on_tool_start",
                    "name": "generator_write_test",
                    "parent_ids": [],
                    "data": {
                        "input": {
                            "fileName": "test_case/demo/a_case.spec.ts",
                            "code": (
                                "// spec: test_case/aaaplanning_demo/aaa_demo.md\n"
                                "test.describe('Demo', () => {\n"
                                "  test('a_case', async () => {});\n"
                                "});\n"
                            ),
                        }
                    },
                }
                yield {
                    "event": "on_tool_end",
                    "name": "generator_write_test",
                    "parent_ids": [],
                    "data": {"output": {"status": "success", "content": "ok"}},
                }
                yield {
                    "event": "on_chain_end",
                    "name": "generator-specialist",
                    "parent_ids": [],
                    "data": {"output": {"messages": [AIMessage(content="generator-finished")] }},
                }

        with (
            patch("deep_agent.agent.base_agent.init_chat_model", return_value=object()),
            patch("deep_agent.agent.base_agent.create_deep_agent", return_value=FakePartialStreamAgent()),
        ):
            result = await agent.execute(
                {
                    "messages": [],
                    "extracted_params": {
                        "project_dir": str(project_dir),
                        "test_plan_files": [relative_plan_path],
                    },
                }
            )

        self.assertIn("Generator 阶段", result["messages"][0].content)
        self.assertIn("状态：exception", result["messages"][0].content)
        self.assertIn("test_case/demo/b_case.spec.ts", result["messages"][0].content)

    async def test_generator_execute_succeeds_when_all_expected_scripts_are_written(self) -> None:
        project_dir = self.root_path / "generator-complete"
        relative_plan_path = "test_case/aaaplanning_demo/aaa_demo.md"
        self._create_generator_plan_file(project_dir, relative_plan_path, case_names=["a_case", "b_case"])
        manager = FakeMCPManager(self._build_generator_tools())
        agent = GeneratorAgent(self._build_settings(), mcp_manager=manager)

        class FakeCompleteStreamAgent:
            async def astream_events(self, input_data, config=None, version=None):  # noqa: ANN001
                for case_name in ("a_case", "b_case"):
                    yield {
                        "event": "on_tool_start",
                        "name": "generator_write_test",
                        "parent_ids": [],
                        "data": {
                            "input": {
                                "fileName": f"test_case/demo/{case_name}.spec.ts",
                                "code": (
                                    "// spec: test_case/aaaplanning_demo/aaa_demo.md\n"
                                    "test.describe('Demo', () => {\n"
                                    f"  test('{case_name}', async () => {{}});\n"
                                    "});\n"
                                ),
                            }
                        },
                    }
                    yield {
                        "event": "on_tool_end",
                        "name": "generator_write_test",
                        "parent_ids": [],
                        "data": {"output": {"status": "success", "content": "ok"}},
                    }
                yield {
                    "event": "on_chain_end",
                    "name": "generator-specialist",
                    "parent_ids": [],
                    "data": {"output": {"messages": [AIMessage(content="generator-finished")] }},
                }

        with (
            patch("deep_agent.agent.base_agent.init_chat_model", return_value=object()),
            patch("deep_agent.agent.base_agent.create_deep_agent", return_value=FakeCompleteStreamAgent()),
        ):
            result = await agent.execute(
                {
                    "messages": [],
                    "extracted_params": {
                        "project_dir": str(project_dir),
                        "test_plan_files": [relative_plan_path],
                    },
                }
            )

        self.assertIn("Generator 阶段", result["messages"][0].content)
        self.assertIn("a_case.spec.ts", result["messages"][0].content)
        self.assertIn("b_case.spec.ts", result["messages"][0].content)
        self.assertEqual(
            result["latest_artifacts"]["generator"]["output_files"],
            ["test_case/demo/a_case.spec.ts", "test_case/demo/b_case.spec.ts"],
        )

    async def test_generator_execute_only_requires_requested_test_case_subset(self) -> None:
        project_dir = self.root_path / "generator-subset"
        relative_plan_path = "test_case/aaaplanning_demo/aaa_demo.md"
        self._create_generator_plan_file(project_dir, relative_plan_path, case_names=["a_case", "b_case", "c_case"])
        manager = FakeMCPManager(self._build_generator_tools())
        agent = GeneratorAgent(self._build_settings(), mcp_manager=manager)

        class FakeSubsetStreamAgent:
            async def astream_events(self, input_data, config=None, version=None):  # noqa: ANN001
                yield {
                    "event": "on_tool_start",
                    "name": "generator_write_test",
                    "parent_ids": [],
                    "data": {
                        "input": {
                            "fileName": "test_case/demo/b_case.spec.ts",
                            "code": (
                                "// spec: test_case/aaaplanning_demo/aaa_demo.md\n"
                                "test.describe('Demo', () => {\n"
                                "  test('b_case', async () => {});\n"
                                "});\n"
                            ),
                        }
                    },
                }
                yield {
                    "event": "on_tool_end",
                    "name": "generator_write_test",
                    "parent_ids": [],
                    "data": {"output": {"status": "success", "content": "ok"}},
                }
                yield {
                    "event": "on_chain_end",
                    "name": "generator-specialist",
                    "parent_ids": [],
                    "data": {"output": {"messages": [AIMessage(content="generator-finished")] }},
                }

        with (
            patch("deep_agent.agent.base_agent.init_chat_model", return_value=object()),
            patch("deep_agent.agent.base_agent.create_deep_agent", return_value=FakeSubsetStreamAgent()),
        ):
            result = await agent.execute(
                {
                    "messages": [],
                    "extracted_params": {
                        "project_dir": str(project_dir),
                        "test_plan_files": [relative_plan_path],
                        "test_cases": ["b_case"],
                    },
                }
            )

        self.assertIn("Generator 阶段", result["messages"][0].content)
        self.assertNotIn("状态：exception", result["messages"][0].content)
        self.assertEqual(
            result["latest_artifacts"]["generator"]["output_files"],
            ["test_case/demo/b_case.spec.ts"],
        )

    async def test_generator_execute_treats_expected_browser_close_after_write_as_success(self) -> None:
        project_dir = self.root_path / "generator-close"
        relative_plan_path = "test_case/aaaplanning_demo/aaa_demo.md"
        self._create_generator_plan_file(project_dir, relative_plan_path)
        manager = FakeMCPManager(self._build_generator_tools())
        agent = GeneratorAgent(self._build_settings(), mcp_manager=manager)

        class FakeCloseStreamAgent:
            def __init__(self) -> None:
                self.inputs: list[tuple[dict, dict | None, str | None]] = []

            async def astream_events(self, input_data, config=None, version=None):  # noqa: ANN001
                self.inputs.append((input_data, config, version))
                yield {
                    "event": "on_tool_start",
                    "name": "generator_write_test",
                    "parent_ids": [],
                    "data": {
                        "input": {
                            "fileName": "test_case/demo/a_case.spec.ts",
                            "code": (
                                "// spec: test_case/aaaplanning_demo/aaa_demo.md\n"
                                "test.describe('Demo', () => {\n"
                                "  test('a_case', async () => {});\n"
                                "});\n"
                            ),
                        }
                    },
                }
                yield {
                    "event": "on_tool_end",
                    "name": "generator_write_test",
                    "parent_ids": [],
                    "data": {"output": {"status": "success", "content": "ok"}},
                }
                raise RuntimeError("Target page, context or browser has been closed")

        fake_deep_agent = FakeCloseStreamAgent()

        with (
            patch("deep_agent.agent.base_agent.init_chat_model", return_value=object()),
            patch("deep_agent.agent.base_agent.create_deep_agent", return_value=fake_deep_agent),
        ):
            result = await agent.execute(
                {
                    "messages": [AIMessage(content="existing")],
                    "extracted_params": {
                        "project_dir": str(project_dir),
                        "test_plan_files": [relative_plan_path],
                    },
                }
            )

        self.assertIn("Generator 阶段", result["messages"][0].content)
        self.assertIn("a_case.spec.ts", result["messages"][0].content)

    async def test_generator_runtime_binds_real_workspace_backend_for_deepagents_filesystem_tools(self) -> None:
        project_dir = self.root_path / "generator-backend"
        relative_plan_path = "test_case/aaaplanning_demo/aaa_demo.md"
        self._create_generator_plan_file(project_dir, relative_plan_path)
        manager = FakeMCPManager(self._build_generator_tools())
        agent = GeneratorAgent(self._build_settings(), mcp_manager=manager)
        context = await agent._prepare_execution(
            {
                "extracted_params": {
                    "project_dir": str(project_dir),
                    "test_plan_files": [relative_plan_path],
                }
            }
        )

        with (
            patch("deep_agent.agent.base_agent.init_chat_model", return_value=object()),
            patch("deep_agent.agent.base_agent.create_deep_agent", return_value=object()) as create_agent_mock,
        ):
            agent._create_specialist_agent(context)

        backend = create_agent_mock.call_args.kwargs["backend"]
        permissions = create_agent_mock.call_args.kwargs["permissions"]
        resolved_project_dir = project_dir.resolve()
        read_allow_rules = [rule for rule in permissions if rule.operations == ["read"] and rule.mode == "allow"]
        read_deny_paths = [rule.paths[0] for rule in permissions if rule.operations == ["read"] and rule.mode == "deny"]
        self.assertIsInstance(backend, FilesystemBackend)
        self.assertEqual(backend.cwd, resolved_project_dir)
        self.assertEqual(read_allow_rules[0].paths, [str(resolved_project_dir), f"{resolved_project_dir}/**"])
        self.assertIn(f"{resolved_project_dir}/test-results", read_deny_paths)
        self.assertIn(f"{resolved_project_dir}/test-results/**", read_deny_paths)
        self.assertIn(f"{resolved_project_dir}/node_modules", read_deny_paths)
        self.assertIn(f"{resolved_project_dir}/**/*.trace", read_deny_paths)
        self.assertEqual(permissions[-1].operations, ["write"])
        self.assertEqual(permissions[-1].mode, "deny")

    async def test_healer_execute_uses_streaming_deep_agent_runtime(self) -> None:
        project_dir = self.root_path / "healer-runtime"
        relative_script_path = "test_case/demo/a_case.spec.ts"
        self._create_healer_script_file(project_dir, relative_script_path)
        manager = FakeMCPManager(self._build_healer_tools())
        agent = HealerAgent(self._build_settings(), mcp_manager=manager)
        fake_deep_agent = FakeStreamAgent(
            {
                "messages": [
                    AIMessage(content="existing"),
                    AIMessage(content="healer-finished"),
                ]
            }
        )

        with (
            patch("deep_agent.agent.base_agent.init_chat_model", return_value=object()),
            patch("deep_agent.agent.base_agent.create_deep_agent", return_value=fake_deep_agent),
        ):
            result = await agent.execute(
                {
                    "messages": [AIMessage(content="existing")],
                    "extracted_params": {
                        "project_dir": str(project_dir),
                        "test_scripts": [relative_script_path],
                    },
                }
            )

        self.assertIn("Healer 阶段", result["messages"][0].content)
        self.assertIn(relative_script_path, result["messages"][0].content)
        self.assertEqual(fake_deep_agent.inputs[0][0]["messages"][0].content, "existing")
        self.assertEqual(fake_deep_agent.inputs[0][1]["recursion_limit"], self._build_settings().specialist_recursion_limit)
        self.assertEqual(fake_deep_agent.inputs[0][2], "v2")

    async def test_healer_execute_treats_expected_browser_close_after_final_output_as_success(self) -> None:
        project_dir = self.root_path / "healer-close"
        relative_script_path = "test_case/demo/a_case.spec.ts"
        self._create_healer_script_file(project_dir, relative_script_path)
        manager = FakeMCPManager(self._build_healer_tools())
        agent = HealerAgent(self._build_settings(), mcp_manager=manager)

        class FakeCloseStreamAgent:
            def __init__(self) -> None:
                self.inputs: list[tuple[dict, dict | None, str | None]] = []

            async def astream_events(self, input_data, config=None, version=None):  # noqa: ANN001
                self.inputs.append((input_data, config, version))
                yield {
                    "event": "on_chain_end",
                    "name": "healer-specialist",
                    "parent_ids": [],
                    "data": {
                        "output": {
                            "messages": [
                                AIMessage(content="existing"),
                                AIMessage(content="healer-finished"),
                            ]
                        }
                    },
                }
                raise RuntimeError("Target page, context or browser has been closed")

        fake_deep_agent = FakeCloseStreamAgent()

        with (
            patch("deep_agent.agent.base_agent.init_chat_model", return_value=object()),
            patch("deep_agent.agent.base_agent.create_deep_agent", return_value=fake_deep_agent),
        ):
            result = await agent.execute(
                {
                    "messages": [AIMessage(content="existing")],
                    "extracted_params": {
                        "project_dir": str(project_dir),
                        "test_scripts": [relative_script_path],
                    },
                }
            )

        self.assertIn("Healer 阶段", result["messages"][0].content)
        self.assertIn(relative_script_path, result["messages"][0].content)

    async def test_healer_runtime_binds_writable_workspace_permissions(self) -> None:
        project_dir = self.root_path / "healer-backend"
        relative_script_path = "test_case/demo/a_case.spec.ts"
        self._create_healer_script_file(project_dir, relative_script_path)
        manager = FakeMCPManager(self._build_healer_tools())
        agent = HealerAgent(self._build_settings(), mcp_manager=manager)
        context = await agent._prepare_execution(
            {
                "extracted_params": {
                    "project_dir": str(project_dir),
                    "test_scripts": [relative_script_path],
                }
            }
        )

        with (
            patch("deep_agent.agent.base_agent.init_chat_model", return_value=object()),
            patch("deep_agent.agent.base_agent.create_deep_agent", return_value=object()) as create_agent_mock,
        ):
            agent._create_specialist_agent(context)

        backend = create_agent_mock.call_args.kwargs["backend"]
        permissions = create_agent_mock.call_args.kwargs["permissions"]
        resolved_project_dir = project_dir.resolve()
        read_allow_rules = [rule for rule in permissions if rule.operations == ["read"] and rule.mode == "allow"]
        read_deny_paths = [rule.paths[0] for rule in permissions if rule.operations == ["read"] and rule.mode == "deny"]
        write_allow_rules = [rule for rule in permissions if rule.operations == ["write"] and rule.mode == "allow"]
        self.assertIsInstance(backend, FilesystemBackend)
        self.assertEqual(backend.cwd, resolved_project_dir)
        self.assertEqual(read_allow_rules[0].paths, [str(resolved_project_dir), f"{resolved_project_dir}/**"])
        self.assertIn(f"{resolved_project_dir}/test-results", read_deny_paths)
        self.assertIn(f"{resolved_project_dir}/node_modules", read_deny_paths)
        self.assertIn(f"{resolved_project_dir}/**/*.trace", read_deny_paths)
        self.assertNotIn(f"{resolved_project_dir}/.playwright-mcp", read_deny_paths)
        self.assertNotIn(f"{resolved_project_dir}/.playwright-mcp/**", read_deny_paths)
        self.assertEqual(write_allow_rules[0].paths, [str(resolved_project_dir), f"{resolved_project_dir}/**"])
        self.assertEqual(permissions[-1].mode, "deny")

    def test_runtime_allowlists_match_attachment_exactly(self) -> None:
        self.assertEqual(
            PLAN_RUNTIME_CONFIG.allowed_playwright_test_mcp_tools,
            (
                "playwright-test/browser_click",
                "playwright-test/browser_close",
                "playwright-test/browser_console_messages",
                "playwright-test/browser_drag",
                "playwright-test/browser_evaluate",
                "playwright-test/browser_file_upload",
                "playwright-test/browser_handle_dialog",
                "playwright-test/browser_hover",
                "playwright-test/browser_navigate",
                "playwright-test/browser_navigate_back",
                "playwright-test/browser_network_requests",
                "playwright-test/browser_press_key",
                "playwright-test/browser_run_code",
                "playwright-test/browser_select_option",
                "playwright-test/browser_snapshot",
                "playwright-test/browser_take_screenshot",
                "playwright-test/browser_type",
                "playwright-test/browser_wait_for",
                "playwright-test/planner_setup_page",
                "playwright-test/planner_save_plan",
            ),
        )
        self.assertEqual(
            GENERATOR_RUNTIME_CONFIG.allowed_playwright_test_mcp_tools,
            (
                "playwright-test/browser_click",
                "playwright-test/browser_drag",
                "playwright-test/browser_evaluate",
                "playwright-test/browser_file_upload",
                "playwright-test/browser_handle_dialog",
                "playwright-test/browser_hover",
                "playwright-test/browser_navigate",
                "playwright-test/browser_press_key",
                "playwright-test/browser_run_code",
                "playwright-test/browser_select_option",
                "playwright-test/browser_snapshot",
                "playwright-test/browser_type",
                "playwright-test/browser_verify_element_visible",
                "playwright-test/browser_verify_list_visible",
                "playwright-test/browser_verify_text_visible",
                "playwright-test/browser_verify_value",
                "playwright-test/browser_wait_for",
                "playwright-test/generator_read_log",
                "playwright-test/generator_setup_page",
                "playwright-test/generator_write_test",
            ),
        )
        self.assertEqual(
            HEALER_RUNTIME_CONFIG.allowed_playwright_test_mcp_tools,
            (
                "playwright-test/browser_console_messages",
                "playwright-test/browser_evaluate",
                "playwright-test/browser_generate_locator",
                "playwright-test/browser_network_requests",
                "playwright-test/browser_snapshot",
                "playwright-test/test_debug",
                "playwright-test/test_list",
                "playwright-test/test_run",
            ),
        )

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool

from deep_agent.core.config import AppSettings
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
        self.tools = tools
        self.requests: list[tuple[str, object, tuple[str, ...] | None]] = []

    async def get_tools(self, server_name, workspace_dir=None, allowed_tool_ids=None):  # noqa: ANN001
        normalized_ids = None if allowed_tool_ids is None else tuple(allowed_tool_ids)
        self.requests.append((server_name, workspace_dir, normalized_ids))
        return self.tools


class FakeEventAgent:
    def __init__(self, events: list[dict]) -> None:
        self.events = events
        self.inputs: list[tuple[dict, dict | None, str]] = []

    async def astream_events(self, input_data, config=None, version="v2"):  # noqa: ANN001
        self.inputs.append((input_data, config, version))
        for event in self.events:
            yield event


class PlanExecutionTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)
        self.settings = AppSettings(
            default_automation_project_root=str(self.root_path / "projects"),
        )
        self.tools = [
            DummyTool(name="browser_navigate"),
            DummyTool(name="browser_run_code"),
            DummyTool(name="planner_setup_page"),
            DummyTool(name="planner_save_plan"),
        ]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_plan_execute_reads_events_and_requires_planner_save(self) -> None:
        fake_manager = FakeMCPManager(self.tools)
        fake_agent = FakeEventAgent(
            [
                {"event": "on_chat_model_start", "name": "model", "data": {"input": {"messages": []}}, "parent_ids": []},
                {"event": "on_tool_start", "name": "planner_setup_page", "data": {"input": {}}, "parent_ids": ["root"]},
                {"event": "on_tool_end", "name": "planner_setup_page", "data": {"output": "ok"}, "parent_ids": ["root"]},
                {"event": "on_tool_start", "name": "planner_save_plan", "data": {"input": {"fileName": "x"}}, "parent_ids": ["root"]},
                {"event": "on_tool_end", "name": "planner_save_plan", "data": {"output": "saved"}, "parent_ids": ["root"]},
                {
                    "event": "on_chain_end",
                    "name": "plan-specialist",
                    "data": {"output": {"messages": [AIMessage(content="测试计划已保存")] }},
                    "parent_ids": [],
                },
            ]
        )
        agent = PlanAgent(self.settings, mcp_manager=fake_manager)
        project_dir = self.root_path / "project"
        state = {
            "messages": [],
            "extracted_params": {
                "project_name": "project",
                "url": "https://example.com",
                "project_dir": str(project_dir),
            },
        }

        fake_model = object()
        with (
            patch("deep_agent.agent.base_agent.init_chat_model", return_value=fake_model),
            patch("deep_agent.agent.base_agent.create_deep_agent", return_value=fake_agent) as create_agent_mock,
        ):
            result = await agent.execute(state)

        self.assertEqual(len(fake_agent.inputs), 1)
        input_data, config, version = fake_agent.inputs[0]
        self.assertEqual(input_data, {"messages": []})
        self.assertEqual(version, "v2")
        self.assertEqual(config["recursion_limit"], 999)
        self.assertEqual(
            fake_manager.requests,
            [
                (
                    PLAYWRIGHT_TEST_MCP_SERVER_NAME,
                    project_dir.resolve(),
                    PLAN_RUNTIME_CONFIG.allowed_playwright_test_mcp_tools,
                )
            ],
        )
        self.assertEqual(result["messages"][0].content, "测试计划已保存")
        self.assertEqual(create_agent_mock.call_args.kwargs["model"], fake_model)
        self.assertNotIn("middleware", create_agent_mock.call_args.kwargs)

    async def test_plan_execute_passes_custom_recursion_limit(self) -> None:
        fake_manager = FakeMCPManager(self.tools)
        fake_agent = FakeEventAgent(
            [
                {"event": "on_tool_start", "name": "planner_save_plan", "data": {"input": {"fileName": "x"}}, "parent_ids": ["root"]},
                {"event": "on_tool_end", "name": "planner_save_plan", "data": {"output": "saved"}, "parent_ids": ["root"]},
                {
                    "event": "on_chain_end",
                    "name": "plan-specialist",
                    "data": {"output": {"messages": [AIMessage(content="测试计划已保存")] }},
                    "parent_ids": [],
                },
            ]
        )
        settings = AppSettings(
            default_automation_project_root=str(self.root_path / "projects"),
            specialist_recursion_limit=123,
        )
        agent = PlanAgent(settings, mcp_manager=fake_manager)
        state = {
            "messages": [],
            "extracted_params": {
                "project_name": "project-custom-limit",
                "url": "https://example.com",
                "project_dir": str(self.root_path / "project-custom-limit"),
            },
        }

        with (
            patch("deep_agent.agent.base_agent.init_chat_model", return_value=object()),
            patch("deep_agent.agent.base_agent.create_deep_agent", return_value=fake_agent),
        ):
            result = await agent.execute(state)

        self.assertEqual(result["messages"][0].content, "测试计划已保存")
        self.assertEqual(fake_agent.inputs[0][1]["recursion_limit"], 123)

    async def test_plan_execute_returns_failure_message_when_plan_was_not_saved(self) -> None:
        fake_manager = FakeMCPManager(self.tools)
        fake_agent = FakeEventAgent(
            [
                {"event": "on_tool_start", "name": "planner_setup_page", "data": {"input": {}}, "parent_ids": ["root"]},
                {"event": "on_tool_end", "name": "planner_setup_page", "data": {"output": "ok"}, "parent_ids": ["root"]},
                {
                    "event": "on_chain_end",
                    "name": "plan-specialist",
                    "data": {"output": {"messages": [AIMessage(content="仅分析，没有保存")] }},
                    "parent_ids": [],
                },
            ]
        )
        agent = PlanAgent(self.settings, mcp_manager=fake_manager)
        project_dir = self.root_path / "project-failure"
        state = {
            "messages": [],
            "extracted_params": {
                "project_name": "project-failure",
                "url": "https://example.com",
                "project_dir": str(project_dir),
            },
        }

        with (
            patch("deep_agent.agent.base_agent.init_chat_model", return_value=object()),
            patch("deep_agent.agent.base_agent.create_deep_agent", return_value=fake_agent),
        ):
            result = await agent.execute(state)

        self.assertEqual(fake_manager.requests[0][0], PLAYWRIGHT_TEST_MCP_SERVER_NAME)
        self.assertIn("Plan Agent 执行过程中遇到未处理异常", result["messages"][0].content)
        self.assertIn("planner_save_plan", result["messages"][0].content)

    async def test_plan_event_truncation_uses_debug_max_chars(self) -> None:
        agent = PlanAgent(
            AppSettings(agent_debug_max_chars=12),
            mcp_manager=FakeMCPManager(self.tools),
        )

        self.assertEqual(agent._truncate("x" * 20), "xxxxxxxxxxxx...")

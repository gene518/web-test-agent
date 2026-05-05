from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage
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
        planner_payload = {
            "overview": "登录页测试概览",
            "name": "demo",
            "fileName": "test_case/aaaplanning_demo/aaa_demo.md",
            "suites": [
                {
                    "name": "登录场景",
                    "seedFile": "test_case/specs/seed.spec.ts",
                    "tests": [
                        {
                            "name": "a_login_success",
                            "file": "test_case/aaaplanning_demo/a_login_success.spec.ts",
                            "steps": [
                                {"perform": "打开登录页", "expect": ["显示登录表单"]},
                            ],
                        }
                    ],
                }
            ],
        }
        fake_agent = FakeEventAgent(
            [
                {"event": "on_chat_model_start", "name": "model", "data": {"input": {"messages": []}}, "parent_ids": []},
                {"event": "on_tool_start", "name": "planner_setup_page", "data": {"input": {}}, "parent_ids": ["root"]},
                {"event": "on_tool_end", "name": "planner_setup_page", "data": {"output": "ok"}, "parent_ids": ["root"]},
                {"event": "on_tool_start", "name": "planner_save_plan", "data": {"input": planner_payload}, "parent_ids": ["root"]},
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
        self.assertIn("Plan 阶段", result["messages"][0].content)
        self.assertIn("aaa_demo.md", result["messages"][0].content)
        self.assertIn("a_login_success", result["messages"][0].content)
        self.assertIn("待生成脚本规划", result["messages"][0].content)
        self.assertIn("下一阶段建议输入", result["messages"][0].content)
        self.assertEqual(result["artifact_history"][0]["output_files"], ["test_case/aaaplanning_demo/aaa_demo.md"])
        self.assertEqual(
            result["latest_artifacts"]["plan"]["planned_test_case_files"],
            ["test_case/aaaplanning_demo/a_login_success.spec.ts"],
        )
        self.assertEqual(result["latest_artifacts"]["plan"]["saved_test_case_files"], [])
        self.assertEqual(create_agent_mock.call_args.kwargs["model"], fake_model)
        self.assertNotIn("middleware", create_agent_mock.call_args.kwargs)
        permissions = create_agent_mock.call_args.kwargs["permissions"]
        write_allow_rules = [rule for rule in permissions if rule.operations == ["write"] and rule.mode == "allow"]
        self.assertEqual(write_allow_rules[0].paths, [str(project_dir.resolve()), f"{project_dir.resolve()}/**"])

    async def test_plan_execute_accepts_write_file_when_markdown_exists_by_node_end(self) -> None:
        fake_manager = FakeMCPManager(self.tools)
        project_dir = self.root_path / "write-file-project"
        relative_plan_path = "test_case/aaaplanning_demo/aaa_demo.md"
        plan_file = project_dir / relative_plan_path
        plan_markdown = "\n".join(
            [
                "# demo Plan",
                "",
                "## Test Scenarios",
                "",
                "### 1. 登录场景",
                "**Seed:** `test_case/specs/seed.spec.ts`",
                "",
                "#### 1.1. a_login_success",
                "",
                "**File:** `test_case/aaaplanning_demo/a_login_success.spec.ts`",
                "",
                "**Steps:**",
                "1. 打开登录页",
                "   - expect:",
                "     - 显示登录表单",
                "",
            ]
        )

        class FakeWriteFilePlanAgent:
            async def astream_events(self, input_data, config=None, version="v2"):  # noqa: ANN001
                yield {
                    "event": "on_tool_start",
                    "name": "write_file",
                    "data": {"input": {"file_path": str(plan_file.resolve()), "content": plan_markdown}},
                    "parent_ids": ["root"],
                }
                plan_file.parent.mkdir(parents=True, exist_ok=True)
                plan_file.write_text(plan_markdown, encoding="utf-8")
                yield {
                    "event": "on_tool_end",
                    "name": "write_file",
                    "data": {"output": f"Updated file {plan_file.resolve()}"},
                    "parent_ids": ["root"],
                }
                yield {
                    "event": "on_chain_end",
                    "name": "plan-specialist",
                    "data": {"output": {"messages": [AIMessage(content="测试计划已保存")] }},
                    "parent_ids": [],
                }

        agent = PlanAgent(self.settings, mcp_manager=fake_manager)
        state = {
            "messages": [],
            "extracted_params": {
                "project_name": "write-file-project",
                "url": "https://example.com",
                "project_dir": str(project_dir),
            },
        }

        with (
            patch("deep_agent.agent.base_agent.init_chat_model", return_value=object()),
            patch("deep_agent.agent.base_agent.create_deep_agent", return_value=FakeWriteFilePlanAgent()),
        ):
            result = await agent.execute(state)

        self.assertIn("Plan 阶段", result["messages"][0].content)
        self.assertNotIn("状态：exception", result["messages"][0].content)
        self.assertEqual(
            result["latest_artifacts"]["plan"]["output_files"],
            [relative_plan_path],
        )
        self.assertEqual(
            result["latest_artifacts"]["plan"]["planned_test_case_files"],
            ["test_case/aaaplanning_demo/a_login_success.spec.ts"],
        )

    async def test_plan_execute_preserves_streamed_messages_without_root_chain_output(self) -> None:
        fake_manager = FakeMCPManager(self.tools)
        planner_payload = {
            "overview": "登录页测试概览",
            "name": "demo",
            "fileName": "test_case/aaaplanning_demo/aaa_demo.md",
            "suites": [
                {
                    "name": "登录场景",
                    "seedFile": "test_case/specs/seed.spec.ts",
                    "tests": [
                        {
                            "name": "a_login_success",
                            "file": "test_case/aaaplanning_demo/a_login_success.spec.ts",
                            "steps": [
                                {"perform": "打开登录页", "expect": ["显示登录表单"]},
                            ],
                        }
                    ],
                }
            ],
        }
        streamed_tool_call = AIMessage(
            content="",
            id="ai-tool-call",
            tool_calls=[
                {
                    "name": "write_todos",
                    "args": {"todos": [{"content": "初始化页面", "status": "in_progress"}]},
                    "id": "call-write-todos",
                    "type": "tool_call",
                }
            ],
        )
        streamed_tool_result = ToolMessage(
            content="Updated todo list to [{'content': '初始化页面', 'status': 'in_progress'}]",
            tool_call_id="call-write-todos",
            id="tool-write-todos",
            name="write_todos",
            status="success",
        )
        streamed_planner_result = ToolMessage(
            content="Test plan saved to test_case/aaaplanning_demo/aaa_demo.md",
            tool_call_id="call-save-plan",
            id="tool-save-plan",
            name="planner_save_plan",
            status="success",
        )
        streamed_final_ai = AIMessage(
            content="已生成并保存测试计划。",
            id="ai-final",
        )
        fake_agent = FakeEventAgent(
            [
                {
                    "event": "on_chat_model_end",
                    "name": "plan-specialist",
                    "data": {"output": streamed_tool_call},
                    "parent_ids": ["root"],
                },
                {
                    "event": "on_tool_end",
                    "name": "write_todos",
                    "data": {"output": SimpleNamespace(update={"messages": [streamed_tool_result]})},
                    "parent_ids": ["root"],
                },
                {
                    "event": "on_tool_start",
                    "name": "planner_save_plan",
                    "data": {"input": planner_payload},
                    "parent_ids": ["root"],
                },
                {
                    "event": "on_tool_end",
                    "name": "planner_save_plan",
                    "data": {"output": streamed_planner_result},
                    "parent_ids": ["root"],
                },
                {
                    "event": "on_chat_model_end",
                    "name": "plan-specialist",
                    "data": {"output": streamed_final_ai},
                    "parent_ids": ["root"],
                },
                {
                    "event": "on_chain_end",
                    "name": "plan-specialist",
                    "data": {"output": "done"},
                    "parent_ids": [],
                },
            ]
        )
        agent = PlanAgent(self.settings, mcp_manager=fake_manager)
        project_dir = self.root_path / "streamed-project"
        state = {
            "messages": [],
            "requested_pipeline": ["plan"],
            "pipeline_cursor": 0,
            "extracted_params": {
                "project_name": "streamed-project",
                "url": "https://example.com",
                "project_dir": str(project_dir),
            },
        }

        with (
            patch("deep_agent.agent.base_agent.init_chat_model", return_value=object()),
            patch("deep_agent.agent.base_agent.create_deep_agent", return_value=fake_agent),
        ):
            result = await agent.execute(state)

        self.assertEqual(result["messages"], [])
        self.assertEqual(
            [message.id for message in result["display_messages"]],
            ["ai-tool-call", "tool-write-todos", "tool-save-plan", "ai-final"],
        )
        self.assertEqual(result["display_messages"][0].tool_calls[0]["name"], "write_todos")
        self.assertEqual(result["display_messages"][1].name, "write_todos")
        self.assertEqual(result["display_messages"][2].name, "planner_save_plan")
        self.assertEqual(result["display_messages"][3].content, "已生成并保存测试计划。")

    async def test_plan_execute_passes_custom_recursion_limit(self) -> None:
        fake_manager = FakeMCPManager(self.tools)
        planner_payload = {
            "overview": "登录页测试概览",
            "name": "demo",
            "fileName": "test_case/aaaplanning_demo/aaa_demo.md",
            "suites": [
                {
                    "name": "登录场景",
                    "seedFile": "test_case/specs/seed.spec.ts",
                    "tests": [
                        {
                            "name": "a_login_success",
                            "file": "test_case/aaaplanning_demo/a_login_success.spec.ts",
                            "steps": [
                                {"perform": "打开登录页", "expect": ["显示登录表单"]},
                            ],
                        }
                    ],
                }
            ],
        }
        fake_agent = FakeEventAgent(
            [
                {"event": "on_tool_start", "name": "planner_save_plan", "data": {"input": planner_payload}, "parent_ids": ["root"]},
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

        self.assertIn("aaa_demo.md", result["messages"][0].content)
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

    async def test_plan_execute_rejects_invalid_planner_payload_even_when_tool_succeeds(self) -> None:
        fake_manager = FakeMCPManager(self.tools)
        fake_agent = FakeEventAgent(
            [
                {
                    "event": "on_tool_start",
                    "name": "planner_save_plan",
                    "data": {
                        "input": {
                            "overview": "invalid plan",
                            "name": "Demo Plan",
                            "fileName": "test_case/aaaplanning_demo/aaa_demo.md",
                            "suites": [],
                        }
                    },
                    "parent_ids": ["root"],
                },
                {"event": "on_tool_end", "name": "planner_save_plan", "data": {"output": "saved"}, "parent_ids": ["root"]},
            ]
        )
        agent = PlanAgent(self.settings, mcp_manager=fake_manager)
        state = {
            "messages": [],
            "extracted_params": {
                "project_name": "invalid-project",
                "url": "https://example.com",
                "project_dir": str(self.root_path / "invalid-project"),
            },
        }

        with (
            patch("deep_agent.agent.base_agent.init_chat_model", return_value=object()),
            patch("deep_agent.agent.base_agent.create_deep_agent", return_value=fake_agent),
        ):
            result = await agent.execute(state)

        self.assertIn("Plan 阶段", result["messages"][0].content)
        self.assertIn("状态：exception", result["messages"][0].content)
        self.assertIn("planner_save_plan.suites", result["messages"][0].content)

    async def test_plan_execute_rejects_plan_markdown_outside_aaaplanning_directory(self) -> None:
        fake_manager = FakeMCPManager(self.tools)
        fake_agent = FakeEventAgent(
            [
                {
                    "event": "on_tool_start",
                    "name": "planner_save_plan",
                    "data": {
                        "input": {
                            "overview": "invalid plan path",
                            "name": "demo",
                            "fileName": "test_case/aaa_demo.md",
                            "suites": [
                                {
                                    "name": "登录场景",
                                    "seedFile": "test_case/specs/seed.spec.ts",
                                    "tests": [
                                        {
                                            "name": "a_login_success",
                                            "file": "test_case/aaaplanning_demo/a_login_success.spec.ts",
                                            "steps": [{"perform": "打开登录页", "expect": ["显示登录表单"]}],
                                        }
                                    ],
                                }
                            ],
                        }
                    },
                    "parent_ids": ["root"],
                },
                {"event": "on_tool_end", "name": "planner_save_plan", "data": {"output": "saved"}, "parent_ids": ["root"]},
            ]
        )
        agent = PlanAgent(self.settings, mcp_manager=fake_manager)
        state = {
            "messages": [],
            "extracted_params": {
                "project_name": "invalid-plan-path",
                "url": "https://example.com",
                "project_dir": str(self.root_path / "invalid-plan-path"),
            },
        }

        with (
            patch("deep_agent.agent.base_agent.init_chat_model", return_value=object()),
            patch("deep_agent.agent.base_agent.create_deep_agent", return_value=fake_agent),
        ):
            result = await agent.execute(state)

        self.assertIn("状态：exception", result["messages"][0].content)
        self.assertIn("aaaplanning_{plan-name}", result["messages"][0].content)

    async def test_plan_execute_rejects_case_file_outside_matching_aaaplanning_directory(self) -> None:
        fake_manager = FakeMCPManager(self.tools)
        fake_agent = FakeEventAgent(
            [
                {
                    "event": "on_tool_start",
                    "name": "planner_save_plan",
                    "data": {
                        "input": {
                            "overview": "invalid case path",
                            "name": "demo",
                            "fileName": "test_case/aaaplanning_demo/aaa_demo.md",
                            "suites": [
                                {
                                    "name": "登录场景",
                                    "seedFile": "test_case/specs/seed.spec.ts",
                                    "tests": [
                                        {
                                            "name": "a_login_success",
                                            "file": "test_case/demo/a_login_success.spec.ts",
                                            "steps": [{"perform": "打开登录页", "expect": ["显示登录表单"]}],
                                        }
                                    ],
                                }
                            ],
                        }
                    },
                    "parent_ids": ["root"],
                },
                {"event": "on_tool_end", "name": "planner_save_plan", "data": {"output": "saved"}, "parent_ids": ["root"]},
            ]
        )
        agent = PlanAgent(self.settings, mcp_manager=fake_manager)
        state = {
            "messages": [],
            "extracted_params": {
                "project_name": "invalid-case-path",
                "url": "https://example.com",
                "project_dir": str(self.root_path / "invalid-case-path"),
            },
        }

        with (
            patch("deep_agent.agent.base_agent.init_chat_model", return_value=object()),
            patch("deep_agent.agent.base_agent.create_deep_agent", return_value=fake_agent),
        ):
            result = await agent.execute(state)

        self.assertIn("状态：exception", result["messages"][0].content)
        self.assertIn("test_case/aaaplanning_demo/a_login_success.spec.ts", result["messages"][0].content)

    async def test_plan_event_truncation_uses_debug_max_chars(self) -> None:
        agent = PlanAgent(
            AppSettings(agent_debug_max_chars=12),
            mcp_manager=FakeMCPManager(self.tools),
        )

        self.assertEqual(agent.log_truncate("x" * 20), "xxxxxxxxxxxx...")

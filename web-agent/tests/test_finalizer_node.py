from __future__ import annotations

import unittest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from deep_agent.agent.finalizer import FinalizeTurnNode
from deep_agent.agent.state import WorkflowState


class FinalizerNodeTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_finalize_turn_returns_only_display_delta(self) -> None:
        node = FinalizeTurnNode()
        human_message = HumanMessage(content="帮我生成脚本", id="human-1")
        stage_message = AIMessage(content="Generator 阶段已完成。", id="ai-1")

        result = await node.execute(
            {
                "messages": [human_message],
                "display_messages": [human_message, stage_message],
                "pending_stage_summaries": [
                    {"stage": "generator", "status": "success", "text": "Generator 阶段已完成。"}
                ],
            }
        )

        self.assertEqual(result["messages"][0].content, "Generator 阶段已完成。")
        self.assertEqual(len(result["display_messages"]), 1)
        self.assertEqual(result["display_messages"][0].content, "Generator 阶段已完成。")

    async def test_finalizer_preserves_parameter_completion_and_runtime_timeline_in_graph(self) -> None:
        def runtime_node(state):  # noqa: ANN001
            return {
                "display_messages": [
                    AIMessage(content="调用 planner_setup_page", id="ai-tool-call"),
                    ToolMessage(
                        content="页面初始化完成",
                        name="planner_setup_page",
                        tool_call_id="call-setup",
                        id="tool-setup",
                    ),
                ],
                "pending_stage_summaries": [
                    {"stage": "plan", "status": "success", "text": "Plan 阶段已完成。"}
                ],
            }

        graph = StateGraph(WorkflowState)
        finalizer = FinalizeTurnNode()
        graph.add_node("runtime_node", runtime_node)
        graph.add_node("finalize_turn_node", finalizer.execute)
        graph.add_edge(START, "runtime_node")
        graph.add_edge("runtime_node", "finalize_turn_node")
        graph.add_edge("finalize_turn_node", END)
        compiled = graph.compile()

        opening_message = HumanMessage(content="为 baidu 生成测试用例", id="human-opening")
        resume_message = HumanMessage(content="项目名 demo", id="human-resume")
        result = await compiled.ainvoke(
            {
                "messages": [opening_message, resume_message],
                "display_messages": [opening_message, resume_message],
            }
        )

        self.assertEqual(
            [message.content for message in result["display_messages"]],
            [
                "为 baidu 生成测试用例",
                "项目名 demo",
                "调用 planner_setup_page",
                "页面初始化完成",
                "Plan 阶段已完成。",
            ],
        )

from __future__ import annotations

import unittest

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from deep_agent.agent.master.nodes import IntentJudgeNode
from deep_agent.agent.state import WorkflowState
from deep_agent.workflow import build_master_graph


class FakeMasterService:
    def __init__(self, *, initial_params: dict | None = None, agent_type: str = "plan") -> None:
        self.initial_params = initial_params or {}
        self.agent_type = agent_type
        self.classify_calls = 0
        self.resume_texts: list[str] = []

    async def classify_intent_and_params(self, state, config=None):  # noqa: ANN001
        self.classify_calls += 1
        if self.agent_type == "general":
            return {
                "agent_type": "general",
                "extracted_params": {},
                "missing_params": [],
                "pending_missing_params": [],
                "routing_reason": "general request",
            }
        return {
            "agent_type": self.agent_type,
            "pending_agent_type": self.agent_type,
            "extracted_params": self.initial_params,
            "missing_params": ["project_name"],
            "pending_missing_params": ["project_name"],
            "routing_reason": "need params",
        }

    def build_missing_param_interrupt_payload(self, *, agent_type, missing_param, extracted_params):  # noqa: ANN001
        return {
            "agent_type": agent_type,
            "missing_param": missing_param,
            "question": "请提供自动化工程名字。",
            "known_context": f"url={extracted_params.get('url')}",
        }

    async def extract_params_for_fixed_intent(  # noqa: ANN001, PLR0913
        self,
        *,
        agent_type,
        existing_params,
        resume_text,
        routing_reason,
        config=None,
    ):
        self.resume_texts.append(resume_text)
        if "demo" not in resume_text:
            return {}
        return {"project_name": "demo"}

    def merge_extracted_params(self, existing_params, new_params):  # noqa: ANN001
        merged = dict(existing_params)
        merged.update(new_params)
        return merged

    async def answer_general_request(self, state, config=None):  # noqa: ANN001
        return "general raw answer"

    async def summarize_final_response(self, *, state, stage_name, raw_result, config=None):  # noqa: ANN001
        return f"summary: {raw_result}"


class MasterRoutingTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_intent_judge_routes_specialist_to_param_completion(self) -> None:
        node = IntentJudgeNode(FakeMasterService(initial_params={"project_name": "demo", "url": "https://example.com"}))

        result = await node.execute({"messages": [HumanMessage(content="帮我写计划")]})

        self.assertEqual(result["agent_type"], "plan")
        self.assertEqual(result["next_action"], "complete_params")

    async def test_intent_judge_ends_when_specialist_returns_to_master(self) -> None:
        service = FakeMasterService()
        node = IntentJudgeNode(service)

        result = await node.execute({"return_to_master": True})

        self.assertEqual(result["next_action"], "end")
        self.assertFalse(result["return_to_master"])
        self.assertEqual(service.classify_calls, 0)

    async def test_master_graph_interrupts_for_missing_params_and_resume_keeps_intent(self) -> None:
        service = FakeMasterService(initial_params={"url": "https://example.com"})
        graph = self._build_outer_graph(service)
        config = {"configurable": {"thread_id": "missing-param-test"}}

        first_result = await graph.ainvoke({"messages": [HumanMessage(content="帮我写测试计划")]}, config=config)

        interrupt_payload = first_result["__interrupt__"][0].value
        self.assertEqual(interrupt_payload["agent_type"], "plan")
        self.assertEqual(interrupt_payload["missing_param"], "project_name")
        self.assertIn("url=https://example.com", interrupt_payload["known_context"])

        resumed_result = await graph.ainvoke(Command(resume="项目名 demo"), config=config)

        self.assertEqual(resumed_result["agent_type"], "plan")
        self.assertEqual(resumed_result["next_action"], "plan")
        self.assertEqual(resumed_result["missing_params"], [])
        self.assertEqual(
            resumed_result["extracted_params"],
            {
                "project_name": "demo",
                "url": "https://example.com",
            },
        )
        self.assertEqual(service.resume_texts, ["项目名 demo"])

    async def test_master_graph_keeps_interrupting_when_resume_does_not_fill_missing_param(self) -> None:
        service = FakeMasterService(initial_params={"url": "https://example.com"})
        graph = self._build_outer_graph(service)
        config = {"configurable": {"thread_id": "still-missing-param-test"}}

        await graph.ainvoke({"messages": [HumanMessage(content="帮我写测试计划")]}, config=config)
        second_result = await graph.ainvoke(Command(resume="暂时不知道"), config=config)

        self.assertEqual(second_result["__interrupt__"][0].value["missing_param"], "project_name")
        self.assertEqual(service.resume_texts, ["暂时不知道"])

    async def test_master_graph_handles_general_inside_subgraph(self) -> None:
        service = FakeMasterService(agent_type="general")
        graph = self._build_outer_graph(service)

        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="怎么设计登录测试点？")]},
            config={"configurable": {"thread_id": "general-test"}},
        )

        self.assertEqual(result["next_action"], "end")
        self.assertEqual(result["messages"][-1].content, "summary: general raw answer")
        self.assertEqual(result["stage_result"]["agent_type"], "general")

    def _build_outer_graph(self, service: FakeMasterService):
        master_graph = build_master_graph(service)
        workflow = StateGraph(WorkflowState)
        workflow.add_node("master_graph_node", master_graph)
        workflow.add_edge(START, "master_graph_node")
        workflow.add_edge("master_graph_node", END)
        return workflow.compile(checkpointer=InMemorySaver())

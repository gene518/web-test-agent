from __future__ import annotations

import unittest

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from deep_agent.agent.master.nodes import IntentJudgeNode, ResolveStageFilesNode
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
                "requested_pipeline": [],
                "pipeline_cursor": 0,
                "routing_reason": "general request",
            }
        return {
            "agent_type": self.agent_type,
            "pending_agent_type": self.agent_type,
            "extracted_params": self.initial_params,
            "missing_params": ["project_name"],
            "pending_missing_params": ["project_name"],
            "requested_pipeline": [self.agent_type],
            "pipeline_cursor": 0,
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
        self.assertEqual(result["next_action"], "resolve_stage_files")

    async def test_intent_judge_routes_scheduler_to_param_completion(self) -> None:
        node = IntentJudgeNode(
            FakeMasterService(
                initial_params={"project_name": "demo", "schedule_task_id": "daily_smoke"},
                agent_type="scheduler",
            )
        )

        result = await node.execute({"messages": [HumanMessage(content="把 daily_smoke 改成无头执行")]})

        self.assertEqual(result["agent_type"], "scheduler")
        self.assertEqual(result["next_action"], "complete_params")

    async def test_intent_judge_advances_pipeline_when_specialist_returns_to_master(self) -> None:
        service = FakeMasterService()
        node = IntentJudgeNode(service)

        result = await node.execute(
            {
                "pipeline_handoff": True,
                "agent_type": "plan",
                "requested_pipeline": ["plan", "generator"],
                "pipeline_cursor": 0,
                "stage_result": {"status": "success"},
            }
        )

        self.assertEqual(result["next_action"], "resolve_stage_files")
        self.assertEqual(result["agent_type"], "generator")
        self.assertEqual(result["pipeline_cursor"], 1)
        self.assertFalse(result["pipeline_handoff"])
        self.assertEqual(service.classify_calls, 0)

    async def test_intent_judge_finalizes_pipeline_after_last_stage(self) -> None:
        service = FakeMasterService()
        node = IntentJudgeNode(service)

        result = await node.execute(
            {
                "pipeline_handoff": True,
                "agent_type": "generator",
                "requested_pipeline": ["generator"],
                "pipeline_cursor": 0,
                "stage_result": {"status": "success"},
            }
        )

        self.assertEqual(result["next_action"], "finalize_turn")
        self.assertEqual(service.classify_calls, 0)

    async def test_resolve_stage_files_node_inherits_latest_plan_files_for_generator(self) -> None:
        node = ResolveStageFilesNode()

        result = await node.execute(
            {
                "agent_type": "generator",
                "pending_agent_type": "generator",
                "requested_pipeline": ["generator"],
                "pipeline_cursor": 0,
                "extracted_params": {
                    "project_name": "demo-project",
                },
                "latest_artifacts": {
                    "plan": {
                        "stage": "plan",
                        "project_name": "demo-project",
                        "project_dir": "/tmp/demo-project",
                        "output_files": ["test_case/aaaplanning_demo/aaa_demo.md"],
                        "test_plan_files": ["test_case/aaaplanning_demo/aaa_demo.md"],
                    }
                },
            }
        )

        self.assertEqual(result["next_action"], "generator")
        self.assertEqual(
            result["extracted_params"]["test_plan_files"],
            ["test_case/aaaplanning_demo/aaa_demo.md"],
        )
        self.assertEqual(result["extracted_params"]["project_dir"], "/tmp/demo-project")

    async def test_resolve_stage_files_node_expands_selector_like_test_cases_from_latest_plan(self) -> None:
        node = ResolveStageFilesNode()

        result = await node.execute(
            {
                "agent_type": "generator",
                "pending_agent_type": "generator",
                "requested_pipeline": ["plan", "generator"],
                "pipeline_cursor": 1,
                "extracted_params": {
                    "project_name": "demo-project",
                    "test_cases": ["优先级高的三条用例"],
                },
                "latest_artifacts": {
                    "plan": {
                        "stage": "plan",
                        "project_name": "demo-project",
                        "project_dir": "/tmp/demo-project",
                        "output_files": ["test_case/aaaplanning_demo/aaa_demo.md"],
                        "test_plan_files": ["test_case/aaaplanning_demo/aaa_demo.md"],
                        "saved_test_cases": [
                            {"case_name": "a_search_submit_success"},
                            {"case_name": "b_search_suggestion_navigate"},
                            {"case_name": "c_empty_search_guard"},
                        ],
                    }
                },
            }
        )

        self.assertEqual(result["next_action"], "generator")
        self.assertEqual(
            result["extracted_params"]["test_cases"],
            [
                "a_search_submit_success",
                "b_search_suggestion_navigate",
                "c_empty_search_guard",
            ],
        )

    async def test_resolve_stage_files_node_keeps_explicit_matching_test_cases(self) -> None:
        node = ResolveStageFilesNode()

        result = await node.execute(
            {
                "agent_type": "generator",
                "pending_agent_type": "generator",
                "requested_pipeline": ["plan", "generator"],
                "pipeline_cursor": 1,
                "extracted_params": {
                    "project_name": "demo-project",
                    "test_cases": ["b_search_suggestion_navigate"],
                },
                "latest_artifacts": {
                    "plan": {
                        "stage": "plan",
                        "project_name": "demo-project",
                        "project_dir": "/tmp/demo-project",
                        "output_files": ["test_case/aaaplanning_demo/aaa_demo.md"],
                        "test_plan_files": ["test_case/aaaplanning_demo/aaa_demo.md"],
                        "saved_test_cases": [
                            {"case_name": "a_search_submit_success"},
                            {"case_name": "b_search_suggestion_navigate"},
                            {"case_name": "c_empty_search_guard"},
                        ],
                    }
                },
            }
        )

        self.assertEqual(
            result["extracted_params"]["test_cases"],
            ["b_search_suggestion_navigate"],
        )

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

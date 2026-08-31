from __future__ import annotations

import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage
from langgraph_api.errors import UserInterrupt

from deep_agent.agent.finalizer import (
    GENERATOR_FINALIZE_CONFIG,
    HEALER_FINALIZE_CONFIG,
    PLAN_FINALIZE_CONFIG,
    SCHEDULER_FINALIZE_CONFIG,
    FinalizeStageConfig,
    FinalizeStageNode,
    FinalizerAgent,
)
from deep_agent.core.config import AppSettings
from deep_agent.web_autotest_agent_workflow import build_web_autotest_agent_workflow


class CapturingModel:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls = 0
        self.messages: list[object] = []

    async def ainvoke(self, messages, config=None):  # noqa: ANN001
        self.calls += 1
        self.messages = list(messages)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class FakeFinalizerAgent:
    def __init__(self, summary: str = "阶段模型总结") -> None:
        self.summary = summary
        self.calls: list[dict[str, object]] = []

    async def finalize_stage(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return self.summary


class NoopGraphAgent:
    async def execute(self, state, config=None):  # noqa: ANN001
        return {}


def _plan_stage_result(*, status: str = "success", key: str = "plan:run-1") -> dict:
    artifact = {
        "artifact_id": "plan-artifact-1",
        "stage": "plan",
        "status": status,
        "project_dir": "/tmp/demo",
        "output_files": ["test_case/aaaplanning_demo/aaa_demo.md"],
        "planned_test_case_files": ["tests/demo.spec.ts"],
        "saved_test_cases": [],
    }
    return {
        "agent_type": "plan",
        "display_name": "Plan Agent",
        "status": status,
        "artifact": artifact,
        "raw_result": {"status": status, "artifact": artifact},
        "raw_messages": ["计划已保存"],
        "finalization_key": key,
    }


class FinalizerNodeTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_intermediate_stage_only_appends_display_and_is_idempotent(self) -> None:
        finalizer = FakeFinalizerAgent("Plan 阶段模型总结")
        node = FinalizeStageNode(finalizer, PLAN_FINALIZE_CONFIG)
        human = HumanMessage(content="先做计划再生成脚本", id="human-1")
        state = {
            "messages": [human],
            "display_messages": [human],
            "requested_pipeline": ["plan", "generator"],
            "pipeline_cursor": 0,
            "stage_result": _plan_stage_result(),
            "finalization_key": "plan:run-1",
        }

        result = await node.execute(state)

        self.assertEqual(len(finalizer.calls), 1)
        self.assertEqual(result["messages"], [])
        self.assertEqual(result["display_messages"][0].content, "Plan 阶段模型总结")
        self.assertEqual(len(result["pending_stage_summaries"]), 1)
        self.assertTrue(result["pipeline_handoff"])
        self.assertEqual(result["finalized_stage_keys"], ["plan:run-1"])

        merged_state = {
            **state,
            **result,
            "display_messages": [*state["display_messages"], *result["display_messages"]],
        }
        repeated_result = await node.execute(merged_state)

        self.assertEqual(len(finalizer.calls), 1)
        self.assertNotIn("messages", repeated_result)
        self.assertNotIn("display_messages", repeated_result)
        self.assertTrue(repeated_result["pipeline_handoff"])

    async def test_terminal_stage_moves_all_summaries_to_completed_and_messages(self) -> None:
        finalizer = FakeFinalizerAgent(
            "Generator 阶段模型总结\n- 可选后续操作：继续提供输入"
        )
        node = FinalizeStageNode(finalizer, GENERATOR_FINALIZE_CONFIG)
        state = {
            "messages": [HumanMessage(content="先做计划再生成脚本")],
            "requested_pipeline": ["plan", "generator"],
            "pipeline_cursor": 1,
            "pending_stage_summaries": [
                {
                    "stage": "plan",
                    "status": "success",
                    "text": "Plan 阶段模型总结",
                    "finalization_key": "plan:run-1",
                }
            ],
            "stage_result": {
                "agent_type": "generator",
                "status": "success",
                "artifact": None,
                "raw_result": {"status": "success", "message": "脚本已生成"},
                "finalization_key": "generator:run-1",
            },
        }

        result = await node.execute(state)

        terminal_summary = result["messages"][0].content
        self.assertIn("Generator 阶段模型总结", terminal_summary)
        self.assertNotIn("可选后续操作", terminal_summary)
        self.assertIn("当前请求已完成，无需补充信息", terminal_summary)
        self.assertEqual(result["pending_stage_summaries"], [])
        self.assertEqual(
            [summary["stage"] for summary in result["completed_stage_summaries"]],
            ["plan", "generator"],
        )
        canonical_summary = str(finalizer.calls[0]["canonical_summary"])
        self.assertIn("当前请求已完成，无需补充信息", canonical_summary)

    async def test_failure_stage_is_terminal_even_when_pipeline_has_more_stages(self) -> None:
        finalizer = FakeFinalizerAgent("Plan 阶段失败总结")
        node = FinalizeStageNode(finalizer, PLAN_FINALIZE_CONFIG)
        state = {
            "messages": [HumanMessage(content="执行完整流程")],
            "requested_pipeline": ["plan", "generator", "healer"],
            "pipeline_cursor": 0,
            "stage_result": _plan_stage_result(status="error"),
        }

        result = await node.execute(state)

        self.assertEqual(result["messages"][0].content, "Plan 阶段失败总结")
        self.assertEqual(result["pending_stage_summaries"], [])
        self.assertEqual(len(result["completed_stage_summaries"]), 1)

    async def test_legacy_checkpoint_summary_does_not_call_model_or_duplicate_display(self) -> None:
        finalizer = FakeFinalizerAgent()
        node = FinalizeStageNode(finalizer, PLAN_FINALIZE_CONFIG)
        legacy_summary = {
            "artifact_id": "plan-artifact-1",
            "stage": "plan",
            "status": "success",
            "text": "旧版 Plan 阶段总结",
        }
        state = {
            "messages": [HumanMessage(content="执行计划")],
            "display_messages": [AIMessage(content="旧版 Plan 阶段总结")],
            "requested_pipeline": ["plan", "generator"],
            "pipeline_cursor": 0,
            "pending_stage_summaries": [legacy_summary],
            "stage_result": {
                **_plan_stage_result(),
                "finalization_key": None,
                "stage_summary": legacy_summary,
            },
        }

        result = await node.execute(state)

        self.assertEqual(finalizer.calls, [])
        self.assertNotIn("messages", result)
        self.assertNotIn("display_messages", result)
        self.assertEqual(len(result["pending_stage_summaries"]), 1)
        self.assertTrue(result["finalized_stage_keys"][0].startswith("legacy:plan:"))

    async def test_scheduler_uses_its_canonical_fallback_shape(self) -> None:
        finalizer = FakeFinalizerAgent("")
        node = FinalizeStageNode(finalizer, SCHEDULER_FINALIZE_CONFIG)
        state = {
            "messages": [HumanMessage(content="每天执行")],
            "stage_result": {
                "agent_type": "scheduler",
                "status": "success",
                "finalization_key": "scheduler:run-1",
                "raw_result": {
                    "status": "success",
                    "operation": "created",
                    "project_dir": "/tmp/demo",
                    "config_path": "/tmp/scheduler.json",
                    "task_id": "task-1",
                    "schedule": "0 9 * * *",
                    "headed": False,
                    "enabled": True,
                    "locations": ["tests/demo.spec.ts"],
                },
            },
        }

        result = await node.execute(state)

        canonical = str(finalizer.calls[0]["canonical_summary"])
        self.assertIn("**Scheduler 阶段**", canonical)
        self.assertIn("- Cron：`0 9 * * *`", canonical)
        self.assertIn("`tests/demo.spec.ts`", canonical)
        self.assertEqual(result["next_action"], "end")
        self.assertEqual(len(result["messages"]), 1)

    async def test_finalizer_agent_falls_back_on_model_error_and_empty_output(self) -> None:
        settings = AppSettings(_env_file=None)
        canonical = "规范阶段摘要"

        error_agent = FinalizerAgent(
            settings, model=CapturingModel(RuntimeError("model unavailable"))
        )
        self.assertEqual(
            await error_agent.finalize_stage(
                state={"messages": [HumanMessage(content="执行计划")]},
                stage_name="Plan Agent",
                stage_result={"status": "success"},
                canonical_summary=canonical,
                is_terminal=True,
            ),
            canonical,
        )

        empty_agent = FinalizerAgent(
            settings, model=CapturingModel(AIMessage(content="   "))
        )
        self.assertEqual(
            await empty_agent.finalize_stage(
                state={"messages": []},
                stage_name="Generator Agent",
                stage_result={"status": "success"},
                canonical_summary=canonical,
                is_terminal=False,
            ),
            canonical,
        )

    async def test_finalizer_agent_propagates_user_cancellation(self) -> None:
        agent = FinalizerAgent(
            AppSettings(_env_file=None), model=CapturingModel(UserInterrupt())
        )

        with self.assertRaises(UserInterrupt):
            await agent.finalize_stage(
                state={"messages": [HumanMessage(content="停止")]},
                stage_name="Healer Agent",
                stage_result={"status": "success"},
                canonical_summary="规范阶段摘要",
                is_terminal=True,
            )

    def test_all_supported_specialists_have_explicit_finalizer_config(self) -> None:
        configs: tuple[FinalizeStageConfig, ...] = (
            PLAN_FINALIZE_CONFIG,
            GENERATOR_FINALIZE_CONFIG,
            HEALER_FINALIZE_CONFIG,
            SCHEDULER_FINALIZE_CONFIG,
        )

        self.assertEqual(
            [config.stage for config in configs],
            ["plan", "generator", "healer", "scheduler"],
        )
        self.assertTrue(all(config.return_to_master for config in configs[:3]))
        self.assertFalse(configs[-1].return_to_master)

    def test_main_graph_routes_every_specialist_through_one_stage_finalizer(self) -> None:
        noop_agent = NoopGraphAgent()
        with (
            patch(
                "deep_agent.web_autotest_agent_workflow.get_settings",
                return_value=AppSettings(_env_file=None),
            ),
            patch(
                "deep_agent.web_autotest_agent_workflow.MasterAgent",
                return_value=object(),
            ),
            patch(
                "deep_agent.web_autotest_agent_workflow.build_master_graph",
                return_value=noop_agent.execute,
            ),
            patch(
                "deep_agent.web_autotest_agent_workflow.PlanAgent",
                return_value=noop_agent,
            ),
            patch(
                "deep_agent.web_autotest_agent_workflow.GeneratorAgent",
                return_value=noop_agent,
            ),
            patch(
                "deep_agent.web_autotest_agent_workflow.HealerAgent",
                return_value=noop_agent,
            ),
            patch(
                "deep_agent.web_autotest_agent_workflow.SchedulerAgent",
                return_value=noop_agent,
            ),
            patch(
                "deep_agent.web_autotest_agent_workflow.FinalizerAgent",
                return_value=FakeFinalizerAgent(),
            ),
        ):
            graph = build_web_autotest_agent_workflow().get_graph()

        edges = [(edge.source, edge.target) for edge in graph.edges]
        expected_edges = {
            ("plan_node", "finalize_plan_stage_node"),
            ("generator_node", "finalize_generator_stage_node"),
            ("healer_node", "finalize_healer_stage_node"),
            ("scheduler_config_node", "finalize_scheduler_stage_node"),
        }
        self.assertTrue(expected_edges.issubset(set(edges)))
        for source, target in expected_edges:
            self.assertEqual(edges.count((source, target)), 1)
        self.assertFalse(any("finalize_turn" in node_name for node_name in graph.nodes))


if __name__ == "__main__":
    unittest.main()

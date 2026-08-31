"""对外暴露的 scheduled-run 独立 LangGraph 工作流。"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from deep_agent.agent.scheduled_run import ScheduledRunAgent, ScheduledRunState
from deep_agent.core.config import get_settings


def build_scheduled_run_workflow(*, scheduled_run_agent: ScheduledRunAgent | Any | None = None):
    """构建带持久化幂等检查的定时执行图；checkpointer 由 LangGraph API 注入。"""

    agent = scheduled_run_agent or ScheduledRunAgent(get_settings())
    workflow = StateGraph(ScheduledRunState)
    workflow.add_node("prepare_scheduled_run", agent.prepare)
    workflow.add_node("execute_playwright", agent.execute_tests)
    workflow.add_node("summarize_execution", agent.summarize)
    workflow.add_node("diagnose_failures", agent.diagnose)
    workflow.add_node("heal_test_automation", agent.heal)
    workflow.add_node("finalize_scheduled_run", agent.finalize)
    workflow.add_edge(START, "prepare_scheduled_run")
    workflow.add_conditional_edges(
        "prepare_scheduled_run",
        _route_after_prepare,
        {"execute": "execute_playwright", "already_complete": END},
    )
    workflow.add_edge("execute_playwright", "summarize_execution")
    workflow.add_edge("summarize_execution", "diagnose_failures")
    workflow.add_edge("diagnose_failures", "heal_test_automation")
    workflow.add_edge("heal_test_automation", "finalize_scheduled_run")
    workflow.add_edge("finalize_scheduled_run", END)
    return workflow.compile()


def _route_after_prepare(state: ScheduledRunState) -> str:
    return "already_complete" if state.get("idempotent_replay") else "execute"


__all__ = ["build_scheduled_run_workflow"]

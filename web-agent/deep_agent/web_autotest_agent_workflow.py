"""Web AutoTest Agent 主工作流定义。"""

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from deep_agent.agent.finalizer import (
    GENERATOR_FINALIZE_CONFIG,
    HEALER_FINALIZE_CONFIG,
    PLAN_FINALIZE_CONFIG,
    SCHEDULER_FINALIZE_CONFIG,
    FinalizeStageNode,
    FinalizerAgent,
)
from deep_agent.agent.generator import GeneratorAgent
from deep_agent.agent.healer import HealerAgent
from deep_agent.agent.master import MasterAgent, build_master_graph
from deep_agent.agent.plan import PlanAgent
from deep_agent.agent.scheduler import SchedulerAgent
from deep_agent.agent.state import WorkflowState
from deep_agent.core.config import get_settings
from deep_agent.core.runtime_logging import (
    build_trace_context,
    get_logger,
    log_title,
    summarize_state,
)


logger = get_logger(__name__)


def build_web_autotest_agent_workflow(*, checkpointer: Any | None = None):
    """构建 Web AutoTest Agent 对外暴露的 LangGraph 主工作流。

    该函数由 `app.py` 在应用启动时调用，消费项目配置和各阶段 Agent，最终返回
    `web-autotest-agent` graph 对外运行所需的编译后主图。
    """

    logger.info(
        "%s 开始构建 Web AutoTest Agent 主工作流。",
        log_title("初始化", "图构建"),
    )
    settings = get_settings()
    master_agent = MasterAgent(settings)
    master_graph = build_master_graph(master_agent)
    plan_agent = PlanAgent(settings)
    generator_agent = GeneratorAgent(settings)
    healer_agent = HealerAgent(settings)
    scheduler_agent = SchedulerAgent(settings)
    finalizer_agent = FinalizerAgent(settings)
    plan_finalize_node = FinalizeStageNode(finalizer_agent, PLAN_FINALIZE_CONFIG)
    generator_finalize_node = FinalizeStageNode(
        finalizer_agent, GENERATOR_FINALIZE_CONFIG
    )
    healer_finalize_node = FinalizeStageNode(finalizer_agent, HEALER_FINALIZE_CONFIG)
    scheduler_finalize_node = FinalizeStageNode(
        finalizer_agent, SCHEDULER_FINALIZE_CONFIG
    )

    web_autotest_agent_workflow = StateGraph(WorkflowState)
    web_autotest_agent_workflow.add_node("master_graph_node", master_graph)
    web_autotest_agent_workflow.add_node(
        "finalize_plan_stage_node",
        plan_finalize_node.execute,
    )
    web_autotest_agent_workflow.add_node(
        "finalize_generator_stage_node",
        generator_finalize_node.execute,
    )
    web_autotest_agent_workflow.add_node(
        "finalize_healer_stage_node",
        healer_finalize_node.execute,
    )
    web_autotest_agent_workflow.add_node(
        "finalize_scheduler_stage_node",
        scheduler_finalize_node.execute,
    )
    web_autotest_agent_workflow.add_node("plan_node", plan_agent.execute)
    web_autotest_agent_workflow.add_node("generator_node", generator_agent.execute)
    web_autotest_agent_workflow.add_node("healer_node", healer_agent.execute)
    web_autotest_agent_workflow.add_node("scheduler_config_node", scheduler_agent.execute)

    web_autotest_agent_workflow.add_edge(START, "master_graph_node")
    web_autotest_agent_workflow.add_conditional_edges(
        "master_graph_node",
        _route_after_master,
        {
            "plan": "plan_node",
            "generator": "generator_node",
            "healer": "healer_node",
            "scheduler": "scheduler_config_node",
            "end": END,
        },
    )
    web_autotest_agent_workflow.add_edge("plan_node", "finalize_plan_stage_node")
    web_autotest_agent_workflow.add_edge(
        "generator_node", "finalize_generator_stage_node"
    )
    web_autotest_agent_workflow.add_edge(
        "healer_node", "finalize_healer_stage_node"
    )
    web_autotest_agent_workflow.add_edge(
        "scheduler_config_node", "finalize_scheduler_stage_node"
    )
    web_autotest_agent_workflow.add_edge(
        "finalize_plan_stage_node", "master_graph_node"
    )
    web_autotest_agent_workflow.add_edge(
        "finalize_generator_stage_node", "master_graph_node"
    )
    web_autotest_agent_workflow.add_edge(
        "finalize_healer_stage_node", "master_graph_node"
    )
    web_autotest_agent_workflow.add_edge("finalize_scheduler_stage_node", END)

    # LangGraph API / `langgraph dev` 会注入自己的持久化层；这里默认不绑定自定义
    # checkpointer，避免导出的 graph 在 CLI 加载阶段被直接拒绝。
    compile_kwargs = {}
    if checkpointer is not None:
        compile_kwargs["checkpointer"] = checkpointer
    compiled_workflow = web_autotest_agent_workflow.compile(**compile_kwargs)
    logger.info(
        "%s Web AutoTest Agent 主工作流构建完成。",
        log_title("初始化", "图构建"),
    )
    return compiled_workflow


# 保留旧函数名作为兼容入口，避免已有测试或外部脚本仍按历史名称导入时直接失效。
build_workflow = build_web_autotest_agent_workflow


def _route_after_master(state: WorkflowState, config: RunnableConfig | None = None) -> str:
    """根据 Master 子图输出选择主工作流下一跳。

    该函数只由主工作流条件边调用，消费 Master 子图写入的 `next_action`，最终保证主图
    路由到已声明的 Specialist 节点、汇总节点或结束分支。
    """

    next_action = state.get("next_action", "end")
    # 旧 checkpoint 可能仍携带已经废弃的整体汇总路由；恢复后直接结束，
    # 不能再次调用 Finalizer 复述已显示过的阶段结果。
    if next_action == "finalize_turn":
        next_action = "end"
    if next_action not in {
        "plan",
        "generator",
        "healer",
        "scheduler",
        "end",
    }:
        next_action = "end"
    logger.info(
        "%s event=route_decision trace=%s next_action=%s state=%s",
        log_title("路由", "条件路由", node_name="master_graph_node"),
        build_trace_context(
            config,
            node_name="master_graph_node",
            event_name="route_decision",
        ),
        next_action,
        summarize_state(state),
    )
    return next_action

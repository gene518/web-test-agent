"""Web Agent 根目录下的 LangGraph 工作流定义。"""

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from deep_agent.agent.generator import GeneratorAgent
from deep_agent.agent.healer import HealerAgent
from deep_agent.agent.master import MasterAgent
from deep_agent.agent.master.nodes import CompleteParamsNode, GeneralTestNode, IntentJudgeNode
from deep_agent.agent.plan import PlanAgent
from deep_agent.agent.state import WorkflowState
from deep_agent.core.config import get_settings
from deep_agent.core.runtime_logging import build_trace_context, get_logger, log_title, summarize_state


logger = get_logger(__name__)


def build_workflow():
    """构建对外暴露的 LangGraph 主工作流。"""

    logger.info("%s 开始构建 Web Agent 工作流。",
        log_title("初始化", "图构建"),)
    settings = get_settings()
    master_agent = MasterAgent(settings)
    master_graph = build_master_graph(master_agent)
    plan_agent = PlanAgent(settings)
    generator_agent = GeneratorAgent(settings)
    healer_agent = HealerAgent(settings)

    workflow = StateGraph(WorkflowState)
    workflow.add_node("master_graph_node", master_graph)
    workflow.add_node("plan_node", plan_agent.execute)
    workflow.add_node("generator_node", generator_agent.execute)
    workflow.add_node("healer_node", healer_agent.execute)

    workflow.add_edge(START, "master_graph_node")
    workflow.add_conditional_edges(
        "master_graph_node",
        _route_after_master,
        {
            "plan": "plan_node",
            "generator": "generator_node",
            "healer": "healer_node",
            "end": END,
        },
    )
    workflow.add_edge("plan_node", "master_graph_node")
    workflow.add_edge("generator_node", "master_graph_node")
    workflow.add_edge("healer_node", "master_graph_node")

    compiled_workflow = workflow.compile(checkpointer=InMemorySaver())
    logger.info("%s Web Agent 工作流构建完成。",
        log_title("初始化", "图构建"),)
    return compiled_workflow


def build_master_graph(master_agent: MasterAgent):
    """构建唯一的 Master 子图。"""

    intent_judge_node = IntentJudgeNode(master_agent)
    complete_params_node = CompleteParamsNode(master_agent)
    general_test_node = GeneralTestNode(master_agent)

    master_workflow = StateGraph(WorkflowState)
    master_workflow.add_node("intent_judge_node", intent_judge_node.execute)
    master_workflow.add_node("complete_params_node", complete_params_node.execute)
    master_workflow.add_node("general_test_node", general_test_node.execute)

    master_workflow.add_edge(START, "intent_judge_node")
    master_workflow.add_conditional_edges(
        "intent_judge_node",
        _route_after_intent,
        {
            "complete_params": "complete_params_node",
            "general": "general_test_node",
            "end": END,
        },
    )
    master_workflow.add_edge("complete_params_node", END)
    master_workflow.add_edge("general_test_node", END)

    return master_workflow.compile()


def _route_after_master(state: WorkflowState, config: RunnableConfig | None = None) -> str:
    """根据 Master 子图输出选择主工作流下一跳。"""

    next_action = state.get("next_action", "end")
    if next_action not in {"plan", "generator", "healer", "end"}:
        next_action = "end"
    logger.info("%s event=route_decision trace=%s next_action=%s state=%s",
        log_title("路由", "条件路由", node_name="master_graph_node"), build_trace_context(config, node_name="master_graph_node", event_name="route_decision"), next_action, summarize_state(state),)
    return next_action


def _route_after_intent(state: WorkflowState, config: RunnableConfig | None = None) -> str:
    """根据意图判断节点输出选择 Master 子图下一跳。"""

    next_action = state.get("next_action", "end")
    if next_action not in {"complete_params", "general", "end"}:
        next_action = "end"
    logger.info("%s event=route_decision trace=%s next_action=%s state=%s",
        log_title("路由", "Master子图路由", node_name="intent_judge_node"), build_trace_context(config, node_name="intent_judge_node", event_name="route_decision"), next_action, summarize_state(state),)
    return next_action

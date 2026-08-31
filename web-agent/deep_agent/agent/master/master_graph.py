"""Master 子图构建逻辑。

本模块只负责初始化 Master 子图，供主工作流 `build_web_autotest_agent_workflow()` 调用。
这样主图和 Master 子图的构建职责互不混在一起，后续维护节点路由时可以直接从
`agent/master` 目录内定位 Master 自身的图结构。
"""

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from deep_agent.agent.master.master_agent import MasterAgent
from deep_agent.agent.master.nodes import (
    CompleteParamsNode,
    GeneralTestNode,
    IntentJudgeNode,
    ResolveStageFilesNode,
)
from deep_agent.agent.state import WorkflowState
from deep_agent.core.runtime_logging import (
    build_trace_context,
    get_logger,
    log_title,
    summarize_state,
)


logger = get_logger(__name__)


def build_master_graph(master_agent: MasterAgent):
    """构建唯一的 Master 子图。

    该函数由主图构建函数调用，消费已经初始化好的 `MasterAgent`，最终产出可嵌入主图的
    编译后子图，用来完成意图判断、阶段输入解析、参数补全和普通问答。
    """

    intent_judge_node = IntentJudgeNode(master_agent)
    resolve_stage_files_node = ResolveStageFilesNode()
    complete_params_node = CompleteParamsNode(master_agent)
    general_test_node = GeneralTestNode(master_agent)

    master_workflow = StateGraph(WorkflowState)
    master_workflow.add_node("intent_judge_node", intent_judge_node.execute)
    master_workflow.add_node(
        "resolve_stage_files_node",
        resolve_stage_files_node.execute,
    )
    master_workflow.add_node("complete_params_node", complete_params_node.execute)
    master_workflow.add_node("general_test_node", general_test_node.execute)

    master_workflow.add_edge(START, "intent_judge_node")
    master_workflow.add_conditional_edges(
        "intent_judge_node",
        _route_after_intent,
        {
            "resolve_stage_files": "resolve_stage_files_node",
            "complete_params": "complete_params_node",
            "general": "general_test_node",
            "end": END,
        },
    )
    master_workflow.add_conditional_edges(
        "resolve_stage_files_node",
        _route_after_resolve,
        {
            "complete_params": "complete_params_node",
            "plan": END,
            "generator": END,
            "healer": END,
            "end": END,
        },
    )
    master_workflow.add_edge("complete_params_node", END)
    master_workflow.add_edge("general_test_node", END)

    return master_workflow.compile()


def _route_after_intent(state: WorkflowState, config: RunnableConfig | None = None) -> str:
    """根据意图判断节点输出选择 Master 子图下一跳。

    该方法只被 Master 子图条件边调用，消费节点写入的 `next_action`，最终保证子图只会走向
    已声明的节点或结束分支。
    """

    next_action = state.get("next_action", "end")
    if next_action == "finalize_turn":
        next_action = "end"
    if next_action not in {
        "resolve_stage_files",
        "complete_params",
        "general",
        "end",
    }:
        next_action = "end"
    logger.info(
        "%s event=route_decision trace=%s next_action=%s state=%s",
        log_title("路由", "Master子图路由", node_name="intent_judge_node"),
        build_trace_context(
            config,
            node_name="intent_judge_node",
            event_name="route_decision",
        ),
        next_action,
        summarize_state(state),
    )
    return next_action


def _route_after_resolve(state: WorkflowState, config: RunnableConfig | None = None) -> str:
    """根据文件解析节点输出选择 Master 子图下一跳。

    该方法只被 Master 子图的文件解析条件边调用，消费阶段输入解析结果，最终决定继续补参
    还是把控制权交还主图进入具体 Specialist。
    """

    next_action = state.get("next_action", "end")
    if next_action not in {"complete_params", "plan", "generator", "healer", "end"}:
        next_action = "end"
    logger.info(
        "%s event=route_decision trace=%s next_action=%s state=%s",
        log_title("路由", "文件解析路由", node_name="resolve_stage_files_node"),
        build_trace_context(
            config,
            node_name="resolve_stage_files_node",
            event_name="route_decision",
        ),
        next_action,
        summarize_state(state),
    )
    return next_action

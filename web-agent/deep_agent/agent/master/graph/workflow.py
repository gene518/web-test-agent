"""Master Agent 的 LangGraph 工作流定义。

这个文件的目的，是把“节点有哪些、如何路由、哪里结束”收敛成一张可编译的图，
让业务判断留在 Agent 内部，图本身只表达流程结构。
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from deep_agent.core.config import get_settings
from deep_agent.agent.generator import GeneratorAgent
from deep_agent.agent.healer import HealerAgent
from deep_agent.agent.master import MasterAgent
from deep_agent.agent.plan import PlanAgent
from deep_agent.core.runtime_logging import build_trace_context, get_logger, log_title, summarize_state
from deep_agent.agent.state import WorkflowState


logger = get_logger(__name__)


def build_workflow():
    """构建 Master 阶段使用的 LangGraph 工作流。

    这个函数不仅负责“把节点连起来”，更负责把配置、Agent 实例和图结构在启动期一次性固定好，
    这样运行时只需要消费编译后的图对象。

    Returns:
        CompiledStateGraph: 编译后的 LangGraph 图对象。

    Raises:
        None.
    """

    logger.info("%s 开始构建 Master 工作流。",
        log_title("初始化", "图构建"),)
    # 先读取全局配置；这里不需要手动传参，是因为 `get_settings()` 内部会自动从
    # 环境变量和 `.env` 文件里加载配置。后面的 Agent 和工具初始化都会复用这一份配置。
    settings = get_settings()
    # 这些对象分别对应 LangGraph 里的各个节点实现，后面会绑定到工作流中。
    # TODO(重点流程): 这里完成核心 Agent 实例化，后续图节点运行时都会复用这些对象。
    master_agent = MasterAgent(settings)
    plan_agent = PlanAgent(settings)
    generator_agent = GeneratorAgent(settings)
    healer_agent = HealerAgent(settings)

    # `StateGraph(WorkflowState)` 用来声明整张图共享同一份状态结构。
    workflow = StateGraph(WorkflowState)
    # `add_node` 注册的是“节点名 + 节点执行函数”，不是要求先创建一个独立 Node 对象。
    # 这里传入的 `execute` 会在节点运行时接收 state，并返回新的状态增量。
    workflow.add_node("master_node", master_agent.execute)
    workflow.add_node("ask_params_node", master_agent.ask_for_missing_params)
    workflow.add_node("general_node", master_agent.answer_general_request)
    workflow.add_node("plan_node", plan_agent.execute)
    workflow.add_node("generator_node", generator_agent.execute)
    workflow.add_node("healer_node", healer_agent.execute)

    # 起点先进入 master_node，由 Master 负责统一分类和路由。
    workflow.add_edge(START, "master_node")
    # 条件边会读取 `_route_after_master` 的返回值，决定从 Master 走向哪个节点。
    workflow.add_conditional_edges(
        "master_node",
        _route_after_master,
        {
            "general": "general_node",
            "ask_params": "ask_params_node",
            "plan": "plan_node",
            "generator": "generator_node",
            "healer": "healer_node",
        },
    )
    workflow.add_edge("ask_params_node", END)
    workflow.add_edge("general_node", END)
    workflow.add_edge("plan_node", END)
    workflow.add_edge("generator_node", END)
    workflow.add_edge("healer_node", END)

    # TODO(重点流程): 这里把声明式图结构编译成真正可运行的 LangGraph 对象，
    # 后续应用入口暴露给外部的就是这个编译结果。
    compiled_workflow = workflow.compile()
    logger.info("%s Master 工作流构建完成。",
        log_title("初始化", "图构建"),)
    return compiled_workflow


def _route_after_master(state: WorkflowState, config: RunnableConfig | None = None) -> str:
    """根据 `next_action` 选择条件边。

    这层路由函数只做状态读取，不重复做业务判断，目的是把“路由规则来源”稳定收敛到 Master 输出字段。

    Args:
        state: 当前工作流状态。

    Returns:
        str: 条件边目标键。

    Raises:
        None.
    """

    # 从共享状态里取出 Master 已经写好的路由结果；如果缺失则保守地走通用回答。
    next_action = state.get("next_action", "general")
    logger.info("%s event=route_decision trace=%s next_action=%s state=%s",
        log_title("路由", "条件路由", node_name="master_node"), build_trace_context(config, node_name="master_node", event_name="route_decision"), next_action, summarize_state(state),)
    return next_action

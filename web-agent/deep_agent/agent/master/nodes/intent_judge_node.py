"""Master 子图的意图判断节点。"""

from langchain_core.runnables import RunnableConfig

from deep_agent.agent.master.master_agent import MasterAgent
from deep_agent.agent.state import WorkflowState
from deep_agent.core.runtime_logging import build_trace_context, format_state_for_log, get_logger, log_title


logger = get_logger(__name__)


class IntentJudgeNode:
    """判断本轮请求意图，或在 Specialist 回流时执行收尾守卫。"""

    def __init__(self, master_agent: MasterAgent) -> None:
        """保存共享 Master 服务对象，供节点执行时复用模型和提示词。"""

        self._master_agent = master_agent

    async def execute(self, state: WorkflowState, config: RunnableConfig | None = None) -> WorkflowState:
        """执行意图判断节点。"""

        logger.info("%s event=node_enter trace=%s state=%s",
            log_title("执行", "节点入参", node_name="intent_judge_node"), build_trace_context(config, node_name="intent_judge_node", event_name="node_enter"), format_state_for_log(state),)

        if state.get("return_to_master"):
            result: WorkflowState = {
                "return_to_master": False,
                "next_action": "end",
                "routing_reason": "Specialist 阶段已完成并回到 Master，当前轮次结束。",
            }
            logger.info("%s event=node_exit trace=%s result=%s",
                log_title("执行", "节点出参", node_name="intent_judge_node"), build_trace_context(config, node_name="intent_judge_node", event_name="node_exit"), format_state_for_log(result),)
            return result

        classification_state = await self._master_agent.classify_intent_and_params(state, config=config)
        agent_type = classification_state.get("agent_type")
        if agent_type in {"plan", "generator", "healer"}:
            classification_state["next_action"] = "complete_params"
        elif agent_type == "general":
            classification_state["next_action"] = "general"
        else:
            classification_state["next_action"] = "general"

        logger.info("%s event=node_exit trace=%s result=%s",
            log_title("执行", "节点出参", node_name="intent_judge_node"), build_trace_context(config, node_name="intent_judge_node", event_name="node_exit"), format_state_for_log(classification_state),)
        return classification_state

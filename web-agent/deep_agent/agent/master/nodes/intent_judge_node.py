"""Master 子图的意图判断节点。"""

from langchain_core.runnables import RunnableConfig

from deep_agent.agent.artifacts import next_pipeline_stage
from deep_agent.agent.master.master_agent import MasterAgent
from deep_agent.agent.state import WorkflowState
from deep_agent.core.display_message import extract_missing_display_messages
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

        if state.get("pipeline_handoff") or state.get("return_to_master"):
            next_stage = next_pipeline_stage(state)
            stage_status = self._stage_status(state)
            if next_stage is not None and stage_status == "success":
                pipeline_cursor = state.get("pipeline_cursor", 0)
                next_cursor = pipeline_cursor + 1 if isinstance(pipeline_cursor, int) else 0
                result = {
                    "return_to_master": False,
                    "pipeline_handoff": False,
                    "agent_type": next_stage,
                    "pending_agent_type": next_stage,
                    "pipeline_cursor": next_cursor,
                    "missing_params": [],
                    "pending_missing_params": [],
                    "next_action": "resolve_stage_files",
                    "routing_reason": f"上一阶段完成，准备继续执行 `{next_stage}` 阶段。",
                }
            else:
                result = {
                    "return_to_master": False,
                    "pipeline_handoff": False,
                    "next_action": "finalize_turn",
                    "routing_reason": (
                        "当前轮阶段链已执行完成，准备统一汇总。"
                        if stage_status == "success"
                        else f"阶段链在 `{state.get('agent_type')}` 阶段结束，准备输出截至当前阶段的汇总。"
                    ),
                }
            result = self._with_display_delta(state, result)
            logger.info("%s event=node_exit trace=%s result=%s",
                log_title("执行", "节点出参", node_name="intent_judge_node"), build_trace_context(config, node_name="intent_judge_node", event_name="node_exit"), format_state_for_log(result),)
            return result

        classification_state = await self._master_agent.classify_intent_and_params(state, config=config)
        agent_type = classification_state.get("agent_type")
        if agent_type in {"plan", "generator", "healer"}:
            classification_state["next_action"] = "resolve_stage_files"
        elif agent_type == "scheduler":
            classification_state["next_action"] = "complete_params"
        elif agent_type == "general":
            classification_state["next_action"] = "general"
        else:
            classification_state["next_action"] = "general"

        classification_state = self._with_display_delta(state, classification_state)
        logger.info("%s event=node_exit trace=%s result=%s",
            log_title("执行", "节点出参", node_name="intent_judge_node"), build_trace_context(config, node_name="intent_judge_node", event_name="node_exit"), format_state_for_log(classification_state),)
        return classification_state

    def _with_display_delta(self, state: WorkflowState, result: WorkflowState) -> WorkflowState:
        """把主消息列表里尚未进入 UI 时间线的消息作为增量返回。"""

        display_delta = extract_missing_display_messages(dict(state))
        if not display_delta:
            return result
        return {
            **result,
            "display_messages": display_delta,
        }

    def _stage_status(self, state: WorkflowState) -> str:
        """读取当前阶段状态，默认把无显式错误视为成功。"""

        stage_result = state.get("stage_result", {})
        if not isinstance(stage_result, dict):
            return "success"
        raw_result = stage_result.get("raw_result", {})
        if isinstance(raw_result, dict):
            status = raw_result.get("status")
            if isinstance(status, str) and status:
                return status
        status = stage_result.get("status")
        if isinstance(status, str) and status:
            return status
        return "success"

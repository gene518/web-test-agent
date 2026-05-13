"""Master 子图的意图判断节点。"""

from langchain_core.runnables import RunnableConfig

from deep_agent.helpers.artifacts import next_pipeline_stage
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
            # 主链路：这里处理 Specialist 阶段回流后的下一步决策；
            # 如果还有后续阶段就继续推进，否则直接进入统一汇总。
            next_stage = next_pipeline_stage(state)
            stage_status = self._stage_status(state)
            pending_stage_count = self._pending_stage_summary_count(state)
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
            elif pending_stage_count <= 1:
                # 单阶段已经由 Specialist 自己把 stage_summary 作为用户可见消息发出，
                # 这里直接结束，避免 finalize_turn 把相同内容再原样复述一次。
                result = {
                    "return_to_master": False,
                    "pipeline_handoff": False,
                    "completed_stage_summaries": list(state.get("pending_stage_summaries", [])),
                    "pending_stage_summaries": [],
                    "current_turn_artifact_ids": [],
                    "next_action": "end",
                    "routing_reason": (
                        "当前轮为单阶段请求，Specialist 已输出用户可见总结，直接结束。"
                        if stage_status == "success"
                        else f"阶段链在 `{state.get('agent_type')}` 阶段提前结束，直接返回当前阶段结果。"
                    ),
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

        # 主链路：这里进入首轮意图识别；Master 会在此决定当前请求应该去写用例、
        # 写脚本、调试修复、改定时任务，还是直接走 general 回答。
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

    def _pending_stage_summary_count(self, state: WorkflowState) -> int:
        """返回本轮已累计的阶段摘要数量。

        调用方：回流分支判断当前轮到底经过了几个 Specialist。
        目的：`finalize_turn_node` 存在的价值是把多阶段摘要合并为一条；单阶段时
        Specialist 已经在 messages 里发过阶段摘要，再走一次 finalize 会造成 UI 重复。
        """

        pending = state.get("pending_stage_summaries")
        if not isinstance(pending, list):
            return 0
        return sum(1 for item in pending if isinstance(item, dict))

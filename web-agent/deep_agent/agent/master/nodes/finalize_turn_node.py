"""Master 工作流的最终汇总节点。"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from deep_agent.agent.artifacts import build_final_turn_summary, clear_current_turn_buffers
from deep_agent.core.display_message import (
    build_display_summary_message,
    extract_missing_display_messages,
)
from deep_agent.agent.state import WorkflowState
from deep_agent.core.runtime_logging import build_trace_context, format_messages_for_log, format_state_for_log, get_logger, log_title


logger = get_logger(__name__)


class FinalizeTurnNode:
    """将当前轮所有阶段摘要汇总成唯一用户可见回复。"""

    async def execute(self, state: WorkflowState, config: RunnableConfig | None = None) -> WorkflowState:
        """生成单条最终回复，并清理当前轮缓冲字段。"""

        logger.info("%s event=node_enter trace=%s state=%s",
            log_title("执行", "节点入参", node_name="finalize_turn_node"), build_trace_context(config, node_name="finalize_turn_node", event_name="node_enter"), format_state_for_log(state),)

        final_summary = build_final_turn_summary(state.get("pending_stage_summaries"))
        completed_stage_summaries = list(state.get("pending_stage_summaries", []))
        reset_buffers = clear_current_turn_buffers(dict(state))
        final_message = build_display_summary_message(
            final_summary,
            prefix="final-summary",
        )
        result: WorkflowState = {
            "messages": [final_message],
            "display_messages": [
                *extract_missing_display_messages(dict(state)),
                final_message,
            ],
            "final_summary": final_summary,
            "completed_stage_summaries": completed_stage_summaries,
            "next_action": "end",
            **reset_buffers,
        }
        logger.info("%s event=node_exit trace=%s messages=%s",
            log_title("执行", "节点出参", node_name="finalize_turn_node"), build_trace_context(config, node_name="finalize_turn_node", event_name="node_exit"), format_messages_for_log(result["messages"]),)
        return result

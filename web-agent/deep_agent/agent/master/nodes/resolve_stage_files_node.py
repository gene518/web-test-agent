"""Master 子图的阶段文件解析节点。"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from deep_agent.agent.artifacts import current_stage_from_pipeline, previous_pipeline_stage, resolve_stage_inputs
from deep_agent.agent.master.models.intent import compute_missing_params_for_intent
from deep_agent.agent.state import WorkflowState
from deep_agent.core.runtime_logging import build_trace_context, format_state_for_log, get_logger, log_title


logger = get_logger(__name__)


class ResolveStageFilesNode:
    """在进入 Specialist 前合并用户显式文件与历史可继承文件。"""

    async def execute(self, state: WorkflowState, config: RunnableConfig | None = None) -> WorkflowState:
        """解析当前阶段需要处理的文件，并决定是否还需要补参。"""

        logger.info("%s event=node_enter trace=%s state=%s",
            log_title("执行", "节点入参", node_name="resolve_stage_files_node"), build_trace_context(config, node_name="resolve_stage_files_node", event_name="node_enter"), format_state_for_log(state),)

        stage = current_stage_from_pipeline(state)
        if stage is None:
            result: WorkflowState = {
                "next_action": "end",
                "routing_reason": "文件解析节点未能识别当前阶段，直接结束当前轮次。",
            }
            logger.info("%s event=node_exit trace=%s result=%s",
                log_title("执行", "节点出参", node_name="resolve_stage_files_node"), build_trace_context(config, node_name="resolve_stage_files_node", event_name="node_exit"), format_state_for_log(result),)
            return result

        extracted_params = resolve_stage_inputs(
            stage=stage,
            extracted_params=dict(state.get("extracted_params", {})),
            latest_artifacts=state.get("latest_artifacts"),
            previous_stage=previous_pipeline_stage(state),
        )
        missing_params = compute_missing_params_for_intent(stage, extracted_params)
        result: WorkflowState = {
            "agent_type": stage,
            "pending_agent_type": stage,
            "extracted_params": extracted_params,
            "missing_params": missing_params,
            "pending_missing_params": missing_params,
            "next_action": "complete_params" if missing_params else stage,
            "routing_reason": (
                f"{stage} 阶段文件解析后仍缺少参数，回到参数补全。"
                if missing_params
                else f"{stage} 阶段文件解析完成，准备进入对应 Specialist。"
            ),
        }
        logger.info("%s event=node_exit trace=%s result=%s",
            log_title("执行", "节点出参", node_name="resolve_stage_files_node"), build_trace_context(config, node_name="resolve_stage_files_node", event_name="node_exit"), format_state_for_log(result),)
        return result

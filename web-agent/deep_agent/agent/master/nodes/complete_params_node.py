"""Master 子图的参数补全节点。"""

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from deep_agent.agent.master.master_agent import MasterAgent
from deep_agent.agent.master.models.intent import compute_missing_params_for_intent
from deep_agent.agent.state import WorkflowState
from deep_agent.core.runtime_logging import build_trace_context, format_state_for_log, get_logger, log_title


logger = get_logger(__name__)


class CompleteParamsNode:
    """检查并补齐 Specialist 必填参数。"""

    def __init__(self, master_agent: MasterAgent) -> None:
        """保存共享 Master 服务对象，供补参抽取和追问文案复用。"""

        self._master_agent = master_agent

    async def execute(self, state: WorkflowState, config: RunnableConfig | None = None) -> WorkflowState:
        """执行参数完整性判断；缺参时使用 LangGraph interrupt 暂停。"""

        logger.info("%s event=node_enter trace=%s state=%s",
            log_title("执行", "节点入参", node_name="complete_params_node"), build_trace_context(config, node_name="complete_params_node", event_name="node_enter"), format_state_for_log(state),)

        agent_type = str(state.get("pending_agent_type") or state.get("agent_type") or "")
        if agent_type not in {"plan", "generator", "healer"}:
            return {"next_action": "end", "routing_reason": "参数补全节点未收到可执行 Specialist 意图。"}

        extracted_params = dict(state.get("extracted_params", {}))
        routing_reason = state.get("routing_reason")
        missing_params = compute_missing_params_for_intent(agent_type, extracted_params)

        while missing_params:
            missing_param = missing_params[0]
            payload = self._master_agent.build_missing_param_interrupt_payload(
                agent_type=agent_type,
                missing_param=missing_param,
                extracted_params=extracted_params,
            )
            resume_value = interrupt(payload)
            resume_text = self._resume_value_to_text(resume_value)
            new_params = await self._master_agent.extract_params_for_fixed_intent(
                agent_type=agent_type,
                existing_params=extracted_params,
                resume_text=resume_text,
                routing_reason=routing_reason,
                config=config,
            )
            extracted_params = self._master_agent.merge_extracted_params(extracted_params, new_params)
            missing_params = compute_missing_params_for_intent(agent_type, extracted_params)

        result: WorkflowState = {
            "agent_type": agent_type,
            "pending_agent_type": agent_type,
            "extracted_params": extracted_params,
            "missing_params": [],
            "pending_missing_params": [],
            "next_action": agent_type,
            "routing_reason": f"{agent_type} 参数已补齐，准备进入对应 Specialist。",
        }
        logger.info("%s event=node_exit trace=%s result=%s",
            log_title("执行", "节点出参", node_name="complete_params_node"), build_trace_context(config, node_name="complete_params_node", event_name="node_exit"), format_state_for_log(result),)
        return result

    def _resume_value_to_text(self, resume_value: Any) -> str:
        """把 LangGraph resume 载荷转成用户补参文本。"""

        if isinstance(resume_value, str):
            return resume_value
        if isinstance(resume_value, dict):
            for key in ("content", "text", "message", "answer"):
                value = resume_value.get(key)
                if value:
                    return str(value)
        return str(resume_value)

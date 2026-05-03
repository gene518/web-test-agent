"""Master 子图的通用测试专家问答节点。"""

from langchain_core.runnables import RunnableConfig

from deep_agent.core.display_message import build_display_summary_message, extract_missing_display_messages
from deep_agent.agent.master.master_agent import MasterAgent
from deep_agent.agent.state import WorkflowState
from deep_agent.core.runtime_logging import build_trace_context, format_messages_for_log, format_state_for_log, get_logger, log_title


logger = get_logger(__name__)


class GeneralTestNode:
    """对 general 请求给出测试专家回答，并包装成最终总结。"""

    def __init__(self, master_agent: MasterAgent) -> None:
        """保存共享 Master 服务对象。"""

        self._master_agent = master_agent

    async def execute(self, state: WorkflowState, config: RunnableConfig | None = None) -> WorkflowState:
        """执行通用测试专家问答。"""

        logger.info("%s event=node_enter trace=%s state=%s",
            log_title("执行", "节点入参", node_name="general_test_node"), build_trace_context(config, node_name="general_test_node", event_name="node_enter"), format_state_for_log(state),)

        raw_answer = await self._master_agent.answer_general_request(state, config=config)
        final_summary = await self._master_agent.summarize_final_response(
            state=state,
            stage_name="General Test Agent",
            raw_result=raw_answer,
            config=config,
        )
        final_message = build_display_summary_message(
            final_summary,
            prefix="general-summary",
        )
        result: WorkflowState = {
            "messages": [final_message],
            "display_messages": [
                *extract_missing_display_messages(dict(state)),
                final_message,
            ],
            "stage_result": {
                "agent_type": "general",
                "raw_answer": raw_answer,
            },
            "final_summary": final_summary,
            "next_action": "end",
        }
        logger.info("%s event=node_exit trace=%s messages=%s",
            log_title("执行", "节点出参", node_name="general_test_node"), build_trace_context(config, node_name="general_test_node", event_name="node_exit"), format_messages_for_log(result["messages"]),)
        return result

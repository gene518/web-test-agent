"""历史线程标题的无状态 LangGraph 工作流。"""

from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from deep_agent.agent.master.models.thread_title import ThreadTitleGeneration
from deep_agent.agent.master.prompts.thread_title import THREAD_TITLE_SYSTEM_PROMPT
from deep_agent.core.cancellation import is_langgraph_user_cancellation
from deep_agent.core.config import AppSettings, get_settings
from deep_agent.core.runtime_logging import get_logger, log_title
from deep_agent.model import (
    adapt_chat_model,
    invoke_structured,
    resolve_model_capabilities,
)


logger = get_logger(__name__)
THREAD_TITLE_SOURCE_MAX_LENGTH = 8000


class ThreadTitleState(TypedDict, total=False):
    """标题图内部状态。"""

    source_text: str
    thread_title: str | None


class ThreadTitleInput(TypedDict):
    """标题图公开输入。"""

    source_text: str


class ThreadTitleOutput(TypedDict):
    """标题图公开输出。"""

    thread_title: str | None


class ThreadTitleNode:
    """使用 Master 模型配置为一段历史输入生成标题。"""

    def __init__(self, settings: AppSettings) -> None:
        self._connection = settings.resolve_model_connection("master")
        self._capabilities = resolve_model_capabilities(self._connection)
        raw_model = init_chat_model(**settings.build_model_kwargs(role="master"))
        self._model = adapt_chat_model(
            raw_model,
            connection=self._connection,
            capabilities=self._capabilities,
        )

    async def execute(
        self,
        state: ThreadTitleState,
        config: RunnableConfig | None = None,
    ) -> ThreadTitleOutput:
        """生成标题；失败时返回空标题，避免影响任何业务任务。"""

        source_text = state.get("source_text")
        if not isinstance(source_text, str) or not source_text.strip():
            return {"thread_title": None}

        normalized_source = source_text.strip()[:THREAD_TITLE_SOURCE_MAX_LENGTH]
        messages = [
            SystemMessage(content=THREAD_TITLE_SYSTEM_PROMPT),
            HumanMessage(content=normalized_source),
        ]
        try:
            result = await invoke_structured(
                model=self._model,
                schema=ThreadTitleGeneration,
                messages=messages,
                capabilities=self._capabilities,
                connection=self._connection,
                config=config,
            )
        except Exception as exc:  # noqa: BLE001
            if is_langgraph_user_cancellation(exc):
                raise
            logger.warning(
                "%s 历史线程标题生成失败，返回空标题。error=%s",
                log_title("模型", "标题生成兜底", node_name="thread_title_node"),
                exc,
            )
            return {"thread_title": None}
        return {"thread_title": result.parsed.thread_title}


def build_thread_title_workflow(*, title_node: ThreadTitleNode | Any | None = None):
    """构建不绑定 checkpointer 的历史线程标题图。"""

    resolved_node = title_node or ThreadTitleNode(get_settings())
    workflow = StateGraph(
        ThreadTitleState,
        input_schema=ThreadTitleInput,
        output_schema=ThreadTitleOutput,
    )
    workflow.add_node("thread_title_node", resolved_node.execute)
    workflow.add_edge(START, "thread_title_node")
    workflow.add_edge("thread_title_node", END)
    return workflow.compile()

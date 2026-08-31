"""Specialist 阶段共用的最终总结 Agent。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from deep_agent.agent.state import WorkflowState
from deep_agent.core.cancellation import is_langgraph_user_cancellation
from deep_agent.core.config import AppSettings
from deep_agent.core.runtime_logging import (
    build_trace_context,
    debug_max_chars,
    get_logger,
    log_debug_event,
    log_title,
    summarize_model_kwargs,
)
from deep_agent.model import adapt_chat_model, resolve_model_capabilities


logger = get_logger(__name__)


FINALIZER_SYSTEM_PROMPT = """\
你是 Web AutoTest Agent 的阶段结果整理器。

你只负责整理刚刚结束的一个 Specialist 阶段，不重新执行任务，也不总结整个工作流。
请以提供的规范摘要为事实基线，结合原始阶段结果生成简洁、准确的中文阶段总结。

要求：
- 保留规范摘要中的阶段名称、状态、文件路径、数量和错误事实，不得虚构执行结果。
- 不暴露内部模型消息、MCP 调用流水、调试日志或 finalization_key。
- 中间阶段只说明本阶段已完成，不要声称整个请求已经完成，也不要要求用户补充下一阶段输入。
- 终止成功阶段要明确当前请求已经完成、无需补充信息。
- 只有原始结果明确表示缺参或失败时，才可以要求用户补充或处理问题。
- 只输出最终阶段总结，不添加前言或解释。
"""


class FinalizerAgent:
    """使用 master role 模型整理 Plan/Generator/Healer/Scheduler 结果。"""

    def __init__(self, settings: AppSettings, *, model: Any | None = None) -> None:
        self._settings = settings
        if model is not None:
            self._model = model
            return

        model_connection = settings.resolve_model_connection("master")
        model_kwargs = settings.build_model_kwargs(role="master")
        raw_model = init_chat_model(**model_kwargs)
        self._model = adapt_chat_model(
            raw_model,
            connection=model_connection,
            capabilities=resolve_model_capabilities(model_connection),
        )
        logger.info(
            "%s Finalizer 模型初始化完成 model_kwargs=%s",
            log_title("初始化", "模型初始化", node_name="finalize_stage_node"),
            summarize_model_kwargs(model_kwargs),
        )

    async def finalize_stage(
        self,
        *,
        state: WorkflowState,
        stage_name: str,
        stage_result: dict[str, Any],
        canonical_summary: str,
        is_terminal: bool,
        config: RunnableConfig | None = None,
    ) -> str:
        """生成一次阶段总结；模型异常或空输出时返回规范摘要。"""

        latest_user_request = self._latest_human_message_text(
            state.get("messages", [])
        )
        prompt = (
            f"阶段：{stage_name}\n"
            f"是否为本轮终止阶段：{'是' if is_terminal else '否'}\n"
            f"用户要求：{latest_user_request or '未识别到明确用户原文'}\n\n"
            f"规范摘要：\n{canonical_summary}\n\n"
            f"阶段原始结果：\n{self._format_stage_result(stage_result)}"
        )
        model_messages = [
            SystemMessage(content=FINALIZER_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]
        trace = build_trace_context(
            config,
            node_name=f"finalize_{stage_name}_stage_node",
            event_name="model_start",
        )
        log_debug_event(
            logger,
            self._settings,
            log_title("模型", "调用", node_name="finalize_stage_node"),
            "model_start",
            trace,
            model="stage_finalizer",
            messages=model_messages,
        )
        try:
            response = await self._model.ainvoke(model_messages, config=config)
        except Exception as exc:  # noqa: BLE001
            if is_langgraph_user_cancellation(exc):
                raise
            logger.warning(
                "%s 阶段总结模型调用失败，使用规范摘要。stage=%s error=%s",
                log_title("模型", "总结兜底", node_name="finalize_stage_node"),
                stage_name,
                exc,
            )
            return canonical_summary

        log_debug_event(
            logger,
            self._settings,
            log_title("模型", "调用", node_name="finalize_stage_node"),
            "model_end",
            build_trace_context(
                config,
                node_name=f"finalize_{stage_name}_stage_node",
                event_name="model_end",
            ),
            model="stage_finalizer",
            messages=[response],
        )
        summary = self._message_to_text(response).strip()
        return summary or canonical_summary

    def _format_stage_result(self, stage_result: dict[str, Any]) -> str:
        """限制原始结果体积，避免阶段产物把总结上下文撑爆。"""

        text = "\n".join(f"{key}: {value}" for key, value in stage_result.items())
        max_chars = debug_max_chars(self._settings)
        if len(text) <= max_chars:
            return text
        return f"{text[:max_chars]}... [truncated]"

    def _latest_human_message_text(self, messages: Sequence[Any]) -> str:
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                return self._message_to_text(message)
        return ""

    def _message_to_text(self, message: Any) -> str:
        if isinstance(message, BaseMessage):
            content = message.content
        else:
            content = getattr(message, "content", message)
        return content if isinstance(content, str) else str(content)

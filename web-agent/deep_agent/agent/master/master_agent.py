"""Master 子图共享能力。

这个模块不再直接承载所有 LangGraph 节点逻辑，而是提供 Master 子图内多个节点共用的
模型调用、参数整理、追问文案、通用问答和结果总结能力。具体节点分别放在
`master/nodes/` 下，避免单个文件继续膨胀。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from deep_agent.agent.artifacts import summarize_latest_artifacts
from deep_agent.agent.master.models.intent import (
    IntentClassification,
    build_extracted_params,
    compute_missing_params_for_intent,
    build_requested_pipeline,
)
from deep_agent.agent.master.prompts.complete_params import build_master_complete_params_prompt
from deep_agent.agent.master.prompts.general_test import GENERAL_TEST_SYSTEM_PROMPT
from deep_agent.agent.master.prompts.intent_judge import INTENT_JUDGE_SYSTEM_PROMPT
from deep_agent.agent.master.prompts.summary import FINAL_RESPONSE_SUMMARY_SYSTEM_PROMPT
from deep_agent.agent.state import WorkflowState
from deep_agent.core.config import AppSettings
from deep_agent.core.runtime_logging import (
    build_trace_context,
    debug_max_chars,
    get_logger,
    log_debug_event,
    log_title,
    summarize_model_kwargs,
)


logger = get_logger(__name__)
SPECIALIST_AGENT_TYPES = frozenset({"plan", "generator", "healer"})
RECENT_MESSAGES_AFTER_SUMMARY_LIMIT = 40


class MasterAgent:
    """Master 子图节点共享的轻量服务对象。

    它只负责可复用的模型调用和格式化逻辑；`intent_judge_node`、
    `complete_params_node`、`general_test_node` 才是真正的图节点。
    """

    def __init__(self, settings: AppSettings) -> None:
        """初始化 Master 使用的模型。

        Args:
            settings: 应用运行配置，后续会被节点用于读取模型名、超时、对话轮数等。
        """

        self._settings = settings
        model_kwargs = settings.build_model_kwargs(settings.master_model)
        # 这个模型实例同时用于意图识别、参数补全、general 回答、历史压缩和最终总结。
        self._model = init_chat_model(**model_kwargs)
        logger.info("%s Master 模型初始化完成 model_kwargs=%s",
            log_title("初始化", "模型初始化"), summarize_model_kwargs(model_kwargs),)

    async def classify_intent_and_params(
        self,
        state: WorkflowState,
        config: RunnableConfig | None = None,
    ) -> WorkflowState:
        """识别用户意图并抽取第一批参数。"""

        classifier_model = self._model.with_structured_output(
            IntentClassification,
            method="function_calling",
        )
        state_with_summary = await self.ensure_conversation_summary(state, config=config)
        model_messages = self._build_classifier_messages(state_with_summary)
        log_debug_event(logger, self._settings, log_title("模型", "调用"), "model_start", build_trace_context(config, node_name="intent_judge_node", event_name="model_start"), model="master_classifier", messages=model_messages)
        classification = await classifier_model.ainvoke(model_messages, config=config)
        log_debug_event(logger, self._settings, log_title("模型", "调用"), "model_end", build_trace_context(config, node_name="intent_judge_node", event_name="model_end"), model="master_classifier", output=classification.model_dump())

        extracted_params = build_extracted_params(classification)
        latest_user_request = self.latest_human_message_text(state.get("messages", []))
        requested_pipeline = build_requested_pipeline(classification, latest_user_request=latest_user_request)
        resolved_agent_type = requested_pipeline[0] if requested_pipeline else classification.intent_type
        missing_params = compute_missing_params_for_intent(resolved_agent_type, extracted_params)
        result: WorkflowState = {
            "agent_type": resolved_agent_type,
            "pending_agent_type": resolved_agent_type if resolved_agent_type in SPECIALIST_AGENT_TYPES else None,
            "extracted_params": extracted_params,
            "missing_params": missing_params,
            "pending_missing_params": missing_params,
            "routing_reason": classification.reasoning or "Master completed routing analysis.",
            "requested_pipeline": requested_pipeline,
            "pipeline_cursor": 0,
            "pending_stage_summaries": [],
            "current_turn_artifact_ids": [],
        }
        if "conversation_summary" in state_with_summary:
            result["conversation_summary"] = state_with_summary["conversation_summary"]
        if "summarized_message_count" in state_with_summary:
            result["summarized_message_count"] = state_with_summary["summarized_message_count"]
        return result

    async def extract_params_for_fixed_intent(
        self,
        *,
        agent_type: str,
        existing_params: dict[str, Any],
        resume_text: str,
        routing_reason: str | None,
        config: RunnableConfig | None = None,
    ) -> dict[str, Any]:
        """从 interrupt 恢复文本中抽取参数，并固定原始意图不变。"""

        classifier_model = self._model.with_structured_output(
            IntentClassification,
            method="function_calling",
        )
        context_prompt = build_master_complete_params_prompt(
            agent_type=agent_type,
            extracted_params=existing_params,
            missing_params=compute_missing_params_for_intent(agent_type, existing_params),
            routing_reason=routing_reason,
        )
        fixed_intent_prompt = (
            f"当前正在补齐 `{agent_type}` 任务的参数。"
            "只从用户补充内容中抽取参数，不要切换任务意图；"
            f"结构化输出里的 intent_type 必须继续使用 `{agent_type}`。"
        )
        model_messages = [
            SystemMessage(content=INTENT_JUDGE_SYSTEM_PROMPT),
            SystemMessage(content=context_prompt),
            SystemMessage(content=fixed_intent_prompt),
            HumanMessage(content=resume_text),
        ]
        log_debug_event(logger, self._settings, log_title("模型", "调用"), "model_start", build_trace_context(config, node_name="complete_params_node", event_name="model_start"), model="master_param_completion", messages=model_messages)
        classification = await classifier_model.ainvoke(model_messages, config=config)
        log_debug_event(logger, self._settings, log_title("模型", "调用"), "model_end", build_trace_context(config, node_name="complete_params_node", event_name="model_end"), model="master_param_completion", output=classification.model_dump())
        return build_extracted_params(classification)

    async def answer_general_request(
        self,
        state: WorkflowState,
        config: RunnableConfig | None = None,
    ) -> str:
        """按测试专家提示词回答 general 请求，并返回原始回答文本。"""

        state_with_summary = await self.ensure_conversation_summary(state, config=config)
        model_messages = [
            SystemMessage(content=GENERAL_TEST_SYSTEM_PROMPT),
            *self._messages_for_model(state_with_summary),
        ]
        log_debug_event(logger, self._settings, log_title("模型", "调用"), "model_start", build_trace_context(config, node_name="general_test_node", event_name="model_start"), model="master_general", messages=model_messages)
        response = await self._model.ainvoke(model_messages, config=config)
        log_debug_event(logger, self._settings, log_title("模型", "调用"), "model_end", build_trace_context(config, node_name="general_test_node", event_name="model_end"), model="master_general", messages=[response])
        return self._message_to_text(response)

    async def summarize_final_response(
        self,
        *,
        state: WorkflowState,
        stage_name: str,
        raw_result: Any,
        config: RunnableConfig | None = None,
    ) -> str:
        """把某个阶段的原始结果包装成用户最终可见总结。"""

        latest_user_request = self.latest_human_message_text(state.get("messages", []))
        result_text = self._format_raw_result(raw_result)
        summary_request = (
            f"阶段：{stage_name}\n"
            f"用户要求：{latest_user_request or '未识别到明确用户原文'}\n"
            f"阶段原始结果：\n{result_text}\n\n"
            "请生成最终回复，必须覆盖：用户要求什么、分析怎么做、如何做、完成了什么。"
        )
        model_messages = [
            SystemMessage(content=FINAL_RESPONSE_SUMMARY_SYSTEM_PROMPT),
            HumanMessage(content=summary_request),
        ]
        log_debug_event(logger, self._settings, log_title("模型", "调用"), "model_start", build_trace_context(config, node_name="summary", event_name="model_start"), model="master_summary", messages=model_messages)
        try:
            response = await self._model.ainvoke(model_messages, config=config)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s 总结模型调用失败，回退到阶段原始结果。error=%s",
                log_title("模型", "总结兜底", node_name="summary"), exc,)
            return result_text
        log_debug_event(logger, self._settings, log_title("模型", "调用"), "model_end", build_trace_context(config, node_name="summary", event_name="model_end"), model="master_summary", messages=[response])
        return self._message_to_text(response)

    async def ensure_conversation_summary(
        self,
        state: WorkflowState,
        config: RunnableConfig | None = None,
    ) -> WorkflowState:
        """在超过配置轮数后压缩历史，并返回带摘要字段的状态副本。"""

        messages = state.get("messages", [])
        human_turns = self._count_human_turns(messages)
        if human_turns <= self._settings.max_conversation_turns:
            return state

        summarized_count = int(state.get("summarized_message_count") or 0)
        if summarized_count >= len(messages):
            return state

        existing_summary = state.get("conversation_summary") or ""
        prompt = (
            "请压缩以下长对话历史，保留用户目标、已确认参数、重要阶段结果、未解决问题。"
            "输出中文摘要，避免逐字复述。\n\n"
            f"已有摘要：\n{existing_summary or '暂无'}\n\n"
            f"待压缩消息：\n{self._format_messages(messages)}"
        )
        model_messages = [
            SystemMessage(content="你负责压缩 Web AutoTest Agent 的长对话历史。"),
            HumanMessage(content=prompt),
        ]
        response = await self._model.ainvoke(model_messages, config=config)
        summarized_state: WorkflowState = dict(state)
        summarized_state["conversation_summary"] = self._message_to_text(response)
        summarized_state["summarized_message_count"] = len(messages)
        return summarized_state

    def build_missing_param_interrupt_payload(
        self,
        *,
        agent_type: str,
        missing_param: str,
        extracted_params: dict[str, Any],
    ) -> dict[str, Any]:
        """构建 LangGraph interrupt 需要返回给调用方的补参载荷。"""

        return {
            "agent_type": agent_type,
            "missing_param": missing_param,
            "question": self.question_for_field(missing_param),
            "known_context": self.format_known_context(extracted_params),
        }

    def merge_extracted_params(self, existing_params: dict[str, Any], new_params: dict[str, Any]) -> dict[str, Any]:
        """合并参数补全结果，新识别出的非空字段覆盖旧值。"""

        merged_params = dict(existing_params)
        for key, value in new_params.items():
            if value is None:
                continue
            if isinstance(value, list) and not value:
                continue
            merged_params[key] = value
        return merged_params

    def question_for_field(self, field_name: str) -> str:
        """把内部字段名映射为用户可读追问。"""

        mapping = {
            "project_name": "请提供自动化工程名字，例如 `baidu-web` 或 `an-autotest-demo`；如果已有现成工程目录，也可以直接提供 `project_dir`。",
            "url": "请提供被测页面的完整 URL。",
            "feature_points": "请提供你希望覆盖的功能点列表，至少 1 条。",
            "test_plan_files": "请提供待生成脚本的测试计划文件或目录路径，至少 1 个；优先传相对 `project_dir` 的路径。",
            "test_cases": "请提供待生成脚本的测试用例列表，至少 1 条。",
            "test_scripts": "请提供待调试脚本文件或目录路径，至少 1 个；优先传相对 `project_dir` 的路径。",
        }
        return mapping.get(field_name, "请补充这部分关键信息。")

    def format_known_context(self, extracted_params: dict[str, Any]) -> str:
        """将已识别参数格式化成便于用户确认的摘要。"""

        if not extracted_params:
            return "暂无。"
        return "；".join(f"{key}={value}" for key, value in extracted_params.items())

    def latest_human_message_text(self, messages: Sequence[BaseMessage]) -> str:
        """返回最近一条用户消息文本。"""

        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                return self._message_to_text(message)
        return ""

    def _build_classifier_messages(self, state: WorkflowState) -> list[BaseMessage]:
        """构造意图识别模型输入，始终显式携带 Master 系统提示词。"""

        model_messages: list[BaseMessage] = [SystemMessage(content=INTENT_JUDGE_SYSTEM_PROMPT)]
        conversation_summary = state.get("conversation_summary")
        if conversation_summary:
            model_messages.append(SystemMessage(content=f"## 历史摘要\n{conversation_summary}"))
        artifact_context = summarize_latest_artifacts(state.get("latest_artifacts"))
        if artifact_context:
            model_messages.append(SystemMessage(content=artifact_context))
        model_messages.extend(self._messages_for_model(state))
        return model_messages

    def _messages_for_model(self, state: WorkflowState) -> list[BaseMessage]:
        """返回送入模型的近期消息，长对话时配合摘要控制上下文长度。"""

        messages = list(state.get("messages", []))
        if state.get("conversation_summary") and len(messages) > RECENT_MESSAGES_AFTER_SUMMARY_LIMIT:
            return messages[-RECENT_MESSAGES_AFTER_SUMMARY_LIMIT:]
        return messages

    def _count_human_turns(self, messages: Sequence[BaseMessage]) -> int:
        """统计用户消息轮数。"""

        return sum(1 for message in messages if isinstance(message, HumanMessage))

    def _message_to_text(self, message: BaseMessage) -> str:
        """把任意消息内容压成字符串。"""

        content = message.content
        return content if isinstance(content, str) else str(content)

    def _format_messages(self, messages: Sequence[BaseMessage]) -> str:
        """把消息列表压成摘要模型可读文本。"""

        max_chars = debug_max_chars(self._settings)
        lines = [f"- {message.__class__.__name__}: {self._message_to_text(message)}" for message in messages]
        text = "\n".join(lines)
        if len(text) <= max_chars:
            return text
        return f"{text[:max_chars]}... [truncated]"

    def _format_raw_result(self, raw_result: Any) -> str:
        """把阶段原始结果转成总结模型可消费文本。"""

        if isinstance(raw_result, dict):
            parts = []
            for key, value in raw_result.items():
                if key == "messages" and isinstance(value, list):
                    parts.append(f"{key}: {self._format_messages(value)}")
                else:
                    parts.append(f"{key}: {value}")
            return "\n".join(parts)
        if isinstance(raw_result, list):
            return self._format_messages(raw_result)
        return str(raw_result)

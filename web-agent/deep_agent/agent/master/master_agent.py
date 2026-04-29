"""Master Agent：负责识别、补参、路由和通用回答。

本文件的重点不是单纯“调一次模型”，而是把入口请求先分流成稳定的结构化决策，
让后续 Specialist 只处理自己擅长的任务，同时把缺参追问和通用问答也统一收口。
"""

from __future__ import annotations

from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from deep_agent.agent.base_agent import BaseAgent
from deep_agent.core.config import AppSettings
from deep_agent.agent.master.models.intent import (
    IntentClassification,
    build_extracted_params,
    compute_missing_params,
)
from deep_agent.agent.master.prompts.ask_params_context import build_master_ask_params_context_prompt
from deep_agent.agent.master.prompts.general import GENERAL_ASSISTANT_SYSTEM_PROMPT
from deep_agent.agent.master.prompts.router import MASTER_ROUTER_SYSTEM_PROMPT
from deep_agent.core.runtime_logging import (
    build_trace_context,
    format_messages_for_log,
    format_state_for_log,
    get_logger,
    log_debug_event,
    log_title,
    summarize_model_kwargs,
)
from deep_agent.agent.state import WorkflowState


logger = get_logger(__name__)
SPECIALIST_AGENT_TYPES = frozenset({"plan", "generator", "healer"})


class MasterAgent(BaseAgent):
    """驱动工作流第一阶段主流程的核心 Agent。

    它存在的目的，是先把原始用户表达翻译成“属于哪个 Specialist、当前还缺哪些参数、
    下一跳节点是什么”这几个稳定结论，从而降低后续节点的判断复杂度。
    """

    def __init__(self, settings: AppSettings) -> None:
        """初始化 Master Agent。

        Args:
            settings: 应用运行配置。

        Returns:
            None.

        Raises:
            None.
        """

        self._settings = settings
        # 先把模型初始化参数统一从配置对象里组装出来，目的是避免每个 Agent 都散落一份 provider 适配逻辑。
        model_kwargs = settings.build_model_kwargs(settings.master_model)
        # TODO(重点流程): 这里完成 Master 使用的 LLM 初始化；后续结构化分类和通用问答都会复用这一实例。
        self._model = init_chat_model(**model_kwargs)
        logger.info("%s Master 模型初始化完成 model_kwargs=%s",
            log_title("初始化", "模型初始化"), summarize_model_kwargs(model_kwargs),)

    async def execute(self, state: WorkflowState, config: RunnableConfig | None = None) -> WorkflowState:
        """识别意图、抽取参数并生成下一步路由决策。

        Args:
            state: 当前 LangGraph 工作流状态。

        Returns:
            WorkflowState: Master 节点产出的结构化识别结果和路由字段。

        Raises:
            None.
        """

        logger.info("%s event=node_enter trace=%s state=%s",
            log_title("执行", "节点入参", node_name="master_node"), build_trace_context(config, node_name="master_node", event_name="node_enter"), format_state_for_log(state, self._settings),)

        try:
            # 这里显式指定 `function_calling`，是因为部分 OpenAI 兼容网关在默认结构化模式下
            # 不会稳定返回可解析 JSON；改成工具调用后兼容性更好、改动也最小。
            # TODO(重点流程): 这里先把普通聊天模型包装成结构化分类器，目的是稳定产出可路由的字段。
            classifier_model = self._model.with_structured_output(
                IntentClassification,
                method="function_calling",
            )
            # 这里把系统提示词和当前消息历史一起传给模型，让它完成分类和参数抽取。
            # TODO(重点流程): 这里正式发起一次结构化模型调用，产出的 classification 将决定整个工作流走向。
            model_messages = self._build_classifier_messages(state)
            log_debug_event(logger, self._settings, log_title("模型", "调用"), "model_start", build_trace_context(config, node_name="master_node", event_name="model_start"), model="master_classifier", messages=model_messages)
            classification = await classifier_model.ainvoke(model_messages, config=config)
            log_debug_event(logger, self._settings, log_title("模型", "调用"), "model_end", build_trace_context(config, node_name="master_node", event_name="model_end"), model="master_classifier", output=classification.model_dump())

            # 下面这三步的目的，是把“模型输出”转换成“图调度真正需要的字段”，
            # 避免后续节点继续依赖原始分类对象自行推导。
            # 先把模型结果整理成后续路由节点真正关心的参数字典；参数抽取仍以模型输出为准。
            extracted_params = build_extracted_params(classification)
            # 再根据规则重新计算缺失项，避免完全依赖模型自由发挥。
            missing_params = compute_missing_params(classification)
            # 最后统一计算下一跳节点名，供 LangGraph 条件边使用。
            next_action = self._decide_next_action(classification.intent_type, missing_params)

            result: WorkflowState = {
                "agent_type": classification.intent_type,
                "extracted_params": extracted_params,
                "missing_params": missing_params,
                "next_action": next_action,
                "routing_reason": classification.reasoning or "Master completed routing analysis.",
            }
            logger.info("%s event=node_exit trace=%s classification=%s result=%s",
                log_title("执行", "节点出参", node_name="master_node"), build_trace_context(config, node_name="master_node", event_name="node_exit"), classification.model_dump(), format_state_for_log(result, self._settings),)
            return result
        except Exception:  # noqa: BLE001
            logger.exception("%s event=node_error trace=%s Master 节点执行失败。",
                log_title("执行", "节点异常", node_name="master_node"), build_trace_context(config, node_name="master_node", event_name="node_error"),)
            raise

    async def ask_for_missing_params(self, state: WorkflowState, config: RunnableConfig | None = None) -> WorkflowState:
        """根据缺失参数生成单轮追问。

        Args:
            state: 当前 LangGraph 工作流状态。

        Returns:
            WorkflowState: 仅追加一条追问消息。

        Raises:
            None.
        """

        logger.info("%s event=node_enter trace=%s state=%s",
            log_title("执行", "节点入参", node_name="ask_params_node"), build_trace_context(config, node_name="ask_params_node", event_name="node_enter"), format_state_for_log(state, self._settings),)

        # 先统计用户已经说了几轮，目的是给“补参对话”设置一个硬上限，避免反复追问不收敛。
        human_turns = self._count_human_turns(state.get("messages", []))
        if human_turns >= self._settings.max_conversation_turns:
            result: WorkflowState = {
                "messages": [
                    AIMessage(
                        content=(
                            f"当前对话已经达到最大追问轮次 {self._settings.max_conversation_turns}。"
                            "请整理完整信息后重新发起任务，我再继续帮你路由。"
                        )
                    )
                ]
            }
            logger.info("%s event=node_exit trace=%s messages=%s",
                log_title("执行", "节点出参", node_name="ask_params_node"), build_trace_context(config, node_name="ask_params_node", event_name="node_exit"), format_messages_for_log(result["messages"], self._settings),)
            return result

        # 从共享状态中取出上一节点已经分析出的结果，目的是生成一条既能补缺参、又能让用户确认上下文的追问。
        missing_params = state.get("missing_params", [])
        agent_type = state.get("agent_type", "unknown")
        extracted_params = state.get("extracted_params", {})
        next_field = missing_params[0] if missing_params else "details"
        # 把字段名转成自然语言问题，减少对用户暴露内部字段名的生硬感。
        field_question = self._question_for_field(next_field)
        # 把已经识别出的信息整理成摘要，方便用户确认和补充。
        known_context = self._format_known_context(extracted_params)

        result = {
            "messages": [
                AIMessage(
                    content=(
                        f"我已识别这是 `{agent_type}` 类任务。"
                        f"目前还缺少 `{next_field}`。{field_question}\n"
                        f"已识别信息：{known_context}"
                    )
                )
            ]
        }
        logger.info("%s event=node_exit trace=%s messages=%s",
            log_title("执行", "节点出参", node_name="ask_params_node"), build_trace_context(config, node_name="ask_params_node", event_name="node_exit"), format_messages_for_log(result["messages"], self._settings),)
        return result

    async def answer_general_request(self, state: WorkflowState, config: RunnableConfig | None = None) -> WorkflowState:
        """对 general 路由请求给出通用回答。

        Args:
            state: 当前 LangGraph 工作流状态。

        Returns:
            WorkflowState: 仅追加一条通用回答消息。

        Raises:
            None.
        """

        logger.info("%s event=node_enter trace=%s state=%s",
            log_title("执行", "节点入参", node_name="general_node"), build_trace_context(config, node_name="general_node", event_name="node_enter"), format_state_for_log(state, self._settings),)

        try:
            # 通用问答不需要结构化输出，直接让基础模型生成一条普通回复即可。
            # TODO(重点流程): 这里直接调用基础聊天模型生成最终回答，不再走结构化路由链路。
            model_messages = [SystemMessage(content=GENERAL_ASSISTANT_SYSTEM_PROMPT), *state.get("messages", [])]
            log_debug_event(logger, self._settings, log_title("模型", "调用"), "model_start", build_trace_context(config, node_name="general_node", event_name="model_start"), model="master_general", messages=model_messages)
            response = await self._model.ainvoke(model_messages, config=config)
            log_debug_event(logger, self._settings, log_title("模型", "调用"), "model_end", build_trace_context(config, node_name="general_node", event_name="model_end"), model="master_general", messages=[response])
            result = {"messages": [self._ensure_ai_message(response)]}
            logger.info("%s event=node_exit trace=%s messages=%s",
                log_title("执行", "节点出参", node_name="general_node"), build_trace_context(config, node_name="general_node", event_name="node_exit"), format_messages_for_log(result["messages"], self._settings),)
            return result
        except Exception:  # noqa: BLE001
            logger.exception("%s event=node_error trace=%s 通用回答节点执行失败。",
                log_title("执行", "节点异常", node_name="general_node"), build_trace_context(config, node_name="general_node", event_name="node_error"),)
            raise

    def _decide_next_action(self, agent_type: str, missing_params: list[str]) -> str:
        """根据结构化结果计算条件边出口。

        Args:
            agent_type: 当前确认后的目标 Agent 类型。
            missing_params: 规则计算后的缺失参数列表。

        Returns:
            str: 条件边使用的节点名别名。

        Raises:
            None.
        """

        # 意图不明确或已被分类为 general 时，保守走通用回答，避免误触发 Specialist。
        if agent_type in {"general", "unknown"}:
            return "general"

        # 能识别出 Specialist 但关键信息还不全时，优先补参而不是让下游 Agent 带着歧义执行。
        if missing_params:
            return "ask_params"

        # 只有意图明确且参数齐全时，才真正把请求交给对应 Specialist。
        return agent_type

    def _build_classifier_messages(self, state: WorkflowState) -> list[BaseMessage]:
        """构造 Master 分类模型的输入消息列表。"""

        model_messages: list[BaseMessage] = [SystemMessage(content=MASTER_ROUTER_SYSTEM_PROMPT)]
        ask_params_context_prompt = self._build_ask_params_context_prompt(state)
        if ask_params_context_prompt:
            model_messages.append(SystemMessage(content=ask_params_context_prompt))
        model_messages.extend(state.get("messages", []))
        return model_messages

    def _build_ask_params_context_prompt(self, state: WorkflowState) -> str | None:
        """在 ask_params 续聊场景下，把上一轮 state 显式提供给模型。"""

        if state.get("next_action") != "ask_params":
            return None

        agent_type = state.get("agent_type")
        if agent_type not in SPECIALIST_AGENT_TYPES:
            return None

        extracted_params = state.get("extracted_params", {})
        missing_params = state.get("missing_params", [])
        return build_master_ask_params_context_prompt(
            agent_type=str(agent_type),
            extracted_params=extracted_params if isinstance(extracted_params, dict) else {},
            missing_params=list(missing_params) if isinstance(missing_params, list) else [],
            routing_reason=state.get("routing_reason"),
        )

    def _count_human_turns(self, messages: list[BaseMessage]) -> int:
        """统计当前消息历史中的用户轮次。

        Args:
            messages: 当前对话消息列表。

        Returns:
            int: 用户消息数量。

        Raises:
            None.
        """

        # 这里只统计 HumanMessage，因为我们真正关心的是“用户被要求补参”的轮次，而不是系统自己追加了多少消息。
        return sum(1 for message in messages if isinstance(message, HumanMessage))

    def _question_for_field(self, field_name: str) -> str:
        """把参数名映射成自然语言追问。

        Args:
            field_name: 缺失参数名。

        Returns:
            str: 面向用户的单轮追问文本。

        Raises:
            None.
        """

        mapping = {
            "project_name": "请提供自动化工程名字，例如 `baidu-web` 或 `an-autotest-demo`；如果已有现成工程目录，也可以直接提供 `project_dir`。",
            "url": "请提供被测页面的完整 URL。",
            "feature_points": "请提供你希望覆盖的功能点列表，至少 1 条。",
            "test_plan_files": "请提供待生成脚本的测试计划文件或目录路径，至少 1 个；优先传相对 `project_dir` 的路径。",
            "test_cases": "请提供待生成脚本的测试用例列表，至少 1 条。",
            "test_scripts": "请提供待调试脚本文件或目录路径，至少 1 个；优先传相对 `project_dir` 的路径。",
        }
        # 通过映射表把内部字段名翻译成自然语言，目的是减少用户看到内部 schema 字段名时的割裂感。
        return mapping.get(field_name, "请补充这部分关键信息。")

    def _format_known_context(self, extracted_params: dict[str, Any]) -> str:
        """将已识别参数格式化成可读摘要。

        Args:
            extracted_params: 当前已提取到的业务参数。

        Returns:
            str: 便于用户确认的简洁摘要。

        Raises:
            None.
        """

        if not extracted_params:
            return "暂无。"

        summary_parts: list[str] = []
        for key, value in extracted_params.items():
            # 这里直接保留 `key=value` 形式，目的是让用户能够最小心智成本确认识别结果是否偏了。
            summary_parts.append(f"{key}={value}")
        return "；".join(summary_parts)

    def _ensure_ai_message(self, message: BaseMessage) -> AIMessage:
        """把模型返回消息统一成 AIMessage。

        Args:
            message: LangChain 模型返回的消息对象。

        Returns:
            AIMessage: 可直接写回 LangGraph state 的消息。

        Raises:
            None.
        """

        if isinstance(message, AIMessage):
            return message

        # 某些模型返回的内容不一定是纯字符串，这里统一转成字符串再封装成 AIMessage，
        # 目的是保证写回 LangGraph state 时始终是稳定的消息类型。
        content = message.content if isinstance(message.content, str) else str(message.content)
        return AIMessage(content=content)

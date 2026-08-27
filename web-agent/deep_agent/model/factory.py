"""为 Deep Agents 包装模型，使消息和工具参数在每次调用前完成适配。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any, Callable

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_core.prompt_values import PromptValue
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.tools import BaseTool
from pydantic import ConfigDict

from deep_agent.model.capabilities import ModelCapabilities
from deep_agent.model.messages import normalize_messages
from deep_agent.model.settings import ResolvedModelConnection
from deep_agent.model.tools import adapt_tool_binding


class ProviderCompatibleChatModel(BaseChatModel):
    """在底层 LangChain 模型外统一处理第三方兼容协议差异。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    delegate: BaseChatModel
    connection: ResolvedModelConnection
    capabilities: ModelCapabilities

    @property
    def _llm_type(self) -> str:
        return f"provider-compatible-{self.connection.family}"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {
            "model": self.connection.api_model_name,
            "family": self.connection.family,
            "channel": self.connection.channel,
        }

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self.delegate._generate(  # noqa: SLF001
            normalize_messages(messages, self.capabilities),
            stop=stop,
            run_manager=run_manager,
            **kwargs,
        )

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return await self.delegate._agenerate(  # noqa: SLF001
            normalize_messages(messages, self.capabilities),
            stop=stop,
            run_manager=run_manager,
            **kwargs,
        )

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        yield from self.delegate._stream(  # noqa: SLF001
            normalize_messages(messages, self.capabilities),
            stop=stop,
            run_manager=run_manager,
            **kwargs,
        )

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        async for chunk in self.delegate._astream(  # noqa: SLF001
            normalize_messages(messages, self.capabilities),
            stop=stop,
            run_manager=run_manager,
            **kwargs,
        ):
            yield chunk

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[Any, AIMessage]:
        adapted_choice, adapted_kwargs = adapt_tool_binding(
            tool_choice=tool_choice,
            kwargs=kwargs,
            capabilities=self.capabilities,
            connection=self.connection,
        )
        if adapted_choice is None:
            bound = self.delegate.bind_tools(tools, **adapted_kwargs)
        else:
            bound = self.delegate.bind_tools(tools, tool_choice=adapted_choice, **adapted_kwargs)
        return RunnableLambda(self._normalize_model_input) | bound

    def with_structured_output(
        self,
        schema: dict[str, Any] | type,
        *,
        include_raw: bool = False,
        **kwargs: Any,
    ) -> Runnable[Any, Any]:
        structured = self.delegate.with_structured_output(
            schema,
            include_raw=include_raw,
            **kwargs,
        )
        return RunnableLambda(self._normalize_model_input) | structured

    def _normalize_model_input(self, value: Any) -> Any:
        if isinstance(value, PromptValue):
            return normalize_messages(value.to_messages(), self.capabilities)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            messages = list(value)
            if all(isinstance(message, BaseMessage) for message in messages):
                return normalize_messages(messages, self.capabilities)
        return value


def adapt_chat_model(
    model: Any,
    *,
    connection: ResolvedModelConnection,
    capabilities: ModelCapabilities,
) -> Any:
    """为真实 BaseChatModel 注入 Profile 和协议包装；测试替身保持原样。"""

    if not isinstance(model, BaseChatModel):
        return model

    profile = dict(model.profile or {})
    if capabilities.max_input_tokens is not None:
        profile["max_input_tokens"] = capabilities.max_input_tokens
    if capabilities.max_output_tokens is not None:
        profile["max_output_tokens"] = capabilities.max_output_tokens

    return ProviderCompatibleChatModel(
        delegate=model,
        connection=connection,
        capabilities=capabilities,
        profile=profile or None,
    )

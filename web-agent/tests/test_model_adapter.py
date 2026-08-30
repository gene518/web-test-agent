from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

import httpx
from deepagents import create_deep_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph_api.errors import UserInterrupt
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from deep_agent.core.config import AppSettings
from deep_agent.agent.master.master_agent import MasterAgent
from deep_agent.agent.master.models.intent import IntentClassification
from deep_agent.model.factory import adapt_chat_model
from deep_agent.model.diagnostics import collect_model_diagnostics
from deep_agent.model.capabilities import ModelCapabilities
from deep_agent.model.errors import ModelConfigurationError, StructuredOutputError
from deep_agent.model.messages import normalize_messages
from deep_agent.model.profiles import resolve_model_capabilities
from deep_agent.model.structured_output import invoke_structured
from deep_agent.model.tools import adapt_tool_binding


class SimplePayload(BaseModel):
    value: str


class FakeRunnable:
    def __init__(self, owner: "FakeStructuredModel") -> None:
        self.owner = owner

    async def ainvoke(self, messages, config=None):  # noqa: ANN001
        self.owner.inputs.append(messages)
        response = self.owner.responses.pop(0)
        return response


class FakeStructuredModel:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.inputs: list[object] = []
        self.structured_calls: list[dict[str, object]] = []

    def with_structured_output(self, schema, **kwargs):  # noqa: ANN001
        self.structured_calls.append({"schema": schema, **kwargs})
        return FakeRunnable(self)

    async def ainvoke(self, messages, config=None):  # noqa: ANN001
        self.inputs.append(messages)
        return self.responses.pop(0)


class FakeLegacyStructuredModel:
    def __init__(self) -> None:
        self.structured_kwargs: dict[str, object] | None = None

    def with_structured_output(self, schema, **kwargs):  # noqa: ANN001
        self.structured_kwargs = {"schema": schema, **kwargs}
        return self

    async def ainvoke(self, messages, config=None):  # noqa: ANN001
        return IntentClassification(intent_type="general", reasoning="legacy")


class CancellingStructuredModel:
    def with_structured_output(self, schema, **kwargs):  # noqa: ANN001
        return self

    async def ainvoke(self, messages, config=None):  # noqa: ANN001
        raise UserInterrupt()


class ModelAdapterTestCase(unittest.IsolatedAsyncioTestCase):
    def _chat_completion_response(self, content: str, *, model: str) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
        )

    def _build_http_model(
        self,
        settings: AppSettings,
        *,
        role: str,
        response_content: str,
    ) -> tuple[object, list[dict[str, object]], httpx.AsyncClient]:
        requests: list[dict[str, object]] = []
        connection = settings.resolve_model_connection(role)
        capabilities = resolve_model_capabilities(connection)

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return self._chat_completion_response(
                response_content, model=connection.api_model_name
            )

        async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        kwargs = settings.build_model_kwargs(role=role)
        kwargs.pop("model_provider", None)
        raw_model = ChatOpenAI(**kwargs, http_async_client=async_client)
        return (
            adapt_chat_model(
                raw_model, connection=connection, capabilities=capabilities
            ),
            requests,
            async_client,
        )

    def test_nested_role_config_supports_different_providers(self) -> None:
        settings = AppSettings(
            _env_file=None,
            master_llm={
                "family": "qwen",
                "channel": "dashscope_openai",
                "model": "qwen3.5-plus",
                "api_key": "qwen-key",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "thinking": "disabled",
            },
            specialist_llm={
                "family": "minimax",
                "channel": "minimax_anthropic",
                "model": "MiniMax-M2.7",
                "api_key": "minimax-key",
                "base_url": "https://api.minimax.io/anthropic",
            },
        )
        master = settings.resolve_model_connection("master")
        specialist = settings.resolve_model_connection("specialist")
        self.assertEqual(
            (master.family, master.channel, master.api_key),
            ("qwen", "dashscope_openai", "qwen-key"),
        )
        self.assertEqual(
            (specialist.protocol, specialist.api_key), ("anthropic", "minimax-key")
        )

    def test_nested_role_config_loads_from_environment(self) -> None:
        env = {
            "MASTER_LLM__FAMILY": "glm",
            "MASTER_LLM__CHANNEL": "zhipu_openai",
            "MASTER_LLM__MODEL": "glm-5.2",
            "MASTER_LLM__API_KEY": "glm-key",
            "MASTER_LLM__BASE_URL": "https://open.bigmodel.cn/api/paas/v4/",
            "MASTER_LLM__THINKING": "enabled",
        }
        with patch.dict(os.environ, env, clear=False):
            settings = AppSettings(_env_file=None)

        connection = settings.resolve_model_connection("master")
        self.assertEqual(connection.family, "glm")
        self.assertEqual(connection.channel, "zhipu_openai")
        self.assertEqual(connection.api_key, "glm-key")
        self.assertEqual(connection.thinking, "enabled")

    def test_explicit_role_config_does_not_fall_back_to_legacy_api_key(self) -> None:
        settings = AppSettings(
            _env_file=None,
            openai_api_key="unrelated-key",
            master_llm={
                "family": "minimax",
                "channel": "minimax_openai",
                "model": "MiniMax-M2.7",
                "base_url": "https://api.minimax.io/v1",
            },
        )
        connection = settings.resolve_model_connection("master")

        self.assertIsNone(connection.api_key)

    def test_legacy_anthropic_model_prefix_keeps_provider(self) -> None:
        settings = AppSettings(
            _env_file=None,
            master_model="anthropic:claude-sonnet-4-5",
            openai_base_url=None,
        )

        connection = settings.resolve_model_connection("master")
        kwargs = settings.build_model_kwargs(role="master")

        self.assertEqual(connection.channel, "generic_anthropic")
        self.assertEqual(connection.protocol, "anthropic")
        self.assertEqual(kwargs["model_provider"], "anthropic")
        self.assertEqual(kwargs["model"], "claude-sonnet-4-5")

    def test_rejects_mismatched_family_and_channel(self) -> None:
        settings = AppSettings(
            _env_file=None,
            master_llm={
                "family": "qwen",
                "channel": "minimax_anthropic",
                "model": "qwen3.5-plus",
            },
        )

        with self.assertRaises(ModelConfigurationError):
            settings.resolve_model_connection("master")

    def test_provider_specific_outbound_kwargs(self) -> None:
        qwen = AppSettings(
            _env_file=None,
            master_llm={
                "family": "qwen",
                "channel": "dashscope_openai",
                "model": "qwen3.5-plus",
                "api_key": "key",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "thinking": "disabled",
            },
        ).build_model_kwargs(role="master")
        minimax = AppSettings(
            _env_file=None,
            master_llm={
                "family": "minimax",
                "channel": "minimax_openai",
                "model": "MiniMax-M2.7",
                "api_key": "key",
                "base_url": "https://api.minimax.io/v1",
            },
        ).build_model_kwargs(role="master")
        glm = AppSettings(
            _env_file=None,
            specialist_llm={
                "family": "glm",
                "channel": "zhipu_openai",
                "model": "glm-5.2",
                "api_key": "key",
                "base_url": "https://open.bigmodel.cn/api/paas/v4/",
                "thinking": "enabled",
                "reasoning_effort": "max",
            },
        ).build_model_kwargs(role="specialist")
        glm_dashscope = AppSettings(
            _env_file=None,
            specialist_llm={
                "family": "glm",
                "channel": "dashscope_openai",
                "model": "glm-5.2",
                "api_key": "key",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "thinking": "enabled",
            },
        ).build_model_kwargs(role="specialist")
        minimax_anthropic = AppSettings(
            _env_file=None,
            specialist_llm={
                "family": "minimax",
                "channel": "minimax_anthropic",
                "model": "MiniMax-M2.7",
                "api_key": "key",
                "base_url": "https://api.minimax.io/anthropic",
            },
        ).build_model_kwargs(role="specialist")

        self.assertEqual(qwen["extra_body"], {"enable_thinking": False})
        self.assertNotIn("disabled_params", qwen)
        self.assertEqual(minimax["extra_body"], {"reasoning_split": False})
        self.assertEqual(
            minimax["disabled_params"],
            {"parallel_tool_calls": None, "tool_choice": None, "response_format": None},
        )
        self.assertEqual(glm["extra_body"], {"thinking": {"type": "enabled"}})
        self.assertEqual(glm["reasoning_effort"], "max")
        self.assertEqual(glm["disabled_params"], {"parallel_tool_calls": None})
        self.assertNotIn("enable_thinking", glm["extra_body"])
        self.assertEqual(
            glm_dashscope["extra_body"], {"enable_thinking": True, "tool_stream": True}
        )
        self.assertEqual(minimax_anthropic["model_provider"], "anthropic")
        self.assertEqual(minimax_anthropic["max_tokens"], 131_072)
        self.assertNotIn("extra_body", minimax_anthropic)

    def test_model_diagnostics_do_not_expose_api_keys(self) -> None:
        settings = AppSettings(
            _env_file=None,
            master_llm={
                "family": "qwen",
                "model": "qwen3.5-plus",
                "api_key": "top-secret",
            },
            specialist_llm={
                "family": "glm",
                "model": "glm-5.2",
                "api_key": "another-secret",
            },
        )

        serialized = json.dumps(collect_model_diagnostics(settings), ensure_ascii=False)

        self.assertNotIn("top-secret", serialized)
        self.assertNotIn("another-secret", serialized)
        self.assertIn('"has_api_key": true', serialized)

    async def test_adapter_flag_restores_legacy_master_behavior(self) -> None:
        settings = AppSettings(
            _env_file=None,
            model_adapter_v2_enabled=False,
            master_model="openai:qwen3.5-plus",
            openai_api_key="test-key",
            openai_base_url="https://mock.local/v1",
            llm_enable_thinking=False,
        )
        fake_model = FakeLegacyStructuredModel()
        with patch(
            "deep_agent.agent.master.master_agent.init_chat_model",
            return_value=fake_model,
        ) as init_model:
            master = MasterAgent(settings)

        classification, strategy, attempts = await master._invoke_intent_classification(  # noqa: SLF001
            [HumanMessage(content="go")],
            config=None,
        )

        self.assertIs(master._model, fake_model)  # noqa: SLF001
        self.assertEqual(init_model.call_args.kwargs["model"], "openai:qwen3.5-plus")
        self.assertEqual(
            init_model.call_args.kwargs["extra_body"], {"enable_thinking": False}
        )
        self.assertEqual(fake_model.structured_kwargs["method"], "function_calling")
        self.assertNotIn("include_raw", fake_model.structured_kwargs)
        self.assertEqual(classification.intent_type, "general")
        self.assertEqual((strategy, attempts), ("legacy_function_calling", 1))

    def test_profiles_use_channel_specific_context_limits(self) -> None:
        qwen_settings = AppSettings(
            _env_file=None,
            master_llm={"family": "qwen", "model": "qwen3.5-plus"},
        )
        minimax_settings = AppSettings(
            _env_file=None,
            master_llm={
                "family": "minimax",
                "channel": "minimax_openai",
                "model": "MiniMax-M2.7",
                "base_url": "https://api.minimax.io/v1",
            },
        )
        glm_settings = AppSettings(
            _env_file=None,
            master_llm={"family": "glm", "model": "glm-5.2"},
        )

        self.assertEqual(
            resolve_model_capabilities(
                qwen_settings.resolve_model_connection("master")
            ).max_input_tokens,
            1_000_000,
        )
        self.assertEqual(
            resolve_model_capabilities(
                minimax_settings.resolve_model_connection("master")
            ).max_input_tokens,
            204_800,
        )
        self.assertEqual(
            resolve_model_capabilities(
                glm_settings.resolve_model_connection("master")
            ).max_input_tokens,
            1_000_000,
        )

    def test_system_messages_are_merged_without_reordering_tool_history(self) -> None:
        capabilities = ModelCapabilities(structured_output_strategy="prompted_json")
        ai_message = AIMessage(
            content="<think>reasoning</think>",
            tool_calls=[
                {"name": "lookup", "args": {}, "id": "call-1", "type": "tool_call"}
            ],
        )
        messages = [
            HumanMessage(content="start"),
            SystemMessage(content="base"),
            ai_message,
            ToolMessage(content="result", tool_call_id="call-1"),
            SystemMessage(content="runtime"),
            HumanMessage(content="continue"),
        ]

        normalized = normalize_messages(messages, capabilities)

        self.assertEqual(
            [type(message) for message in normalized],
            [SystemMessage, HumanMessage, AIMessage, ToolMessage, HumanMessage],
        )
        self.assertEqual(normalized[0].content, "base\n\nruntime")
        self.assertIs(normalized[2], ai_message)
        self.assertEqual(normalized[2].content, "<think>reasoning</think>")

    def test_tool_binding_omits_minimax_unsupported_parameters(self) -> None:
        settings = AppSettings(
            _env_file=None,
            master_llm={
                "family": "minimax",
                "channel": "minimax_openai",
                "model": "MiniMax-M2.7",
            },
        )
        connection = settings.resolve_model_connection("master")
        capabilities = resolve_model_capabilities(connection)

        tool_choice, kwargs = adapt_tool_binding(
            tool_choice="required",
            kwargs={"parallel_tool_calls": True, "strict": False},
            capabilities=capabilities,
            connection=connection,
        )

        self.assertIsNone(tool_choice)
        self.assertEqual(kwargs, {"strict": False})

    def test_tool_binding_forces_glm_tool_choice_to_auto(self) -> None:
        settings = AppSettings(
            _env_file=None,
            master_llm={"family": "glm", "channel": "zhipu_openai", "model": "glm-5.2"},
        )
        connection = settings.resolve_model_connection("master")
        capabilities = resolve_model_capabilities(connection)

        tool_choice, kwargs = adapt_tool_binding(
            tool_choice="required",
            kwargs={"parallel_tool_calls": False},
            capabilities=capabilities,
            connection=connection,
        )

        self.assertEqual(tool_choice, "auto")
        self.assertEqual(kwargs, {})

    def test_generic_gateway_does_not_receive_parallel_tool_parameter(self) -> None:
        settings = AppSettings(
            _env_file=None,
            master_llm={
                "family": "generic",
                "channel": "generic_openai",
                "model": "custom-model",
            },
        )
        connection = settings.resolve_model_connection("master")
        capabilities = resolve_model_capabilities(connection)

        _, kwargs = adapt_tool_binding(
            tool_choice=None,
            kwargs={"parallel_tool_calls": True},
            capabilities=capabilities,
            connection=connection,
        )

        self.assertEqual(kwargs, {})

    async def test_qwen_structured_output_uses_json_mode_and_single_system(
        self,
    ) -> None:
        settings = AppSettings(
            _env_file=None,
            master_llm={
                "family": "qwen",
                "channel": "dashscope_openai",
                "model": "qwen3.5-plus",
            },
        )
        connection = settings.resolve_model_connection("master")
        capabilities = resolve_model_capabilities(connection)
        model = FakeStructuredModel(
            [
                {
                    "raw": AIMessage(content='{"value":"ok"}'),
                    "parsed": SimplePayload(value="ok"),
                    "parsing_error": None,
                }
            ]
        )

        result = await invoke_structured(
            model=model,
            schema=SimplePayload,
            messages=[
                SystemMessage(content="base"),
                SystemMessage(content="runtime"),
                HumanMessage(content="go"),
            ],
            capabilities=capabilities,
            connection=connection,
        )

        self.assertEqual(result.parsed.value, "ok")
        self.assertEqual(model.structured_calls[0]["method"], "json_mode")
        self.assertTrue(model.structured_calls[0]["include_raw"])
        sent_messages = model.inputs[0]
        self.assertEqual(
            sum(isinstance(message, SystemMessage) for message in sent_messages), 1
        )
        self.assertIn("JSON", sent_messages[0].content)

    async def test_minimax_prompted_json_parses_after_think_content(self) -> None:
        settings = AppSettings(
            _env_file=None,
            master_llm={
                "family": "minimax",
                "channel": "minimax_openai",
                "model": "MiniMax-M2.7",
            },
        )
        connection = settings.resolve_model_connection("master")
        capabilities = resolve_model_capabilities(connection)
        model = FakeStructuredModel(
            [AIMessage(content='<think>{"value":"draft"}</think>\n{"value":"ok"}')]
        )

        result = await invoke_structured(
            model=model,
            schema=SimplePayload,
            messages=[HumanMessage(content="go")],
            capabilities=capabilities,
            connection=connection,
        )

        self.assertEqual(result.parsed.value, "ok")
        self.assertEqual(result.strategy, "prompted_json")
        self.assertEqual(model.structured_calls, [])

    async def test_empty_function_call_result_becomes_typed_error(self) -> None:
        settings = AppSettings(
            _env_file=None,
            master_llm={
                "family": "generic",
                "channel": "generic_openai",
                "model": "custom-model",
            },
        )
        connection = settings.resolve_model_connection("master")
        capabilities = resolve_model_capabilities(connection)
        empty_response = {
            "raw": AIMessage(content=""),
            "parsed": None,
            "parsing_error": "no tool call",
        }
        model = FakeStructuredModel([empty_response, empty_response])

        with self.assertRaises(StructuredOutputError) as context:
            await invoke_structured(
                model=model,
                schema=SimplePayload,
                messages=[HumanMessage(content="go")],
                capabilities=capabilities,
                connection=connection,
            )

        self.assertIn("[structured_output_error]", str(context.exception))
        self.assertTrue(context.exception.context["has_raw_response"])
        self.assertEqual(len(model.structured_calls), 2)

    async def test_structured_output_propagates_user_cancellation(self) -> None:
        settings = AppSettings(
            _env_file=None,
            master_llm={
                "family": "generic",
                "channel": "generic_openai",
                "model": "custom-model",
            },
        )
        connection = settings.resolve_model_connection("master")

        with self.assertRaises(UserInterrupt):
            await invoke_structured(
                model=CancellingStructuredModel(),
                schema=SimplePayload,
                messages=[HumanMessage(content="go")],
                capabilities=resolve_model_capabilities(connection),
                connection=connection,
            )

    async def test_qwen_http_contract_merges_system_and_sends_json_mode(self) -> None:
        settings = AppSettings(
            _env_file=None,
            master_llm={
                "family": "qwen",
                "channel": "dashscope_openai",
                "model": "qwen3.5-plus",
                "api_key": "test-key",
                "base_url": "https://mock.local/v1",
                "thinking": "disabled",
            },
        )
        connection = settings.resolve_model_connection("master")
        capabilities = resolve_model_capabilities(connection)
        model, requests, client = self._build_http_model(
            settings,
            role="master",
            response_content='{"value":"ok"}',
        )
        try:
            result = await invoke_structured(
                model=model,
                schema=SimplePayload,
                messages=[
                    SystemMessage(content="base"),
                    SystemMessage(content="runtime"),
                    HumanMessage(content="go"),
                ],
                capabilities=capabilities,
                connection=connection,
            )
        finally:
            await client.aclose()

        payload = requests[0]
        self.assertEqual(result.parsed.value, "ok")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertFalse(payload["enable_thinking"])
        self.assertNotIn("tool_choice", payload)
        self.assertEqual(
            [message["role"] for message in payload["messages"]], ["system", "user"]
        )
        self.assertIn("JSON", payload["messages"][0]["content"])

    async def test_minimax_http_contract_omits_unsupported_fields_and_replays_thinking(
        self,
    ) -> None:
        settings = AppSettings(
            _env_file=None,
            specialist_llm={
                "family": "minimax",
                "channel": "minimax_openai",
                "model": "MiniMax-M2.7",
                "api_key": "test-key",
                "base_url": "https://mock.local/v1",
            },
        )
        model, requests, client = self._build_http_model(
            settings,
            role="specialist",
            response_content="done",
        )
        tool = {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "lookup",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        bound = model.bind_tools(
            [tool], tool_choice="required", parallel_tool_calls=True
        )
        thinking_content = "<think>reasoning that must be replayed</think>"
        try:
            await bound.ainvoke(
                [
                    HumanMessage(content="first"),
                    AIMessage(
                        content=thinking_content,
                        tool_calls=[
                            {
                                "name": "lookup",
                                "args": {},
                                "id": "call-1",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    ToolMessage(content="result", tool_call_id="call-1"),
                    HumanMessage(content="continue"),
                ]
            )
        finally:
            await client.aclose()

        payload = requests[0]
        self.assertFalse(payload["reasoning_split"])
        self.assertNotIn("tool_choice", payload)
        self.assertNotIn("parallel_tool_calls", payload)
        self.assertNotIn("response_format", payload)
        assistant_message = next(
            message for message in payload["messages"] if message["role"] == "assistant"
        )
        self.assertEqual(assistant_message["content"], thinking_content)

    async def test_glm_http_contract_limits_tool_choice_to_auto(self) -> None:
        settings = AppSettings(
            _env_file=None,
            specialist_llm={
                "family": "glm",
                "channel": "zhipu_openai",
                "model": "glm-5.2",
                "api_key": "test-key",
                "base_url": "https://mock.local/v1",
                "thinking": "enabled",
            },
        )
        model, requests, client = self._build_http_model(
            settings,
            role="specialist",
            response_content="done",
        )
        tool = {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "lookup",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        try:
            await model.bind_tools(
                [tool], tool_choice="required", parallel_tool_calls=True
            ).ainvoke([HumanMessage(content="go")])
        finally:
            await client.aclose()

        payload = requests[0]
        self.assertEqual(payload["tool_choice"], "auto")
        self.assertNotIn("parallel_tool_calls", payload)
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertNotIn("enable_thinking", payload)

    async def test_adapted_model_runs_inside_deep_agent(self) -> None:
        settings = AppSettings(
            _env_file=None,
            specialist_llm={
                "family": "qwen",
                "channel": "dashscope_openai",
                "model": "qwen3.5-plus",
                "api_key": "test-key",
                "base_url": "https://mock.local/v1",
                "thinking": "disabled",
            },
        )
        model, requests, client = self._build_http_model(
            settings,
            role="specialist",
            response_content="specialist done",
        )
        agent = create_deep_agent(
            model=model, tools=[], system_prompt="specialist system"
        )
        try:
            result = await agent.ainvoke({"messages": [HumanMessage(content="run")]})
        finally:
            await client.aclose()

        self.assertEqual(result["messages"][-1].content, "specialist done")
        self.assertEqual(requests[0]["messages"][0]["role"], "system")
        self.assertEqual(
            sum(message["role"] == "system" for message in requests[0]["messages"]), 1
        )
        self.assertFalse(requests[0]["parallel_tool_calls"])

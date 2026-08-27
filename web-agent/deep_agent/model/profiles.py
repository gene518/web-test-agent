"""根据模型家族、接入通道和模型版本解析协议能力。"""

from __future__ import annotations

from dataclasses import replace

from deep_agent.model.capabilities import ModelCapabilities
from deep_agent.model.settings import ResolvedModelConnection


def resolve_model_capabilities(connection: ResolvedModelConnection) -> ModelCapabilities:
    """返回指定连接的能力 Profile，并应用用户显式的 token 覆盖。"""

    family = connection.family
    channel = connection.channel
    model_name = connection.api_model_name.lower()

    if family == "qwen":
        max_input_tokens = 1_000_000 if any(token in model_name for token in ("plus", "flash", "max")) else 262_144
        capabilities = ModelCapabilities(
            structured_output_strategy="json_mode",
            supports_json_mode=True,
            json_mode_requires_keyword=True,
            supports_forced_tool_choice=True,
            supports_parallel_tool_calls=True,
            allowed_tool_choices=("auto", "none", "required", "function"),
            thinking_parameter="enable_thinking",
            max_input_tokens=max_input_tokens,
        )
    elif family == "minimax" and channel == "minimax_anthropic":
        capabilities = ModelCapabilities(
            structured_output_strategy="function_calling",
            supports_forced_tool_choice=True,
            allowed_tool_choices=("auto", "any", "tool", "none"),
            thinking_parameter="thinking",
            thinking_can_disable="m3" in model_name,
            reasoning_replay_required=True,
            reasoning_transport="anthropic_content_blocks",
            max_input_tokens=_minimax_context_window(channel, model_name),
            max_output_tokens=131_072,
        )
    elif family == "minimax":
        capabilities = ModelCapabilities(
            structured_output_strategy="prompted_json",
            supports_forced_tool_choice=False,
            supports_parallel_tool_calls=False,
            allowed_tool_choices=("auto",),
            thinking_parameter="thinking",
            thinking_can_disable="m3" in model_name,
            reasoning_replay_required=True,
            reasoning_transport="think_tags",
            max_input_tokens=_minimax_context_window(channel, model_name),
            max_output_tokens=131_072,
        )
    elif family == "glm":
        thinking_parameter = "enable_thinking" if channel == "dashscope_openai" else "thinking"
        capabilities = ModelCapabilities(
            structured_output_strategy="json_mode",
            supports_json_mode=True,
            supports_forced_tool_choice=False,
            supports_parallel_tool_calls=False,
            allowed_tool_choices=("auto",),
            thinking_parameter=thinking_parameter,
            max_tools=128,
            max_input_tokens=1_000_000 if "5.2" in model_name else None,
            max_output_tokens=131_072 if "5.2" in model_name else None,
        )
    elif family == "openai":
        capabilities = ModelCapabilities(
            structured_output_strategy="function_calling",
            system_message_policy="provider_default",
            supports_json_mode=True,
            supports_forced_tool_choice=True,
            supports_parallel_tool_calls=True,
            allowed_tool_choices=("auto", "none", "required", "function"),
        )
    else:
        # 未识别网关维持旧的 Function Calling 行为，避免破坏已有部署。
        capabilities = ModelCapabilities(
            structured_output_strategy="function_calling",
            supports_json_mode=False,
            supports_forced_tool_choice=True,
            supports_parallel_tool_calls=False,
            allowed_tool_choices=("auto", "none", "required", "function"),
        )

    overrides: dict[str, int] = {}
    if connection.context_window is not None:
        overrides["max_input_tokens"] = connection.context_window
    if connection.max_output_tokens is not None:
        overrides["max_output_tokens"] = connection.max_output_tokens
    return replace(capabilities, **overrides) if overrides else capabilities


def _minimax_context_window(channel: str, model_name: str) -> int:
    if "m3" in model_name:
        return 1_000_000
    if channel == "dashscope_openai":
        return 196_608
    return 204_800

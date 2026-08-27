"""模型协议能力声明。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


StructuredOutputStrategy = Literal["function_calling", "json_mode", "prompted_json"]
SystemMessagePolicy = Literal["single_first", "provider_default"]


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    """一次模型连接在当前通道中可依赖的协议能力。"""

    structured_output_strategy: StructuredOutputStrategy
    system_message_policy: SystemMessagePolicy = "single_first"
    supports_json_mode: bool = False
    json_mode_requires_keyword: bool = False
    supports_forced_tool_choice: bool = False
    supports_parallel_tool_calls: bool = False
    allowed_tool_choices: tuple[str, ...] = ("auto",)
    thinking_parameter: str | None = None
    thinking_can_disable: bool = True
    reasoning_replay_required: bool = False
    reasoning_transport: str = "standard"
    max_tools: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None

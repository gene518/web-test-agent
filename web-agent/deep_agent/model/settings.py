"""模型连接配置与解析后的稳定数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, field_validator


ModelRole = Literal["master", "specialist"]
ModelFamily = Literal["auto", "openai", "qwen", "minimax", "glm", "generic"]
ModelChannel = Literal[
    "auto",
    "openai",
    "dashscope_openai",
    "minimax_openai",
    "minimax_anthropic",
    "zhipu_openai",
    "generic_openai",
    "generic_anthropic",
]
ThinkingMode = Literal["auto", "enabled", "disabled"]


class ModelConnectionSettings(BaseModel):
    """单个 Agent 角色使用的模型连接配置。"""

    family: ModelFamily = "auto"
    channel: ModelChannel = "auto"
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    thinking: ThinkingMode = "auto"
    reasoning_effort: str | None = None
    context_window: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    timeout_seconds: int | None = Field(default=None, gt=0)
    max_retries: int | None = Field(default=None, ge=0)
    stream_chunk_timeout_seconds: int | None = None

    @field_validator("model", "api_key", "base_url", "reasoning_effort", mode="before")
    @classmethod
    def _empty_string_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    def has_explicit_configuration(self) -> bool:
        """判断是否启用了新的角色级模型配置。"""

        return any(
            (
                self.family != "auto",
                self.channel != "auto",
                self.model is not None,
                self.api_key is not None,
                self.base_url is not None,
                self.thinking != "auto",
                self.reasoning_effort is not None,
                self.context_window is not None,
                self.max_output_tokens is not None,
                self.timeout_seconds is not None,
                self.max_retries is not None,
                self.stream_chunk_timeout_seconds is not None,
            )
        )


@dataclass(frozen=True, slots=True)
class ResolvedModelConnection:
    """完成旧配置迁移、模型识别和默认值补齐后的连接配置。"""

    role: ModelRole
    model: str
    api_model_name: str
    family: str
    channel: str
    protocol: str
    api_key: str | None
    base_url: str | None
    thinking: ThinkingMode
    reasoning_effort: str | None
    context_window: int | None
    max_output_tokens: int | None
    timeout_seconds: int
    max_retries: int
    stream_chunk_timeout_seconds: int | None
    legacy_config: bool = False
    metadata: dict[str, object] = field(default_factory=dict)

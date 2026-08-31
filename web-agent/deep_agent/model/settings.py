"""模型连接配置与解析后的稳定数据结构。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


ModelRole = Literal["master", "specialist"]
ModelFamily = Literal["openai", "qwen", "minimax", "glm", "generic"]
ModelChannel = Literal[
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

    model_config = ConfigDict(extra="forbid")

    family: ModelFamily | None = None
    channel: ModelChannel | None = None
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    thinking: ThinkingMode = "auto"

    @field_validator("family", "channel", "model", mode="before")
    @classmethod
    def _normalize_required_text(cls, value: object) -> object:
        """让空必填项进入统一的连接配置错误路径。"""

        if isinstance(value, str):
            return value.strip() or None
        return value


@dataclass(frozen=True, slots=True)
class ResolvedModelConnection:
    """完成必填校验和运行参数补齐后的模型连接配置。"""

    role: ModelRole
    api_model_name: str
    family: ModelFamily
    channel: ModelChannel
    protocol: Literal["openai", "anthropic"]
    api_key: str | None
    base_url: str | None
    thinking: ThinkingMode
    timeout_seconds: int
    max_retries: int
    stream_chunk_timeout_seconds: int | None

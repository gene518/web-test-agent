"""线程标题生成使用的最小结构化输出模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from deep_agent.agent.master.models.intent import normalize_thread_title


class ThreadTitleGeneration(BaseModel):
    """无状态标题图返回的结构化结果。"""

    thread_title: str | None = Field(
        default=None,
        description="根据首个明确用户目标生成的简短会话标题。",
    )

    @field_validator("thread_title", mode="before")
    @classmethod
    def normalize_generated_thread_title(cls, value: Any) -> str | None:
        """与 Master 分类链路使用同一套标题归一化规则。"""

        return normalize_thread_title(value)

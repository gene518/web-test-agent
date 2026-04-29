"""LangGraph 工作流共享状态定义。"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class WorkflowState(TypedDict, total=False):
    """定义 Master 工作流在各节点间传递的共享状态。"""

    messages: Annotated[
        list[AnyMessage],
        add_messages,
        Field(description="对话消息列表；通过 add_messages 聚合，供各节点追加而不是覆盖。"),
    ]
    agent_type: Annotated[
        str | None,
        Field(description="Master 识别出的目标 Specialist 类型，例如 plan、generator、healer。"),
    ]
    extracted_params: Annotated[
        dict[str, Any],
        Field(description="从用户输入中提取出的结构化参数，供后续 Specialist 消费。"),
    ]
    missing_params: Annotated[
        list[str],
        Field(description="仍然缺失、需要继续追问用户补齐的参数名列表。"),
    ]
    next_action: Annotated[
        str,
        Field(description="当前工作流下一步要执行的动作或节点标识。"),
    ]
    routing_reason: Annotated[
        str,
        Field(description="记录当前路由决策原因，便于调试和日志追踪。"),
    ]

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
    return_to_master: Annotated[
        bool,
        Field(description="Specialist 完成后回到 Master 子图的收尾标记，用于避免同轮重复分类。"),
    ]
    stage_result: Annotated[
        dict[str, Any],
        Field(description="当前阶段的内部执行结果摘要，供最终总结使用，不直接作为用户消息返回。"),
    ]
    final_summary: Annotated[
        str,
        Field(description="最终返回给用户的总结文本，覆盖用户要求、分析方式、执行方式和完成内容。"),
    ]
    conversation_summary: Annotated[
        str,
        Field(description="长对话压缩后的历史摘要，后续模型输入会结合该摘要和最近消息。"),
    ]
    summarized_message_count: Annotated[
        int,
        Field(description="生成 conversation_summary 时已纳入压缩的消息数量，用于避免重复压缩同一批消息。"),
    ]
    pending_agent_type: Annotated[
        str | None,
        Field(description="参数补全过程中锁定的目标 Specialist 类型，resume 后不允许切换意图。"),
    ]
    pending_missing_params: Annotated[
        list[str],
        Field(description="参数补全过程中仍待补齐的字段列表。"),
    ]

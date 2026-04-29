"""Master 的 ask_params 续聊上下文提示词。"""

from __future__ import annotations

from typing import Any


def build_master_ask_params_context_prompt(
    *,
    agent_type: str | None,
    extracted_params: dict[str, Any] | None,
    missing_params: list[str] | None,
    routing_reason: str | None,
) -> str | None:
    """为 ask_params 之后的补参续聊构造追加上下文提示词。"""

    if not agent_type or not missing_params:
        return None

    known_context = extracted_params or {}
    resolved_reason = routing_reason or "上一轮已确认需要继续补参。"
    return (
        "你正在处理一次 `ask_params` 之后的补参续聊，而不是默认把它当成全新任务。\n"
        "请继续沿用上一轮已经确认的任务上下文，并结合本轮用户回复重新输出完整结构化结果。\n\n"
        f"- 上一轮已确认的 agent_type: `{agent_type}`\n"
        f"- 上一轮已提取参数: {known_context}\n"
        f"- 上一轮仍缺失的参数: {missing_params}\n"
        f"- 上一轮路由原因: {resolved_reason}\n\n"
        "续聊处理要求：\n"
        "1. 如果本轮用户只是补充缺失参数，不要把任务重置成 `unknown` 或 `general`。\n"
        "2. 输出时保留上一轮已经确认且本轮未被用户否定或覆盖的参数。\n"
        "3. 如果用户明确改口、否定之前信息或发起了一个全新任务，才重新分类并按新任务抽取。\n"
        "4. 抽取结果仍然只能来自你的语义判断，不要臆造缺失字段。"
    )

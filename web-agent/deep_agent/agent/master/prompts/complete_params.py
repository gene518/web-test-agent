"""Master 参数补全上下文提示词。"""

from __future__ import annotations

from typing import Any


def build_master_complete_params_prompt(
    *,
    agent_type: str | None,
    extracted_params: dict[str, Any] | None,
    missing_params: list[str] | None,
    routing_reason: str | None,
) -> str | None:
    """为 interrupt/resume 补参构造追加上下文提示词。"""

    if not agent_type or not missing_params:
        return None

    known_context = extracted_params or {}
    resolved_reason = routing_reason or "上一轮已确认需要继续补参。"
    return (
        f"你正在补齐一次 `{agent_type}` 任务的参数，而不是处理全新任务。\n"
        "本轮只允许从用户补充内容中抽取缺失参数，并合并到已有上下文。\n\n"
        f"- 固定 agent_type: `{agent_type}`\n"
        f"- 已提取参数: {known_context}\n"
        f"- 仍缺失的参数: {missing_params}\n"
        f"- 路由原因: {resolved_reason}\n\n"
        "补参处理要求：\n"
        f"1. 结构化输出里的 intent_type 必须继续使用 `{agent_type}`。\n"
        "2. 不要因为用户补充内容里出现新词就切换为其他 intent。\n"
        "3. 如果本轮没有补齐任何字段，也保持原 intent，并只返回真实识别出的字段。\n"
        "4. 抽取结果只能来自用户补充内容和已知上下文，不要臆造缺失字段。"
    )

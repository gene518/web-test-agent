"""User-visible summaries for specialist stage artifacts."""

from __future__ import annotations

from typing import Any

from .common import (
    ArtifactHistoryEntry,
    StageName,
    StageSummaryEntry,
    dedupe,
    normalize_optional_text,
    normalize_string_list,
)


def build_stage_summary(
    *,
    stage: StageName,
    status: str,
    artifact: ArtifactHistoryEntry | None,
    fallback_message: str | None = None,
) -> StageSummaryEntry:
    """构建面向用户的阶段摘要块。"""

    if artifact is None:
        text = _build_failure_stage_summary(stage=stage, status=status, fallback_message=fallback_message)
        return StageSummaryEntry(artifact_id=None, stage=stage, status=status, text=text)

    if status != "success":
        text = _build_failure_stage_summary(stage=stage, status=status, fallback_message=fallback_message, artifact=artifact)
        return StageSummaryEntry(
            artifact_id=artifact.get("artifact_id"),
            stage=stage,
            status=status,
            text=text,
        )

    if stage == "plan":
        text = _build_plan_stage_summary(artifact)
    elif stage == "generator":
        text = _build_generator_stage_summary(artifact)
    else:
        text = _build_healer_stage_summary(artifact)

    return StageSummaryEntry(
        artifact_id=artifact.get("artifact_id"),
        stage=stage,
        status=status,
        text=text,
    )


def build_final_turn_summary(pending_stage_summaries: Any) -> str:
    """构建当前轮最终展示给用户的单条回复。"""

    if not isinstance(pending_stage_summaries, list) or not pending_stage_summaries:
        return "当前轮次已结束，但没有可汇总的阶段结果。"

    blocks: list[str] = []
    for summary in pending_stage_summaries:
        if not isinstance(summary, dict):
            continue
        text = str(summary.get("text") or "").strip()
        if text:
            blocks.append(text)
    return "\n\n".join(blocks) if blocks else "当前轮次已结束，但没有可汇总的阶段结果。"


def _build_plan_stage_summary(artifact: ArtifactHistoryEntry) -> str:
    plan_files = ", ".join(f"`{path}`" for path in artifact.get("output_files", [])) or "无"
    planned_files = _plan_target_files_from_artifact(artifact)
    case_detail_lines = _build_plan_case_detail_lines(artifact)
    lines = [
        "**Plan 阶段**",
        "- 状态：成功",
        f"- 项目目录：`{artifact.get('project_dir', '未知')}`",
        f"- 已保存测试计划：共 {len(normalize_string_list(artifact.get('output_files')))} 个，{plan_files}",
        (
            f"- 待生成脚本规划：共 {len(planned_files)} 个，"
            + "、".join(f"`{path}`" for path in planned_files)
            if planned_files
            else "- 待生成脚本规划：无"
        ),
        (
            f"- 用例明细：共 {len(case_detail_lines)} 条"
            if case_detail_lines
            else "- 用例明细：无"
        ),
    ]
    if case_detail_lines:
        lines.extend(case_detail_lines)
    lines.append(f"- 下一阶段建议输入：{_next_stage_input_hint('plan', artifact)}")
    return "\n".join(lines)


def _build_generator_stage_summary(artifact: ArtifactHistoryEntry) -> str:
    input_plans = ", ".join(f"`{path}`" for path in artifact.get("input_files", [])) or "无"
    output_scripts = ", ".join(f"`{path}`" for path in artifact.get("output_files", [])) or "无"
    script_detail_lines = _build_script_detail_lines(
        artifact=artifact,
        detail_prefix="脚本",
        include_source_plan=True,
    )
    lines = [
        "**Generator 阶段**",
        "- 状态：成功",
        f"- 项目目录：`{artifact.get('project_dir', '未知')}`",
        f"- 来源测试计划：共 {len(normalize_string_list(artifact.get('input_files')))} 个，{input_plans}",
        f"- 已生成脚本：共 {len(normalize_string_list(artifact.get('output_files')))} 个，{output_scripts}",
        (
            f"- 脚本明细：共 {len(script_detail_lines)} 条"
            if script_detail_lines
            else "- 脚本明细：无"
        ),
    ]
    if script_detail_lines:
        lines.extend(script_detail_lines)
    lines.append(f"- 下一阶段建议输入：{_next_stage_input_hint('generator', artifact)}")
    return "\n".join(lines)


def _build_healer_stage_summary(artifact: ArtifactHistoryEntry) -> str:
    input_scripts = ", ".join(f"`{path}`" for path in artifact.get("input_files", [])) or "无"
    changed_files = ", ".join(f"`{path}`" for path in artifact.get("output_files", [])) or "无"
    validation_runs = ", ".join(f"`{path}`" for path in artifact.get("validation_runs", [])) or "无"
    script_detail_lines = _build_script_detail_lines(
        artifact=artifact,
        detail_prefix="调试对象",
        include_source_plan=False,
    )
    lines = [
        "**Healer 阶段**",
        "- 状态：成功",
        f"- 项目目录：`{artifact.get('project_dir', '未知')}`",
        f"- 调试目标脚本：共 {len(normalize_string_list(artifact.get('input_files')))} 个，{input_scripts}",
        f"- 实际变更文件：共 {len(normalize_string_list(artifact.get('output_files')))} 个，{changed_files}",
        f"- 验证运行目标：共 {len(normalize_string_list(artifact.get('validation_runs')))} 个，{validation_runs}",
        (
            f"- 脚本明细：共 {len(script_detail_lines)} 条"
            if script_detail_lines
            else "- 脚本明细：无"
        ),
    ]
    if script_detail_lines:
        lines.extend(script_detail_lines)
    lines.append(f"- 下一阶段建议输入：{_next_stage_input_hint('healer', artifact)}")
    return "\n".join(lines)


def _plan_target_files_from_artifact(artifact: ArtifactHistoryEntry) -> list[str]:
    """返回 Plan 阶段规划出的脚本路径列表。"""

    return dedupe(
        [
            *normalize_string_list(artifact.get("planned_test_case_files")),
            *normalize_string_list(artifact.get("saved_test_case_files")),
        ]
    )


def _build_plan_case_detail_lines(artifact: ArtifactHistoryEntry) -> list[str]:
    """为 Plan 阶段构建更易读的用例详情列表。"""

    detail_lines: list[str] = []
    for index, item in enumerate(artifact.get("saved_test_cases", []), start=1):
        if not isinstance(item, dict):
            continue
        case_name = normalize_optional_text(item.get("case_name"))
        target_file = normalize_optional_text(item.get("file"))
        if not case_name and not target_file:
            continue
        suite_name = normalize_optional_text(item.get("suite_name")) or "未分组"
        step_count = item.get("step_count")
        step_text = f"{step_count} 步" if isinstance(step_count, int) and step_count > 0 else "步骤未标注"
        parts = [
            f"- 用例 {index}：`{case_name or '未命名用例'}`",
            f"所属分组 `{suite_name}`",
            step_text,
        ]
        if target_file:
            parts.append(f"计划生成 `{target_file}`")
        detail_lines.append("，".join(parts))
    return detail_lines


def _build_script_detail_lines(
    *,
    artifact: ArtifactHistoryEntry,
    detail_prefix: str,
    include_source_plan: bool,
) -> list[str]:
    """为 Generator / Healer 阶段构建统一的脚本详情列表。"""

    detail_lines: list[str] = []
    for index, item in enumerate(artifact.get("items", []), start=1):
        if not isinstance(item, dict):
            continue
        file_path = normalize_optional_text(item.get("file"))
        if not file_path:
            continue
        title_text = _format_test_title_summary(item.get("test_titles"))
        parts = [f"- {detail_prefix} {index}：`{file_path}`", title_text]
        if include_source_plan:
            source_plan = normalize_optional_text(item.get("source_plan"))
            if source_plan:
                parts.append(f"来源计划 `{source_plan}`")
        detail_lines.append("，".join(parts))
    return detail_lines


def _format_test_title_summary(value: Any) -> str:
    """把脚本里的标题列表压缩成简洁摘要。"""

    titles = normalize_string_list(value)
    if not titles:
        return "未提取到测试标题"
    return "覆盖标题 " + "、".join(f"`{title}`" for title in titles)


def _next_stage_input_hint(stage: StageName, artifact: ArtifactHistoryEntry | dict[str, Any]) -> str:
    """返回各阶段尾部的下一步输入建议。"""

    if stage == "plan":
        plan_files = normalize_string_list(artifact.get("test_plan_files"))
        plan_hint = "、".join(f"`{path}`" for path in plan_files) if plan_files else "`test_plan_files`"
        return (
            "如需继续生成测试脚本，可直接回复“继续生成测试脚本”；"
            f"系统会优先复用当前测试计划 {plan_hint}，"
            "也可补充 `test_plan_files` 或 `test_cases` 来缩小生成范围。"
        )

    if stage == "generator":
        output_scripts = normalize_string_list(artifact.get("output_files"))
        script_hint = "、".join(f"`{path}`" for path in output_scripts) if output_scripts else "`test_scripts`"
        plan_files = normalize_string_list(artifact.get("input_files"))
        plan_hint = "、".join(f"`{path}`" for path in plan_files) if plan_files else "`test_plan_files`"
        return (
            "如需继续调试脚本，可直接回复“调试脚本通过”；"
            f"系统会优先复用当前脚本 {script_hint}，并关联测试计划 {plan_hint}。"
            "也可额外补充 `test_scripts` 或 `test_plan_files` 指定调试范围。"
        )

    output_scripts = normalize_string_list(artifact.get("output_files"))
    input_scripts = normalize_string_list(artifact.get("input_files"))
    preferred_scripts = output_scripts or input_scripts
    script_hint = "、".join(f"`{path}`" for path in preferred_scripts) if preferred_scripts else "`test_scripts`"
    return (
        "如需继续复测或追加修复，可继续提供 "
        f"{script_hint}；如需重新生成为其他用例写脚本，可回复“继续生成测试脚本”，"
        "并按需补充 `test_plan_files` / `test_cases`。"
    )


def _build_failure_stage_summary(
    *,
    stage: StageName,
    status: str,
    fallback_message: str | None,
    artifact: ArtifactHistoryEntry | None = None,
) -> str:
    stage_label = {
        "plan": "Plan",
        "generator": "Generator",
        "healer": "Healer",
    }[stage]
    lines = [
        f"**{stage_label} 阶段**",
        f"- 状态：{status}",
    ]
    if artifact is not None:
        lines.append(f"- 项目目录：`{artifact.get('project_dir', '未知')}`")
        input_files = ", ".join(f"`{path}`" for path in artifact.get("input_files", []))
        if input_files:
            lines.append(f"- 已识别输入文件：{input_files}")
    if fallback_message:
        lines.append(f"- 说明：{fallback_message}")
    lines.append(f"- 下一阶段建议输入：{_next_stage_input_hint(stage, artifact or {})}")
    return "\n".join(lines)

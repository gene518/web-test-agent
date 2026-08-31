"""阶段链状态与产物继承辅助。"""

from __future__ import annotations

from typing import Any

from .common import (
    PLAN_FILE_FIELD,
    SCRIPT_FILE_FIELD,
    STAGE_SEQUENCE,
    ArtifactHistoryEntry,
    LatestArtifacts,
    StageName,
    StageSummaryEntry,
    dedupe,
    normalize_optional_text,
    normalize_stage_name,
    normalize_string_list,
)


def normalize_requested_pipeline(value: Any, *, default_stage: str | None = None) -> list[StageName]:
    """归一化请求的流水线阶段列表，并保持顺序与唯一性。"""

    candidates: list[str]
    if isinstance(value, (list, tuple)):
        candidates = [str(item) for item in value]
    elif value is None:
        candidates = []
    else:
        candidates = [str(value)]

    normalized: list[StageName] = []
    seen: set[str] = set()
    for candidate in candidates:
        stage = normalize_stage_name(candidate)
        if stage is None or stage in seen:
            continue
        seen.add(stage)
        normalized.append(stage)

    if normalized:
        return normalized

    default_normalized = normalize_stage_name(default_stage)
    return [default_normalized] if default_normalized is not None else []


def merge_file_lists(explicit_files: Any, inherited_files: Any) -> list[str]:
    """合并显式文件列表与继承文件列表，并保持顺序去重。"""

    merged: list[str] = []
    seen: set[str] = set()
    for candidate in normalize_string_list(explicit_files) + normalize_string_list(inherited_files):
        if candidate in seen:
            continue
        seen.add(candidate)
        merged.append(candidate)
    return merged


def append_artifact_history(
    state: dict[str, Any],
    artifact: ArtifactHistoryEntry | None,
) -> tuple[list[ArtifactHistoryEntry], LatestArtifacts, list[str]]:
    """把阶段产物追加到历史记录和最新产物指针中。"""

    history = list(state.get("artifact_history", []))
    latest_artifacts = dict(state.get("latest_artifacts", {}))
    current_turn_artifact_ids = list(state.get("current_turn_artifact_ids", []))
    if artifact is None:
        return history, latest_artifacts, current_turn_artifact_ids

    history.append(artifact)
    latest_artifacts[artifact["stage"]] = artifact
    artifact_id = artifact.get("artifact_id")
    if artifact_id and artifact_id not in current_turn_artifact_ids:
        current_turn_artifact_ids.append(artifact_id)
    return history, latest_artifacts, current_turn_artifact_ids


def append_stage_summary(
    state: dict[str, Any],
    stage_summary: StageSummaryEntry,
) -> list[StageSummaryEntry]:
    """把阶段摘要幂等追加到当前轮缓冲区。"""

    pending = list(state.get("pending_stage_summaries", []))
    finalization_key = stage_summary.get("finalization_key")
    for existing in pending:
        if not isinstance(existing, dict):
            continue
        if finalization_key and existing.get("finalization_key") == finalization_key:
            return pending
        if all(
            existing.get(field_name) == stage_summary.get(field_name)
            for field_name in ("artifact_id", "stage", "status", "text")
        ):
            return pending
    pending.append(stage_summary)
    return pending


def summarize_latest_artifacts(latest_artifacts: Any) -> str:
    """把最新产物整理成供 Master 决策使用的紧凑提示块。"""

    if not isinstance(latest_artifacts, dict) or not latest_artifacts:
        return ""

    lines = ["## 历史产物上下文"]
    for stage_name in STAGE_SEQUENCE:
        artifact = latest_artifacts.get(stage_name)
        if not isinstance(artifact, dict):
            continue
        output_files = ", ".join(artifact.get("output_files", [])) or "无"
        input_files = ", ".join(artifact.get("input_files", [])) or "无"
        project_dir = artifact.get("project_dir") or "未知"
        status = artifact.get("status") or "unknown"
        lines.append(
            f"- {stage_name}: status={status}; project_dir=`{project_dir}`; "
            f"input_files={input_files}; output_files={output_files}"
        )
    return "\n".join(lines) if len(lines) > 1 else ""


def resolve_stage_inputs(
    *,
    stage: StageName,
    extracted_params: dict[str, Any],
    latest_artifacts: Any,
    previous_stage: StageName | None = None,
) -> dict[str, Any]:
    """在参数补全前，按阶段合并显式文件和历史文件。"""

    resolved = dict(extracted_params)
    normalized_latest = latest_artifacts if isinstance(latest_artifacts, dict) else {}

    preferred_artifacts = artifact_candidates_for_stage(stage, normalized_latest)
    for field_name in ("project_dir", "project_name"):
        if normalize_optional_text(resolved.get(field_name)):
            continue
        for artifact in preferred_artifacts:
            inherited_value = normalize_optional_text(artifact.get(field_name))
            if inherited_value:
                resolved[field_name] = inherited_value
                break

    if stage == "plan":
        inherited_plan_files = collect_plan_files(preferred_artifacts)
        merged_plan_files = merge_file_lists(resolved.get(PLAN_FILE_FIELD), inherited_plan_files)
        if merged_plan_files:
            resolved[PLAN_FILE_FIELD] = merged_plan_files
        return resolved

    if stage == "generator":
        inherited_plan_files = collect_plan_files(preferred_artifacts)
        merged_plan_files = merge_file_lists(resolved.get(PLAN_FILE_FIELD), inherited_plan_files)
        if merged_plan_files:
            resolved[PLAN_FILE_FIELD] = merged_plan_files
        align_generator_test_cases_with_latest_plan(
            resolved,
            preferred_artifacts,
            previous_stage=previous_stage,
        )
        return resolved

    inherited_scripts = collect_script_files(preferred_artifacts)
    merged_scripts = merge_file_lists(resolved.get(SCRIPT_FILE_FIELD), inherited_scripts)
    if merged_scripts:
        resolved[SCRIPT_FILE_FIELD] = merged_scripts

    inherited_plan_files = collect_plan_files(preferred_artifacts)
    merged_plan_files = merge_file_lists(resolved.get(PLAN_FILE_FIELD), inherited_plan_files)
    if merged_plan_files:
        resolved[PLAN_FILE_FIELD] = merged_plan_files
    return resolved


def current_stage_from_pipeline(state: dict[str, Any]) -> StageName | None:
    """根据状态中的流水线字段解析当前阶段。"""

    requested_pipeline = normalize_requested_pipeline(state.get("requested_pipeline"), default_stage=state.get("agent_type"))
    pipeline_cursor = state.get("pipeline_cursor", 0)
    if not requested_pipeline:
        return normalize_stage_name(state.get("pending_agent_type") or state.get("agent_type"))
    if not isinstance(pipeline_cursor, int) or pipeline_cursor < 0 or pipeline_cursor >= len(requested_pipeline):
        return requested_pipeline[0]
    return requested_pipeline[pipeline_cursor]


def has_more_pipeline_stages(state: dict[str, Any]) -> bool:
    """判断当前阶段之后是否还有待执行阶段。"""

    requested_pipeline = normalize_requested_pipeline(state.get("requested_pipeline"), default_stage=state.get("agent_type"))
    pipeline_cursor = state.get("pipeline_cursor", 0)
    if not isinstance(pipeline_cursor, int):
        return False
    return pipeline_cursor + 1 < len(requested_pipeline)


def next_pipeline_stage(state: dict[str, Any]) -> StageName | None:
    """返回下一个流水线阶段（如果存在）。"""

    requested_pipeline = normalize_requested_pipeline(state.get("requested_pipeline"), default_stage=state.get("agent_type"))
    pipeline_cursor = state.get("pipeline_cursor", 0)
    if not isinstance(pipeline_cursor, int):
        return None
    next_index = pipeline_cursor + 1
    if next_index < 0 or next_index >= len(requested_pipeline):
        return None
    return requested_pipeline[next_index]


def previous_pipeline_stage(state: dict[str, Any]) -> StageName | None:
    """返回上一个流水线阶段（如果存在）。"""

    requested_pipeline = normalize_requested_pipeline(state.get("requested_pipeline"), default_stage=state.get("agent_type"))
    pipeline_cursor = state.get("pipeline_cursor", 0)
    if not isinstance(pipeline_cursor, int):
        return None
    previous_index = pipeline_cursor - 1
    if previous_index < 0 or previous_index >= len(requested_pipeline):
        return None
    return requested_pipeline[previous_index]


def clear_current_turn_buffers(state: dict[str, Any]) -> dict[str, Any]:
    """在最终汇总后重置当前轮的摘要缓冲区。"""

    return {
        "pending_stage_summaries": [],
        "current_turn_artifact_ids": [],
        "pipeline_handoff": False,
    }


def artifact_candidates_for_stage(stage: StageName, latest_artifacts: LatestArtifacts) -> list[ArtifactHistoryEntry]:
    """返回可用于继承的同阶段和上游阶段候选产物。"""

    candidates: list[ArtifactHistoryEntry] = []
    if stage == "plan":
        ordered_stage_names = ("plan",)
    elif stage == "generator":
        ordered_stage_names = ("generator", "plan")
    else:
        ordered_stage_names = ("healer", "generator", "plan")

    for stage_name in ordered_stage_names:
        artifact = latest_artifacts.get(stage_name)
        if isinstance(artifact, dict):
            candidates.append(artifact)
    return candidates


def collect_plan_files(artifacts: list[ArtifactHistoryEntry]) -> list[str]:
    """从阶段产物中收集测试计划文件。"""

    collected: list[str] = []
    for artifact in artifacts:
        collected.extend(normalize_string_list(artifact.get("test_plan_files")))
        if artifact.get("stage") == "plan":
            collected.extend(normalize_string_list(artifact.get("output_files")))
        if artifact.get("stage") == "generator":
            collected.extend(normalize_string_list(artifact.get("input_files")))
    return dedupe(collected)


def collect_script_files(artifacts: list[ArtifactHistoryEntry]) -> list[str]:
    """从阶段产物中收集脚本文件。"""

    collected: list[str] = []
    for artifact in artifacts:
        collected.extend(normalize_string_list(artifact.get("test_scripts")))
        if artifact.get("stage") == "generator":
            collected.extend(normalize_string_list(artifact.get("output_files")))
        if artifact.get("stage") == "healer":
            collected.extend(normalize_string_list(artifact.get("input_files")))
    return dedupe(collected)


def collect_saved_case_names(artifacts: list[ArtifactHistoryEntry]) -> list[str]:
    """从上游 Plan 产物中收集已保存的用例名。"""

    collected: list[str] = []
    for artifact in artifacts:
        saved_test_cases = artifact.get("saved_test_cases")
        if not isinstance(saved_test_cases, list):
            continue
        for item in saved_test_cases:
            if not isinstance(item, dict):
                continue
            case_name = normalize_optional_text(item.get("case_name"))
            if case_name:
                collected.append(case_name)
    return dedupe(collected)


def align_generator_test_cases_with_latest_plan(
    resolved_params: dict[str, Any],
    artifacts: list[ArtifactHistoryEntry],
    *,
    previous_stage: StageName | None,
) -> None:
    """在 plan 到 generator 交接时，把类似选择器的 `test_cases` 文本替换为具体的计划用例名。"""

    requested_test_cases = normalize_string_list(resolved_params.get("test_cases"))
    if previous_stage != "plan" or len(requested_test_cases) != 1:
        return

    planned_case_names = collect_saved_case_names(artifacts)
    if not planned_case_names:
        return

    requested_case = requested_test_cases[0]
    if requested_case in set(planned_case_names):
        return
    if not looks_like_case_selector_text(requested_case):
        return

    resolved_params["test_cases"] = planned_case_names


def looks_like_case_selector_text(value: str) -> bool:
    """启发式识别“高优先级三条用例”这类自然语言用例选择表达。"""

    normalized_value = (value or "").strip().lower()
    if not normalized_value:
        return False

    selector_keywords = (
        "用例",
        "测试",
        "优先级",
        "全部",
        "所有",
        "前三",
        "前3",
        "top",
        "highest",
        "high priority",
        "first three",
    )
    return any(keyword in normalized_value for keyword in selector_keywords)

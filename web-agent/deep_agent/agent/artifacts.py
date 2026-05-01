"""Workflow artifact helpers for plan/generator/healer stages."""

from __future__ import annotations

import asyncio
from pathlib import Path
import re
from typing import Any, Literal
from uuid import uuid4

from typing_extensions import TypedDict


StageName = Literal["plan", "generator", "healer"]
STAGE_SEQUENCE: tuple[StageName, ...] = ("plan", "generator", "healer")
SKIPPED_SNAPSHOT_DIR_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "test-results",
    }
)
PLAN_FILE_FIELD = "test_plan_files"
SCRIPT_FILE_FIELD = "test_scripts"
PLAN_MARKDOWN_RE = re.compile(r"^aaa_.+\.md$", re.IGNORECASE)
TEST_DESCRIBE_RE = re.compile(r"""test\.describe\(\s*(['"])(?P<title>.+?)\1""")
TEST_TITLE_RE = re.compile(r"""test(?:\.\w+)?\(\s*(['"])(?P<title>.+?)\1""")
SPEC_SOURCE_RE = re.compile(r"""//\s*spec:\s*(?P<path>.+)$""", re.MULTILINE)
PLAN_CASE_HEADER_RE = re.compile(r"^\s*####\s+.*?(?P<case_name>[a-z][a-z0-9_]*(?:_[a-z0-9_]+)*)\s*$")
PLAN_FILE_LINE_RE = re.compile(r"^\s*\*\*File:\*\*\s*`(?P<file>[^`]+)`\s*$", re.IGNORECASE)
PLANNING_DIR_PREFIX = "aaaplanning_"
PLAN_FILE_PREFIX = "aaa_"


class ArtifactItem(TypedDict, total=False):
    """A small, stage-specific artifact entry."""

    kind: str
    suite_name: str
    case_name: str
    file: str
    seed_file: str
    step_count: int
    source_plan: str
    describe_title: str
    test_titles: list[str]
    validation_targets: list[str]


class ArtifactHistoryEntry(TypedDict, total=False):
    """A lightweight persisted stage artifact entry."""

    artifact_id: str
    stage: StageName
    status: str
    project_name: str
    project_dir: str
    input_files: list[str]
    touched_files: list[str]
    output_files: list[str]
    items: list[ArtifactItem]
    message: str
    test_plan_files: list[str]
    test_scripts: list[str]
    saved_test_cases: list[ArtifactItem]
    saved_test_case_files: list[str]
    validation_runs: list[str]


class StageSummaryEntry(TypedDict, total=False):
    """A formatted per-stage summary buffered for finalization."""

    artifact_id: str | None
    stage: StageName
    status: str
    text: str


class FileManifestEntry(TypedDict):
    """Filesystem manifest metadata used for before/after diffs."""

    mtime_ns: int
    size: int


LatestArtifacts = dict[str, ArtifactHistoryEntry]
WorkspaceManifest = dict[str, FileManifestEntry]


def normalize_requested_pipeline(value: Any, *, default_stage: str | None = None) -> list[StageName]:
    """Normalize a requested pipeline list, preserving stage order and uniqueness."""

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
        stage = _normalize_stage_name(candidate)
        if stage is None or stage in seen:
            continue
        seen.add(stage)
        normalized.append(stage)

    if normalized:
        return normalized

    default_normalized = _normalize_stage_name(default_stage)
    return [default_normalized] if default_normalized is not None else []


def merge_file_lists(explicit_files: Any, inherited_files: Any) -> list[str]:
    """Merge explicit and inherited file lists, preserving order and deduplicating."""

    merged: list[str] = []
    seen: set[str] = set()
    for candidate in _normalize_string_list(explicit_files) + _normalize_string_list(inherited_files):
        if candidate in seen:
            continue
        seen.add(candidate)
        merged.append(candidate)
    return merged


def append_artifact_history(
    state: dict[str, Any],
    artifact: ArtifactHistoryEntry | None,
) -> tuple[list[ArtifactHistoryEntry], LatestArtifacts, list[str]]:
    """Append a stage artifact into history and latest pointers."""

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
    """Append a formatted stage summary to the current turn buffer."""

    pending = list(state.get("pending_stage_summaries", []))
    pending.append(stage_summary)
    return pending


def summarize_latest_artifacts(latest_artifacts: Any) -> str:
    """Format the latest artifacts into a compact prompt block for master decisions."""

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


def snapshot_workspace_manifest(workspace_dir: Path | None) -> WorkspaceManifest:
    """Snapshot the workspace into a lightweight manifest."""

    if workspace_dir is None or not workspace_dir.is_dir():
        return {}

    manifest: WorkspaceManifest = {}
    for path in workspace_dir.rglob("*"):
        if not path.is_file():
            continue
        if _should_skip_snapshot_path(path.relative_to(workspace_dir)):
            continue
        stat = path.stat()
        manifest[path.relative_to(workspace_dir).as_posix()] = {
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
        }
    return manifest


async def snapshot_workspace_manifest_async(workspace_dir: Path | None) -> WorkspaceManifest:
    """Build a workspace manifest without blocking the event loop."""

    return await asyncio.to_thread(snapshot_workspace_manifest, workspace_dir)


def diff_workspace_manifest(before: WorkspaceManifest, after: WorkspaceManifest) -> dict[str, list[str]]:
    """Compute added / modified / removed files between two manifests."""

    before_paths = set(before)
    after_paths = set(after)
    added = sorted(after_paths - before_paths)
    removed = sorted(before_paths - after_paths)
    modified = sorted(
        path
        for path in (before_paths & after_paths)
        if before[path]["mtime_ns"] != after[path]["mtime_ns"] or before[path]["size"] != after[path]["size"]
    )
    touched = sorted({*added, *modified, *removed})
    return {
        "added": added,
        "modified": modified,
        "removed": removed,
        "touched": touched,
    }


def resolve_stage_inputs(
    *,
    stage: StageName,
    extracted_params: dict[str, Any],
    latest_artifacts: Any,
    previous_stage: StageName | None = None,
) -> dict[str, Any]:
    """Merge explicit and historical files for a stage before parameter completion."""

    resolved = dict(extracted_params)
    normalized_latest = latest_artifacts if isinstance(latest_artifacts, dict) else {}

    preferred_artifacts = _artifact_candidates_for_stage(stage, normalized_latest)
    for field_name in ("project_dir", "project_name"):
        if _normalize_optional_text(resolved.get(field_name)):
            continue
        for artifact in preferred_artifacts:
            inherited_value = _normalize_optional_text(artifact.get(field_name))
            if inherited_value:
                resolved[field_name] = inherited_value
                break

    if stage == "plan":
        inherited_plan_files = _collect_plan_files(preferred_artifacts)
        merged_plan_files = merge_file_lists(resolved.get(PLAN_FILE_FIELD), inherited_plan_files)
        if merged_plan_files:
            resolved[PLAN_FILE_FIELD] = merged_plan_files
        return resolved

    if stage == "generator":
        inherited_plan_files = _collect_plan_files(preferred_artifacts)
        merged_plan_files = merge_file_lists(resolved.get(PLAN_FILE_FIELD), inherited_plan_files)
        if merged_plan_files:
            resolved[PLAN_FILE_FIELD] = merged_plan_files
        _align_generator_test_cases_with_latest_plan(
            resolved,
            preferred_artifacts,
            previous_stage=previous_stage,
        )
        return resolved

    inherited_scripts = _collect_script_files(preferred_artifacts)
    merged_scripts = merge_file_lists(resolved.get(SCRIPT_FILE_FIELD), inherited_scripts)
    if merged_scripts:
        resolved[SCRIPT_FILE_FIELD] = merged_scripts

    inherited_plan_files = _collect_plan_files(preferred_artifacts)
    merged_plan_files = merge_file_lists(resolved.get(PLAN_FILE_FIELD), inherited_plan_files)
    if merged_plan_files:
        resolved[PLAN_FILE_FIELD] = merged_plan_files
    return resolved


def extract_plan_artifact_from_planner_payload(
    *,
    payload: Any,
    project_dir: Path,
    project_name: str,
    input_files: list[str] | None = None,
) -> ArtifactHistoryEntry:
    """Validate a planner payload and convert it into a plan-stage artifact."""

    if not isinstance(payload, dict):
        raise RuntimeError("`planner_save_plan` 输入 payload 非法：必须是对象。")

    plan_file = _validate_relative_workspace_path(
        payload.get("fileName"),
        project_dir=project_dir,
        expected_suffix=".md",
        field_name="planner_save_plan.fileName",
    )
    plan_identifier = _validate_planner_markdown_layout(
        plan_file,
        field_name="planner_save_plan.fileName",
    )
    overview = _require_non_empty_text(payload.get("overview"), field_name="planner_save_plan.overview")
    plan_name = _require_non_empty_text(payload.get("name"), field_name="planner_save_plan.name")

    suites = payload.get("suites")
    if not isinstance(suites, list) or not suites:
        raise RuntimeError("`planner_save_plan.suites` 不能为空。")

    items: list[ArtifactItem] = []
    saved_test_case_files: list[str] = []
    for suite in suites:
        if not isinstance(suite, dict):
            raise RuntimeError("`planner_save_plan.suites[]` 必须是对象。")
        suite_name = _require_non_empty_text(suite.get("name"), field_name="planner_save_plan.suites[].name")
        seed_file = _require_non_empty_text(suite.get("seedFile"), field_name="planner_save_plan.suites[].seedFile")
        tests = suite.get("tests")
        if not isinstance(tests, list) or not tests:
            raise RuntimeError(f"`planner_save_plan` suite `{suite_name}` 缺少 tests。")
        for test_case in tests:
            if not isinstance(test_case, dict):
                raise RuntimeError("`planner_save_plan.suites[].tests[]` 必须是对象。")
            case_name = _require_non_empty_text(test_case.get("name"), field_name="planner_save_plan.suites[].tests[].name")
            target_file = _validate_relative_workspace_path(
                test_case.get("file"),
                project_dir=project_dir,
                expected_suffix=".spec.ts",
                field_name="planner_save_plan.suites[].tests[].file",
            )
            _validate_planner_case_file_layout(
                target_file,
                case_name=case_name,
                plan_identifier=plan_identifier,
                field_name="planner_save_plan.suites[].tests[].file",
            )
            steps = test_case.get("steps")
            if not isinstance(steps, list) or not steps:
                raise RuntimeError(f"`planner_save_plan` case `{case_name}` 缺少 steps。")
            step_count = 0
            for step in steps:
                if not isinstance(step, dict):
                    raise RuntimeError("`planner_save_plan.suites[].tests[].steps[]` 必须是对象。")
                expect = step.get("expect")
                if not isinstance(expect, list) or not [_require_non_empty_text(item, field_name="planner_save_plan.suites[].tests[].steps[].expect[]") for item in expect]:
                    raise RuntimeError(
                        f"`planner_save_plan` case `{case_name}` 的 steps.expect 必须是非空字符串数组。"
                    )
                step_count += 1

            items.append(
                ArtifactItem(
                    kind="test_case",
                    suite_name=suite_name,
                    case_name=case_name,
                    file=target_file,
                    seed_file=seed_file,
                    step_count=step_count,
                )
            )
            saved_test_case_files.append(target_file)

    deduplicated_case_files = _dedupe(saved_test_case_files)
    return ArtifactHistoryEntry(
        artifact_id=_build_artifact_id("plan"),
        stage="plan",
        status="success",
        project_name=project_name,
        project_dir=str(project_dir),
        input_files=_dedupe(input_files or []),
        touched_files=[plan_file],
        output_files=[plan_file],
        items=items,
        message=overview,
        test_plan_files=[plan_file],
        saved_test_cases=items,
        saved_test_case_files=deduplicated_case_files,
    )


def extract_generator_artifact_from_writes_and_snapshot(
    *,
    writes: list[dict[str, str]],
    before_manifest: WorkspaceManifest,
    after_manifest: WorkspaceManifest,
    workspace_dir: Path,
    project_name: str,
    input_files: list[str],
) -> ArtifactHistoryEntry:
    """Build a generator-stage artifact from write-tool inputs and workspace diff."""

    if not writes:
        raise RuntimeError("Generator 阶段没有观测到 `generator_write_test` 写文件输入。")

    diff = diff_workspace_manifest(before_manifest, after_manifest)
    output_files = _dedupe(write.get("fileName", "") for write in writes if write.get("fileName"))
    touched_files = _dedupe([*output_files, *diff["touched"]])
    items: list[ArtifactItem] = []
    for write in writes:
        raw_file_name = write.get("fileName")
        code = write.get("code", "")
        if not raw_file_name:
            raise RuntimeError("`generator_write_test.fileName` 不能为空。")
        output_file = _validate_relative_workspace_path(
            raw_file_name,
            project_dir=workspace_dir,
            expected_suffix=".spec.ts",
            field_name="generator_write_test.fileName",
        )
        file_path = workspace_dir / output_file
        code_text = file_path.read_text(encoding="utf-8") if file_path.is_file() else code
        titles = extract_test_titles_from_code(code_text)
        source_plan = extract_spec_source_from_code(code_text)
        items.append(
            ArtifactItem(
                kind="test_script",
                file=output_file,
                source_plan=source_plan or "",
                describe_title=titles[0] if titles else "",
                test_titles=titles,
            )
        )

    return ArtifactHistoryEntry(
        artifact_id=_build_artifact_id("generator"),
        stage="generator",
        status="success",
        project_name=project_name,
        project_dir=str(workspace_dir),
        input_files=_dedupe(input_files),
        touched_files=touched_files,
        output_files=output_files,
        items=items,
        message=f"共生成 {len(output_files)} 个脚本文件。",
        test_plan_files=_dedupe(input_files),
        test_scripts=output_files,
    )


def extract_healer_artifact_from_snapshot_and_runs(
    *,
    before_manifest: WorkspaceManifest,
    after_manifest: WorkspaceManifest,
    workspace_dir: Path,
    project_name: str,
    input_files: list[str],
    validation_runs: list[str],
) -> ArtifactHistoryEntry:
    """Build a healer-stage artifact from before/after snapshots and validation runs."""

    diff = diff_workspace_manifest(before_manifest, after_manifest)
    touched_files = _dedupe([*input_files, *diff["touched"]])
    output_files = _dedupe([*diff["added"], *diff["modified"]])
    items: list[ArtifactItem] = []
    for relative_file in _dedupe([*input_files, *output_files]):
        file_path = workspace_dir / relative_file
        if not file_path.is_file():
            continue
        text = file_path.read_text(encoding="utf-8")
        titles = extract_test_titles_from_code(text)
        items.append(
            ArtifactItem(
                kind="healed_script",
                file=relative_file,
                describe_title=titles[0] if titles else "",
                test_titles=titles,
            )
        )

    return ArtifactHistoryEntry(
        artifact_id=_build_artifact_id("healer"),
        stage="healer",
        status="success",
        project_name=project_name,
        project_dir=str(workspace_dir),
        input_files=_dedupe(input_files),
        touched_files=touched_files,
        output_files=output_files,
        items=items,
        message=f"共处理 {len(input_files)} 个脚本，实际变更 {len(output_files)} 个文件。",
        test_scripts=_dedupe(input_files),
        validation_runs=_dedupe(validation_runs),
    )


def extract_test_titles_from_code(code_text: str) -> list[str]:
    """Extract describe/test titles from a Playwright spec."""

    if not code_text:
        return []

    titles: list[str] = []
    for match in TEST_DESCRIBE_RE.finditer(code_text):
        title = match.group("title").strip()
        if title:
            titles.append(title)
    for match in TEST_TITLE_RE.finditer(code_text):
        title = match.group("title").strip()
        if title:
            titles.append(title)
    return _dedupe(titles)


def extract_spec_source_from_code(code_text: str) -> str | None:
    """Extract the `// spec:` source plan path from generated code."""

    match = SPEC_SOURCE_RE.search(code_text or "")
    if match is None:
        return None
    source_path = match.group("path").strip()
    return source_path or None


def extract_expected_generator_test_scripts_from_plan_files(
    *,
    plan_files: list[Path],
    project_dir: Path,
    selected_test_cases: Any = None,
) -> list[str]:
    """Parse plan markdown files and resolve the script files Generator must produce."""

    if not plan_files:
        raise RuntimeError("Generator 模式未提供可解析的测试计划文件。")

    resolved_project_dir = project_dir.resolve()
    requested_test_cases = _normalize_string_list(selected_test_cases)
    requested_case_set = set(requested_test_cases)
    matched_requested_cases: set[str] = set()
    expected_output_files: list[str] = []

    for plan_file in plan_files:
        resolved_plan_file = plan_file.resolve()
        try:
            relative_plan_file = resolved_plan_file.relative_to(resolved_project_dir).as_posix()
        except ValueError as exc:
            raise RuntimeError(
                f"Generator 模式测试计划文件 `{resolved_plan_file}` 不在项目目录 `{resolved_project_dir}` 下，无法继续。"
            ) from exc

        if not resolved_plan_file.is_file():
            raise RuntimeError(f"Generator 模式测试计划文件 `{relative_plan_file}` 不存在，无法继续。")

        plan_entries = _extract_plan_case_targets_from_markdown(
            plan_text=resolved_plan_file.read_text(encoding="utf-8"),
            plan_file=relative_plan_file,
            project_dir=project_dir,
        )
        if not plan_entries:
            raise RuntimeError(f"Generator 模式测试计划 `{relative_plan_file}` 未解析出任何 `**File:**` 目标脚本。")

        for case_name, planned_script in plan_entries:
            candidate_case_names = {case_name, Path(planned_script).stem}
            if requested_case_set and candidate_case_names.isdisjoint(requested_case_set):
                continue
            matched_requested_cases.update(candidate_case_names & requested_case_set)
            expected_output_files.append(_normalize_generator_output_file_from_plan_target(planned_script))

    if requested_case_set:
        missing_requested_cases = [case_name for case_name in requested_test_cases if case_name not in matched_requested_cases]
        if missing_requested_cases:
            missing_case_text = "、".join(f"`{case_name}`" for case_name in missing_requested_cases)
            raise RuntimeError(f"Generator 模式在测试计划中未找到指定的 `test_cases`：{missing_case_text}。")

    deduplicated_output_files = _dedupe(expected_output_files)
    if not deduplicated_output_files:
        raise RuntimeError("Generator 模式未从测试计划中解析出任何目标脚本。")
    return deduplicated_output_files


def build_stage_summary(
    *,
    stage: StageName,
    status: str,
    artifact: ArtifactHistoryEntry | None,
    fallback_message: str | None = None,
) -> StageSummaryEntry:
    """Build a user-facing per-stage summary block."""

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
    """Build the final single reply shown to the user for the current turn."""

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


def current_stage_from_pipeline(state: dict[str, Any]) -> StageName | None:
    """Resolve the current stage from state pipeline fields."""

    requested_pipeline = normalize_requested_pipeline(state.get("requested_pipeline"), default_stage=state.get("agent_type"))
    pipeline_cursor = state.get("pipeline_cursor", 0)
    if not requested_pipeline:
        return _normalize_stage_name(state.get("pending_agent_type") or state.get("agent_type"))
    if not isinstance(pipeline_cursor, int) or pipeline_cursor < 0 or pipeline_cursor >= len(requested_pipeline):
        return requested_pipeline[0]
    return requested_pipeline[pipeline_cursor]


def has_more_pipeline_stages(state: dict[str, Any]) -> bool:
    """Whether there are more stages to run after the current one."""

    requested_pipeline = normalize_requested_pipeline(state.get("requested_pipeline"), default_stage=state.get("agent_type"))
    pipeline_cursor = state.get("pipeline_cursor", 0)
    if not isinstance(pipeline_cursor, int):
        return False
    return pipeline_cursor + 1 < len(requested_pipeline)


def next_pipeline_stage(state: dict[str, Any]) -> StageName | None:
    """Return the next pipeline stage, if any."""

    requested_pipeline = normalize_requested_pipeline(state.get("requested_pipeline"), default_stage=state.get("agent_type"))
    pipeline_cursor = state.get("pipeline_cursor", 0)
    if not isinstance(pipeline_cursor, int):
        return None
    next_index = pipeline_cursor + 1
    if next_index < 0 or next_index >= len(requested_pipeline):
        return None
    return requested_pipeline[next_index]


def previous_pipeline_stage(state: dict[str, Any]) -> StageName | None:
    """Return the previous pipeline stage, if any."""

    requested_pipeline = normalize_requested_pipeline(state.get("requested_pipeline"), default_stage=state.get("agent_type"))
    pipeline_cursor = state.get("pipeline_cursor", 0)
    if not isinstance(pipeline_cursor, int):
        return None
    previous_index = pipeline_cursor - 1
    if previous_index < 0 or previous_index >= len(requested_pipeline):
        return None
    return requested_pipeline[previous_index]


def clear_current_turn_buffers(state: dict[str, Any]) -> dict[str, Any]:
    """Reset per-turn summary buffers after finalization."""

    return {
        "pending_stage_summaries": [],
        "current_turn_artifact_ids": [],
        "pipeline_handoff": False,
    }


def _artifact_candidates_for_stage(stage: StageName, latest_artifacts: LatestArtifacts) -> list[ArtifactHistoryEntry]:
    """Return same-stage + upstream candidates for inheritance."""

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


def _collect_plan_files(artifacts: list[ArtifactHistoryEntry]) -> list[str]:
    """Collect plan files from stage artifacts."""

    collected: list[str] = []
    for artifact in artifacts:
        collected.extend(_normalize_string_list(artifact.get("test_plan_files")))
        if artifact.get("stage") == "plan":
            collected.extend(_normalize_string_list(artifact.get("output_files")))
        if artifact.get("stage") == "generator":
            collected.extend(_normalize_string_list(artifact.get("input_files")))
    return _dedupe(collected)


def _collect_script_files(artifacts: list[ArtifactHistoryEntry]) -> list[str]:
    """Collect script files from stage artifacts."""

    collected: list[str] = []
    for artifact in artifacts:
        collected.extend(_normalize_string_list(artifact.get("test_scripts")))
        if artifact.get("stage") == "generator":
            collected.extend(_normalize_string_list(artifact.get("output_files")))
        if artifact.get("stage") == "healer":
            collected.extend(_normalize_string_list(artifact.get("input_files")))
    return _dedupe(collected)


def _collect_saved_case_names(artifacts: list[ArtifactHistoryEntry]) -> list[str]:
    """Collect saved case names from upstream plan artifacts."""

    collected: list[str] = []
    for artifact in artifacts:
        saved_test_cases = artifact.get("saved_test_cases")
        if not isinstance(saved_test_cases, list):
            continue
        for item in saved_test_cases:
            if not isinstance(item, dict):
                continue
            case_name = _normalize_optional_text(item.get("case_name"))
            if case_name:
                collected.append(case_name)
    return _dedupe(collected)


def _align_generator_test_cases_with_latest_plan(
    resolved_params: dict[str, Any],
    artifacts: list[ArtifactHistoryEntry],
    *,
    previous_stage: StageName | None,
) -> None:
    """Replace selector-like `test_cases` text with concrete plan case names on plan->generator handoff."""

    requested_test_cases = _normalize_string_list(resolved_params.get("test_cases"))
    if previous_stage != "plan" or len(requested_test_cases) != 1:
        return

    planned_case_names = _collect_saved_case_names(artifacts)
    if not planned_case_names:
        return

    requested_case = requested_test_cases[0]
    if requested_case in set(planned_case_names):
        return
    if not _looks_like_case_selector_text(requested_case):
        return

    resolved_params["test_cases"] = planned_case_names


def _looks_like_case_selector_text(value: str) -> bool:
    """Heuristically detect natural-language case selectors such as '高优先级三条用例'."""

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


def _build_plan_stage_summary(artifact: ArtifactHistoryEntry) -> str:
    plan_files = ", ".join(f"`{path}`" for path in artifact.get("output_files", [])) or "无"
    target_files = ", ".join(f"`{path}`" for path in artifact.get("saved_test_case_files", [])) or "无"
    case_details = "；".join(
        f"`{item['case_name']}` -> `{item['file']}`"
        for item in artifact.get("saved_test_cases", [])
        if item.get("case_name") and item.get("file")
    ) or "无"
    return "\n".join(
        [
            "**Plan 阶段**",
            f"- 状态：成功",
            f"- 项目目录：`{artifact.get('project_dir', '未知')}`",
            f"- 保存的测试计划：{plan_files}",
            f"- 目标脚本文件：{target_files}",
            f"- 用例明细：{case_details}",
        ]
    )


def _build_generator_stage_summary(artifact: ArtifactHistoryEntry) -> str:
    input_plans = ", ".join(f"`{path}`" for path in artifact.get("input_files", [])) or "无"
    output_scripts = ", ".join(f"`{path}`" for path in artifact.get("output_files", [])) or "无"
    script_details = "；".join(
        f"`{item['file']}` 包含 {', '.join(f'`{title}`' for title in item.get('test_titles', [])) or '无测试标题'}"
        for item in artifact.get("items", [])
        if item.get("file")
    ) or "无"
    return "\n".join(
        [
            "**Generator 阶段**",
            f"- 状态：成功",
            f"- 项目目录：`{artifact.get('project_dir', '未知')}`",
            f"- 来源测试计划：{input_plans}",
            f"- 生成/改写脚本：{output_scripts}",
            f"- 脚本明细：{script_details}",
        ]
    )


def _build_healer_stage_summary(artifact: ArtifactHistoryEntry) -> str:
    input_scripts = ", ".join(f"`{path}`" for path in artifact.get("input_files", [])) or "无"
    changed_files = ", ".join(f"`{path}`" for path in artifact.get("output_files", [])) or "无"
    validation_runs = ", ".join(f"`{path}`" for path in artifact.get("validation_runs", [])) or "无"
    script_details = "；".join(
        f"`{item['file']}` 包含 {', '.join(f'`{title}`' for title in item.get('test_titles', [])) or '无测试标题'}"
        for item in artifact.get("items", [])
        if item.get("file")
    ) or "无"
    return "\n".join(
        [
            "**Healer 阶段**",
            f"- 状态：成功",
            f"- 项目目录：`{artifact.get('project_dir', '未知')}`",
            f"- 调试脚本：{input_scripts}",
            f"- 实际变更文件：{changed_files}",
            f"- 验证运行目标：{validation_runs}",
            f"- 脚本明细：{script_details}",
        ]
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
    return "\n".join(lines)


def _build_artifact_id(stage: StageName) -> str:
    """Build a stable-ish artifact id for state history."""

    return f"{stage}-{uuid4().hex[:12]}"


def _dedupe(values: Any) -> list[str]:
    """Dedupe an iterable of strings while preserving order."""

    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized_value = _normalize_optional_text(value)
        if normalized_value is None or normalized_value in seen:
            continue
        seen.add(normalized_value)
        deduped.append(normalized_value)
    return deduped


def _normalize_string_list(value: Any) -> list[str]:
    """Normalize scalar or list input into a deduped string list."""

    if isinstance(value, (list, tuple)):
        values = value
    elif value is None:
        values = []
    else:
        values = [value]
    return _dedupe(values)


def _normalize_optional_text(value: Any) -> str | None:
    """Normalize any text-like input to a non-empty string."""

    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _require_non_empty_text(value: Any, *, field_name: str) -> str:
    """Require a non-empty string-like value."""

    text = _normalize_optional_text(value)
    if text is None:
        raise RuntimeError(f"`{field_name}` 不能为空。")
    return text


def _normalize_stage_name(value: Any) -> StageName | None:
    """Normalize an arbitrary value into a supported stage name."""

    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"plan", "generator", "healer"}:
        return text  # type: ignore[return-value]
    return None


def _extract_plan_case_targets_from_markdown(
    *,
    plan_text: str,
    plan_file: str,
    project_dir: Path,
) -> list[tuple[str, str]]:
    """Extract `(case_name, target_file)` tuples from a saved markdown test plan."""

    current_case_name: str | None = None
    extracted_targets: list[tuple[str, str]] = []

    for line in plan_text.splitlines():
        heading_case_name = _extract_case_name_from_plan_heading(line)
        if heading_case_name is not None:
            current_case_name = heading_case_name
            continue

        file_match = PLAN_FILE_LINE_RE.match(line)
        if file_match is None:
            continue

        target_file = _validate_relative_workspace_path(
            file_match.group("file"),
            project_dir=project_dir,
            expected_suffix=".spec.ts",
            field_name=f"{plan_file} **File:**",
        )
        extracted_targets.append((current_case_name or Path(target_file).stem, target_file))
        current_case_name = None

    return extracted_targets


def _extract_case_name_from_plan_heading(line: str) -> str | None:
    """Extract the case identifier from a markdown heading like `#### 1.1. a_case_name`."""

    match = PLAN_CASE_HEADER_RE.match(line)
    if match is None:
        return None
    return _normalize_optional_text(match.group("case_name"))


def _validate_planner_markdown_layout(relative_plan_file: str, *, field_name: str) -> str:
    """Enforce the `test_case/aaaplanning_{plan}/aaa_{plan}.md` plan layout."""

    path = Path(relative_plan_file)
    if len(path.parts) != 3 or path.parts[0] != "test_case":
        raise RuntimeError(
            f"`{field_name}` 必须保存到 `test_case/aaaplanning_{{plan-name}}/aaa_{{plan-name}}.md`，当前收到：`{relative_plan_file}`。"
        )

    planning_dir = path.parts[1]
    if not planning_dir.startswith(PLANNING_DIR_PREFIX):
        raise RuntimeError(
            f"`{field_name}` 必须保存到 `test_case/aaaplanning_{{plan-name}}/aaa_{{plan-name}}.md`，当前收到：`{relative_plan_file}`。"
        )

    plan_identifier = planning_dir.removeprefix(PLANNING_DIR_PREFIX)
    if not plan_identifier:
        raise RuntimeError(f"`{field_name}` 缺少合法的 `plan-name` 标识，当前收到：`{relative_plan_file}`。")

    expected_file_name = f"{PLAN_FILE_PREFIX}{plan_identifier}.md"
    if path.name != expected_file_name:
        raise RuntimeError(
            f"`{field_name}` 文件名必须与计划目录标识一致，期望 `{expected_file_name}`，当前收到：`{relative_plan_file}`。"
        )
    return plan_identifier


def _validate_planner_case_file_layout(
    relative_case_file: str,
    *,
    case_name: str,
    plan_identifier: str,
    field_name: str,
) -> None:
    """Enforce the `test_case/aaaplanning_{plan}/{case}.spec.ts` plan case layout."""

    path = Path(relative_case_file)
    expected_dir_name = f"{PLANNING_DIR_PREFIX}{plan_identifier}"
    if len(path.parts) != 3 or path.parts[0] != "test_case" or path.parts[1] != expected_dir_name:
        raise RuntimeError(
            f"`{field_name}` 必须保存到 `test_case/{expected_dir_name}/{case_name}.spec.ts`，当前收到：`{relative_case_file}`。"
        )
    file_name = path.name
    if not file_name.endswith(".spec.ts"):
        raise RuntimeError(
            f"`{field_name}` 必须保存到 `test_case/{expected_dir_name}/{case_name}.spec.ts`，当前收到：`{relative_case_file}`。"
        )
    actual_case_name = file_name.removesuffix(".spec.ts")
    if actual_case_name != case_name:
        raise RuntimeError(
            f"`{field_name}` 文件名必须与用例名 `{case_name}` 一致，当前收到：`{relative_case_file}`。"
        )


def _normalize_generator_output_file_from_plan_target(planned_script_file: str) -> str:
    """Convert a plan-time script path into the runtime output path Generator should write."""

    path = Path(planned_script_file)
    normalized_parts = list(path.parts)
    for index, part in enumerate(normalized_parts):
        if part.startswith(PLANNING_DIR_PREFIX):
            normalized_plan_dir = part.removeprefix(PLANNING_DIR_PREFIX)
            if normalized_plan_dir:
                normalized_parts[index] = normalized_plan_dir
            break
    return Path(*normalized_parts).as_posix()


def _validate_relative_workspace_path(
    value: Any,
    *,
    project_dir: Path,
    expected_suffix: str,
    field_name: str,
) -> str:
    """Validate that a path is relative to the workspace and has the expected suffix."""

    raw_path = _require_non_empty_text(value, field_name=field_name)
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        raise RuntimeError(f"`{field_name}` 必须使用相对 `project_dir` 的路径，当前收到绝对路径：`{raw_path}`。")
    if candidate.name in {".", ".."}:
        raise RuntimeError(f"`{field_name}` 不是合法文件路径：`{raw_path}`。")
    resolved = (project_dir / candidate).resolve()
    try:
        relative_path = resolved.relative_to(project_dir.resolve()).as_posix()
    except ValueError as exc:
        raise RuntimeError(f"`{field_name}` 路径越出了项目目录：`{raw_path}`。") from exc
    if not relative_path.endswith(expected_suffix):
        raise RuntimeError(f"`{field_name}` 必须以 `{expected_suffix}` 结尾，当前收到：`{raw_path}`。")
    if expected_suffix == ".md" and not PLAN_MARKDOWN_RE.match(Path(relative_path).name):
        raise RuntimeError(f"`{field_name}` 必须使用 `aaa_*.md` 命名，当前收到：`{raw_path}`。")
    return relative_path


def _should_skip_snapshot_path(relative_path: Path) -> bool:
    """Whether a path should be skipped from manifests."""

    return any(part in SKIPPED_SNAPSHOT_DIR_NAMES for part in relative_path.parts)

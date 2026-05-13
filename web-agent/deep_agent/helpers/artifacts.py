"""Plan/Generator/Healer 阶段的工作流产物辅助方法。

本模块作为 helpers 层的产物能力门面；具体实现拆到 `artifact_helpers` 下，避免单文件
继续承担流水线状态、文件快照、产物提取、摘要生成和路径校验等所有职责。
"""

from __future__ import annotations

from deep_agent.helpers.artifact_helpers.common import (
    ArtifactHistoryEntry,
    ArtifactItem,
    FileManifestEntry,
    LatestArtifacts,
    StageName,
    StageSummaryEntry,
    WorkspaceManifest,
)
from deep_agent.helpers.artifact_helpers.extractors import (
    extract_expected_generator_test_scripts_from_plan_files,
    extract_generator_artifact_from_writes_and_snapshot,
    extract_healer_artifact_from_snapshot_and_runs,
    extract_plan_artifact_from_planner_payload,
    extract_plan_artifact_from_saved_markdown,
    extract_spec_source_from_code,
    extract_test_titles_from_code,
)
from deep_agent.helpers.artifact_helpers.manifest import (
    diff_workspace_manifest,
    snapshot_workspace_manifest,
    snapshot_workspace_manifest_async,
)
from deep_agent.helpers.artifact_helpers.pipeline import (
    append_artifact_history,
    append_stage_summary,
    clear_current_turn_buffers,
    current_stage_from_pipeline,
    has_more_pipeline_stages,
    merge_file_lists,
    next_pipeline_stage,
    normalize_requested_pipeline,
    previous_pipeline_stage,
    resolve_stage_inputs,
    summarize_latest_artifacts,
)
from deep_agent.helpers.artifact_helpers.summaries import (
    build_final_turn_summary,
    build_stage_summary,
)


__all__ = [
    "ArtifactHistoryEntry",
    "ArtifactItem",
    "FileManifestEntry",
    "LatestArtifacts",
    "StageName",
    "StageSummaryEntry",
    "WorkspaceManifest",
    "append_artifact_history",
    "append_stage_summary",
    "build_final_turn_summary",
    "build_stage_summary",
    "clear_current_turn_buffers",
    "current_stage_from_pipeline",
    "diff_workspace_manifest",
    "extract_expected_generator_test_scripts_from_plan_files",
    "extract_generator_artifact_from_writes_and_snapshot",
    "extract_healer_artifact_from_snapshot_and_runs",
    "extract_plan_artifact_from_planner_payload",
    "extract_plan_artifact_from_saved_markdown",
    "extract_spec_source_from_code",
    "extract_test_titles_from_code",
    "has_more_pipeline_stages",
    "merge_file_lists",
    "next_pipeline_stage",
    "normalize_requested_pipeline",
    "previous_pipeline_stage",
    "resolve_stage_inputs",
    "snapshot_workspace_manifest",
    "snapshot_workspace_manifest_async",
    "summarize_latest_artifacts",
]

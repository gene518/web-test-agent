from __future__ import annotations

from deep_agent.helpers import artifacts


def test_helpers_artifacts_facade_exports_public_helpers() -> None:
    expected_names = [
        "append_artifact_history",
        "build_final_turn_summary",
        "build_stage_summary",
        "extract_expected_generator_test_scripts_from_plan_files",
        "extract_generator_artifact_from_writes_and_snapshot",
        "extract_healer_artifact_from_snapshot_and_runs",
        "extract_plan_artifact_from_planner_payload",
        "normalize_requested_pipeline",
        "resolve_stage_inputs",
        "snapshot_workspace_manifest_async",
    ]

    for name in expected_names:
        assert hasattr(artifacts, name)

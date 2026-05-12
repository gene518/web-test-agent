"""Plan、Generator、Healer 阶段的产物抽取辅助。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import (
    ArtifactHistoryEntry,
    ArtifactItem,
    WorkspaceManifest,
    build_artifact_id,
    dedupe,
    extract_plan_case_targets_from_markdown,
    normalize_generator_output_file_from_plan_target,
    normalize_string_list,
    require_non_empty_text,
    validate_planner_case_file_layout,
    validate_planner_markdown_layout,
    validate_relative_workspace_path,
    SPEC_SOURCE_RE,
    TEST_DESCRIBE_RE,
    TEST_TITLE_RE,
)
from .manifest import diff_workspace_manifest


def extract_plan_artifact_from_planner_payload(
    *,
    payload: Any,
    project_dir: Path,
    project_name: str,
    input_files: list[str] | None = None,
) -> ArtifactHistoryEntry:
    """校验 Planner 的 payload，并将其转换为 Plan 阶段产物。"""

    if not isinstance(payload, dict):
        raise RuntimeError("`planner_save_plan` 输入 payload 非法：必须是对象。")

    plan_file = validate_relative_workspace_path(
        payload.get("fileName"),
        project_dir=project_dir,
        expected_suffix=".md",
        field_name="planner_save_plan.fileName",
    )
    plan_identifier = validate_planner_markdown_layout(
        plan_file,
        field_name="planner_save_plan.fileName",
    )
    overview = require_non_empty_text(payload.get("overview"), field_name="planner_save_plan.overview")
    plan_name = require_non_empty_text(payload.get("name"), field_name="planner_save_plan.name")

    suites = payload.get("suites")
    if not isinstance(suites, list) or not suites:
        raise RuntimeError("`planner_save_plan.suites` 不能为空。")

    items: list[ArtifactItem] = []
    planned_test_case_files: list[str] = []
    for suite in suites:
        if not isinstance(suite, dict):
            raise RuntimeError("`planner_save_plan.suites[]` 必须是对象。")
        suite_name = require_non_empty_text(suite.get("name"), field_name="planner_save_plan.suites[].name")
        seed_file = require_non_empty_text(suite.get("seedFile"), field_name="planner_save_plan.suites[].seedFile")
        tests = suite.get("tests")
        if not isinstance(tests, list) or not tests:
            raise RuntimeError(f"`planner_save_plan` suite `{suite_name}` 缺少 tests。")
        for test_case in tests:
            if not isinstance(test_case, dict):
                raise RuntimeError("`planner_save_plan.suites[].tests[]` 必须是对象。")
            case_name = require_non_empty_text(test_case.get("name"), field_name="planner_save_plan.suites[].tests[].name")
            target_file = validate_relative_workspace_path(
                test_case.get("file"),
                project_dir=project_dir,
                expected_suffix=".spec.ts",
                field_name="planner_save_plan.suites[].tests[].file",
            )
            validate_planner_case_file_layout(
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
                if not isinstance(expect, list) or not [
                    require_non_empty_text(item, field_name="planner_save_plan.suites[].tests[].steps[].expect[]")
                    for item in expect
                ]:
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
            planned_test_case_files.append(target_file)

    deduplicated_case_files = dedupe(planned_test_case_files)
    return ArtifactHistoryEntry(
        artifact_id=build_artifact_id("plan"),
        stage="plan",
        status="success",
        project_name=project_name,
        project_dir=str(project_dir),
        input_files=dedupe(input_files or []),
        touched_files=[plan_file],
        output_files=[plan_file],
        items=items,
        message=overview,
        test_plan_files=[plan_file],
        planned_test_case_files=deduplicated_case_files,
        saved_test_cases=items,
        saved_test_case_files=[],
    )


def extract_plan_artifact_from_saved_markdown(
    *,
    plan_file: str,
    project_dir: Path,
    project_name: str,
    input_files: list[str] | None = None,
) -> ArtifactHistoryEntry:
    """从 workspace 中已经落盘的测试计划 Markdown 构建 Plan 阶段产物。"""

    relative_plan_file = validate_relative_workspace_path(
        plan_file,
        project_dir=project_dir,
        expected_suffix=".md",
        field_name="saved_plan_file",
    )
    validate_planner_markdown_layout(
        relative_plan_file,
        field_name="saved_plan_file",
    )
    plan_path = project_dir / relative_plan_file
    if not plan_path.is_file():
        raise RuntimeError(f"测试计划 `{relative_plan_file}` 不存在，无法构建 Plan 阶段产物。")

    plan_entries = extract_plan_case_targets_from_markdown(
        plan_text=plan_path.read_text(encoding="utf-8"),
        plan_file=relative_plan_file,
        project_dir=project_dir,
    )
    if not plan_entries:
        raise RuntimeError(f"测试计划 `{relative_plan_file}` 未解析出任何 `**File:**` 目标脚本。")

    items = [
        ArtifactItem(
            kind="test_case",
            case_name=case_name,
            file=target_file,
        )
        for case_name, target_file in plan_entries
    ]
    planned_test_case_files = dedupe(target_file for _, target_file in plan_entries)
    return ArtifactHistoryEntry(
        artifact_id=build_artifact_id("plan"),
        stage="plan",
        status="success",
        project_name=project_name,
        project_dir=str(project_dir),
        input_files=dedupe(input_files or []),
        touched_files=[relative_plan_file],
        output_files=[relative_plan_file],
        items=items,
        message=f"测试计划 `{relative_plan_file}` 已保存。",
        test_plan_files=[relative_plan_file],
        planned_test_case_files=planned_test_case_files,
        saved_test_cases=items,
        saved_test_case_files=[],
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
    """根据写入工具输入和工作区差异构建 Generator 阶段产物。"""

    if not writes:
        raise RuntimeError("Generator 阶段没有观测到 `generator_write_test` 写文件输入。")

    diff = diff_workspace_manifest(before_manifest, after_manifest)
    output_files = dedupe(write.get("fileName", "") for write in writes if write.get("fileName"))
    touched_files = dedupe([*output_files, *diff["touched"]])
    items: list[ArtifactItem] = []
    for write in writes:
        raw_file_name = write.get("fileName")
        code = write.get("code", "")
        if not raw_file_name:
            raise RuntimeError("`generator_write_test.fileName` 不能为空。")
        output_file = validate_relative_workspace_path(
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
        artifact_id=build_artifact_id("generator"),
        stage="generator",
        status="success",
        project_name=project_name,
        project_dir=str(workspace_dir),
        input_files=dedupe(input_files),
        touched_files=touched_files,
        output_files=output_files,
        items=items,
        message=f"共生成 {len(output_files)} 个脚本文件。",
        test_plan_files=dedupe(input_files),
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
    """根据前后快照和校验运行结果构建 Healer 阶段产物。"""

    diff = diff_workspace_manifest(before_manifest, after_manifest)
    touched_files = dedupe([*input_files, *diff["touched"]])
    output_files = dedupe([*diff["added"], *diff["modified"]])
    items: list[ArtifactItem] = []
    for relative_file in dedupe([*input_files, *output_files]):
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
        artifact_id=build_artifact_id("healer"),
        stage="healer",
        status="success",
        project_name=project_name,
        project_dir=str(workspace_dir),
        input_files=dedupe(input_files),
        touched_files=touched_files,
        output_files=output_files,
        items=items,
        message=f"共处理 {len(input_files)} 个脚本，实际变更 {len(output_files)} 个文件。",
        test_scripts=dedupe(input_files),
        validation_runs=dedupe(validation_runs),
    )


def extract_test_titles_from_code(code_text: str) -> list[str]:
    """从 Playwright 规范文件中提取 describe/test 标题。"""

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
    return dedupe(titles)


def extract_spec_source_from_code(code_text: str) -> str | None:
    """从生成代码中提取 `// spec:` 对应的来源计划路径。"""

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
    """解析测试计划 Markdown 文件，并解析出 Generator 必须产出的脚本文件。"""

    if not plan_files:
        raise RuntimeError("Generator 模式未提供可解析的测试计划文件。")

    resolved_project_dir = project_dir.resolve()
    requested_test_cases = normalize_string_list(selected_test_cases)
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

        plan_entries = extract_plan_case_targets_from_markdown(
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
            expected_output_files.append(normalize_generator_output_file_from_plan_target(planned_script))

    if requested_case_set:
        missing_requested_cases = [case_name for case_name in requested_test_cases if case_name not in matched_requested_cases]
        if missing_requested_cases:
            missing_case_text = "、".join(f"`{case_name}`" for case_name in missing_requested_cases)
            raise RuntimeError(f"Generator 模式在测试计划中未找到指定的 `test_cases`：{missing_case_text}。")

    deduplicated_output_files = dedupe(expected_output_files)
    if not deduplicated_output_files:
        raise RuntimeError("Generator 模式未从测试计划中解析出任何目标脚本。")
    return deduplicated_output_files

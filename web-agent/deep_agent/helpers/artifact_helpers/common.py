"""阶段产物共享类型、常量与底层校验工具。"""

from __future__ import annotations

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
    """单个阶段使用的小型产物条目。"""

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
    """轻量级的持久化阶段产物条目。"""

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
    planned_test_case_files: list[str]
    saved_test_cases: list[ArtifactItem]
    saved_test_case_files: list[str]
    validation_runs: list[str]


class StageSummaryEntry(TypedDict, total=False):
    """按阶段格式化、供最终汇总缓冲的摘要条目。"""

    artifact_id: str | None
    stage: StageName
    status: str
    text: str


class FileManifestEntry(TypedDict):
    """用于前后差异比对的文件系统清单元数据。"""

    mtime_ns: int
    size: int


LatestArtifacts = dict[str, ArtifactHistoryEntry]
WorkspaceManifest = dict[str, FileManifestEntry]


def build_artifact_id(stage: StageName) -> str:
    """为状态历史生成相对稳定的产物 ID。"""

    return f"{stage}-{uuid4().hex[:12]}"


def dedupe(values: Any) -> list[str]:
    """对字符串可迭代对象去重，并保持原有顺序。"""

    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized_value = normalize_optional_text(value)
        if normalized_value is None or normalized_value in seen:
            continue
        seen.add(normalized_value)
        deduped.append(normalized_value)
    return deduped


def normalize_string_list(value: Any) -> list[str]:
    """把标量或列表输入归一化为去重后的字符串列表。"""

    if isinstance(value, (list, tuple)):
        values = value
    elif value is None:
        values = []
    else:
        values = [value]
    return dedupe(values)


def normalize_optional_text(value: Any) -> str | None:
    """把任意类文本输入归一化为非空字符串。"""

    if value is None:
        return None
    text = str(value).strip()
    return text or None


def require_non_empty_text(value: Any, *, field_name: str) -> str:
    """要求传入非空的类字符串值。"""

    text = normalize_optional_text(value)
    if text is None:
        raise RuntimeError(f"`{field_name}` 不能为空。")
    return text


def normalize_stage_name(value: Any) -> StageName | None:
    """把任意值归一化为受支持的阶段名称。"""

    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"plan", "generator", "healer"}:
        return text  # type: ignore[return-value]
    return None


def extract_plan_case_targets_from_markdown(
    *,
    plan_text: str,
    plan_file: str,
    project_dir: Path,
) -> list[tuple[str, str]]:
    """从已保存的 Markdown 测试计划中提取 `(case_name, target_file)` 元组。"""

    current_case_name: str | None = None
    extracted_targets: list[tuple[str, str]] = []

    for line in plan_text.splitlines():
        heading_case_name = extract_case_name_from_plan_heading(line)
        if heading_case_name is not None:
            current_case_name = heading_case_name
            continue

        file_match = PLAN_FILE_LINE_RE.match(line)
        if file_match is None:
            continue

        target_file = validate_relative_workspace_path(
            file_match.group("file"),
            project_dir=project_dir,
            expected_suffix=".spec.ts",
            field_name=f"{plan_file} **File:**",
        )
        extracted_targets.append((current_case_name or Path(target_file).stem, target_file))
        current_case_name = None

    return extracted_targets


def extract_case_name_from_plan_heading(line: str) -> str | None:
    """从 `#### 1.1. a_case_name` 这类 Markdown 标题中提取用例标识。"""

    match = PLAN_CASE_HEADER_RE.match(line)
    if match is None:
        return None
    return normalize_optional_text(match.group("case_name"))


def validate_planner_markdown_layout(relative_plan_file: str, *, field_name: str) -> str:
    """强制校验 `test_case/aaaplanning_{plan}/aaa_{plan}.md` 计划路径布局。"""

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


def validate_planner_case_file_layout(
    relative_case_file: str,
    *,
    case_name: str,
    plan_identifier: str,
    field_name: str,
) -> None:
    """强制校验 `test_case/aaaplanning_{plan}/{case}.spec.ts` 用例路径布局。"""

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


def normalize_generator_output_file_from_plan_target(planned_script_file: str) -> str:
    """把计划阶段的脚本路径转换为 Generator 运行时应写入的输出路径。"""

    path = Path(planned_script_file)
    normalized_parts = list(path.parts)
    for index, part in enumerate(normalized_parts):
        if part.startswith(PLANNING_DIR_PREFIX):
            normalized_plan_dir = part.removeprefix(PLANNING_DIR_PREFIX)
            if normalized_plan_dir:
                normalized_parts[index] = normalized_plan_dir
            break
    return Path(*normalized_parts).as_posix()


def validate_relative_workspace_path(
    value: Any,
    *,
    project_dir: Path,
    expected_suffix: str,
    field_name: str,
) -> str:
    """校验路径是否相对工作区且具备期望的后缀。"""

    raw_path = require_non_empty_text(value, field_name=field_name)
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


def should_skip_snapshot_path(relative_path: Path) -> bool:
    """判断某个路径是否应从清单中跳过。"""

    return any(part in SKIPPED_SNAPSHOT_DIR_NAMES for part in relative_path.parts)

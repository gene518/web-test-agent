"""自动化项目目录解析、模板引导与工程初始化辅助方法。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


DEFAULT_AUTOTEST_DEMO_PROJECT_NAME = "demo"
NULL_LIKE_TEXT_VALUES = frozenset({"null", "none", "nil", "undefined"})
AUTOTEST_TEMPLATE_IGNORE_PATTERNS = (
    ".DS_Store",
    "node_modules",
    "test-results",
    "playwright-report",
)


def normalize_runtime_text(value: Any) -> str | None:
    """把运行时文本参数归一化为可判空字符串。"""

    if value is None:
        return None

    normalized_value = str(value).strip()
    if not normalized_value:
        return None
    if normalized_value.lower() in NULL_LIKE_TEXT_VALUES:
        return None
    return normalized_value


def resolve_autotest_project_dir(
    *,
    automation_root: Path,
    bundled_template_dir: Path,
    project_name: str | None,
    raw_project_dir: Any,
    missing_project_name_error: str,
) -> Path:
    """按 Plan 规则解析并准备自动化项目目录。"""

    automation_root = automation_root.resolve()
    demo_project_dir = ensure_demo_project(automation_root=automation_root, bundled_template_dir=bundled_template_dir)

    if raw_project_dir:
        workspace_dir = Path(str(raw_project_dir)).expanduser()
        if not workspace_dir.is_absolute():
            # `project_dir` 的业务语义是“自动化工程目录”，相对路径应理解为相对自动化根目录，
            # 而不是相对当前进程 cwd；否则像 `baidu-web` 这样的工程名会被错误解析到仓库目录下。
            workspace_dir = automation_root / workspace_dir
    else:
        if not project_name:
            raise RuntimeError(missing_project_name_error)
        workspace_dir = automation_root / project_name

    workspace_dir = workspace_dir.resolve()
    if workspace_dir.exists():
        if not workspace_dir.is_dir():
            raise RuntimeError(f"自动化工程路径 `{workspace_dir}` 已存在但不是目录，无法继续。")
        return workspace_dir

    template_source_dir = demo_project_dir
    if workspace_dir == demo_project_dir:
        template_source_dir = bundled_template_dir

    copy_template_project(template_source_dir, workspace_dir)
    return workspace_dir


def ensure_demo_project(*, automation_root: Path, bundled_template_dir: Path) -> Path:
    """确保默认 demo 工程已同步到自动化根目录。"""

    automation_root.mkdir(parents=True, exist_ok=True)
    demo_project_dir = (automation_root / DEFAULT_AUTOTEST_DEMO_PROJECT_NAME).resolve()
    if demo_project_dir.exists():
        if not demo_project_dir.is_dir():
            raise RuntimeError(f"默认 demo 工程路径 `{demo_project_dir}` 已存在但不是目录，无法继续。")
        return demo_project_dir

    copy_template_project(bundled_template_dir, demo_project_dir)
    return demo_project_dir


def copy_template_project(source_dir: Path, target_dir: Path) -> None:
    """从模板目录复制出一个新的自动化工程。"""

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, target_dir, ignore=shutil.ignore_patterns(*AUTOTEST_TEMPLATE_IGNORE_PATTERNS))

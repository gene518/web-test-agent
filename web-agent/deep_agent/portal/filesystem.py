"""Portal 项目发现与文件树构建的文件系统辅助方法。"""

from __future__ import annotations

from pathlib import Path

from deep_agent.portal.models import ActiveProject, FileTreeNode, PortalProjectSummary


SKIPPED_FILE_TREE_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "__pycache__",
        "node_modules",
        "test-results",
        "playwright-report",
        ".playwright-mcp",
    }
)


def list_projects(automation_root: Path) -> list[PortalProjectSummary]:
    """返回自动化根目录下的一级子目录。"""

    root = automation_root.expanduser().resolve()
    if not root.is_dir():
        return []

    projects: list[PortalProjectSummary] = []
    for path in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_dir() or path.name in SKIPPED_FILE_TREE_NAMES:
            continue
        projects.append(
            PortalProjectSummary(
                project_name=path.name,
                project_dir=str(path.resolve()),
                updated_at=_safe_mtime(path),
            )
        )
    return projects


def resolve_project_dir(automation_root: Path, project_name: str) -> Path:
    """基于自动化根目录解析并校验项目名。"""

    normalized_project_name = project_name.strip()
    if not normalized_project_name:
        raise ValueError("项目名不能为空。")

    root = automation_root.expanduser().resolve()
    project_dir = (root / normalized_project_name).resolve()
    try:
        project_dir.relative_to(root)
    except ValueError as exc:
        raise ValueError("项目路径必须位于默认自动化根目录内。") from exc

    if project_dir.parent != root:
        raise ValueError("本期只允许选择自动化根目录下的一级项目。")

    return project_dir


def build_active_project(automation_root: Path, project_name: str) -> ActiveProject:
    """构建当前激活项目的元数据，不强制要求目录已存在。"""

    project_dir = resolve_project_dir(automation_root, project_name)
    return ActiveProject(project_name=project_dir.name, project_dir=str(project_dir), exists=project_dir.is_dir())


def build_file_tree(project_dir: Path) -> list[FileTreeNode]:
    """为单个自动化项目构建受限文件树。"""

    root = project_dir.expanduser().resolve()
    if not root.is_dir():
        return []
    return _build_children(root, root)


def _build_children(root: Path, directory: Path) -> list[FileTreeNode]:
    children: list[FileTreeNode] = []
    for child in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        if child.name in SKIPPED_FILE_TREE_NAMES:
            continue
        if child.is_dir():
            children.append(
                FileTreeNode(
                    name=child.name,
                    path=child.relative_to(root).as_posix(),
                    type="directory",
                    children=_build_children(root, child),
                )
            )
            continue
        if child.is_file():
            children.append(
                FileTreeNode(
                    name=child.name,
                    path=child.relative_to(root).as_posix(),
                    type="file",
                )
            )
    return children


def _safe_mtime(path: Path):
    try:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None

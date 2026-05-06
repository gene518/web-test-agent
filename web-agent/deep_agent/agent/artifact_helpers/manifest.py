"""Workspace manifest snapshot helpers for agent artifacts."""

from __future__ import annotations

import asyncio
from pathlib import Path

from .common import WorkspaceManifest, should_skip_snapshot_path


def snapshot_workspace_manifest(workspace_dir: Path | None) -> WorkspaceManifest:
    """为工作区生成轻量级清单快照。"""

    if workspace_dir is None or not workspace_dir.is_dir():
        return {}

    manifest: WorkspaceManifest = {}
    for path in workspace_dir.rglob("*"):
        if not path.is_file():
            continue
        if should_skip_snapshot_path(path.relative_to(workspace_dir)):
            continue
        stat = path.stat()
        manifest[path.relative_to(workspace_dir).as_posix()] = {
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
        }
    return manifest


async def snapshot_workspace_manifest_async(workspace_dir: Path | None) -> WorkspaceManifest:
    """在不阻塞事件循环的情况下构建工作区清单。"""

    return await asyncio.to_thread(snapshot_workspace_manifest, workspace_dir)


def diff_workspace_manifest(before: WorkspaceManifest, after: WorkspaceManifest) -> dict[str, list[str]]:
    """计算两个清单之间新增、修改和删除的文件。"""

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

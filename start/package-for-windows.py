#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path, PurePosixPath


EXCLUDED_DIRECTORY_NAMES = {
    ".git",
    ".idea",
    ".vscode",
    ".pytest_cache",
    ".uv-cache",
    ".mypy_cache",
    ".ruff_cache",
    ".playwright-cli",
    ".playwright-mcp",
    ".langgraph_api",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "output",
    "playwright-report",
    "target",
    "test-results",
}

EXCLUDED_DIRECTORY_PREFIXES = {
    "start/.cache",
    "start/script/.cache",
    "web-agent/baidu-web",
    "web-agent/runtime",
    "web-agent-client/src-tauri/gen/schemas",
}

EXCLUDED_FILES = {
    "start/backend.log",
    "web-agent/project_conversation.md",
    "web-agent/scheduler_tasks.json",
}


def is_excluded_directory(relative_path: PurePosixPath) -> bool:
    if relative_path.name in EXCLUDED_DIRECTORY_NAMES:
        return True
    if relative_path.name.endswith(".egg-info"):
        return True
    relative = relative_path.as_posix()
    return any(
        relative == prefix or relative.startswith(f"{prefix}/")
        for prefix in EXCLUDED_DIRECTORY_PREFIXES
    )


def is_excluded_file(relative_path: PurePosixPath) -> bool:
    relative = relative_path.as_posix()
    name = relative_path.name
    if relative in EXCLUDED_FILES:
        return True
    if name in {".DS_Store", "Thumbs.db"}:
        return True
    if name == ".coverage" or name.startswith(".coverage."):
        return True
    return relative_path.suffix.lower() in {
        ".log",
        ".pckl",
        ".pyc",
        ".pyo",
        ".tsbuildinfo",
    }


def create_archive(project_root: Path, archive_path: Path) -> int:
    project_root = project_root.resolve()
    archive_path = archive_path.resolve()
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    file_count = 0

    with zipfile.ZipFile(
        archive_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=True,
    ) as archive:
        for current_root, directories, files in os.walk(project_root):
            current_path = Path(current_root)
            relative_root = current_path.relative_to(project_root)
            directories[:] = sorted(
                directory
                for directory in directories
                if not is_excluded_directory(
                    PurePosixPath((relative_root / directory).as_posix())
                )
            )

            for filename in sorted(files):
                source_path = current_path / filename
                relative_path = source_path.relative_to(project_root)
                archive_relative = PurePosixPath(relative_path.as_posix())
                if is_excluded_file(archive_relative):
                    continue
                archive.write(
                    source_path,
                    arcname=f"{project_root.name}/{archive_relative.as_posix()}",
                )
                file_count += 1

    return file_count


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: package-for-windows.py PROJECT_ROOT ARCHIVE_PATH", file=sys.stderr)
        return 2

    count = create_archive(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"已写入 {count} 个源码与配置文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""生成适合拷贝到 Windows 解压的精简 ZIP 压缩包。"""

from __future__ import annotations

import argparse
import fnmatch
import os
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = REPO_ROOT / f"{REPO_ROOT.name}-windows.zip"

# 只打包真正需要带走的源码和说明文档，避免把仓库元数据和本地工具配置一起带出去。
INCLUDE_ROOTS = (
    "README.md",
    "DEVELOPMENT_GUIDE.md",
    "PRD-当前实现需求总结.md",
    "start",
    "web-agent",
    "web-portal",
)

# 本地 `.env` 需要随包带走时，优先级高于通用排除规则。
ALWAYS_INCLUDE_PATHS = {
    "web-agent/.env",
    "web-portal/.env",
}

EXCLUDED_DIR_NAMES = {
    ".git",
    ".github",
    ".idea",
    ".vscode",
    ".langgraph_api",
    ".pytest_cache",
    ".uv-cache",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".next",
    "dist",
    "build",
    ".cache",
    ".turbo",
    ".mypy_cache",
    ".ruff_cache",
    "coverage",
}

EXCLUDED_FILE_NAMES = {
    ".DS_Store",
}

EXCLUDED_SUFFIXES = {
    ".log",
    ".pyc",
    ".pyo",
    ".pyd",
    ".tsbuildinfo",
    ".zip",
}

EXCLUDED_PATH_GLOBS = (
    "*.egg-info/*",
    "start/*.log",
    "start/script/.cache/*",
    "web-agent/.env",
    "web-agent/.env.*",
    "web-agent/runtime/*",
    "web-agent/tests/debug/*.log",
    "web-portal/.env",
    "web-portal/.env.*",
)


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(
        description="生成一个兼容 Windows 解压、尽量精简的项目 ZIP 压缩包。",
    )
    parser.add_argument(
        "output",
        nargs="?",
        default=str(DEFAULT_OUTPUT),
        help="输出 ZIP 路径，默认写到仓库根目录。",
    )
    return parser.parse_args()


def should_exclude(relative_path: str, *, is_dir: bool) -> bool:
    """根据相对路径判断是否应排除。"""

    normalized = relative_path.strip("/")
    if not normalized:
        return False

    if normalized in ALWAYS_INCLUDE_PATHS:
        return False

    parts = Path(normalized).parts
    name = parts[-1]

    if name in EXCLUDED_FILE_NAMES:
        return True

    if is_dir and name in EXCLUDED_DIR_NAMES:
        return True

    if any(part in EXCLUDED_DIR_NAMES for part in parts[:-1]):
        return True

    if not is_dir and Path(name).suffix in EXCLUDED_SUFFIXES:
        return True

    for pattern in EXCLUDED_PATH_GLOBS:
        if fnmatch.fnmatch(normalized, pattern):
            return True

    return False


def iter_files() -> list[Path]:
    """枚举需要打包的文件列表。"""

    files: list[Path] = []

    for include_root in INCLUDE_ROOTS:
        candidate = REPO_ROOT / include_root
        if not candidate.exists():
            continue

        if candidate.is_file():
            relative_path = candidate.relative_to(REPO_ROOT).as_posix()
            if not should_exclude(relative_path, is_dir=False):
                files.append(candidate)
            continue

        for current_root, dirnames, filenames in os.walk(candidate, topdown=True):
            current_root_path = Path(current_root)

            pruned_dirnames: list[str] = []
            for dirname in dirnames:
                dir_path = current_root_path / dirname
                relative_dir = dir_path.relative_to(REPO_ROOT).as_posix()
                if should_exclude(relative_dir, is_dir=True):
                    continue
                pruned_dirnames.append(dirname)
            dirnames[:] = pruned_dirnames

            for filename in filenames:
                file_path = current_root_path / filename
                relative_file = file_path.relative_to(REPO_ROOT).as_posix()
                if should_exclude(relative_file, is_dir=False):
                    continue
                files.append(file_path)

    # 固定排序，方便重复打包时得到稳定的文件顺序。
    return sorted(set(files), key=lambda path: path.relative_to(REPO_ROOT).as_posix())


def build_archive_bytes(file_path: Path) -> bytes:
    """返回写入 ZIP 的最终字节内容。"""

    raw_bytes = file_path.read_bytes()
    if file_path.suffix.lower() != ".ps1":
        return raw_bytes

    # Windows PowerShell 5.1 对启动脚本最稳妥的组合是 UTF-8 BOM + CRLF。
    # 这里在打包阶段统一归一化，避免不同开发机上的行尾和编码差异进到发布包里。
    normalized_text = raw_bytes.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    normalized_text = normalized_text.replace("\n", "\r\n")
    return normalized_text.encode("utf-8-sig")


def build_zip(output_path: Path) -> tuple[int, int]:
    """生成 ZIP，并返回文件数与原始总字节数。"""

    files = iter_files()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    source_bytes = sum(file_path.stat().st_size for file_path in files)
    with ZipFile(output_path, mode="w", compression=ZIP_DEFLATED, compresslevel=9, allowZip64=True) as archive:
        for file_path in files:
            relative_name = file_path.relative_to(REPO_ROOT).as_posix()
            # Python 的 zipfile 会为非 ASCII 文件名写入 UTF-8 标记，Windows 新版资源管理器可正常识别。
            archive.writestr(relative_name, build_archive_bytes(file_path))

    return len(files), source_bytes


def main() -> int:
    """脚本入口。"""

    args = parse_args()
    output_path = Path(args.output).expanduser().resolve()
    file_count, source_bytes = build_zip(output_path)
    archive_size = output_path.stat().st_size

    print(f"ZIP created: {output_path}")
    print(f"Files packed: {file_count}")
    print(f"Source size: {source_bytes} bytes")
    print(f"Archive size: {archive_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

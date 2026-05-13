"""Specialist 共用的运行时输入解析工具。

Plan / Generator / Healer 三个 Specialist 此前各自维护了大量形状一致的辅助方法：
`_normalized_runtime_text`、`_normalized_project_name`、`_normalized_test_plan_files`、
`_normalized_test_scripts`、`_resolve_test_plan_files`、`_expand_test_plan_directory`、
`_bundled_demo_template_dir` 等。这些方法的实现几乎逐字相同，只是把"计划 md"换成
"`.spec.ts` 脚本"，或者错误提示文案略有差别。

本模块把这批共性能力抽出来：
- `normalize_runtime_text` / `normalize_string_list` 做字符串与列表归一化；
- `resolve_workspace_scoped_files` 统一做"绝对化 → workspace 边界校验 → 目录展开 → 去重"；
- `bundled_demo_template_dir` 用仓库内 `assets/` 的 demo 模板，只实现一份。

Specialist 直接调用这些函数即可，不需要再在各自类里维护"几乎一样"的私有方法。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from deep_agent.core.autotest_project_directory import (
    DEFAULT_AUTOTEST_DEMO_PROJECT_NAME,
    normalize_runtime_text,
)


def normalize_string_list(value: Any) -> list[str]:
    """把任意传入值归一化为去重后的非空字符串列表。

    调用方：Specialist 在消费 `test_plan_files`、`test_scripts` 等列表参数前做校验。
    规则：
    - `None` / 空值返回空列表；
    - 单个字符串会被当作只有一个元素的列表；
    - 列表/元组中的每一项都会先走 `normalize_runtime_text`，失败则跳过；
    - 最终按首次出现顺序去重。
    """

    if isinstance(value, (list, tuple)):
        candidate_values: Iterable[Any] = value
    elif value is None:
        candidate_values = ()
    else:
        candidate_values = (value,)

    normalized_values: list[str] = []
    seen: set[str] = set()
    for item in candidate_values:
        normalized_item = normalize_runtime_text(item)
        if not normalized_item or normalized_item in seen:
            continue
        seen.add(normalized_item)
        normalized_values.append(normalized_item)
    return normalized_values


def resolve_workspace_scoped_files(
    *,
    workspace_dir: Path,
    raw_values: Any,
    kind_label: str,
    directory_expander: Callable[[Path], list[Path]],
) -> list[Path]:
    """把用户输入的文件或目录解析成 workspace 内的绝对文件路径列表。

    调用方：Generator 解析 `test_plan_files`、Healer 解析 `test_scripts`。
    目的：保证三个关键语义点一致：
    1. 路径必须归一化到绝对 Path；
    2. 必须在 `workspace_dir` 内，否则抛出明确错误；
    3. 传入目录时使用 `directory_expander` 展开为具体文件列表，最后去重保持顺序。

    Args:
        workspace_dir: 当前 Specialist 的项目目录。
        raw_values: 用户给出的路径参数，可以是字符串、字符串列表或 `None`。
        kind_label: 错误提示中使用的业务语义标签，例如 `"测试计划文件"` / `"待调试脚本"`。
        directory_expander: 当路径是目录时如何展开成文件列表；调用方只需要关心文件筛选规则。

    Returns:
        去重后的绝对文件 Path 列表。
    """

    normalized_values = normalize_string_list(raw_values)
    if not normalized_values:
        raise RuntimeError(f"未提供合法的{kind_label}，无法继续。")

    resolved_workspace = workspace_dir.resolve()
    resolved_paths: list[Path] = []
    for raw_file in normalized_values:
        candidate_path = Path(raw_file).expanduser()
        if not candidate_path.is_absolute():
            candidate_path = resolved_workspace / candidate_path

        resolved_path = candidate_path.resolve()
        try:
            resolved_path.relative_to(resolved_workspace)
        except ValueError as exc:
            raise RuntimeError(
                f"{kind_label} `{resolved_path}` 不在项目目录 `{resolved_workspace}` 下，无法继续。"
            ) from exc

        if resolved_path.is_file():
            resolved_paths.append(resolved_path)
            continue

        if resolved_path.is_dir():
            resolved_paths.extend(directory_expander(resolved_path))
            continue

        raise RuntimeError(f"{kind_label} `{resolved_path}` 不存在，无法继续。")

    deduplicated_paths: list[Path] = []
    seen: set[str] = set()
    for path in resolved_paths:
        normalized_key = str(path)
        if normalized_key in seen:
            continue
        seen.add(normalized_key)
        deduplicated_paths.append(path)
    return deduplicated_paths


def bundled_demo_template_dir() -> Path:
    """返回仓库内置的 `assets/demo` 模板目录。

    用于在用户没有显式项目目录时按模板复制出新的自动化工程。集中实现的目的，是避免
    三个 Specialist 各自用 `Path(__file__).resolve().parents[2] / 'assets' / ...` 写死。
    """

    template_dir = (
        Path(__file__).resolve().parents[2] / "assets" / DEFAULT_AUTOTEST_DEMO_PROJECT_NAME
    )
    if not template_dir.is_dir():
        raise RuntimeError(f"内置 demo 模板目录不存在：`{template_dir}`。")
    return template_dir


__all__ = [
    "bundled_demo_template_dir",
    "normalize_runtime_text",
    "normalize_string_list",
    "resolve_workspace_scoped_files",
]

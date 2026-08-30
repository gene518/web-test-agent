"""Scheduler 自动化项目路径的安全解析。"""

from __future__ import annotations

from pathlib import Path

from deep_agent.core.config import AppSettings


SCHEDULER_LOG_FILE_NAME = "scheduler-service.log"
_GLOB_CHARACTERS = frozenset("*?[]{}")


def resolve_scheduler_project_dir(
    *,
    settings: AppSettings,
    project_name: str | None,
    project_dir: str | None,
) -> Path:
    """解析项目目录，并限制相对配置只能位于自动化根目录内。"""

    automation_root = settings.resolved_default_automation_project_root.resolve()
    if project_dir:
        project_dir_path = Path(project_dir).expanduser()
        if project_dir_path.is_absolute():
            return project_dir_path.resolve()
        resolved_project_dir = (automation_root / project_dir_path).resolve()
        _ensure_path_within_root(
            path=resolved_project_dir,
            root=automation_root,
            field_name="project_dir",
        )
    else:
        if not project_name:
            raise RuntimeError(
                "调度配置缺少 `project_name` 或 `project_dir`，无法解析项目目录。"
            )
        _validate_project_name(project_name)
        resolved_project_dir = (automation_root / project_name).resolve()
        _ensure_path_within_root(
            path=resolved_project_dir,
            root=automation_root,
            field_name="project_name",
        )
    return resolved_project_dir


def resolve_scheduler_log_path(
    *,
    settings: AppSettings,
    project_name: str | None,
    project_dir: str | None,
    test_root_dir: str,
) -> Path:
    """返回项目测试根目录内的调度日志文件路径。"""

    resolved_project_dir = resolve_scheduler_project_dir(
        settings=settings,
        project_name=project_name,
        project_dir=project_dir,
    )
    test_root_path = Path(test_root_dir).expanduser()
    if test_root_path.is_absolute():
        raise RuntimeError("调度配置中的 `test_root_dir` 必须是项目内的相对路径。")
    resolved_test_root = (resolved_project_dir / test_root_path).resolve()
    _ensure_path_within_root(
        path=resolved_test_root,
        root=resolved_project_dir,
        field_name="test_root_dir",
    )
    return resolved_test_root / SCHEDULER_LOG_FILE_NAME


def resolve_scheduler_locations(
    *,
    project_dir: Path,
    locations: list[str],
) -> tuple[str, ...]:
    """校验 Playwright 目标，并返回项目内规范化的相对路径。"""

    resolved_project_dir = project_dir.resolve()
    resolved_locations: list[str] = []
    for location in locations:
        if any(character in location for character in _GLOB_CHARACTERS):
            raise RuntimeError(
                f"定时任务 location 仅支持文件或目录，不能包含 glob：`{location}`。"
            )
        location_path = Path(location).expanduser()
        if location_path.is_absolute():
            raise RuntimeError(
                f"定时任务 location 必须是项目内相对路径：`{location}`。"
            )
        resolved_location = (resolved_project_dir / location_path).resolve()
        _ensure_path_within_root(
            path=resolved_location,
            root=resolved_project_dir,
            field_name="locations",
        )
        resolved_locations.append(
            resolved_location.relative_to(resolved_project_dir).as_posix()
        )
    return tuple(resolved_locations)


def _validate_project_name(project_name: str) -> None:
    """项目名只能表示自动化根目录下的一个直接子目录。"""

    if (
        not project_name.strip()
        or project_name in {".", ".."}
        or "/" in project_name
        or "\\" in project_name
    ):
        raise RuntimeError("调度配置中的 `project_name` 必须是单个安全目录名。")
    if Path(project_name).is_absolute():
        raise RuntimeError("调度配置中的 `project_name` 不能是绝对路径。")


def _ensure_path_within_root(*, path: Path, root: Path, field_name: str) -> None:
    """拒绝相对配置通过 `..` 或符号链接逃逸声明的根目录。"""

    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(
            f"调度配置中的 `{field_name}` 不能逃逸目录 `{root}`。"
        ) from exc


__all__ = [
    "SCHEDULER_LOG_FILE_NAME",
    "resolve_scheduler_locations",
    "resolve_scheduler_log_path",
    "resolve_scheduler_project_dir",
]

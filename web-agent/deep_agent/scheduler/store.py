"""定时任务配置文件的读写与更新。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from deep_agent.core.config import AppSettings
from deep_agent.scheduler.cron import validate_cron_expression
from deep_agent.scheduler.models import SchedulerConfigFile


SCHEDULER_LOG_FILE_NAME = "scheduler-service.log"


def load_scheduler_config(config_path: Path) -> SchedulerConfigFile:
    """从 JSON 文件读取调度配置。"""

    resolved_path = config_path.expanduser().resolve()
    if not resolved_path.is_file():
        raise RuntimeError(f"定时任务配置文件不存在：`{resolved_path}`。")

    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"定时任务配置文件不是合法 JSON：`{resolved_path}`。") from exc

    if not isinstance(payload, dict):
        raise RuntimeError(f"定时任务配置文件顶层必须是 JSON object：`{resolved_path}`。")
    try:
        return SchedulerConfigFile.model_validate(payload)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"定时任务配置文件校验失败：`{resolved_path}`。{exc}") from exc


def save_scheduler_config(config_path: Path, config_model: SchedulerConfigFile) -> None:
    """把调度配置写回 JSON 文件。"""

    resolved_path = config_path.expanduser().resolve()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path.write_text(
        json.dumps(config_model.model_dump(exclude_none=True), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def resolve_scheduler_project_dir(
    *,
    settings: AppSettings,
    project_name: str | None,
    project_dir: str | None,
) -> Path:
    """把项目标识解析为实际自动化项目目录。"""

    automation_root = settings.resolved_default_automation_project_root
    if project_dir:
        resolved_project_dir = Path(project_dir).expanduser()
        if not resolved_project_dir.is_absolute():
            resolved_project_dir = automation_root / resolved_project_dir
    else:
        if not project_name:
            raise RuntimeError("调度配置缺少 `project_name` 或 `project_dir`，无法解析项目目录。")
        resolved_project_dir = automation_root / project_name
    return resolved_project_dir.resolve()


def resolve_scheduler_log_path(
    *,
    settings: AppSettings,
    project_name: str | None,
    project_dir: str | None,
    test_root_dir: str,
) -> Path:
    """返回项目测试根目录下的调度日志文件路径。"""

    resolved_project_dir = resolve_scheduler_project_dir(
        settings=settings,
        project_name=project_name,
        project_dir=project_dir,
    )
    return (resolved_project_dir / test_root_dir / SCHEDULER_LOG_FILE_NAME).resolve()


def update_existing_task_config(  # noqa: PLR0913
    *,
    settings: AppSettings,
    config_path: Path,
    project_name: str | None,
    project_dir: str | None,
    task_id: str,
    schedule: str | None = None,
    headed: bool | None = None,
    enabled: bool | None = None,
    locations: list[str] | None = None,
) -> dict[str, Any]:
    """修改一个已存在的定时任务配置。"""

    config_model = load_scheduler_config(config_path)
    project_model = _find_project(
        config_model=config_model,
        settings=settings,
        project_name=project_name,
        project_dir=project_dir,
    )
    task_model = _find_task(project_model=project_model, task_id=task_id)

    update_payload: dict[str, Any] = {}
    if schedule is not None:
        validate_cron_expression(schedule)
        task_model.schedule = schedule
        update_payload["schedule"] = schedule
    if headed is not None:
        task_model.headed = headed
        update_payload["headed"] = headed
    if enabled is not None:
        task_model.enabled = enabled
        update_payload["enabled"] = enabled
    if locations is not None:
        task_model.locations = [str(item).strip() for item in locations if str(item).strip()]
        update_payload["locations"] = task_model.locations

    if not update_payload:
        raise RuntimeError("未识别到任何可更新的定时任务字段。")

    save_scheduler_config(config_path, config_model)
    resolved_project_dir = resolve_scheduler_project_dir(
        settings=settings,
        project_name=project_model.project_name,
        project_dir=project_model.project_dir,
    )
    return {
        "status": "success",
        "config_path": str(config_path.expanduser().resolve()),
        "project_name": project_model.project_name or resolved_project_dir.name,
        "project_dir": str(resolved_project_dir),
        "task_id": task_model.task_id,
        "updated_fields": update_payload,
        "log_file": str(
            resolve_scheduler_log_path(
                settings=settings,
                project_name=project_model.project_name,
                project_dir=project_model.project_dir,
                test_root_dir=project_model.test_root_dir,
            )
        ),
    }


def _find_project(
    *,
    config_model: SchedulerConfigFile,
    settings: AppSettings,
    project_name: str | None,
    project_dir: str | None,
):
    """按项目名或项目目录查找配置中的项目条目。"""

    resolved_query_dir = None
    if project_dir is not None:
        resolved_query_dir = resolve_scheduler_project_dir(
            settings=settings,
            project_name=project_name,
            project_dir=project_dir,
        )

    matched_projects = []
    for project_model in config_model.projects:
        if project_name and project_model.project_name == project_name:
            matched_projects.append(project_model)
            continue
        if resolved_query_dir is None:
            continue
        resolved_project_dir = resolve_scheduler_project_dir(
            settings=settings,
            project_name=project_model.project_name,
            project_dir=project_model.project_dir,
        )
        if resolved_project_dir == resolved_query_dir:
            matched_projects.append(project_model)

    if not matched_projects:
        raise RuntimeError("未在定时任务配置文件中找到匹配的项目。")
    if len(matched_projects) > 1:
        raise RuntimeError("匹配到多个项目，请改用更精确的 `project_dir` 或 `project_name`。")
    return matched_projects[0]


def _find_task(*, project_model, task_id: str):
    """按任务 ID 查找项目内任务。"""

    for task_model in project_model.tasks:
        if task_model.task_id == task_id:
            return task_model
    raise RuntimeError(f"项目 `{project_model.project_key()}` 中不存在任务 `{task_id}`。")


__all__ = [
    "SCHEDULER_LOG_FILE_NAME",
    "load_scheduler_config",
    "resolve_scheduler_log_path",
    "resolve_scheduler_project_dir",
    "save_scheduler_config",
    "update_existing_task_config",
]

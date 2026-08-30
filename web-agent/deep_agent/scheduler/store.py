"""定时任务配置文件的读写与更新。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

from deep_agent.core.config import AppSettings
from deep_agent.scheduler.cron import validate_cron_expression
from deep_agent.scheduler.models import (
    ScheduledProjectConfig,
    ScheduledTaskConfig,
    SchedulerConfigFile,
)
from deep_agent.scheduler.paths import (
    SCHEDULER_LOG_FILE_NAME,
    resolve_scheduler_locations,
    resolve_scheduler_log_path,
    resolve_scheduler_project_dir,
)

_SCHEDULER_CONFIG_LOCK = threading.RLock()


def generate_scheduled_task_id(project_dir: Path) -> str:
    """根据项目绝对路径生成不可由用户指定的稳定任务 ID。"""

    resolved_project_dir = project_dir.expanduser().resolve()
    normalized_name = (
        re.sub(r"[^a-z0-9]+", "-", resolved_project_dir.name.lower()).strip("-")
        or "project"
    )
    path_digest = hashlib.sha256(str(resolved_project_dir).encode("utf-8")).hexdigest()[
        :10
    ]
    return f"scheduled-{normalized_name}-{path_digest}"


def load_scheduler_config(config_path: Path) -> SchedulerConfigFile:
    """从 JSON 文件读取调度配置。"""

    resolved_path = config_path.expanduser().resolve()
    with _SCHEDULER_CONFIG_LOCK:
        if not resolved_path.is_file():
            raise RuntimeError(f"定时任务配置文件不存在：`{resolved_path}`。")

        try:
            payload = json.loads(resolved_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"定时任务配置文件不是合法 JSON：`{resolved_path}`。"
            ) from exc

    if not isinstance(payload, dict):
        raise RuntimeError(
            f"定时任务配置文件顶层必须是 JSON object：`{resolved_path}`。"
        )
    try:
        return SchedulerConfigFile.model_validate(payload)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"定时任务配置文件校验失败：`{resolved_path}`。{exc}"
        ) from exc


def save_scheduler_config(config_path: Path, config_model: SchedulerConfigFile) -> None:
    """以原子替换方式把调度配置写回 JSON 文件。"""

    resolved_path = config_path.expanduser().resolve()
    serialized_config = (
        json.dumps(
            config_model.model_dump(exclude_none=True), ensure_ascii=False, indent=2
        )
        + "\n"
    )
    with _SCHEDULER_CONFIG_LOCK:
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=resolved_path.parent,
                prefix=f".{resolved_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_file.write(serialized_config)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
                temporary_path = Path(temporary_file.name)
            os.replace(temporary_path, resolved_path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


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

    with _SCHEDULER_CONFIG_LOCK:
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
            resolved_project_dir = resolve_scheduler_project_dir(
                settings=settings,
                project_name=project_model.project_name,
                project_dir=project_model.project_dir,
            )
            task_model.locations = list(
                resolve_scheduler_locations(
                    project_dir=resolved_project_dir,
                    locations=[
                        str(item).strip() for item in locations if str(item).strip()
                    ],
                )
            )
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


def upsert_auto_scheduled_task_config(  # noqa: PLR0913
    *,
    settings: AppSettings,
    config_path: Path,
    project_name: str | None,
    project_dir: str | None,
    schedule: str,
    headed: bool | None = None,
    enabled: bool | None = None,
    locations: list[str] | None = None,
) -> dict[str, Any]:
    """按项目路径创建或更新系统托管任务，任务 ID 始终由系统生成。"""

    validate_cron_expression(schedule)
    resolved_project_dir = resolve_scheduler_project_dir(
        settings=settings,
        project_name=project_name,
        project_dir=project_dir,
    )
    if not resolved_project_dir.is_dir():
        raise RuntimeError(
            f"自动化项目目录不存在或不是目录：`{resolved_project_dir}`。"
        )
    normalized_locations = (
        list(
            resolve_scheduler_locations(
                project_dir=resolved_project_dir,
                locations=[
                    str(item).strip() for item in locations if str(item).strip()
                ],
            )
        )
        if locations is not None
        else None
    )

    resolved_config_path = config_path.expanduser().resolve()
    with _SCHEDULER_CONFIG_LOCK:
        config_model = (
            load_scheduler_config(resolved_config_path)
            if resolved_config_path.is_file()
            else SchedulerConfigFile()
        )
        project_model = _find_project_by_resolved_dir(
            config_model=config_model,
            settings=settings,
            resolved_project_dir=resolved_project_dir,
        )
        project_created = project_model is None
        if project_model is None:
            project_model = ScheduledProjectConfig(
                project_name=resolved_project_dir.name,
                project_dir=str(resolved_project_dir),
            )
            config_model.projects.append(project_model)

        task_id = generate_scheduled_task_id(resolved_project_dir)
        task_model = next(
            (task for task in project_model.tasks if task.task_id == task_id), None
        )
        task_created = task_model is None
        if task_model is None:
            task_model = ScheduledTaskConfig(
                task_id=task_id,
                schedule=schedule,
                locations=normalized_locations or [],
                enabled=True if enabled is None else enabled,
                headed=headed,
            )
            project_model.tasks.append(task_model)
        else:
            task_model.schedule = schedule
            if headed is not None:
                task_model.headed = headed
            if enabled is not None:
                task_model.enabled = enabled
            if normalized_locations is not None:
                task_model.locations = normalized_locations

        save_scheduler_config(resolved_config_path, config_model)
    return {
        "status": "success",
        "operation": "created" if task_created else "updated",
        "project_created": project_created,
        "config_path": str(resolved_config_path),
        "project_name": project_model.project_name or resolved_project_dir.name,
        "project_dir": str(resolved_project_dir),
        "task_id": task_id,
        "schedule": task_model.schedule,
        "headed": project_model.headed
        if task_model.headed is None
        else task_model.headed,
        "enabled": task_model.enabled,
        "locations": task_model.locations,
        "log_file": str(
            resolve_scheduler_log_path(
                settings=settings,
                project_name=project_model.project_name,
                project_dir=project_model.project_dir,
                test_root_dir=project_model.test_root_dir,
            )
        ),
    }


def _find_project_by_resolved_dir(
    *,
    config_model: SchedulerConfigFile,
    settings: AppSettings,
    resolved_project_dir: Path,
) -> ScheduledProjectConfig | None:
    """按规范化绝对目录查找项目；不存在时返回 None。"""

    matches = [
        project_model
        for project_model in config_model.projects
        if resolve_scheduler_project_dir(
            settings=settings,
            project_name=project_model.project_name,
            project_dir=project_model.project_dir,
        )
        == resolved_project_dir
    ]
    if len(matches) > 1:
        raise RuntimeError(
            f"定时任务配置中存在多个相同项目目录：`{resolved_project_dir}`。"
        )
    return matches[0] if matches else None


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
        raise RuntimeError(
            "匹配到多个项目，请改用更精确的 `project_dir` 或 `project_name`。"
        )
    return matched_projects[0]


def _find_task(*, project_model, task_id: str):
    """按任务 ID 查找项目内任务。"""

    for task_model in project_model.tasks:
        if task_model.task_id == task_id:
            return task_model
    raise RuntimeError(
        f"项目 `{project_model.project_key()}` 中不存在任务 `{task_id}`。"
    )


__all__ = [
    "SCHEDULER_LOG_FILE_NAME",
    "generate_scheduled_task_id",
    "load_scheduler_config",
    "resolve_scheduler_log_path",
    "resolve_scheduler_project_dir",
    "save_scheduler_config",
    "upsert_auto_scheduled_task_config",
    "update_existing_task_config",
]

"""定时任务配置的数据模型。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, model_validator

from deep_agent.scheduler.cron import validate_cron_expression


def _normalized_text(value: Any) -> str | None:
    """把任意输入归一化为可判空字符串。"""

    if value is None:
        return None
    normalized_value = str(value).strip()
    return normalized_value or None


def _normalized_locations(value: Any) -> list[str]:
    """把配置中的脚本路径列表归一化为去重后的字符串数组。"""

    if value is None:
        return []
    if not isinstance(value, list):
        value = [value]

    normalized_values: list[str] = []
    seen: set[str] = set()
    for item in value:
        normalized_item = _normalized_text(item)
        if normalized_item is None or normalized_item in seen:
            continue
        seen.add(normalized_item)
        normalized_values.append(normalized_item)
    return normalized_values


class SchedulerRuntimeConfig(BaseModel):
    """调度服务自身的全局运行参数。"""

    poll_interval_seconds: int = Field(
        default=30,
        ge=5,
        description="调度器轮询配置文件并检查到点任务的时间间隔，单位为秒。",
    )


class ScheduledTaskConfig(BaseModel):
    """单个定时任务条目。"""

    task_id: str = Field(
        description="项目内唯一的定时任务标识，用于 agent 更新现有任务配置。",
    )
    schedule: str = Field(
        description="五段 Cron 表达式，例如 `0 9 * * *`。",
    )
    locations: list[str] = Field(
        default_factory=list,
        description="要执行的测试脚本或目录列表；为空时表示执行整个项目的 Playwright 测试。",
    )
    enabled: bool = Field(
        default=True,
        description="是否启用该定时任务；关闭后调度器会跳过执行。",
    )
    headed: bool | None = Field(
        default=None,
        description="可选的任务级浏览器模式覆盖；`true` 为有头，`false` 为无头，留空时继承项目配置。",
    )

    @model_validator(mode="after")
    def _validate_fields(self) -> "ScheduledTaskConfig":
        """校验并归一化任务字段。"""

        normalized_task_id = _normalized_text(self.task_id)
        normalized_schedule = _normalized_text(self.schedule)
        if normalized_task_id is None:
            raise ValueError("`task_id` 不能为空。")
        if normalized_schedule is None:
            raise ValueError("`schedule` 不能为空。")
        validate_cron_expression(normalized_schedule)
        self.task_id = normalized_task_id
        self.schedule = normalized_schedule
        self.locations = _normalized_locations(self.locations)
        return self


class ScheduledProjectConfig(BaseModel):
    """项目级定时任务配置。"""

    project_name: str | None = Field(
        default=None,
        description="自动化项目名称；未提供 `project_dir` 时，会按默认自动化根目录拼出项目路径。",
    )
    project_dir: str | None = Field(
        default=None,
        description="自动化项目目录；相对路径会相对默认自动化根目录解析。",
    )
    test_root_dir: str = Field(
        default="test_case",
        description="项目内测试根目录；定时执行日志文件会保存在该目录下。",
    )
    timezone: str | None = Field(
        default=None,
        description="项目级时区；未配置时使用服务进程当前本地时区。",
    )
    headed: bool = Field(
        default=False,
        description="项目默认浏览器模式；`true` 为有头，`false` 为无头。",
    )
    tasks: list[ScheduledTaskConfig] = Field(
        default_factory=list,
        description="当前项目下声明的定时任务列表。",
    )

    @model_validator(mode="after")
    def _validate_fields(self) -> "ScheduledProjectConfig":
        """校验项目配置字段。"""

        normalized_project_name = _normalized_text(self.project_name)
        normalized_project_dir = _normalized_text(self.project_dir)
        if normalized_project_name is None and normalized_project_dir is None:
            raise ValueError("项目配置至少需要提供 `project_name` 或 `project_dir`。")

        normalized_test_root_dir = _normalized_text(self.test_root_dir)
        if normalized_test_root_dir is None:
            raise ValueError("`test_root_dir` 不能为空。")

        if self.timezone is not None:
            normalized_timezone = _normalized_text(self.timezone)
            if normalized_timezone is None:
                self.timezone = None
            else:
                try:
                    ZoneInfo(normalized_timezone)
                except ZoneInfoNotFoundError as exc:
                    raise ValueError(f"未知时区：`{normalized_timezone}`。") from exc
                self.timezone = normalized_timezone

        self.project_name = normalized_project_name
        self.project_dir = normalized_project_dir
        self.test_root_dir = Path(normalized_test_root_dir).as_posix()

        seen_task_ids: set[str] = set()
        for task in self.tasks:
            if task.task_id in seen_task_ids:
                raise ValueError(f"项目内存在重复的任务 ID：`{task.task_id}`。")
            seen_task_ids.add(task.task_id)
        return self

    def project_key(self) -> str:
        """返回项目配置的稳定标识。"""

        return self.project_dir or self.project_name or "unknown-project"


class SchedulerConfigFile(BaseModel):
    """整个定时调度配置文件。"""

    scheduler: SchedulerRuntimeConfig = Field(
        default_factory=SchedulerRuntimeConfig,
        description="调度服务的全局运行参数。",
    )
    projects: list[ScheduledProjectConfig] = Field(
        default_factory=list,
        description="所有参与定时执行的项目配置列表。",
    )

    @model_validator(mode="after")
    def _validate_projects(self) -> "SchedulerConfigFile":
        """校验项目键唯一。"""

        seen_project_keys: set[str] = set()
        for project in self.projects:
            project_key = project.project_key()
            if project_key in seen_project_keys:
                raise ValueError(f"配置文件中存在重复项目：`{project_key}`。")
            seen_project_keys.add(project_key)
        return self


__all__ = [
    "ScheduledProjectConfig",
    "ScheduledTaskConfig",
    "SchedulerConfigFile",
    "SchedulerRuntimeConfig",
]

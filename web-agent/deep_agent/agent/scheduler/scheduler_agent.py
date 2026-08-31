"""创建或更新项目系统托管定时任务的 Scheduler Agent。"""

from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.runnables import RunnableConfig

from deep_agent.agent.base_agent import BaseAgent
from deep_agent.agent.state import WorkflowState
from deep_agent.core.config import AppSettings
from deep_agent.core.runtime_logging import (
    build_trace_context,
    format_messages_for_log,
    format_state_for_log,
    get_logger,
    log_title,
)
from deep_agent.scheduler.store import upsert_auto_scheduled_task_config


logger = get_logger(__name__)


class SchedulerAgent(BaseAgent):
    """只负责创建或更新项目的系统托管定时任务配置，不直接执行测试。"""

    agent_type = "scheduler"
    display_name = "Scheduler Agent"

    def __init__(self, settings: AppSettings) -> None:
        """保存 Scheduler 配置写入所需的应用配置。"""

        self._settings = settings

    async def execute(
        self, state: WorkflowState, config: RunnableConfig | None = None
    ) -> WorkflowState:
        """根据提取参数修改定时任务配置文件。"""

        logger.info(
            "%s event=node_enter trace=%s state=%s",
            log_title("执行", "节点入参", node_name="scheduler_config_node"),
            build_trace_context(
                config, node_name="scheduler_config_node", event_name="node_enter"
            ),
            format_state_for_log(state),
        )

        raw_result = await self._build_raw_result(state)
        finalization_key = f"{self.agent_type}:{uuid4().hex}"
        result: WorkflowState = {
            "messages": [],
            "stage_result": {
                "agent_type": self.agent_type,
                "display_name": self.display_name,
                "status": raw_result.get("status", "success"),
                "raw_result": raw_result,
                "finalization_key": finalization_key,
            },
            "finalization_key": finalization_key,
            "pipeline_handoff": False,
            "return_to_master": False,
        }
        logger.info(
            "%s event=node_exit trace=%s messages=%s",
            log_title("执行", "节点出参", node_name="scheduler_config_node"),
            build_trace_context(
                config, node_name="scheduler_config_node", event_name="node_exit"
            ),
            format_messages_for_log(result["messages"]),
        )
        return result

    async def _build_raw_result(self, state: WorkflowState) -> dict[str, Any]:
        """执行更新并把结果整理成可汇总的结构。"""

        extracted_params = dict(state.get("extracted_params", {}))
        config_path = self._settings.resolved_scheduler_config_path
        try:
            update_result = upsert_auto_scheduled_task_config(
                settings=self._settings,
                config_path=Path(config_path),
                project_name=self._optional_text(extracted_params.get("project_name")),
                project_dir=self._optional_text(extracted_params.get("project_dir")),
                schedule=self._required_text(
                    extracted_params.get("schedule_cron"), field_name="schedule_cron"
                ),
                headed=self._optional_bool(extracted_params.get("schedule_headed")),
                enabled=self._optional_bool(extracted_params.get("schedule_enabled")),
                locations=self._optional_string_list(
                    extracted_params.get("schedule_locations")
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "%s 更新定时任务配置失败：%s",
                log_title("执行", "节点异常", node_name="scheduler_config_node"),
                exc,
            )
            return {
                "status": "error",
                "message": str(exc),
                "config_path": str(config_path),
            }

        return {
            **update_result,
            "message": "定时任务配置成功；任务 ID 已由系统生成，独立调度服务将在下一轮扫描时自动读取最新配置。",
        }

    def _optional_text(self, value: Any) -> str | None:
        """把参数归一化为可判空字符串。"""

        if value is None:
            return None
        normalized_value = str(value).strip()
        return normalized_value or None

    def _required_text(self, value: Any, *, field_name: str) -> str:
        """返回必填字符串，否则抛出可读错误。"""

        normalized_value = self._optional_text(value)
        if normalized_value is None:
            raise RuntimeError(f"缺少必填字段 `{field_name}`。")
        return normalized_value

    def _optional_bool(self, value: Any) -> bool | None:
        """把参数归一化为可判空布尔值。"""

        if isinstance(value, bool):
            return value
        return None

    def _optional_string_list(self, value: Any) -> list[str] | None:
        """把参数归一化为字符串数组；未提供时返回 None。"""

        if value is None:
            return None
        if not isinstance(value, list):
            value = [value]

        normalized_values: list[str] = []
        for item in value:
            normalized_item = self._optional_text(item)
            if normalized_item is None:
                continue
            normalized_values.append(normalized_item)
        return normalized_values

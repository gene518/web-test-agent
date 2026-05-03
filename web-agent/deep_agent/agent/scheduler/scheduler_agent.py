"""修改现有定时任务配置的 Scheduler Agent。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from deep_agent.agent.base_agent import BaseAgent
from deep_agent.agent.master.master_agent import MasterAgent
from deep_agent.agent.state import WorkflowState
from deep_agent.core.config import AppSettings
from deep_agent.core.runtime_logging import build_trace_context, format_messages_for_log, format_state_for_log, get_logger, log_title
from deep_agent.scheduler.store import update_existing_task_config


logger = get_logger(__name__)


class SchedulerAgent(BaseAgent):
    """只负责更新已存在的定时任务配置，不执行测试。"""

    agent_type = "scheduler"
    display_name = "Scheduler Agent"

    def __init__(self, master_agent: MasterAgent, settings: AppSettings) -> None:
        """保存共享 Master 服务对象和应用配置。"""

        self._master_agent = master_agent
        self._settings = settings

    async def execute(self, state: WorkflowState, config: RunnableConfig | None = None) -> WorkflowState:
        """根据提取参数修改定时任务配置文件。"""

        logger.info(
            "%s event=node_enter trace=%s state=%s",
            log_title("执行", "节点入参", node_name="scheduler_config_node"),
            build_trace_context(config, node_name="scheduler_config_node", event_name="node_enter"),
            format_state_for_log(state),
        )

        raw_result = await self._build_raw_result(state)
        final_summary = await self._master_agent.summarize_final_response(
            state=state,
            stage_name=self.display_name,
            raw_result=raw_result,
            config=config,
        )
        result: WorkflowState = {
            "messages": [AIMessage(content=final_summary)],
            "stage_result": {
                "agent_type": self.agent_type,
                "raw_result": raw_result,
            },
            "final_summary": final_summary,
            "next_action": "end",
        }
        logger.info(
            "%s event=node_exit trace=%s messages=%s",
            log_title("执行", "节点出参", node_name="scheduler_config_node"),
            build_trace_context(config, node_name="scheduler_config_node", event_name="node_exit"),
            format_messages_for_log(result["messages"]),
        )
        return result

    async def _build_raw_result(self, state: WorkflowState) -> dict[str, Any]:
        """执行更新并把结果整理成可汇总的结构。"""

        extracted_params = dict(state.get("extracted_params", {}))
        config_path = self._settings.resolved_scheduler_config_path
        update_fields = self._build_update_fields(extracted_params)
        if not update_fields:
            return {
                "status": "error",
                "message": (
                    "未识别到任何可修改的定时任务字段。"
                    "当前节点只支持修改已存在任务的执行时间、启用状态、有头/无头模式或脚本列表。"
                ),
                "config_path": str(config_path),
            }

        try:
            update_result = update_existing_task_config(
                settings=self._settings,
                config_path=Path(config_path),
                project_name=self._optional_text(extracted_params.get("project_name")),
                project_dir=self._optional_text(extracted_params.get("project_dir")),
                task_id=self._required_text(extracted_params.get("schedule_task_id"), field_name="schedule_task_id"),
                schedule=self._optional_text(extracted_params.get("schedule_cron")),
                headed=self._optional_bool(extracted_params.get("schedule_headed")),
                enabled=self._optional_bool(extracted_params.get("schedule_enabled")),
                locations=self._optional_string_list(extracted_params.get("schedule_locations")),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s 更新定时任务配置失败：%s", log_title("执行", "节点异常", node_name="scheduler_config_node"), exc)
            return {
                "status": "error",
                "message": str(exc),
                "config_path": str(config_path),
            }

        return {
            **update_result,
            "message": "定时任务配置更新成功；独立调度服务将在下一轮扫描时自动读取最新配置。",
        }

    def _build_update_fields(self, extracted_params: dict[str, Any]) -> dict[str, Any]:
        """筛出本次请求真正想修改的字段。"""

        update_fields: dict[str, Any] = {}
        schedule_cron = self._optional_text(extracted_params.get("schedule_cron"))
        if schedule_cron is not None:
            update_fields["schedule"] = schedule_cron

        schedule_headed = self._optional_bool(extracted_params.get("schedule_headed"))
        if schedule_headed is not None:
            update_fields["headed"] = schedule_headed

        schedule_enabled = self._optional_bool(extracted_params.get("schedule_enabled"))
        if schedule_enabled is not None:
            update_fields["enabled"] = schedule_enabled

        schedule_locations = self._optional_string_list(extracted_params.get("schedule_locations"))
        if schedule_locations is not None:
            update_fields["locations"] = schedule_locations
        return update_fields

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

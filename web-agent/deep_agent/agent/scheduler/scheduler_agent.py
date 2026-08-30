"""创建或更新项目系统托管定时任务的 Scheduler Agent。"""

from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig

from deep_agent.agent.base_agent import BaseAgent
from deep_agent.agent.master.master_agent import MasterAgent
from deep_agent.agent.state import WorkflowState
from deep_agent.core.config import AppSettings
from deep_agent.core.display_message import (
    build_display_summary_message,
    extract_missing_display_messages,
)
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

    def __init__(self, master_agent: MasterAgent, settings: AppSettings) -> None:
        """保存共享 Master 服务对象和应用配置。"""

        self._master_agent = master_agent
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
        narrative = await self._master_agent.summarize_final_response(
            state=state,
            stage_name=self.display_name,
            raw_result=raw_result,
            config=config,
        )
        final_summary = self._build_stage_summary(raw_result, narrative)
        final_message = build_display_summary_message(
            final_summary,
            prefix="scheduler-summary",
        )
        result: WorkflowState = {
            "messages": [final_message],
            "display_messages": [
                *extract_missing_display_messages(dict(state)),
                final_message,
            ],
            "stage_result": {
                "agent_type": self.agent_type,
                "raw_result": raw_result,
                "stage_summary": final_summary,
            },
            "final_summary": final_summary,
            "next_action": "end",
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

    def _build_stage_summary(
        self,
        raw_result: dict[str, Any],
        narrative: str,
    ) -> str:
        """把模型补充说明包装进前端可稳定识别的 Scheduler 阶段摘要。"""

        succeeded = raw_result.get("status") == "success"
        lines = [
            "**Scheduler 阶段**",
            f"- 状态：{'成功' if succeeded else '失败'}",
        ]
        project_dir = self._optional_text(raw_result.get("project_dir"))
        if project_dir:
            lines.append(f"- 项目目录：`{project_dir}`")
        config_path = self._optional_text(raw_result.get("config_path"))
        if config_path:
            lines.append(f"- 配置文件：`{config_path}`")

        if succeeded:
            operation = "新建" if raw_result.get("operation") == "created" else "更新"
            lines.extend(
                [
                    f"- 配置操作：{operation}",
                    f"- 任务 ID：`{raw_result.get('task_id', '未知')}`",
                    f"- Cron：`{raw_result.get('schedule', '未知')}`",
                    f"- 执行方式：{'有头模式' if raw_result.get('headed') else '无头模式'}，"
                    f"{'已启用' if raw_result.get('enabled') else '已停用'}",
                ]
            )
            locations = self._optional_string_list(raw_result.get("locations")) or []
            lines.append(
                "- 测试范围："
                + (
                    "、".join(f"`{location}`" for location in locations)
                    if locations
                    else "全部用例"
                )
            )
            log_file = self._optional_text(raw_result.get("log_file"))
            if log_file:
                lines.append(f"- Scheduler 日志：`{log_file}`")

        detail = self._optional_text(narrative) or self._optional_text(
            raw_result.get("message")
        )
        if detail:
            lines.append(f"- 说明：{detail}")
        return "\n".join(lines)

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

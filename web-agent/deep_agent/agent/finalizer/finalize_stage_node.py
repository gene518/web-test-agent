"""每个 Specialist 实际执行后恰好运行一次的阶段收尾节点。"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig

from deep_agent.agent.finalizer.finalizer_agent import FinalizerAgent
from deep_agent.agent.state import WorkflowState
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
from deep_agent.helpers.artifacts import (
    append_stage_summary,
    build_stage_summary,
    has_more_pipeline_stages,
)


logger = get_logger(__name__)
FinalizedStageName = Literal["plan", "generator", "healer", "scheduler"]


@dataclass(frozen=True, slots=True)
class FinalizeStageConfig:
    """声明一个工作流节点负责收尾的 Specialist。"""

    stage: FinalizedStageName
    display_name: str
    return_to_master: bool = True


PLAN_FINALIZE_CONFIG = FinalizeStageConfig("plan", "Plan Agent")
GENERATOR_FINALIZE_CONFIG = FinalizeStageConfig("generator", "Generator Agent")
HEALER_FINALIZE_CONFIG = FinalizeStageConfig("healer", "Healer Agent")
SCHEDULER_FINALIZE_CONFIG = FinalizeStageConfig(
    "scheduler", "Scheduler Agent", return_to_master=False
)


class FinalizeStageNode:
    """幂等地总结单个 Specialist 阶段并决定消息可见范围。"""

    def __init__(
        self,
        finalizer_agent: FinalizerAgent,
        stage_config: FinalizeStageConfig,
    ) -> None:
        self._finalizer_agent = finalizer_agent
        self._stage_config = stage_config

    async def execute(
        self, state: WorkflowState, config: RunnableConfig | None = None
    ) -> WorkflowState:
        node_name = f"finalize_{self._stage_config.stage}_stage_node"
        logger.info(
            "%s event=node_enter trace=%s state=%s",
            log_title("执行", "节点入参", node_name=node_name),
            build_trace_context(config, node_name=node_name, event_name="node_enter"),
            format_state_for_log(state),
        )

        stage_result = self._normalized_stage_result(state)
        finalization_key = self._resolve_finalization_key(state, stage_result)
        finalized_keys = self._normalized_finalized_keys(state)
        existing_summary = self._normalize_existing_summary(
            stage_result.get("stage_summary"),
            stage_result=stage_result,
            finalization_key=finalization_key,
        )

        if finalization_key in finalized_keys:
            return self._routing_delta(state)

        if existing_summary is not None:
            result = self._finalized_result(
                state=state,
                stage_result=stage_result,
                stage_summary=existing_summary,
                finalization_key=finalization_key,
                finalized_keys=finalized_keys,
                emit_summary=False,
            )
        else:
            stage_status = self._stage_status(stage_result)
            is_terminal = self._is_terminal(state, stage_status=stage_status)
            canonical_summary = self._build_canonical_summary(
                state=state,
                stage_result=stage_result,
                stage_status=stage_status,
                is_terminal=is_terminal,
            )
            final_summary = await self._finalizer_agent.finalize_stage(
                state=state,
                stage_name=self._stage_config.display_name,
                stage_result=stage_result,
                canonical_summary=canonical_summary,
                is_terminal=is_terminal,
                config=config,
            )
            final_summary = self._normalize_model_summary(
                final_summary,
                state=state,
                stage_status=stage_status,
                is_terminal=is_terminal,
                canonical_summary=canonical_summary,
            )
            stage_summary = {
                "artifact_id": self._artifact_id(stage_result),
                "stage": self._stage_config.stage,
                "status": stage_status,
                "text": final_summary,
                "finalization_key": finalization_key,
            }
            result = self._finalized_result(
                state=state,
                stage_result=stage_result,
                stage_summary=stage_summary,
                finalization_key=finalization_key,
                finalized_keys=finalized_keys,
                emit_summary=True,
            )

        logger.info(
            "%s event=node_exit trace=%s messages=%s",
            log_title("执行", "节点出参", node_name=node_name),
            build_trace_context(config, node_name=node_name, event_name="node_exit"),
            format_messages_for_log(result.get("messages", [])),
        )
        return result

    def _finalized_result(
        self,
        *,
        state: WorkflowState,
        stage_result: dict[str, Any],
        stage_summary: dict[str, Any],
        finalization_key: str,
        finalized_keys: list[str],
        emit_summary: bool,
    ) -> WorkflowState:
        stage_status = self._stage_status(stage_result)
        is_terminal = self._is_terminal(state, stage_status=stage_status)
        pending = append_stage_summary(dict(state), stage_summary)
        updated_stage_result = {
            **stage_result,
            "finalization_key": finalization_key,
            "stage_summary": stage_summary,
        }
        result: WorkflowState = {
            "stage_result": updated_stage_result,
            "finalization_key": finalization_key,
            "finalized_stage_keys": [*finalized_keys, finalization_key],
            "final_summary": str(stage_summary.get("text") or ""),
            **self._routing_delta(state),
        }

        if is_terminal:
            result.update(
                {
                    "completed_stage_summaries": pending,
                    "pending_stage_summaries": [],
                    "current_turn_artifact_ids": [],
                }
            )
        else:
            result["pending_stage_summaries"] = pending

        if not emit_summary:
            return result

        summary_message = build_display_summary_message(
            str(stage_summary.get("text") or ""),
            prefix=f"{self._stage_config.stage}-summary",
        )
        result["display_messages"] = [
            *extract_missing_display_messages(dict(state)),
            summary_message,
        ]
        result["messages"] = [summary_message] if is_terminal else []
        return result

    def _routing_delta(self, state: WorkflowState) -> WorkflowState:
        if self._stage_config.return_to_master:
            return {
                "pipeline_handoff": True,
                "return_to_master": False,
            }
        return {
            "pipeline_handoff": False,
            "return_to_master": False,
            "next_action": "end",
        }

    def _normalized_stage_result(self, state: WorkflowState) -> dict[str, Any]:
        stage_result = state.get("stage_result")
        if isinstance(stage_result, dict):
            return dict(stage_result)
        return {
            "agent_type": self._stage_config.stage,
            "display_name": self._stage_config.display_name,
            "status": "exception",
            "raw_result": {
                "status": "exception",
                "message": "阶段执行结束，但没有返回可识别的结构化结果。",
            },
        }

    def _resolve_finalization_key(
        self, state: WorkflowState, stage_result: dict[str, Any]
    ) -> str:
        for value in (
            stage_result.get("finalization_key"),
            state.get("finalization_key"),
        ):
            if isinstance(value, str) and value.strip():
                return value.strip()

        legacy_payload = {
            "stage": self._stage_config.stage,
            "pipeline_cursor": state.get("pipeline_cursor"),
            "artifact_id": self._artifact_id(stage_result),
            "raw_result": stage_result.get("raw_result"),
            "raw_messages": stage_result.get("raw_messages"),
        }
        serialized = json.dumps(
            legacy_payload,
            ensure_ascii=True,
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:20]
        return f"legacy:{self._stage_config.stage}:{digest}"

    def _normalized_finalized_keys(self, state: WorkflowState) -> list[str]:
        values = state.get("finalized_stage_keys")
        if not isinstance(values, list):
            return []
        return list(dict.fromkeys(value for value in values if isinstance(value, str)))

    def _normalize_existing_summary(
        self,
        value: Any,
        *,
        stage_result: dict[str, Any],
        finalization_key: str,
    ) -> dict[str, Any] | None:
        if isinstance(value, dict):
            text = str(value.get("text") or "").strip()
            if not text:
                return None
            return {
                **value,
                "stage": value.get("stage") or self._stage_config.stage,
                "status": value.get("status") or self._stage_status(stage_result),
                "finalization_key": value.get("finalization_key")
                or finalization_key,
            }
        if isinstance(value, str) and value.strip():
            return {
                "artifact_id": self._artifact_id(stage_result),
                "stage": self._stage_config.stage,
                "status": self._stage_status(stage_result),
                "text": value.strip(),
                "finalization_key": finalization_key,
            }
        return None

    def _build_canonical_summary(
        self,
        *,
        state: WorkflowState,
        stage_result: dict[str, Any],
        stage_status: str,
        is_terminal: bool,
    ) -> str:
        raw_result = stage_result.get("raw_result")
        normalized_raw_result = raw_result if isinstance(raw_result, dict) else {}
        stage = self._stage_config.stage
        if stage == "scheduler":
            summary = self._build_scheduler_summary(normalized_raw_result)
        else:
            artifact = stage_result.get("artifact")
            summary_entry = build_stage_summary(
                stage=stage,
                status=stage_status,
                artifact=artifact if isinstance(artifact, dict) else None,
                fallback_message=self._fallback_message(stage_result),
                include_follow_up=len(state.get("requested_pipeline", [])) <= 1,
            )
            summary = summary_entry["text"]

        requested_pipeline = state.get("requested_pipeline")
        if (
            is_terminal
            and stage_status == "success"
            and isinstance(requested_pipeline, list)
            and len(requested_pipeline) > 1
        ):
            summary = (
                f"{summary}\n\n**完成状态**\n"
                "- 当前请求已完成，无需补充信息。"
            )
        return summary

    def _build_scheduler_summary(self, raw_result: dict[str, Any]) -> str:
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
            locations = self._string_list(raw_result.get("locations"))
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
        detail = self._optional_text(raw_result.get("message"))
        if detail:
            lines.append(f"- 说明：{detail}")
        return "\n".join(lines)

    def _normalize_model_summary(
        self,
        value: Any,
        *,
        state: WorkflowState,
        stage_status: str,
        is_terminal: bool,
        canonical_summary: str,
    ) -> str:
        """对模型输出施加阶段链不变量，避免再次产生误导性补参提示。"""

        text = str(value or "").strip() or canonical_summary
        requested_pipeline = state.get("requested_pipeline")
        is_multi_stage = (
            isinstance(requested_pipeline, list) and len(requested_pipeline) > 1
        )
        if is_multi_stage:
            text = "\n".join(
                line
                for line in text.splitlines()
                if "下一阶段建议输入" not in line and "可选后续操作" not in line
            ).strip()
        if (
            is_multi_stage
            and is_terminal
            and stage_status == "success"
            and "当前请求已完成，无需补充信息" not in text
        ):
            text = f"{text}\n\n**完成状态**\n- 当前请求已完成，无需补充信息。"
        return text or canonical_summary

    def _is_terminal(self, state: WorkflowState, *, stage_status: str) -> bool:
        return not (
            self._stage_config.return_to_master
            and stage_status == "success"
            and has_more_pipeline_stages(dict(state))
        )

    def _stage_status(self, stage_result: dict[str, Any]) -> str:
        status = stage_result.get("status")
        if isinstance(status, str) and status:
            return status
        raw_result = stage_result.get("raw_result")
        if isinstance(raw_result, dict):
            raw_status = raw_result.get("status")
            if isinstance(raw_status, str) and raw_status:
                return raw_status
        return "success"

    def _artifact_id(self, stage_result: dict[str, Any]) -> str | None:
        artifact = stage_result.get("artifact")
        if not isinstance(artifact, dict):
            return None
        value = artifact.get("artifact_id")
        return str(value) if value else None

    def _fallback_message(self, stage_result: dict[str, Any]) -> str | None:
        raw_result = stage_result.get("raw_result")
        if isinstance(raw_result, dict):
            value = raw_result.get("message")
            if value is not None and str(value).strip():
                return str(value).strip()
        raw_messages = stage_result.get("raw_messages")
        if isinstance(raw_messages, list):
            for value in reversed(raw_messages):
                if str(value).strip():
                    return str(value).strip()
        return None

    def _optional_text(self, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    def _string_list(self, value: Any) -> list[str]:
        candidates = (
            value if isinstance(value, list) else ([] if value is None else [value])
        )
        return list(
            dict.fromkeys(
                normalized
                for item in candidates
                if (normalized := self._optional_text(item)) is not None
            )
        )

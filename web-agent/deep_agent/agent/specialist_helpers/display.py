"""Display and result-shaping helpers for specialist agents."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from deep_agent.agent.artifacts import build_stage_summary
from deep_agent.agent.state import WorkflowState
from deep_agent.core.display_message import (
    build_display_summary_message,
    build_runtime_message_result,
)
from deep_agent.core.runtime_logging import debug_max_chars


class SpecialistDisplayMixin:
    """Non-core display/result helpers shared by Plan, Generator, and Healer."""

    agent_type: str
    display_name: str
    _settings: Any

    def _build_stage_start_display_message(
        self,
        *,
        state: WorkflowState,
        execution_context: Any,
    ) -> AIMessage:
        """构造阶段开始前展示给用户的提示消息。"""

        stage_label = {
            "plan": "Plan",
            "generator": "Generator",
            "healer": "Healer",
        }.get(self.agent_type, self.display_name)
        stage_goal = {
            "plan": "准备探索页面并生成测试计划。",
            "generator": "准备读取测试计划并生成 Playwright 脚本。",
            "healer": "准备运行失败脚本并调试修复。",
        }.get(self.agent_type, "准备执行当前阶段任务。")
        extracted_params = state.get("extracted_params", {})
        requested_pipeline = state.get("requested_pipeline")
        pipeline_cursor = state.get("pipeline_cursor", 0)

        lines = [
            f"**{stage_label} 阶段开始**",
            "- 状态：进行中",
            f"- 现状：{stage_goal}",
        ]
        if isinstance(requested_pipeline, list) and requested_pipeline:
            if isinstance(pipeline_cursor, int) and pipeline_cursor >= 0:
                current_index = min(pipeline_cursor + 1, len(requested_pipeline))
                lines.append(f"- 阶段链进度：{current_index}/{len(requested_pipeline)}")
        if execution_context.workspace_dir is not None:
            lines.append(f"- 项目目录：`{execution_context.workspace_dir}`")

        project_name = self._display_optional_text(extracted_params.get("project_name"))
        if project_name:
            lines.append(f"- 工程名：`{project_name}`")

        if self.agent_type == "plan":
            url = self._display_optional_text(extracted_params.get("url"))
            if url:
                lines.append(f"- 目标 URL：`{url}`")
        elif self.agent_type == "generator":
            test_plan_files = self._display_string_list(extracted_params.get("test_plan_files"))
            if test_plan_files:
                lines.append(
                    "- 测试计划输入："
                    + "、".join(f"`{path}`" for path in test_plan_files)
                )
        elif self.agent_type == "healer":
            test_scripts = self._display_string_list(extracted_params.get("test_scripts"))
            if test_scripts:
                lines.append(
                    "- 调试脚本输入："
                    + "、".join(f"`{path}`" for path in test_scripts)
                )

        return build_display_summary_message(
            "\n".join(lines),
            prefix=f"{self.agent_type}-start",
        )

    def _extract_new_messages(self, result: dict[str, Any], existing_message_count: int) -> list[Any]:
        """从 Agent 输出中截取新增消息。"""

        all_messages = result.get("messages", [])
        if not isinstance(all_messages, list):
            raise RuntimeError(f"{self.display_name} 返回的 messages 结构非法。")

        new_messages = all_messages[existing_message_count:]
        if not new_messages:
            raise RuntimeError(f"{self.display_name} 未返回新的消息结果。")

        return new_messages

    def _display_optional_text(self, value: Any) -> str | None:
        """把展示用文本参数归一化为可判空字符串。"""

        if value is None:
            return None
        normalized_value = str(value).strip()
        return normalized_value or None

    def _display_string_list(self, value: Any) -> list[str]:
        """把展示用列表参数归一化为去重后的字符串数组。"""

        if isinstance(value, (list, tuple)):
            candidates = value
        elif value is None:
            candidates = []
        else:
            candidates = [value]

        normalized_values: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            normalized_item = self._display_optional_text(item)
            if normalized_item is None or normalized_item in seen:
                continue
            seen.add(normalized_item)
            normalized_values.append(normalized_item)
        return normalized_values

    def _resolve_stage_status(self, raw_result: dict[str, Any]) -> str:
        """解析当前阶段状态，默认把无显式错误视为成功。"""

        status = raw_result.get("status")
        if isinstance(status, str) and status:
            return status
        return "success"

    def _extract_stage_artifact(self, raw_result: dict[str, Any]) -> dict[str, Any] | None:
        """从阶段原始结果中取出结构化产物。"""

        artifact = raw_result.get("artifact")
        if isinstance(artifact, dict):
            return artifact
        return None

    def _workflow_managed_pipeline(self, state: WorkflowState) -> bool:
        """判断当前阶段是否由统一工作流 finalizer 管理用户回复。"""

        requested_pipeline = state.get("requested_pipeline")
        return isinstance(requested_pipeline, list) and bool(requested_pipeline)

    def _fallback_final_summary(self, raw_result: dict[str, Any]) -> str:
        """总结模型不可用时，退回到阶段结果中最接近最终回复的文本。"""

        status = raw_result.get("status")
        message = raw_result.get("message")
        if status and status != "success" and message:
            return str(message)

        messages = raw_result.get("messages", [])
        if isinstance(messages, list):
            for message in reversed(messages):
                if isinstance(message, BaseMessage):
                    text = self._message_to_text(message).strip()
                    if text:
                        return text

        if message:
            return str(message)

        status = raw_result.get("status")
        if status:
            return str(status)

        return "阶段已结束，但总结模型暂时不可用，未能生成最终总结。"

    def _build_runtime_exception_result(
        self,
        *,
        collector: Any,
        existing_messages: Sequence[Any],
        exc: Exception,
    ) -> WorkflowState:
        """保留已流出的运行时消息，同时把当前阶段标记为异常。"""

        message = self._build_unhandled_exception_message(exc)
        result: WorkflowState = build_runtime_message_result(
            collector=collector,
            existing_messages=existing_messages,
            fallback_message=message,
        )
        result["status"] = "exception"
        result["message"] = message
        return result

    def _build_stage_result(
        self,
        raw_result: dict[str, Any],
        *,
        stage_status: str,
        artifact: dict[str, Any] | None,
        stage_summary: dict[str, Any],
    ) -> dict[str, Any]:
        """构造写入 state 的内部阶段结果，不直接暴露给用户。"""

        return {
            "agent_type": self.agent_type,
            "display_name": self.display_name,
            "status": stage_status,
            "artifact": artifact,
            "stage_summary": stage_summary,
            "raw_messages": [self._message_to_text(message) for message in raw_result.get("messages", []) if isinstance(message, BaseMessage)],
            "raw_result": {
                key: value
                for key, value in raw_result.items()
                if key != "messages"
            },
        }

    def _format_stage_result_for_prompt(self, raw_result: dict[str, Any]) -> str:
        """把阶段原始结果压成总结提示词文本。"""

        lines: list[str] = []
        for key, value in raw_result.items():
            if key == "messages" and isinstance(value, list):
                message_lines = [f"{message.__class__.__name__}: {self._message_to_text(message)}" for message in value if isinstance(message, BaseMessage)]
                lines.append(f"messages:\n" + "\n".join(message_lines))
                continue
            lines.append(f"{key}: {value}")
        text = "\n".join(lines)
        max_chars = debug_max_chars(self._settings)
        if len(text) <= max_chars:
            return text
        return f"{text[:max_chars]}... [truncated]"

    def _latest_human_message_text(self, messages: Sequence[Any]) -> str:
        """返回最近一条用户消息文本。"""

        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                return self._message_to_text(message)
        return ""

    def _message_to_text(self, message: BaseMessage) -> str:
        """把消息内容转换成字符串。"""

        content = message.content
        return content if isinstance(content, str) else str(content)

    def _build_unhandled_exception_message(self, exc: Exception) -> str:
        """把漏网异常压缩成一条用户可读、不会打爆 graph 的消息。"""

        error_message = str(exc).strip() or exc.__class__.__name__
        if len(error_message) > 1200:
            error_message = f"{error_message[:1200]}... [truncated]"
        return (
            f"{self.display_name} 执行过程中遇到未处理异常，已停止当前阶段但不会中断整个工作流。"
            f"此前已完成的操作历史仍然保留。"
            f"错误类型：`{exc.__class__.__name__}`。"
            f"错误信息：{error_message}"
        )

    def _format_prompt_value(self, value: Any) -> str:
        """把运行时参数格式化成适合拼接进 prompt 的文本。"""

        if isinstance(value, list):
            if not value:
                return "[]"
            return ", ".join(str(item) for item in value)

        return str(value)

    def _build_stage_summary_for_result(
        self,
        *,
        stage_status: str,
        artifact: dict[str, Any] | None,
        fallback_message: str | None,
    ) -> dict[str, Any]:
        """构建当前阶段 summary，保留一层方法便于测试替换。"""

        return build_stage_summary(
            stage=self.agent_type,
            status=stage_status,
            artifact=artifact,
            fallback_message=fallback_message,
        )

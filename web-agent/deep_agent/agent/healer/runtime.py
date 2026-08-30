"""Healer 阶段的运行时事件流与产物抽取辅助。

这个模块的作用，是把 Healer 在 Deep Agent 执行阶段维护的运行期状态
（`test_run` 验证范围、浏览器关闭 fallback、调试产物抽取）从 `HealerAgent` 类里搬出来。
Agent 类只保留"阶段配置 + 参数校验 + workspace + prompt + 权限"静态职责，
运行时循环交给 `HealerRuntimeHelper`，与 Master 的 `nodes/*.py` 分层保持一致。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig

from deep_agent.helpers.artifacts import (
    extract_healer_artifact_from_snapshot_and_runs,
    snapshot_workspace_manifest_async,
)
from deep_agent.helpers.specialist_helpers import SpecialistExecutionContext
from deep_agent.helpers.specialist_helpers.browser_close import (
    is_expected_browser_close_error,
)
from deep_agent.agent.state import WorkflowState
from deep_agent.core.cancellation import is_langgraph_user_cancellation
from deep_agent.core.config import AppSettings
from deep_agent.core.display_message import (
    VisibleTranscriptCollector,
    build_runtime_message_result,
    emit_display_message_delta,
)
from deep_agent.core.runtime_logging import (
    log_debug_event,
    log_title,
    with_trace_context,
)
from deep_agent.tools.tool_invocation import tool_output_text


_PLAYWRIGHT_SUMMARY_RE = re.compile(
    r"\b(?P<count>\d+)\s+(?P<status>passed|failed|skipped|interrupted|flaky|did\s+not\s+run)\b",
    re.IGNORECASE,
)
_PLAYWRIGHT_LOCATION_SUFFIX_RE = re.compile(r":\d+(?::\d+)?$")
_PLAYWRIGHT_FATAL_MARKERS = (
    "no tests found",
    "error was not a part of any test",
    "errors were not a part of any test",
)


class HealerRuntimeHelper:
    """消费 Healer Agent 提供的 Deep Agent 实例，按事件流完成调试与产物整理。"""

    def __init__(self, *, agent: Any, settings: AppSettings) -> None:
        """保留 Agent 与应用配置引用，供事件流循环复用。"""

        self._agent = agent
        self._settings = settings

    async def run(
        self,
        *,
        specialist_agent: Any,
        state: WorkflowState,
        execution_context: SpecialistExecutionContext,
        config: RunnableConfig | None = None,
    ) -> WorkflowState:
        """消费 Healer 事件流，并校验 `test_run` 的完整结束状态和结果。

        Playwright MCP 会话由 `BaseSpecialistAgent.execute()` 在整个阶段结束时统一关闭，
        因此准备上下文或最终汇总失败时也能覆盖到同一套清理逻辑。
        """

        return await self._run_event_loop(
            specialist_agent=specialist_agent,
            state=state,
            execution_context=execution_context,
            config=config,
        )

    async def _run_event_loop(
        self,
        *,
        specialist_agent: Any,
        state: WorkflowState,
        execution_context: SpecialistExecutionContext,
        config: RunnableConfig | None = None,
    ) -> WorkflowState:
        """消费事件流并生成 Healer 阶段结果；MCP 生命周期由 Base Agent 托管。"""

        agent = self._agent
        existing_messages = state.get("messages", [])
        collector = VisibleTranscriptCollector()
        workspace_dir = execution_context.workspace_dir
        extracted_params = state.get("extracted_params", {})
        project_name = agent._normalized_project_name(
            extracted_params.get("project_name")
        ) or (workspace_dir.name if workspace_dir is not None else "unknown-project")
        if workspace_dir is not None:
            input_scripts = [
                path.relative_to(workspace_dir).as_posix()
                for path in agent._resolve_test_script_files(
                    workspace_dir=workspace_dir,
                    raw_test_scripts=extracted_params.get("test_scripts"),
                )
            ]
        else:
            input_scripts = agent._normalized_test_scripts(
                extracted_params.get("test_scripts")
            )
        before_manifest = await snapshot_workspace_manifest_async(workspace_dir)
        validation_runs: list[str] = []
        pending_validation_runs: dict[str, list[str]] = {}
        pending_legacy_validation_runs: list[list[str]] = []
        last_validation_succeeded = False
        validation_error: str | None = None
        stage_artifact: dict[str, Any] | None = None

        try:
            # Healer 使用事件流执行，是为了在调试过程中持续监听运行事件，
            # 确认脚本确实被执行、修复并完成验证，而不是只看模型最后一句总结。
            async for event in specialist_agent.astream_events(
                {"messages": existing_messages},
                config=with_trace_context(
                    config,
                    execution_context.trace_context,
                    recursion_limit=self._settings.specialist_recursion_limit,
                ),
                version="v2",
            ):
                agent.log_stream_event(event, execution_context.trace_context)
                emit_display_message_delta(collector.consume_event(event))
                (
                    last_validation_succeeded,
                    validation_error,
                ) = self._update_validation_state(
                    event=event,
                    workspace_dir=workspace_dir,
                    input_scripts=input_scripts,
                    pending_validation_runs=pending_validation_runs,
                    pending_legacy_validation_runs=pending_legacy_validation_runs,
                    successful_validation_runs=validation_runs,
                    last_validation_succeeded=last_validation_succeeded,
                    validation_error=validation_error,
                )
        except Exception as exc:  # noqa: BLE001
            if is_langgraph_user_cancellation(exc):
                raise
            if collector.final_output is not None and is_expected_browser_close_error(
                exc
            ):
                agent.log_browser_close_expected(execution_context.trace_context, exc)
                try:
                    stage_artifact = await self._build_stage_artifact(
                        workspace_dir=workspace_dir,
                        project_name=project_name,
                        input_scripts=input_scripts,
                        before_manifest=before_manifest,
                        validation_runs=validation_runs,
                        last_validation_succeeded=last_validation_succeeded,
                        validation_error=validation_error,
                        pending_validation_runs=pending_validation_runs,
                        pending_legacy_validation_runs=pending_legacy_validation_runs,
                    )
                except Exception as artifact_exc:  # noqa: BLE001
                    return agent._build_runtime_exception_result(
                        collector=collector,
                        existing_messages=existing_messages,
                        exc=artifact_exc,
                    )
                result = build_runtime_message_result(
                    collector=collector,
                    existing_messages=existing_messages,
                    fallback_message="脚本调试阶段已完成，浏览器已按预期关闭。",
                )
                result["artifact"] = stage_artifact
                return result
            return agent._build_runtime_exception_result(
                collector=collector,
                existing_messages=existing_messages,
                exc=exc,
            )

        log_debug_event(
            agent.log_get_logger(),
            self._settings,
            log_title("执行", "事件流"),
            "healer_final_output",
            agent.log_event_trace_context(
                execution_context.trace_context, "healer_final_output"
            ),
            final_output=collector.final_output,
            visible_messages=collector.messages,
        )

        try:
            stage_artifact = await self._build_stage_artifact(
                workspace_dir=workspace_dir,
                project_name=project_name,
                input_scripts=input_scripts,
                before_manifest=before_manifest,
                validation_runs=validation_runs,
                last_validation_succeeded=last_validation_succeeded,
                validation_error=validation_error,
                pending_validation_runs=pending_validation_runs,
                pending_legacy_validation_runs=pending_legacy_validation_runs,
            )
        except Exception as exc:  # noqa: BLE001
            return agent._build_runtime_exception_result(
                collector=collector,
                existing_messages=existing_messages,
                exc=exc,
            )

        result = build_runtime_message_result(
            collector=collector,
            existing_messages=existing_messages,
            fallback_message="脚本调试阶段已完成。",
        )
        result["artifact"] = stage_artifact
        return result

    def _update_validation_state(
        self,
        *,
        event: dict[str, Any],
        workspace_dir: Path | None,
        input_scripts: list[str],
        pending_validation_runs: dict[str, list[str]],
        pending_legacy_validation_runs: list[list[str]],
        successful_validation_runs: list[str],
        last_validation_succeeded: bool,
        validation_error: str | None,
    ) -> tuple[bool, str | None]:
        """只把完整结束且明确通过的 `test_run` 计入 Healer 验证结果。"""

        if event.get("name") != "test_run":
            return last_validation_succeeded, validation_error

        event_name = event.get("event")
        run_id = event.get("run_id")
        run_key = str(run_id) if run_id is not None else None
        if event_name == "on_tool_start":
            payload = event.get("data", {}).get("input")
            raw_locations = (
                self._agent._normalized_test_scripts(payload.get("locations"))
                if isinstance(payload, dict)
                else []
            )
            locations = (
                self._normalize_validation_locations(
                    workspace_dir=workspace_dir,
                    raw_locations=raw_locations,
                    input_scripts=input_scripts,
                )
                if raw_locations
                else list(input_scripts)
            )
            if run_key is None:
                pending_legacy_validation_runs.append(locations)
            else:
                pending_validation_runs[run_key] = locations
            return False, "`test_run` 尚未执行完成。"

        if event_name not in {"on_tool_end", "on_tool_error"}:
            return last_validation_succeeded, validation_error

        if run_key is not None:
            locations = pending_validation_runs.pop(run_key, None)
        elif pending_legacy_validation_runs:
            locations = pending_legacy_validation_runs.pop(0)
        else:
            locations = None
        if event_name == "on_tool_error":
            error = self._agent.log_truncate(event.get("data", {}).get("error"))
            return False, f"`test_run` 执行失败：{error}"

        if locations is None:
            return False, "`test_run` 未捕获到对应的开始事件，无法确认执行范围。"

        if not locations:
            return False, "`test_run` 未解析出有效执行范围，无法确认覆盖的脚本。"

        output = event.get("data", {}).get("output")
        if self._agent._tool_output_is_error(output):
            return False, f"`test_run` 返回失败结果：{self._agent.log_truncate(output)}"

        try:
            self._assert_test_run_passed(output)
        except RuntimeError as exc:
            return False, self._agent.log_truncate(str(exc))

        successful_validation_runs.clear()
        successful_validation_runs.extend(locations)
        return True, None

    def _normalize_validation_locations(
        self,
        *,
        workspace_dir: Path | None,
        raw_locations: list[str],
        input_scripts: list[str],
    ) -> list[str]:
        """把 `test_run` 的绝对路径或目录转换为待调试脚本的相对路径。"""

        if workspace_dir is None:
            return self._agent._normalized_test_scripts(raw_locations)

        normalized_locations: list[str] = []
        seen: set[str] = set()
        for raw_location in raw_locations:
            file_location = _PLAYWRIGHT_LOCATION_SUFFIX_RE.sub("", raw_location.strip())
            relative_location = self._agent._normalize_workspace_relative_path(
                workspace_dir,
                file_location,
            )
            if relative_location is None:
                continue

            location_path = (workspace_dir / relative_location).resolve()
            if location_path.is_dir():
                covered_scripts = []
                for input_script in input_scripts:
                    script_path = (workspace_dir / input_script).resolve()
                    try:
                        script_path.relative_to(location_path)
                    except ValueError:
                        continue
                    covered_scripts.append(input_script)
            else:
                covered_scripts = [relative_location]

            for covered_script in covered_scripts:
                if covered_script in seen:
                    continue
                seen.add(covered_script)
                normalized_locations.append(covered_script)
        return normalized_locations

    def _assert_test_run_passed(self, output: Any) -> None:
        """解析 Playwright list reporter 摘要，拒绝失败、跳过执行或含糊结果。"""

        output_text = tool_output_text(output)
        normalized_text = output_text.lower()
        summaries: dict[str, int] = {}
        for match in _PLAYWRIGHT_SUMMARY_RE.finditer(output_text):
            status = re.sub(r"\s+", " ", match.group("status").lower())
            summaries[status] = summaries.get(status, 0) + int(match.group("count"))

        failures = {
            status: count
            for status, count in summaries.items()
            if status != "passed" and count > 0
        }
        fatal_marker = next(
            (
                marker
                for marker in _PLAYWRIGHT_FATAL_MARKERS
                if marker in normalized_text
            ),
            None,
        )
        if failures or fatal_marker:
            detail = failures or fatal_marker
            raise RuntimeError(f"`test_run` 的 Playwright 执行结果未通过：{detail}。")

        if summaries.get("passed", 0) <= 0:
            raise RuntimeError(
                "`test_run` 已结束，但输出中没有可确认的 `N passed` Playwright 摘要。"
            )

    async def _build_stage_artifact(
        self,
        *,
        workspace_dir: Any,
        project_name: str,
        input_scripts: list[str],
        before_manifest: dict[str, Any],
        validation_runs: list[str],
        last_validation_succeeded: bool,
        validation_error: str | None,
        pending_validation_runs: dict[str, list[str]],
        pending_legacy_validation_runs: list[list[str]],
    ) -> dict[str, Any]:
        """在生成成功产物前统一确认最终一轮测试已经结束并通过。"""

        if workspace_dir is None:
            raise RuntimeError("Healer 阶段缺少工作目录，无法验证修复产物。")
        if pending_validation_runs or pending_legacy_validation_runs:
            raise RuntimeError(
                "Healer 阶段结束时仍有 `test_run` 未完成，不能判定调试成功。"
            )
        if not last_validation_succeeded:
            detail = f" 最近一次结果：{validation_error}" if validation_error else ""
            raise RuntimeError(
                "Healer Agent 必须以一次完整且通过的 `test_run` 作为最终验证。" + detail
            )
        missing_validation_runs = [
            script for script in input_scripts if script not in validation_runs
        ]
        if missing_validation_runs:
            missing_text = "、".join(
                f"`{script}`" for script in missing_validation_runs
            )
            raise RuntimeError(
                "Healer Agent 最终一次通过的 `test_run` 未覆盖全部待调试脚本。"
                f" 未覆盖：{missing_text}"
            )

        return extract_healer_artifact_from_snapshot_and_runs(
            before_manifest=before_manifest,
            after_manifest=await snapshot_workspace_manifest_async(workspace_dir),
            workspace_dir=workspace_dir,
            project_name=project_name,
            input_files=input_scripts,
            validation_runs=validation_runs,
        )


__all__ = ["HealerRuntimeHelper"]

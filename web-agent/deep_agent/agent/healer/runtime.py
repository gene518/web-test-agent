"""Healer 阶段的运行时事件流与产物抽取辅助。

这个模块的作用，是把 Healer 在 Deep Agent 执行阶段维护的运行期状态
（`test_run` 验证范围、浏览器关闭 fallback、调试产物抽取）从 `HealerAgent` 类里搬出来。
Agent 类只保留"阶段配置 + 参数校验 + workspace + prompt + 权限"静态职责，
运行时循环交给 `HealerRuntimeHelper`，与 Master 的 `nodes/*.py` 分层保持一致。
"""

from __future__ import annotations

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
from deep_agent.core.runtime_logging import log_debug_event, log_title, with_trace_context


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
        """使用事件流执行 Healer，并在收尾时兜底关闭 Playwright MCP 会话。

        主体循环放在 `_run_event_loop`；外层 `run(...)` 的 `finally` 负责关闭当前
        workspace 的 MCP 会话，保证浏览器和 Playwright 子进程被释放。
        """

        agent = self._agent
        workspace_dir = execution_context.workspace_dir
        try:
            return await self._run_event_loop(
                specialist_agent=specialist_agent,
                state=state,
                execution_context=execution_context,
                config=config,
            )
        finally:
            await agent._close_playwright_mcp_session(
                workspace_dir=workspace_dir,
                trace_context=execution_context.trace_context,
                reason="healer_runtime_finalize",
            )

    async def _run_event_loop(
        self,
        *,
        specialist_agent: Any,
        state: WorkflowState,
        execution_context: SpecialistExecutionContext,
        config: RunnableConfig | None = None,
    ) -> WorkflowState:
        """真正的事件流循环，关闭由外层 `run(...)` 的 finally 兜底。"""

        agent = self._agent
        existing_messages = state.get("messages", [])
        collector = VisibleTranscriptCollector()
        workspace_dir = execution_context.workspace_dir
        extracted_params = state.get("extracted_params", {})
        project_name = agent._normalized_project_name(extracted_params.get("project_name")) or (
            workspace_dir.name if workspace_dir is not None else "unknown-project"
        )
        input_scripts = agent._normalized_test_scripts(extracted_params.get("test_scripts"))
        before_manifest = await snapshot_workspace_manifest_async(workspace_dir)
        validation_runs: list[str] = []
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
                # 采集 `test_run` 的验证范围；后续阶段产物会据此标记
                # 当前轮真正调试过、回归过的脚本集合。
                if event.get("name") == "test_run" and event.get("event") == "on_tool_start":
                    payload = event.get("data", {}).get("input")
                    if isinstance(payload, dict):
                        validation_runs.extend(agent._normalized_test_scripts(payload.get("locations")))
        except Exception as exc:  # noqa: BLE001
            if is_langgraph_user_cancellation(exc):
                raise
            if collector.final_output is not None and is_expected_browser_close_error(exc):
                agent.log_browser_close_expected(execution_context.trace_context, exc)
                if workspace_dir is not None:
                    # 即便浏览器以预期方式关闭，这里仍要抽取一次调试产物，
                    # 把本轮修复后的脚本变化和验证运行结果固化下来。
                    stage_artifact = extract_healer_artifact_from_snapshot_and_runs(
                        before_manifest=before_manifest,
                        after_manifest=await snapshot_workspace_manifest_async(workspace_dir),
                        workspace_dir=workspace_dir,
                        project_name=project_name,
                        input_files=input_scripts,
                        validation_runs=validation_runs or input_scripts,
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
            agent.log_event_trace_context(execution_context.trace_context, "healer_final_output"),
            final_output=collector.final_output,
            visible_messages=collector.messages,
        )

        if workspace_dir is not None:
            # 正常结束时，这里统一汇总调试产物，明确哪些脚本被修改、
            # 哪些脚本被重新执行验证，作为"调试通过/收尾"的最终依据。
            stage_artifact = extract_healer_artifact_from_snapshot_and_runs(
                before_manifest=before_manifest,
                after_manifest=await snapshot_workspace_manifest_async(workspace_dir),
                workspace_dir=workspace_dir,
                project_name=project_name,
                input_files=input_scripts,
                validation_runs=validation_runs or input_scripts,
            )

        result = build_runtime_message_result(
            collector=collector,
            existing_messages=existing_messages,
            fallback_message="脚本调试阶段已完成。",
        )
        result["artifact"] = stage_artifact
        return result


__all__ = ["HealerRuntimeHelper"]

"""Plan 阶段的运行时事件流与产物校验辅助。

这个模块的作用，是把 Plan Agent 在 Deep Agent 执行阶段要维护的局部状态
（`planner_save_plan` 状态机、workspace 写文件跟踪、浏览器关闭 fallback、测试计划
落盘校验）从 `PlanAgent` 类里搬出来，让 Agent 文件只承担"阶段配置 + 运行时上下文"这
类静态职责。

对齐策略：Plan 的分层与 Master 保持一致，Agent 只是节点入口和配置源，运行时循环交给
`PlanRuntimeHelper`，便于测试时替换或复用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig

from deep_agent.agent.artifacts import (
    diff_workspace_manifest,
    extract_plan_artifact_from_planner_payload,
    extract_plan_artifact_from_saved_markdown,
    snapshot_workspace_manifest_async,
)
from deep_agent.agent.specialist_helpers import SpecialistExecutionContext
from deep_agent.agent.specialist_helpers.browser_close import (
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


class PlanRuntimeHelper:
    """负责 Plan 单次执行过程中的事件流监听与产物收尾。

    它不会自己拉起 Deep Agent，只消费 Agent 类提供的实例，然后按事件流维护
    `planner_save_plan` 状态、workspace 写文件集合、最终测试计划文件落盘判定。
    """

    def __init__(self, *, agent: Any, settings: AppSettings) -> None:
        """记录执行时复用的 Agent 实例和应用配置。

        Args:
            agent: `PlanAgent` 实例。Helper 会通过它访问 mixin 提供的日志、写文件
                追踪、异常兜底等基础能力，避免二次实现。
            settings: 当前应用配置，用于控制调试日志与递归上限。
        """

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
        """使用事件流执行 Plan，并强制校验 `planner_save_plan`。

        该方法只做三件事：
        1. 调用内部 `_run_event_loop` 消费事件流并产出阶段结果；
        2. 无论成功或失败，都在 `finally` 里关闭本轮 Playwright MCP 会话，
           避免 Chromium 子进程驻留；
        3. 把内部结果原样返回给外层 Agent。
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
                reason="plan_runtime_finalize",
            )

    async def _run_event_loop(
        self,
        *,
        specialist_agent: Any,
        state: WorkflowState,
        execution_context: SpecialistExecutionContext,
        config: RunnableConfig | None = None,
    ) -> WorkflowState:
        """真正的事件流循环，主动关闭由外层 `run(...)` 在 finally 中兜底。"""

        agent = self._agent
        existing_messages = state.get("messages", [])
        # 维护 `planner_save_succeeded / planner_save_error`，把"是否真正落盘成功"
        # 从自然语言回复中剥离出来，改为基于工具事件做硬判断。
        collector = VisibleTranscriptCollector()
        planner_save_succeeded = False
        planner_save_error: str | None = None
        planner_save_payload: dict[str, Any] | None = None
        stage_artifact: dict[str, Any] | None = None
        pending_workspace_write_paths: list[str] = []
        successful_workspace_write_paths: set[str] = set()
        extracted_params = state.get("extracted_params", {})
        workspace_dir = execution_context.workspace_dir
        project_name = agent._normalized_project_name(extracted_params.get("project_name")) or (
            workspace_dir.name if workspace_dir is not None else "unknown-project"
        )
        input_plan_files = agent._normalized_test_plan_files(extracted_params.get("test_plan_files"))
        before_manifest = await snapshot_workspace_manifest_async(workspace_dir)

        try:
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
                agent._collect_workspace_write_start(
                    event=event,
                    workspace_dir=workspace_dir,
                    pending_write_paths=pending_workspace_write_paths,
                )
                # 监听 `planner_save_plan`：它是"写用例并真正落盘"的唯一硬完成信号，
                # 不能只看模型自然语言里说自己已经完成。
                if event.get("name") == "planner_save_plan" and event.get("event") == "on_tool_start":
                    payload = event.get("data", {}).get("input")
                    if isinstance(payload, dict):
                        planner_save_payload = payload
                planner_save_succeeded, planner_save_error, stage_artifact = self._update_planner_save_state(
                    planner_save_succeeded,
                    planner_save_error,
                    stage_artifact,
                    planner_save_payload,
                    workspace_dir,
                    project_name,
                    input_plan_files,
                    event,
                )
                agent._collect_workspace_write_result(
                    event=event,
                    pending_write_paths=pending_workspace_write_paths,
                    successful_write_paths=successful_workspace_write_paths,
                )
                self._log_planner_save_state(event, planner_save_succeeded, planner_save_error, execution_context.trace_context)
                if planner_save_succeeded:
                    # 一旦确认测试计划已保存成功，就立刻结束本阶段，
                    # 避免后续无关输出覆盖"写用例已完成"的稳定结果。
                    result = build_runtime_message_result(
                        collector=collector,
                        existing_messages=existing_messages,
                        fallback_message="测试计划已保存。",
                    )
                    result["artifact"] = stage_artifact
                    return result
        except Exception as exc:  # noqa: BLE001
            if is_langgraph_user_cancellation(exc):
                raise
            if is_expected_browser_close_error(exc):
                fallback_artifact = stage_artifact
                fallback_error = planner_save_error
                if fallback_artifact is None and workspace_dir is not None:
                    try:
                        fallback_artifact = await self._build_fallback_plan_artifact(
                            workspace_dir=workspace_dir,
                            project_name=project_name,
                            input_plan_files=input_plan_files,
                            successful_workspace_write_paths=successful_workspace_write_paths,
                            before_manifest=before_manifest,
                        )
                    except Exception as artifact_exc:  # noqa: BLE001
                        fallback_error = agent.log_truncate(str(artifact_exc))
                if planner_save_succeeded or fallback_artifact is not None:
                    # 浏览器关闭后的预期异常不应打断"写用例成功"；
                    # 只要计划文件已经落盘，就把它当作正常收尾。
                    agent.log_browser_close_expected(execution_context.trace_context, exc)
                    result = build_runtime_message_result(
                        collector=collector,
                        existing_messages=existing_messages,
                        fallback_message="测试计划已保存，浏览器已按预期关闭。",
                    )
                    result["artifact"] = fallback_artifact
                    return result
                if fallback_error:
                    exc = RuntimeError(f"{exc} 最近一次文件落盘校验失败：{fallback_error}")
            return agent._build_runtime_exception_result(
                collector=collector,
                existing_messages=existing_messages,
                exc=exc,
            )

        log_debug_event(
            agent.log_get_logger(),
            self._settings,
            log_title("执行", "事件流"),
            "plan_final_output",
            agent.log_event_trace_context(execution_context.trace_context, "plan_final_output"),
            planner_save_succeeded=planner_save_succeeded,
            planner_save_error=planner_save_error,
            final_output=collector.final_output,
            visible_messages=collector.messages,
        )

        if not planner_save_succeeded:
            if workspace_dir is not None:
                try:
                    stage_artifact = await self._build_fallback_plan_artifact(
                        workspace_dir=workspace_dir,
                        project_name=project_name,
                        input_plan_files=input_plan_files,
                        successful_workspace_write_paths=successful_workspace_write_paths,
                        before_manifest=before_manifest,
                    )
                except Exception as exc:  # noqa: BLE001
                    error_suffix = f" 最近一次错误：{planner_save_error}" if planner_save_error else ""
                    return agent._build_runtime_exception_result(
                        collector=collector,
                        existing_messages=existing_messages,
                        exc=RuntimeError(
                            "Plan Agent 未成功通过 `planner_save_plan` 或最终文件落盘保存有效测试计划。"
                            f"{error_suffix} 文件落盘校验失败：{agent.log_truncate(str(exc))}"
                        ),
                    )
            else:
                error_suffix = f" 最近一次错误：{planner_save_error}" if planner_save_error else ""
                return agent._build_runtime_exception_result(
                    collector=collector,
                    existing_messages=existing_messages,
                    exc=RuntimeError(f"Plan Agent 未成功调用 `planner_save_plan` 保存用例。{error_suffix}"),
                )

        result = build_runtime_message_result(
            collector=collector,
            existing_messages=existing_messages,
            fallback_message="测试计划已保存。",
        )
        result["artifact"] = stage_artifact
        return result

    def _update_planner_save_state(
        self,
        planner_save_succeeded: bool,
        planner_save_error: str | None,
        current_artifact: dict[str, Any] | None,
        planner_save_payload: dict[str, Any] | None,
        workspace_dir: Path | None,
        project_name: str,
        input_plan_files: list[str],
        event: dict[str, Any],
    ) -> tuple[bool, str | None, dict[str, Any] | None]:
        """根据工具事件更新 `planner_save_plan` 的成功状态。

        这个状态机存在的目的，是把 Plan 的完成标准从"模型口头说完成了"收紧成
        "保存计划工具实际成功执行了"。
        """

        agent = self._agent
        if event.get("name") != "planner_save_plan":
            return planner_save_succeeded, planner_save_error, current_artifact

        # 这里只关注 `planner_save_plan`，其他工具无论成功失败都不改变最终完成判定。
        if event.get("event") == "on_tool_error":
            return False, agent.log_truncate(event.get("data", {}).get("error")), current_artifact

        if event.get("event") != "on_tool_end":
            return planner_save_succeeded, planner_save_error, current_artifact

        output = event.get("data", {}).get("output")
        if agent._tool_output_is_error(output):
            return False, agent.log_truncate(output), current_artifact

        if planner_save_payload is None:
            return False, "`planner_save_plan` 未捕获到输入 payload，无法提取计划产物。", current_artifact
        if workspace_dir is None:
            return False, "Plan 阶段缺少工作目录，无法验证保存产物。", current_artifact

        try:
            artifact = extract_plan_artifact_from_planner_payload(
                payload=planner_save_payload,
                project_dir=workspace_dir,
                project_name=project_name,
                input_files=input_plan_files,
            )
        except Exception as exc:  # noqa: BLE001
            return False, agent.log_truncate(str(exc)), current_artifact

        return True, None, artifact

    def _log_planner_save_state(
        self,
        event: dict[str, Any],
        planner_save_succeeded: bool,
        planner_save_error: str | None,
        trace_context: dict[str, Any],
    ) -> None:
        """记录 `planner_save_plan` 的成功/失败状态，方便按 session grep。"""

        if event.get("name") != "planner_save_plan" or event.get("event") not in {"on_tool_end", "on_tool_error"}:
            return

        status = "success" if planner_save_succeeded else "error"
        self._agent.log_tool_state(
            trace_context=trace_context,
            event_name="planner_save_plan",
            status=status,
            error=planner_save_error,
        )

    async def _build_fallback_plan_artifact(
        self,
        *,
        workspace_dir: Path,
        project_name: str,
        input_plan_files: list[str],
        successful_workspace_write_paths: set[str],
        before_manifest: dict[str, Any],
    ) -> dict[str, Any]:
        """在未观测到 `planner_save_plan` 成功时，用最终落盘的 Markdown 回填 Plan 产物。"""

        after_manifest = await snapshot_workspace_manifest_async(workspace_dir)
        diff = diff_workspace_manifest(before_manifest, after_manifest)
        candidate_plan_files = self._candidate_plan_files_from_workspace(
            workspace_dir=workspace_dir,
            touched_paths=[
                *diff["added"],
                *diff["modified"],
                *sorted(successful_workspace_write_paths),
            ],
        )
        if not candidate_plan_files:
            raise RuntimeError("节点结束时未识别到新建或更新的规范测试计划 Markdown。")
        if len(candidate_plan_files) > 1:
            candidate_text = "、".join(f"`{path}`" for path in candidate_plan_files)
            raise RuntimeError(f"节点结束时识别到多个候选测试计划，无法唯一确定：{candidate_text}")

        return extract_plan_artifact_from_saved_markdown(
            plan_file=candidate_plan_files[0],
            project_dir=workspace_dir,
            project_name=project_name,
            input_files=input_plan_files,
        )

    def _candidate_plan_files_from_workspace(
        self,
        *,
        workspace_dir: Path,
        touched_paths: list[str],
    ) -> list[str]:
        """从本轮新增或更新的文件中筛选规范测试计划 Markdown。"""

        agent = self._agent
        candidate_files: list[str] = []
        seen: set[str] = set()
        for touched_path in touched_paths:
            normalized_path = agent._normalize_workspace_relative_path(workspace_dir, touched_path)
            if not normalized_path or normalized_path in seen:
                continue
            path = Path(normalized_path)
            if len(path.parts) != 3 or path.parts[0] != "test_case":
                continue
            if not path.parts[1].startswith("aaaplanning_") or not path.name.startswith("aaa_") or path.suffix != ".md":
                continue
            if not (workspace_dir / normalized_path).is_file():
                continue
            seen.add(normalized_path)
            candidate_files.append(normalized_path)
        return candidate_files


__all__ = ["PlanRuntimeHelper"]

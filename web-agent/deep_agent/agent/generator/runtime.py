"""Generator 阶段运行期辅助逻辑。

本模块把 Generator Agent 在 Deep Agent 执行过程里要维护的运行期状态
（`generator_write_test` 状态机、workspace 写文件跟踪、浏览器关闭 fallback、脚本落盘
校验）从 `GeneratorAgent` 类中抽出来。Agent 类只保留"阶段配置 + 参数校验 + prompt +
权限"这类静态职责；事件流循环由 `GeneratorRuntimeHelper.run(...)` 承担。
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig

from deep_agent.helpers.artifacts import (
    diff_workspace_manifest,
    extract_expected_generator_test_scripts_from_plan_files,
    extract_generator_artifact_from_writes_and_snapshot,
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


class GeneratorRuntimeHelper:
    """Generator 阶段的运行期助手。

    同时承担两类职责：
    - `run(...)`：消费 Agent 提供的 Deep Agent 实例，按事件流完成脚本生成与产物校验。
    - `update_generator_write_state` / `build_stage_artifact` / `is_expected_browser_close_error`
      等纯函数：独立于事件循环，方便单元测试单独验证。

    构造时可以选择两种方式：
    - `GeneratorRuntimeHelper(normalize_files=..., log_truncate=..., tool_output_is_error=...)`:
      纯粹用回调注入的模式，兼容旧测试。
    - `GeneratorRuntimeHelper.from_agent(agent=..., settings=...)`:
      由 Agent 复用其 mixin 能力，适合在 Agent `_run_deep_agent` 中直接调用 `run(...)`。
    """

    @classmethod
    def from_agent(cls, *, agent: Any, settings: AppSettings) -> "GeneratorRuntimeHelper":
        """基于 Agent 的 mixin 能力构造 helper，省去手动注入回调。"""

        helper = cls(
            normalize_files=agent._normalized_test_plan_files,
            log_truncate=agent.log_truncate,
            tool_output_is_error=agent._tool_output_is_error,
        )
        helper._agent = agent
        helper._settings = settings
        return helper

    def __init__(
        self,
        *,
        normalize_files: Callable[[Any], list[str]],
        log_truncate: Callable[[Any], str],
        tool_output_is_error: Callable[[Any], bool],
    ) -> None:
        self._normalize_files = normalize_files
        self._log_truncate = log_truncate
        self._tool_output_is_error = tool_output_is_error
        # 下面两个字段只在 `from_agent` 构造的 `run(...)` 路径下使用；
        # 纯函数式用法（旧测试注入回调）不会访问，因此允许为 None。
        self._agent: Any | None = None
        self._settings: AppSettings | None = None

    async def run(
        self,
        *,
        specialist_agent: Any,
        state: WorkflowState,
        execution_context: SpecialistExecutionContext,
        config: RunnableConfig | None = None,
    ) -> WorkflowState:
        """使用事件流执行 Generator，并确保期望脚本全部落盘。

        主体事件循环放在 `_run_event_loop` 里；外层 `run(...)` 在 `finally` 中兜底
        关闭当前 Playwright MCP 会话，避免 Chromium 子进程残留，
        与 Plan / Healer runtime 的收尾策略保持一致。
        """

        if self._agent is None or self._settings is None:
            raise RuntimeError(
                "GeneratorRuntimeHelper.run 只能在 `from_agent(...)` 构造的实例上调用。"
            )

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
                reason="generator_runtime_finalize",
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
        settings = self._settings
        existing_messages = state.get("messages", [])
        collector = VisibleTranscriptCollector()
        generator_write_succeeded = False
        generator_write_error: str | None = None
        pending_write_payloads: list[dict[str, str]] = []
        successful_write_payloads: list[dict[str, str]] = []
        pending_workspace_write_paths: list[str] = []
        successful_workspace_write_paths: set[str] = set()
        workspace_dir = execution_context.workspace_dir
        extracted_params = state.get("extracted_params", {})
        project_name = agent._normalized_project_name(extracted_params.get("project_name")) or (
            workspace_dir.name if workspace_dir is not None else "unknown-project"
        )
        input_plan_files = agent._normalized_test_plan_files(extracted_params.get("test_plan_files"))
        expected_test_scripts: list[str] = []
        if workspace_dir is not None:
            _, _, expected_test_scripts = agent._resolve_generation_targets(
                workspace_dir=workspace_dir,
                extracted_params=extracted_params,
            )
        before_manifest = await snapshot_workspace_manifest_async(workspace_dir)
        stage_artifact: dict[str, Any] | None = None

        try:
            # Generator 使用事件流执行，是为了边生成边监听关键写文件事件，
            # 确认"写脚本"不是口头完成，而是目标脚本真正写到了工程目录。
            async for event in specialist_agent.astream_events(
                {"messages": existing_messages},
                config=with_trace_context(
                    config,
                    execution_context.trace_context,
                    recursion_limit=settings.specialist_recursion_limit,
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
                # 监听 `generator_write_test`：它是当前阶段最直接的写脚本信号，
                # 后续还会结合工作区快照校验预期脚本是否全部落盘。
                if event.get("name") == "generator_write_test" and event.get("event") == "on_tool_start":
                    payload = event.get("data", {}).get("input")
                    if isinstance(payload, dict):
                        file_name = agent._normalized_runtime_text(payload.get("fileName"))
                        code = payload.get("code")
                        if file_name and isinstance(code, str):
                            pending_write_payloads.append({"fileName": file_name, "code": code})
                generator_write_succeeded, generator_write_error = self.update_generator_write_state(
                    generator_write_succeeded,
                    generator_write_error,
                    pending_write_payloads,
                    successful_write_payloads,
                    event,
                )
                agent._collect_workspace_write_result(
                    event=event,
                    pending_write_paths=pending_workspace_write_paths,
                    successful_write_paths=successful_workspace_write_paths,
                )
                self.log_generator_write_state(
                    agent=agent,
                    event=event,
                    generator_write_succeeded=generator_write_succeeded,
                    generator_write_error=generator_write_error,
                    trace_context=execution_context.trace_context,
                )
        except Exception as exc:  # noqa: BLE001
            if is_langgraph_user_cancellation(exc):
                raise
            if self.is_expected_browser_close_error(exc):
                agent.log_browser_close_expected(execution_context.trace_context, exc)
                if workspace_dir is not None:
                    try:
                        stage_artifact = await self.build_stage_artifact(
                            successful_write_payloads=successful_write_payloads,
                            successful_workspace_write_paths=successful_workspace_write_paths,
                            before_manifest=before_manifest,
                            workspace_dir=workspace_dir,
                            project_name=project_name,
                            input_files=input_plan_files,
                            expected_test_scripts=expected_test_scripts,
                        )
                    except Exception as artifact_exc:  # noqa: BLE001
                        return agent._build_runtime_exception_result(
                            collector=collector,
                            existing_messages=existing_messages,
                            exc=artifact_exc,
                        )
                # 浏览器关闭后的预期异常不应中断"写脚本成功"；
                # 只要目标脚本已经生成并通过落盘校验，就按正常完成返回。
                result = build_runtime_message_result(
                    collector=collector,
                    existing_messages=existing_messages,
                    fallback_message="测试脚本已生成，浏览器已按预期关闭。",
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
            settings,
            log_title("执行", "事件流"),
            "generator_final_output",
            agent.log_event_trace_context(execution_context.trace_context, "generator_final_output"),
            generator_write_succeeded=generator_write_succeeded,
            generator_write_error=generator_write_error,
            final_output=collector.final_output,
            visible_messages=collector.messages,
        )

        if workspace_dir is not None:
            try:
                # 最终校验预期脚本是否全部落盘；
                # 只有 `expected_test_scripts` 对应文件完整写入后，本阶段才算真正完成。
                stage_artifact = await self.build_stage_artifact(
                    successful_write_payloads=successful_write_payloads,
                    successful_workspace_write_paths=successful_workspace_write_paths,
                    before_manifest=before_manifest,
                    workspace_dir=workspace_dir,
                    project_name=project_name,
                    input_files=input_plan_files,
                    expected_test_scripts=expected_test_scripts,
                )
            except Exception as exc:  # noqa: BLE001
                error_suffix = f" 最近一次错误：{generator_write_error}" if generator_write_error else ""
                return agent._build_runtime_exception_result(
                    collector=collector,
                    existing_messages=existing_messages,
                    exc=RuntimeError(
                        f"Generator Agent 未成功生成有效脚本。{error_suffix} 文件落盘校验失败：{agent.log_truncate(str(exc))}"
                    ),
                )

        result = build_runtime_message_result(
            collector=collector,
            existing_messages=existing_messages,
            fallback_message="测试脚本生成阶段已完成。",
        )
        result["artifact"] = stage_artifact
        return result

    def update_generator_write_state(
        self,
        generator_write_succeeded: bool,
        generator_write_error: str | None,
        pending_write_payloads: list[dict[str, str]],
        successful_write_payloads: list[dict[str, str]],
        event: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """根据工具事件更新 `generator_write_test` 的执行状态。"""

        if event.get("name") != "generator_write_test":
            return generator_write_succeeded, generator_write_error

        if event.get("event") == "on_tool_start":
            return generator_write_succeeded, generator_write_error

        if event.get("event") == "on_tool_error":
            if pending_write_payloads:
                pending_write_payloads.pop(0)
            return False, self._log_truncate(event.get("data", {}).get("error"))

        if event.get("event") != "on_tool_end":
            return generator_write_succeeded, generator_write_error

        pending_payload = pending_write_payloads.pop(0) if pending_write_payloads else None
        output = event.get("data", {}).get("output")
        if self._tool_output_is_error(output):
            return False, self._log_truncate(output)

        if pending_payload is None:
            return False, "`generator_write_test` 未捕获到输入 payload，无法确认写入文件。"

        successful_write_payloads.append(pending_payload)
        return True, None

    async def build_stage_artifact(
        self,
        *,
        successful_write_payloads: list[dict[str, str]],
        successful_workspace_write_paths: set[str],
        before_manifest: dict[str, Any],
        workspace_dir: Path,
        project_name: str,
        input_files: list[str],
        expected_test_scripts: list[str],
    ) -> dict[str, Any]:
        """构建 Generator 产物，并在必要时回退到最终落盘文件做校验。"""

        finalized_input_files = await asyncio.to_thread(
            self._finalize_generated_plan_files,
            workspace_dir,
            input_files,
        )
        after_manifest = await snapshot_workspace_manifest_async(workspace_dir)

        payload_error: Exception | None = None
        try:
            self._assert_expected_test_scripts_written(
                expected_test_scripts=expected_test_scripts,
                actual_test_scripts=[payload.get("fileName", "") for payload in successful_write_payloads],
            )
            return extract_generator_artifact_from_writes_and_snapshot(
                writes=successful_write_payloads,
                before_manifest=before_manifest,
                after_manifest=after_manifest,
                workspace_dir=workspace_dir,
                project_name=project_name,
                input_files=finalized_input_files,
            )
        except Exception as exc:  # noqa: BLE001
            payload_error = exc

        fallback_writes = self._build_workspace_generator_writes(
            workspace_dir=workspace_dir,
            expected_test_scripts=expected_test_scripts,
            successful_workspace_write_paths=successful_workspace_write_paths,
            before_manifest=before_manifest,
            after_manifest=after_manifest,
        )
        try:
            return extract_generator_artifact_from_writes_and_snapshot(
                writes=fallback_writes,
                before_manifest=before_manifest,
                after_manifest=after_manifest,
                workspace_dir=workspace_dir,
                project_name=project_name,
                input_files=finalized_input_files,
            )
        except Exception as exc:  # noqa: BLE001
            if payload_error is None:
                raise
            raise RuntimeError(f"{payload_error} 回退到最终文件落盘校验后仍失败：{exc}") from exc

    def log_generator_write_state(
        self,
        *,
        agent: Any,
        event: dict[str, Any],
        generator_write_succeeded: bool,
        generator_write_error: str | None,
        trace_context: dict[str, Any],
    ) -> None:
        """记录 `generator_write_test` 的成功/失败状态，方便按 session grep。"""

        if event.get("name") != "generator_write_test" or event.get("event") not in {"on_tool_end", "on_tool_error"}:
            return

        status = "success" if generator_write_succeeded else "error"
        agent.log_tool_state(
            trace_context=trace_context,
            event_name="generator_write_test",
            status=status,
            error=generator_write_error,
        )

    @staticmethod
    def is_expected_browser_close_error(exc: Exception) -> bool:
        """判断异常是否为关闭浏览器后的预期错误。"""

        return is_expected_browser_close_error(exc)

    def _finalize_generated_plan_files(
        self,
        workspace_dir: Path,
        input_files: list[str],
    ) -> list[str]:
        """把 aaaplanning 计划文件迁移到正式脚本目录，并清理旧 planning 目录。"""

        finalized_files: list[str] = []
        for input_file in input_files:
            relative_plan_file = self._resolve_relative_plan_file(workspace_dir, input_file)
            finalized_files.append(
                self._finalize_single_generated_plan_file(
                    workspace_dir=workspace_dir,
                    relative_plan_file=relative_plan_file,
                )
            )
        return self._dedupe_strings(finalized_files)

    def _resolve_relative_plan_file(self, workspace_dir: Path, input_file: str) -> Path:
        candidate = Path(input_file).expanduser()
        if not candidate.is_absolute():
            candidate = workspace_dir / candidate

        resolved_workspace = workspace_dir.resolve()
        resolved_candidate = candidate.resolve()
        try:
            return resolved_candidate.relative_to(resolved_workspace)
        except ValueError as exc:
            raise RuntimeError(
                f"Generator 阶段计划文件 `{resolved_candidate}` 不在项目目录 `{resolved_workspace}` 下，无法迁移。"
            ) from exc

    def _finalize_single_generated_plan_file(
        self,
        *,
        workspace_dir: Path,
        relative_plan_file: Path,
    ) -> str:
        parts = relative_plan_file.parts
        if len(parts) != 3 or parts[0] != "test_case" or not parts[1].startswith("aaaplanning_"):
            return relative_plan_file.as_posix()

        plan_name = parts[1].removeprefix("aaaplanning_")
        expected_plan_file_name = f"aaa_{plan_name}.md"
        if not plan_name or parts[2] != expected_plan_file_name:
            return relative_plan_file.as_posix()

        source_file = workspace_dir / relative_plan_file
        target_relative_file = Path("test_case") / plan_name / expected_plan_file_name
        target_file = workspace_dir / target_relative_file
        if source_file.is_file():
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target_file)
            shutil.rmtree(source_file.parent)
            return target_relative_file.as_posix()

        if target_file.is_file():
            return target_relative_file.as_posix()

        raise RuntimeError(
            f"Generator 阶段无法迁移测试计划：源文件 `{relative_plan_file.as_posix()}` 和目标文件 "
            f"`{target_relative_file.as_posix()}` 都不存在。"
        )

    def _dedupe_strings(self, values: list[str]) -> list[str]:
        deduplicated: list[str] = []
        seen: set[str] = set()
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            deduplicated.append(value)
        return deduplicated

    def _assert_expected_test_scripts_written(
        self,
        *,
        expected_test_scripts: list[str],
        actual_test_scripts: list[str],
    ) -> None:
        """确保本次成功写出的脚本集合完整覆盖计划要求。"""

        expected_files = self._normalize_files(expected_test_scripts)
        actual_files = self._normalize_files(actual_test_scripts)
        missing_files = [path for path in expected_files if path not in actual_files]
        if not missing_files:
            return

        missing_text = "、".join(f"`{path}`" for path in missing_files)
        raise RuntimeError(
            "Generator Agent 未完成测试计划要求的全部脚本生成。"
            f" 期望 {len(expected_files)} 个脚本，实际成功写出 {len(actual_files)} 个。"
            f" 缺失脚本：{missing_text}"
        )

    def _build_workspace_generator_writes(
        self,
        *,
        workspace_dir: Path,
        expected_test_scripts: list[str],
        successful_workspace_write_paths: set[str],
        before_manifest: dict[str, Any],
        after_manifest: dict[str, Any],
    ) -> list[dict[str, str]]:
        """基于最终 workspace 文件状态构建 Generator 产物输入。"""

        diff = diff_workspace_manifest(before_manifest, after_manifest)
        touched_paths = set(
            self._normalize_files(
                [
                    *diff["added"],
                    *diff["modified"],
                    *sorted(successful_workspace_write_paths),
                ]
            )
        )
        expected_files = self._normalize_files(expected_test_scripts)
        fallback_writes: list[dict[str, str]] = []
        missing_files: list[str] = []
        untouched_files: list[str] = []
        for relative_file in expected_files:
            file_path = workspace_dir / relative_file
            if not file_path.is_file():
                missing_files.append(relative_file)
                continue
            if relative_file not in touched_paths:
                untouched_files.append(relative_file)
                continue
            fallback_writes.append(
                {
                    "fileName": relative_file,
                    "code": file_path.read_text(encoding="utf-8"),
                }
            )

        if not missing_files and not untouched_files:
            return fallback_writes

        detail_parts: list[str] = []
        if missing_files:
            detail_parts.append("缺失脚本：" + "、".join(f"`{path}`" for path in missing_files))
        if untouched_files:
            detail_parts.append("未观测到本轮新增或更新：" + "、".join(f"`{path}`" for path in untouched_files))
        raise RuntimeError("Generator Agent 未在节点结束前完成全部脚本落盘。 " + "；".join(detail_parts))

"""Generator 阶段专项智能体。

Generator 阶段的目标，是基于已经确认过的测试计划稳定产出脚本，因此这里只保留
“脚本生成”所需的 prompt 和工具边界，避免它重新承担页面规划或失败修复职责。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig

from deep_agent.agent.artifacts import (
    extract_expected_generator_test_scripts_from_plan_files,
    extract_generator_artifact_from_writes_and_snapshot,
    snapshot_workspace_manifest_async,
)
from deep_agent.agent.base_agent import BaseSpecialistAgent, SpecialistExecutionContext, SpecialistRuntimeConfig
from deep_agent.core.display_message import (
    VisibleTranscriptCollector,
    build_runtime_message_result,
    emit_display_message_delta,
)
from deep_agent.config.specialist_file_filter import GENERATOR_QUERY_FILTER_CONFIG
from deep_agent.agent.generator.prompts.generator_conventions import GENERATOR_BUSINESS_PROMPT
from deep_agent.agent.generator.prompts.generator import GENERATOR_SYSTEM_PROMPT
from deep_agent.agent.state import WorkflowState
from deep_agent.core.runtime_logging import log_debug_event, log_title, with_trace_context
from deep_agent.core.autotest_project_directory import DEFAULT_AUTOTEST_DEMO_PROJECT_NAME, normalize_runtime_text, resolve_autotest_project_dir
from deep_agent.tools.playwright import GENERATOR_ALLOWED_PLAYWRIGHT_TEST_MCP_TOOL_IDS


GENERATOR_RUNTIME_CONFIG = SpecialistRuntimeConfig(
    system_prompt_parts=(GENERATOR_SYSTEM_PROMPT, GENERATOR_BUSINESS_PROMPT),
    allowed_playwright_test_mcp_tools=GENERATOR_ALLOWED_PLAYWRIGHT_TEST_MCP_TOOL_IDS,
    load_project_standard=True,
    query_filter_config=GENERATOR_QUERY_FILTER_CONFIG,
)


class GeneratorAgent(BaseSpecialistAgent):
    """负责脚本生成阶段的专项智能体。

    它存在的目的，是把“根据计划落地脚本”的能力和其他阶段拆开，让脚本生成的 prompt、
    工具白名单和约束都围绕代码产出本身收敛。
    """

    agent_type = "generator"
    display_name = "Generator Agent"
    runtime_config = GENERATOR_RUNTIME_CONFIG

    def _validate_extracted_params(self, state: WorkflowState) -> str | None:
        """确保 Generator 运行前至少具备项目目录信息和测试计划输入。"""

        extracted_params = state.get("extracted_params", {})
        project_dir = self._normalized_runtime_text(extracted_params.get("project_dir"))
        project_name = self._normalized_project_name(extracted_params.get("project_name"))
        if not project_dir and not project_name:
            return "Generator 模式缺少自动化工程目录。请提供 `project_dir`，或至少提供 `project_name` 以便按 Plan 规则推导目录。"

        if self._normalized_test_plan_files(extracted_params.get("test_plan_files")):
            return None

        return "Generator 模式缺少待生成脚本的测试计划文件或文件夹。请至少提供 1 个 `test_plan_files` 条目后再继续。"

    def _resolve_workspace_dir(self, state: WorkflowState) -> Path:
        """解析并创建 Generator 使用的自动化项目目录。

        当用户没有显式提供 `project_dir` 时，这里与 Plan 保持完全一致：回落到
        `自动化根目录 / project_name`，并按模板自动准备工程目录。
        """

        extracted_params = state.get("extracted_params", {})
        project_name = self._normalized_project_name(extracted_params.get("project_name"))
        return resolve_autotest_project_dir(
            automation_root=self._settings.resolved_default_automation_project_root,
            bundled_template_dir=self._bundled_demo_template_dir(),
            project_name=project_name,
            raw_project_dir=extracted_params.get("project_dir"),
            missing_project_name_error="Generator 模式缺少合法的 `project_name`，无法按 Plan 规则推导自动化工程目录。",
        )

    def _build_runtime_context_prompt(self, *, state: WorkflowState, workspace_dir: Path | None) -> str:
        """构建 Generator 模式专用的运行时上下文提示词。"""

        if workspace_dir is None:
            raise RuntimeError("Generator 模式缺少工作目录，无法构建运行时上下文。")

        extracted_params = state.get("extracted_params", {})
        project_name = self._normalized_project_name(extracted_params.get("project_name")) or workspace_dir.name
        resolved_test_plan_files, normalized_test_cases, expected_test_scripts = self._resolve_generation_targets(
            workspace_dir=workspace_dir,
            extracted_params=extracted_params,
        )
        relative_test_plan_files = [path.relative_to(workspace_dir).as_posix() for path in resolved_test_plan_files]

        prompt_sections = [
            "## 本次运行上下文",
            f"- project_name: `{project_name}`",
            f"- project_dir: `{workspace_dir}`",
            f"- automation_root_dir: `{self._settings.resolved_default_automation_project_root.resolve()}`",
            f"- test_plan_files: {self._format_prompt_value(relative_test_plan_files)}",
            f"- resolved_test_plan_files: {self._format_prompt_value([str(path) for path in resolved_test_plan_files])}",
            f"- test_cases: {self._format_prompt_value(normalized_test_cases)}",
            f"- expected_test_scripts: {self._format_prompt_value(expected_test_scripts)}",
            f"- expected_case_count: {len(expected_test_scripts)}",
            "## 额外运行时约束",
            f"- 本次共解析出 {len(resolved_test_plan_files)} 个测试计划文件；请按 `test_plan_files` 给出的顺序逐个处理，不要遗漏。",
            f"- 只有当 `expected_test_scripts` 中列出的 {len(expected_test_scripts)} 个脚本都通过 `generator_write_test` 成功写出后，本阶段才算完成。",
            "- 如需查询文件，先从 `test_plan_files` 所在目录用 `ls` 建立目录感知，再缩小到最小必要的计划文件或共享目录。",
            "- 上述测试计划文件都已校验位于当前 `project_dir` 下；生成出的脚本也必须写回同一工程目录。",
        ]
        return "\n".join(prompt_sections)

    def _bundled_demo_template_dir(self) -> Path:
        """返回仓库内置的 demo 模板目录。"""

        template_dir = Path(__file__).resolve().parents[2] / "assets" / DEFAULT_AUTOTEST_DEMO_PROJECT_NAME
        if not template_dir.is_dir():
            raise RuntimeError(f"内置 demo 模板目录不存在：`{template_dir}`。")
        return template_dir

    def _normalized_project_name(self, project_name: Any) -> str | None:
        """把工程名归一化为可判空的字符串。"""

        return self._normalized_runtime_text(project_name)

    def _normalized_runtime_text(self, value: Any) -> str | None:
        """把运行时文本参数归一化为可判空字符串。"""

        return normalize_runtime_text(value)

    def _normalized_test_plan_files(self, value: Any) -> list[str]:
        """把测试计划输入参数归一化为去重后的字符串列表。"""

        if isinstance(value, (list, tuple)):
            candidate_values = value
        elif value is None:
            candidate_values = []
        else:
            candidate_values = [value]

        normalized_files: list[str] = []
        seen: set[str] = set()
        for item in candidate_values:
            normalized_item = self._normalized_runtime_text(item)
            if not normalized_item or normalized_item in seen:
                continue
            seen.add(normalized_item)
            normalized_files.append(normalized_item)
        return normalized_files

    def _normalized_test_cases(self, value: Any) -> list[str]:
        """把测试用例筛选参数归一化为去重后的字符串列表。"""

        if isinstance(value, (list, tuple)):
            candidate_values = value
        elif value is None:
            candidate_values = []
        else:
            candidate_values = [value]

        normalized_cases: list[str] = []
        seen: set[str] = set()
        for item in candidate_values:
            normalized_item = self._normalized_runtime_text(item)
            if not normalized_item or normalized_item in seen:
                continue
            seen.add(normalized_item)
            normalized_cases.append(normalized_item)
        return normalized_cases

    def _resolve_test_plan_files(self, *, workspace_dir: Path, raw_test_plan_files: Any) -> list[Path]:
        """把测试计划文件或目录解析成项目目录下的绝对路径，并展开成计划文件列表。"""

        normalized_test_plan_files = self._normalized_test_plan_files(raw_test_plan_files)
        if not normalized_test_plan_files:
            raise RuntimeError("Generator 模式缺少合法的 `test_plan_files`，无法继续生成脚本。")

        resolved_paths: list[Path] = []
        for raw_file in normalized_test_plan_files:
            candidate_path = Path(raw_file).expanduser()
            if not candidate_path.is_absolute():
                candidate_path = workspace_dir / candidate_path

            resolved_path = candidate_path.resolve()
            try:
                resolved_path.relative_to(workspace_dir)
            except ValueError as exc:
                raise RuntimeError(
                    f"Generator 模式测试计划文件 `{resolved_path}` 不在项目目录 `{workspace_dir}` 下，无法继续。"
                ) from exc

            if resolved_path.is_file():
                resolved_paths.append(resolved_path)
                continue

            if resolved_path.is_dir():
                resolved_paths.extend(self._expand_test_plan_directory(resolved_path))
                continue

            raise RuntimeError(f"Generator 模式测试计划路径 `{resolved_path}` 不存在，无法继续。")

        deduplicated_paths: list[Path] = []
        seen: set[str] = set()
        for path in resolved_paths:
            normalized_key = str(path)
            if normalized_key in seen:
                continue
            seen.add(normalized_key)
            deduplicated_paths.append(path)

        return deduplicated_paths

    def _expand_test_plan_directory(self, directory: Path) -> list[Path]:
        """把测试计划目录按约定展开成 Markdown 测试计划文件列表。"""

        prioritized_patterns = ("aaa_*.md", "*.md")
        for pattern in prioritized_patterns:
            matches = sorted(path.resolve() for path in directory.rglob(pattern) if path.is_file())
            if matches:
                return matches

        raise RuntimeError(f"Generator 模式测试计划目录 `{directory}` 下未找到可用的 Markdown 测试计划文件，无法继续。")

    def _resolve_generation_targets(
        self,
        *,
        workspace_dir: Path,
        extracted_params: dict[str, Any],
    ) -> tuple[list[Path], list[str], list[str]]:
        """解析本次脚本生成所需的计划文件、用例筛选和预期输出脚本。"""

        resolved_test_plan_files = self._resolve_test_plan_files(
            workspace_dir=workspace_dir,
            raw_test_plan_files=extracted_params.get("test_plan_files"),
        )
        normalized_test_cases = self._normalized_test_cases(extracted_params.get("test_cases"))
        expected_test_scripts = extract_expected_generator_test_scripts_from_plan_files(
            plan_files=resolved_test_plan_files,
            project_dir=workspace_dir,
            selected_test_cases=normalized_test_cases,
        )
        return resolved_test_plan_files, normalized_test_cases, expected_test_scripts

    async def _run_deep_agent(
        self,
        specialist_agent: Any,
        state: WorkflowState,
        execution_context: SpecialistExecutionContext,
        config: RunnableConfig | None = None,
    ) -> WorkflowState:
        """使用事件流执行 Generator，并输出与 Plan 同级别的调试日志。"""

        existing_messages = state.get("messages", [])
        collector = VisibleTranscriptCollector()
        generator_write_succeeded = False
        generator_write_error: str | None = None
        pending_write_payloads: list[dict[str, str]] = []
        successful_write_payloads: list[dict[str, str]] = []
        workspace_dir = execution_context.workspace_dir
        extracted_params = state.get("extracted_params", {})
        project_name = self._normalized_project_name(extracted_params.get("project_name")) or (
            workspace_dir.name if workspace_dir is not None else "unknown-project"
        )
        input_plan_files = self._normalized_test_plan_files(extracted_params.get("test_plan_files"))
        expected_test_scripts: list[str] = []
        if workspace_dir is not None:
            _, _, expected_test_scripts = self._resolve_generation_targets(
                workspace_dir=workspace_dir,
                extracted_params=extracted_params,
            )
        before_manifest = await snapshot_workspace_manifest_async(workspace_dir)
        stage_artifact: dict[str, Any] | None = None

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
                self.log_stream_event(event, execution_context.trace_context)
                emit_display_message_delta(collector.consume_event(event))
                if event.get("name") == "generator_write_test" and event.get("event") == "on_tool_start":
                    payload = event.get("data", {}).get("input")
                    if isinstance(payload, dict):
                        file_name = self._normalized_runtime_text(payload.get("fileName"))
                        code = payload.get("code")
                        if file_name and isinstance(code, str):
                            pending_write_payloads.append({"fileName": file_name, "code": code})
                generator_write_succeeded, generator_write_error = self._update_generator_write_state(
                    generator_write_succeeded,
                    generator_write_error,
                    pending_write_payloads,
                    successful_write_payloads,
                    event,
                )
                self.log_generator_write_state(
                    event,
                    generator_write_succeeded,
                    generator_write_error,
                    execution_context.trace_context,
                )
        except Exception as exc:  # noqa: BLE001
            if successful_write_payloads and self._is_expected_browser_close_error(exc):
                self.log_browser_close_expected(execution_context.trace_context, exc)
                if workspace_dir is not None:
                    try:
                        stage_artifact = await self._build_generator_stage_artifact(
                            successful_write_payloads=successful_write_payloads,
                            before_manifest=before_manifest,
                            workspace_dir=workspace_dir,
                            project_name=project_name,
                            input_files=input_plan_files,
                            expected_test_scripts=expected_test_scripts,
                        )
                    except Exception as artifact_exc:  # noqa: BLE001
                        return self._build_runtime_exception_result(
                            collector=collector,
                            existing_messages=existing_messages,
                            exc=artifact_exc,
                        )
                result = build_runtime_message_result(
                    collector=collector,
                    existing_messages=existing_messages,
                    fallback_message="测试脚本已生成，浏览器已按预期关闭。",
                )
                result["artifact"] = stage_artifact
                return result
            return self._build_runtime_exception_result(
                collector=collector,
                existing_messages=existing_messages,
                exc=exc,
            )

        log_debug_event(
            self.log_get_logger(),
            self._settings,
            log_title("执行", "事件流"),
            "generator_final_output",
            self.log_event_trace_context(execution_context.trace_context, "generator_final_output"),
            generator_write_succeeded=generator_write_succeeded,
            generator_write_error=generator_write_error,
            final_output=collector.final_output,
            visible_messages=collector.messages,
        )

        if not successful_write_payloads:
            error_suffix = f" 最近一次错误：{generator_write_error}" if generator_write_error else ""
            return self._build_runtime_exception_result(
                collector=collector,
                existing_messages=existing_messages,
                exc=RuntimeError(f"Generator Agent 未成功调用 `generator_write_test` 生成脚本。{error_suffix}"),
            )

        if workspace_dir is not None:
            try:
                stage_artifact = await self._build_generator_stage_artifact(
                    successful_write_payloads=successful_write_payloads,
                    before_manifest=before_manifest,
                    workspace_dir=workspace_dir,
                    project_name=project_name,
                    input_files=input_plan_files,
                    expected_test_scripts=expected_test_scripts,
                )
            except Exception as exc:  # noqa: BLE001
                return self._build_runtime_exception_result(
                    collector=collector,
                    existing_messages=existing_messages,
                    exc=exc,
                )

        result = build_runtime_message_result(
            collector=collector,
            existing_messages=existing_messages,
            fallback_message="测试脚本生成阶段已完成。",
        )
        result["artifact"] = stage_artifact
        return result

    def _update_generator_write_state(
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
            return False, self.log_truncate(event.get("data", {}).get("error"))

        if event.get("event") != "on_tool_end":
            return generator_write_succeeded, generator_write_error

        pending_payload = pending_write_payloads.pop(0) if pending_write_payloads else None
        output = event.get("data", {}).get("output")
        if self._tool_output_is_error(output):
            return False, self.log_truncate(output)

        if pending_payload is None:
            return False, "`generator_write_test` 未捕获到输入 payload，无法确认写入文件。"

        successful_write_payloads.append(pending_payload)
        return True, None

    async def _build_generator_stage_artifact(
        self,
        *,
        successful_write_payloads: list[dict[str, str]],
        before_manifest: dict[str, Any],
        workspace_dir: Path,
        project_name: str,
        input_files: list[str],
        expected_test_scripts: list[str],
    ) -> dict[str, Any]:
        """构建 Generator 产物，并校验是否已覆盖计划要求的全部脚本。"""

        artifact = extract_generator_artifact_from_writes_and_snapshot(
            writes=successful_write_payloads,
            before_manifest=before_manifest,
            after_manifest=await snapshot_workspace_manifest_async(workspace_dir),
            workspace_dir=workspace_dir,
            project_name=project_name,
            input_files=input_files,
        )
        self._assert_expected_test_scripts_written(
            expected_test_scripts=expected_test_scripts,
            actual_test_scripts=artifact.get("output_files", []),
        )
        return artifact

    def _assert_expected_test_scripts_written(
        self,
        *,
        expected_test_scripts: list[str],
        actual_test_scripts: list[str],
    ) -> None:
        """确保本次成功写出的脚本集合完整覆盖计划要求。"""

        expected_files = self._normalized_test_plan_files(expected_test_scripts)
        actual_files = self._normalized_test_plan_files(actual_test_scripts)
        missing_files = [path for path in expected_files if path not in actual_files]
        if not missing_files:
            return

        missing_text = "、".join(f"`{path}`" for path in missing_files)
        raise RuntimeError(
            "Generator Agent 未完成测试计划要求的全部脚本生成。"
            f" 期望 {len(expected_files)} 个脚本，实际成功写出 {len(actual_files)} 个。"
            f" 缺失脚本：{missing_text}"
        )

    def log_generator_write_state(
        self,
        event: dict[str, Any],
        generator_write_succeeded: bool,
        generator_write_error: str | None,
        trace_context: dict[str, Any],
    ) -> None:
        """记录 `generator_write_test` 的成功/失败状态，方便按 session grep。"""

        if event.get("name") != "generator_write_test" or event.get("event") not in {"on_tool_end", "on_tool_error"}:
            return

        status = "success" if generator_write_succeeded else "error"
        self.log_tool_state(
            trace_context=trace_context,
            event_name="generator_write_test",
            status=status,
            error=generator_write_error,
        )

    def _tool_output_is_error(self, output: Any) -> bool:
        """判断工具输出是否表示失败。"""

        status = getattr(output, "status", None)
        if status == "error":
            return True

        if isinstance(output, dict):
            if output.get("status") == "error":
                return True
            content = output.get("content")
            if isinstance(content, str) and content.lstrip().startswith("Error:"):
                return True

        content = getattr(output, "content", None)
        if isinstance(content, str) and content.lstrip().startswith("Error:"):
            return True

        return False

    def _is_expected_browser_close_error(self, exc: Exception) -> bool:
        """判断异常是否为关闭浏览器后的预期错误。"""

        text = str(exc).lower()
        expected_fragments = (
            "target page, context or browser has been closed",
            "browsercontext.newpage",
            "browser has been closed",
        )
        return any(fragment in text for fragment in expected_fragments)

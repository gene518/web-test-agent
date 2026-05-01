"""Healer Specialist Agent。

Healer 阶段的目标，是围绕已有失败脚本做调试与修复。这里把运行前的目录解析、
脚本文件校验、修复提示词和写权限边界都收敛到一处，让后续执行可以直接复用
BaseSpecialistAgent 的通用 Deep Agent 骨架。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deepagents.middleware import FilesystemPermission
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from deep_agent.agent.base_agent import (
    BaseSpecialistAgent,
    SpecialistExecutionContext,
    SpecialistRuntimeConfig,
)
from deep_agent.config.specialist_file_filter import HEALER_QUERY_FILTER_CONFIG
from deep_agent.agent.healer.prompts.healer import HEALER_SYSTEM_PROMPT
from deep_agent.agent.healer.prompts.healer_conventions import MOBILE_UI_CONVENTIONS_PROMPT
from deep_agent.agent.state import WorkflowState
from deep_agent.core.runtime_logging import (
    log_debug_event,
    log_title,
    with_trace_context,
)
from deep_agent.core.autotest_project_directory import (
    DEFAULT_AUTOTEST_DEMO_PROJECT_NAME,
    normalize_runtime_text,
    resolve_autotest_project_dir,
)
from deep_agent.tools.playwright import HEALER_ALLOWED_PLAYWRIGHT_TEST_MCP_TOOL_IDS


HEALER_RUNTIME_CONFIG = SpecialistRuntimeConfig(
    system_prompt_parts=(HEALER_SYSTEM_PROMPT, MOBILE_UI_CONVENTIONS_PROMPT),
    allowed_playwright_test_mcp_tools=HEALER_ALLOWED_PLAYWRIGHT_TEST_MCP_TOOL_IDS,
    load_project_standard=True,
    query_filter_config=HEALER_QUERY_FILTER_CONFIG,
)


class HealerAgent(BaseSpecialistAgent):
    """负责失败脚本修复阶段的 Specialist Agent。"""

    agent_type = "healer"
    display_name = "Healer Agent"
    runtime_config = HEALER_RUNTIME_CONFIG

    def _validate_extracted_params(self, state: WorkflowState) -> str | None:
        """确保 Healer 运行前具备项目目录上下文和待调试脚本输入。"""

        extracted_params = state.get("extracted_params", {})
        project_dir = self._normalized_runtime_text(extracted_params.get("project_dir"))
        project_name = self._normalized_project_name(extracted_params.get("project_name"))
        if not project_dir and not project_name:
            return "Healer 模式缺少自动化工程目录。请提供 `project_dir`，或至少提供 `project_name` 以便按 Generator 规则推导目录。"

        if self._normalized_test_scripts(extracted_params.get("test_scripts")):
            return None

        return "Healer 模式缺少待调试脚本文件或文件夹。请至少提供 1 个 `test_scripts` 条目后再继续。"

    def _resolve_workspace_dir(self, state: WorkflowState) -> Path:
        """解析并创建 Healer 使用的自动化项目目录。"""

        extracted_params = state.get("extracted_params", {})
        project_name = self._normalized_project_name(extracted_params.get("project_name"))
        return resolve_autotest_project_dir(
            automation_root=self._settings.resolved_default_automation_project_root,
            bundled_template_dir=self._bundled_demo_template_dir(),
            project_name=project_name,
            raw_project_dir=extracted_params.get("project_dir"),
            missing_project_name_error="Healer 模式缺少合法的 `project_name`，无法按 Generator 规则推导自动化工程目录。",
        )

    def _build_runtime_context_prompt(self, *, state: WorkflowState, workspace_dir: Path | None) -> str:
        """构建 Healer 模式专用的运行时上下文提示词。"""

        if workspace_dir is None:
            raise RuntimeError("Healer 模式缺少工作目录，无法构建运行时上下文。")

        extracted_params = state.get("extracted_params", {})
        project_name = self._normalized_project_name(extracted_params.get("project_name")) or workspace_dir.name
        resolved_test_scripts = self._resolve_test_script_files(
            workspace_dir=workspace_dir,
            raw_test_scripts=extracted_params.get("test_scripts"),
        )
        relative_test_scripts = [path.relative_to(workspace_dir).as_posix() for path in resolved_test_scripts]

        prompt_sections = [
            "## 本次运行上下文",
            f"- project_name: `{project_name}`",
            f"- project_dir: `{workspace_dir}`",
            f"- automation_root_dir: `{self._settings.resolved_default_automation_project_root.resolve()}`",
            f"- test_scripts: {self._format_prompt_value(relative_test_scripts)}",
            f"- resolved_test_scripts: {self._format_prompt_value([str(path) for path in resolved_test_scripts])}",
            "## 完成条件",
            f"- 本次共收到 {len(resolved_test_scripts)} 个待调试脚本；请优先按 `test_scripts` 给出的顺序逐个运行、定位和修复。",
            "- `test_run` 与 `test_debug` 应优先只针对这些脚本执行，不要默认扩大到整个工程。",
            "- 如需继续查询文件，先从当前脚本所在目录、`.playwright-mcp/` 或确有必要的 `test_case/shared/` 目录用 `ls` 建立目录感知，再缩小范围。",
            "- 只有位于当前 `project_dir` 下的文件允许被读取和修改；所有修复都必须写回当前工程目录。",
            "- 每次修改后都要重新运行相关脚本验证；如果确认属于产品缺陷，可按 system prompt 规则使用 `test.fixme()` 收敛。",
        ]
        return "\n".join(prompt_sections)

    def _build_deep_agent_permissions(self, workspace_dir: Path | None) -> list[FilesystemPermission] | None:
        """允许 Healer 在当前项目目录内读写，供内置编辑工具修复脚本。"""

        if workspace_dir is None:
            return None

        return self._build_workspace_permissions(workspace_dir, allow_workspace_writes=True)

    async def _run_deep_agent(
        self,
        specialist_agent: Any,
        state: WorkflowState,
        execution_context: SpecialistExecutionContext,
        config: RunnableConfig | None = None,
    ) -> WorkflowState:
        """使用事件流执行 Healer，确保最终结果能沿流式链路抛出。"""

        existing_messages = state.get("messages", [])
        final_output: dict[str, Any] | None = None

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
                final_output = self._capture_final_output(final_output, event)
        except Exception as exc:  # noqa: BLE001
            if final_output is not None and self._is_expected_browser_close_error(exc):
                self.log_browser_close_expected(execution_context.trace_context, exc)
                return self._build_messages_result(final_output, existing_messages, "脚本调试阶段已完成，浏览器已按预期关闭。")
            raise

        log_debug_event(
            self.log_get_logger(),
            self._settings,
            log_title("执行", "事件流"),
            "healer_final_output",
            self.log_event_trace_context(execution_context.trace_context, "healer_final_output"),
            final_output=final_output,
        )

        if final_output is None:
            return {"messages": [AIMessage(content="脚本调试阶段已完成。")]}

        return self._build_messages_result(final_output, existing_messages, "脚本调试阶段已完成。")

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

    def _normalized_test_scripts(self, value: Any) -> list[str]:
        """把待调试脚本输入参数归一化为去重后的字符串列表。"""

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

    def _resolve_test_script_files(self, *, workspace_dir: Path, raw_test_scripts: Any) -> list[Path]:
        """把待调试脚本文件或目录解析成项目目录下的绝对路径，并展开成脚本文件列表。"""

        normalized_test_scripts = self._normalized_test_scripts(raw_test_scripts)
        if not normalized_test_scripts:
            raise RuntimeError("Healer 模式缺少合法的 `test_scripts`，无法继续调试脚本。")

        resolved_paths: list[Path] = []
        for raw_file in normalized_test_scripts:
            candidate_path = Path(raw_file).expanduser()
            if not candidate_path.is_absolute():
                candidate_path = workspace_dir / candidate_path

            resolved_path = candidate_path.resolve()
            try:
                resolved_path.relative_to(workspace_dir)
            except ValueError as exc:
                raise RuntimeError(
                    f"Healer 模式待调试脚本 `{resolved_path}` 不在项目目录 `{workspace_dir}` 下，无法继续。"
                ) from exc

            if resolved_path.is_file():
                resolved_paths.append(resolved_path)
                continue

            if resolved_path.is_dir():
                resolved_paths.extend(self._expand_test_script_directory(resolved_path))
                continue

            raise RuntimeError(f"Healer 模式待调试脚本路径 `{resolved_path}` 不存在，无法继续。")

        deduplicated_paths: list[Path] = []
        seen: set[str] = set()
        for path in resolved_paths:
            normalized_key = str(path)
            if normalized_key in seen:
                continue
            seen.add(normalized_key)
            deduplicated_paths.append(path)

        return deduplicated_paths

    def _expand_test_script_directory(self, directory: Path) -> list[Path]:
        """把待调试脚本目录展开成 `.spec.ts` 文件列表。"""

        matches = sorted(path.resolve() for path in directory.rglob("*.spec.ts") if path.is_file())
        if matches:
            return matches

        raise RuntimeError(f"Healer 模式待调试脚本目录 `{directory}` 下未找到可用的 `.spec.ts` 文件，无法继续。")

    def _capture_final_output(
        self,
        current_output: dict[str, Any] | None,
        event: dict[str, Any],
    ) -> dict[str, Any] | None:
        """只从根链路的结束事件提取最终输出。"""

        if event.get("event") != "on_chain_end" or event.get("parent_ids"):
            return current_output

        output = event.get("data", {}).get("output")
        if isinstance(output, dict):
            return output

        return current_output

    def _build_messages_result(
        self,
        final_output: dict[str, Any],
        existing_messages: list[Any],
        fallback_message: str,
    ) -> WorkflowState:
        """把 Deep Agent 的最终输出转换成工作流增量消息。"""

        all_messages = final_output.get("messages", [])
        if not isinstance(all_messages, list):
            return {"messages": [AIMessage(content=fallback_message)]}

        new_messages = all_messages[len(existing_messages) :]
        if not new_messages:
            new_messages = [AIMessage(content=fallback_message)]
        return {"messages": new_messages}

    def _is_expected_browser_close_error(self, exc: Exception) -> bool:
        """判断异常是否为关闭浏览器后的预期错误。"""

        text = str(exc).lower()
        expected_fragments = (
            "target page, context or browser has been closed",
            "browsercontext.newpage",
            "browser has been closed",
        )
        return any(fragment in text for fragment in expected_fragments)

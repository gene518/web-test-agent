"""Healer 阶段专项智能体。

Healer 阶段的目标，是围绕已有失败脚本做调试与修复。Agent 类只承担"目录解析、
脚本文件校验、修复提示词、写权限"这类静态职责；事件流监听、验证范围采集、产物抽取
等运行期逻辑全部委托给 `HealerRuntimeHelper`，和 Master 子图节点一致的分层策略，
便于后续测试替换与读图。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deepagents.middleware import FilesystemPermission
from langchain_core.runnables import RunnableConfig

from deep_agent.agent.base_agent import (
    BaseSpecialistAgent,
    SpecialistExecutionContext,
    SpecialistRuntimeConfig,
)
from deep_agent.agent.healer.runtime import HealerRuntimeHelper
from deep_agent.agent.specialist_helpers import (
    bundled_demo_template_dir,
    normalize_runtime_text,
    normalize_string_list,
    resolve_workspace_scoped_files,
)
from deep_agent.config.specialist_file_filter import HEALER_QUERY_FILTER_CONFIG
from deep_agent.agent.healer.prompts.healer import HEALER_SYSTEM_PROMPT
from deep_agent.agent.healer.prompts.healer_conventions import MOBILE_UI_CONVENTIONS_PROMPT
from deep_agent.agent.state import WorkflowState
from deep_agent.core.autotest_project_directory import resolve_autotest_project_dir
from deep_agent.tools.playwright import HEALER_ALLOWED_PLAYWRIGHT_TEST_MCP_TOOL_IDS


HEALER_RUNTIME_CONFIG = SpecialistRuntimeConfig(
    system_prompt_parts=(HEALER_SYSTEM_PROMPT, MOBILE_UI_CONVENTIONS_PROMPT),
    allowed_playwright_test_mcp_tools=HEALER_ALLOWED_PLAYWRIGHT_TEST_MCP_TOOL_IDS,
    load_project_standard=True,
    query_filter_config=HEALER_QUERY_FILTER_CONFIG,
)


class HealerAgent(BaseSpecialistAgent):
    """负责失败脚本修复阶段的专项智能体。"""

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
            bundled_template_dir=bundled_demo_template_dir(),
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
        related_test_plan_files = self._normalized_test_plan_files(extracted_params.get("test_plan_files"))

        prompt_sections = [
            "## 本次运行上下文",
            f"- project_name: `{project_name}`",
            f"- project_dir: `{workspace_dir}`",
            f"- automation_root_dir: `{self._settings.resolved_default_automation_project_root.resolve()}`",
            f"- test_scripts: {self._format_prompt_value(relative_test_scripts)}",
            f"- resolved_test_scripts: {self._format_prompt_value([str(path) for path in resolved_test_scripts])}",
            f"- related_test_plan_files: {self._format_prompt_value(related_test_plan_files)}",
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
        """将事件流运行委托给 `HealerRuntimeHelper`，保持 Agent 类职责单一。"""

        return await HealerRuntimeHelper(agent=self, settings=self._settings).run(
            specialist_agent=specialist_agent,
            state=state,
            execution_context=execution_context,
            config=config,
        )

    def _bundled_demo_template_dir(self) -> Path:
        """返回仓库内置的 demo 模板目录。"""

        return bundled_demo_template_dir()

    def _normalized_project_name(self, project_name: Any) -> str | None:
        """把工程名归一化为可判空的字符串。"""

        return normalize_runtime_text(project_name)

    def _normalized_runtime_text(self, value: Any) -> str | None:
        """把运行时文本参数归一化为可判空字符串。"""

        return normalize_runtime_text(value)

    def _normalized_test_scripts(self, value: Any) -> list[str]:
        """把待调试脚本输入参数归一化为去重后的字符串列表。"""

        return normalize_string_list(value)

    def _normalized_test_plan_files(self, value: Any) -> list[str]:
        """把关联测试计划输入参数归一化为去重后的字符串列表。"""

        return normalize_string_list(value)

    def _resolve_test_script_files(self, *, workspace_dir: Path, raw_test_scripts: Any) -> list[Path]:
        """把待调试脚本文件或目录解析成项目目录下的绝对路径，并展开成脚本文件列表。"""

        return resolve_workspace_scoped_files(
            workspace_dir=workspace_dir,
            raw_values=raw_test_scripts,
            kind_label="Healer 模式待调试脚本",
            directory_expander=self._expand_test_script_directory,
        )

    def _expand_test_script_directory(self, directory: Path) -> list[Path]:
        """把待调试脚本目录展开成 `.spec.ts` 文件列表。"""

        matches = sorted(path.resolve() for path in directory.rglob("*.spec.ts") if path.is_file())
        if matches:
            return matches

        raise RuntimeError(f"Healer 模式待调试脚本目录 `{directory}` 下未找到可用的 `.spec.ts` 文件，无法继续。")

"""Plan 阶段专项智能体。

Plan 阶段的职责不是直接产出脚本，而是先把目标页面探索清楚，并把结果沉淀成后续
Generator 可以消费的测试计划。Agent 类只承担"阶段配置 + 参数校验 + workspace 解析 +
运行时上下文 prompt + 文件写权限"这类静态职责；事件流监听、`planner_save_plan`
成功判定和产物抽取等运行期逻辑全部委托给 `PlanRuntimeHelper`，分层与 Master 子图保持一致。
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
from deep_agent.agent.plan.runtime import PlanRuntimeHelper
from deep_agent.agent.specialist_helpers import (
    bundled_demo_template_dir,
    display_workspace_for_agent_prompt,
    normalize_runtime_text,
    normalize_string_list,
)
from deep_agent.config.specialist_file_filter import PLAN_QUERY_FILTER_CONFIG
from deep_agent.agent.plan.prompts.plan_conventions import MOBILE_PLAN_CONVENTIONS_PROMPT
from deep_agent.agent.plan.prompts.plan import PLAN_SYSTEM_PROMPT
from deep_agent.agent.state import WorkflowState
from deep_agent.core.autotest_project_directory import resolve_autotest_project_dir
from deep_agent.tools.playwright import PLAN_ALLOWED_PLAYWRIGHT_TEST_MCP_TOOL_IDS


PLAN_RUNTIME_CONFIG = SpecialistRuntimeConfig(
    system_prompt_parts=(PLAN_SYSTEM_PROMPT, MOBILE_PLAN_CONVENTIONS_PROMPT),
    allowed_playwright_test_mcp_tools=PLAN_ALLOWED_PLAYWRIGHT_TEST_MCP_TOOL_IDS,
    load_project_standard=True,
    query_filter_config=PLAN_QUERY_FILTER_CONFIG,
)


class PlanAgent(BaseSpecialistAgent):
    """负责测试计划生成阶段的专项智能体。

    它存在的目的，是在真正写脚本之前先探索页面、明确功能点和输出计划文件，
    这样后续脚本生成阶段可以直接围绕稳定的计划产物开展，而不是重复理解需求。
    """

    agent_type = "plan"
    display_name = "Plan Agent"
    runtime_config = PLAN_RUNTIME_CONFIG

    def _build_deep_agent_permissions(self, workspace_dir: Path | None) -> list[FilesystemPermission] | None:
        """允许 Plan 在当前项目目录内读写文件。"""

        if workspace_dir is None:
            return None
        return self._build_workspace_permissions(workspace_dir, allow_workspace_writes=True)

    def _validate_extracted_params(self, state: WorkflowState) -> str | None:
        """确保 Plan 运行前至少具备工程名和 URL。"""

        extracted_params = state.get("extracted_params", {})
        project_name = self._normalized_project_name(extracted_params.get("project_name"))
        if not project_name:
            return "Plan 模式缺少自动化工程名字。请补充工程名字后再继续。"

        url = self._normalized_runtime_text(extracted_params.get("url"))
        if url:
            return None

        return "Plan 模式缺少被测页面 URL。请补充完整 URL 后再继续。"

    def _resolve_workspace_dir(self, state: WorkflowState) -> Path:
        """解析并创建 Plan 使用的自动化项目目录。

        Plan 比其他 Specialist 更需要"确定的可写目录"，因为它会把测试计划文件直接落到项目中。
        当前策略固定为"自动化根目录 / 工程名字"，不再按时间戳生成新目录。
        """

        extracted_params = state.get("extracted_params", {})
        project_name = self._normalized_project_name(extracted_params.get("project_name"))
        return resolve_autotest_project_dir(
            automation_root=self._settings.resolved_default_automation_project_root,
            bundled_template_dir=bundled_demo_template_dir(),
            project_name=project_name,
            raw_project_dir=extracted_params.get("project_dir"),
            missing_project_name_error="Plan 模式缺少合法的 `project_name`，无法解析自动化工程目录。",
        )

    def _build_runtime_context_prompt(self, *, state: WorkflowState, workspace_dir: Path | None) -> str:
        """构建 Plan 模式专用的运行时上下文提示词。

        相比基类的通用上下文，Plan 这里会额外把"必须先初始化页面、必须保存计划、保存后才能收尾"
        这类流程约束写进去，目的是防止模型只做分析不真正产出计划文件。
        """

        extracted_params = state.get("extracted_params", {})
        project_name = self._normalized_project_name(extracted_params.get("project_name")) or ""
        url = self._normalized_runtime_text(extracted_params.get("url")) or ""
        feature_points = extracted_params.get("feature_points", [])
        existing_plan_files = self._normalized_test_plan_files(extracted_params.get("test_plan_files"))

        # 这里既放本次请求的动态参数，也放 Plan 阶段的执行约束，
        # 目的是让模型在一个上下文里同时理解"要做什么"和"必须怎么做完"。
        # Windows 下 `project_dir` 显示为虚拟路径 `/`，配合
        # `FilesystemBackend(virtual_mode=True)`；mac/Linux 仍显示真实绝对路径。
        display_project_dir = display_workspace_for_agent_prompt(workspace_dir) if workspace_dir else ""
        prompt_sections = [
            "## 本次运行上下文",
            f"- project_name: `{project_name}`",
            f"- url: `{url}`",
            f"- project_dir: `{display_project_dir}`",
            f"- automation_root_dir: `{self._settings.resolved_default_automation_project_root.resolve()}`",
            f"- feature_points: {self._format_prompt_value(feature_points)}",
            f"- existing_test_plan_files: {self._format_prompt_value(existing_plan_files)}",
            "- `planner_save_plan.fileName` 必须是相对 `project_dir` 的路径。",
            "- `planner_save_plan.fileName` 必须符合 `test_case/aaaplanning_{plan-name}/aaa_{plan-name}.md`。",
            "## 完成条件",
            "- 必须先调用一次 `planner_setup_page` 初始化页面。",
            f"- 初始化完成后，必须使用 `browser_navigate` 打开 `{url}` 并开始探索。",
            "- 如果用户提供了 `feature_points`，优先覆盖这些功能点，但仍需结合页面探索补全关键场景。",
            "- 如果 `existing_test_plan_files` 非空，表示当前请求可能是在补充或更新已有计划；优先基于这些计划文件延续，而不是凭空新建无关计划。",
            "- 只能使用当前可见工具；不要尝试调用 `planner_submit_plan`。",
            "- 如确需查询工程文件，先用 `ls` 确认相关目录，再只读取必要文件；不要对整个 `project_dir` 做递归搜索。",
            "- 优先通过 `planner_save_plan` 保存测试计划；若改用内置文件工具，最终 Markdown 仍必须落到规范路径。",
            "- 只有当规范路径下的测试计划 Markdown 已实际落盘后，本阶段才算完成。",
            "- 测试计划落盘后，调用 `browser_run_code_unsafe` 执行关闭浏览器的函数表达式，然后停止。",
            "- 若关闭浏览器后出现 `Target page, context or browser has been closed` 一类报错，可视为成功收尾。",
        ]
        return "\n".join(prompt_sections)

    def _bundled_demo_template_dir(self) -> Path:
        """返回仓库内置的 demo 模板目录。"""

        return bundled_demo_template_dir()

    def _normalized_project_name(self, project_name: Any) -> str | None:
        """把工程名归一化为可判空的字符串。"""

        return normalize_runtime_text(project_name)

    def _normalized_runtime_text(self, value: Any) -> str | None:
        """把运行时文本参数归一化为可判空字符串。"""

        return normalize_runtime_text(value)

    def _normalized_test_plan_files(self, value: Any) -> list[str]:
        """把测试计划输入参数归一化为去重后的字符串列表。"""

        return normalize_string_list(value)

    async def _run_deep_agent(
        self,
        specialist_agent: Any,
        state: WorkflowState,
        execution_context: SpecialistExecutionContext,
        config: RunnableConfig | None = None,
    ) -> WorkflowState:
        """将事件流运行委托给 `PlanRuntimeHelper`。

        这样 Plan Agent 自身只保留"阶段配置 + 校验 + workspace + prompt + 权限"静态职责，
        对齐 Master 子图中"Agent 只做配置与入口、节点执行细节独立承载"的分层方式。
        """

        return await PlanRuntimeHelper(agent=self, settings=self._settings).run(
            specialist_agent=specialist_agent,
            state=state,
            execution_context=execution_context,
            config=config,
        )

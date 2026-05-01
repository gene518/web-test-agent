"""Plan Specialist Agent。

Plan 阶段的职责不是直接产出脚本，而是先把目标页面探索清楚，并把结果沉淀成后续
Generator 可以消费的测试计划。因此这里会比其他 Specialist 更强调页面初始化、事件流观测
和 `planner_save_plan` 的强约束收尾。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from deep_agent.agent.artifacts import extract_plan_artifact_from_planner_payload
from deep_agent.agent.base_agent import BaseSpecialistAgent, SpecialistExecutionContext, SpecialistRuntimeConfig
from deep_agent.config.specialist_file_filter import PLAN_QUERY_FILTER_CONFIG
from deep_agent.agent.plan.prompts.plan_conventions import MOBILE_PLAN_CONVENTIONS_PROMPT
from deep_agent.core.autotest_project_directory import DEFAULT_AUTOTEST_DEMO_PROJECT_NAME, normalize_runtime_text, resolve_autotest_project_dir
from deep_agent.core.runtime_logging import log_debug_event, log_title, with_trace_context
from deep_agent.agent.state import WorkflowState
from deep_agent.agent.plan.prompts.plan import PLAN_SYSTEM_PROMPT
from deep_agent.tools.playwright import PLAN_ALLOWED_PLAYWRIGHT_TEST_MCP_TOOL_IDS


PLAN_RUNTIME_CONFIG = SpecialistRuntimeConfig(
    system_prompt_parts=(PLAN_SYSTEM_PROMPT, MOBILE_PLAN_CONVENTIONS_PROMPT),
    allowed_playwright_test_mcp_tools=PLAN_ALLOWED_PLAYWRIGHT_TEST_MCP_TOOL_IDS,
    load_project_standard=True,
    query_filter_config=PLAN_QUERY_FILTER_CONFIG,
)


class PlanAgent(BaseSpecialistAgent):
    """负责测试计划生成阶段的 Specialist Agent。

    它存在的目的，是在真正写脚本之前先探索页面、明确功能点和输出计划文件，
    这样后续脚本生成阶段可以直接围绕稳定的计划产物开展，而不是重复理解需求。
    """

    agent_type = "plan"
    display_name = "Plan Agent"
    runtime_config = PLAN_RUNTIME_CONFIG

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

        Plan 比其他 Specialist 更需要“确定的可写目录”，因为它会把测试计划文件直接落到项目中。
        当前策略固定为“自动化根目录 / 工程名字”，不再按时间戳生成新目录。
        """

        extracted_params = state.get("extracted_params", {})
        project_name = self._normalized_project_name(extracted_params.get("project_name"))
        return resolve_autotest_project_dir(
            automation_root=self._settings.resolved_default_automation_project_root,
            bundled_template_dir=self._bundled_demo_template_dir(),
            project_name=project_name,
            raw_project_dir=extracted_params.get("project_dir"),
            missing_project_name_error="Plan 模式缺少合法的 `project_name`，无法解析自动化工程目录。",
        )

    def _build_runtime_context_prompt(self, *, state: WorkflowState, workspace_dir: Path | None) -> str:
        """构建 Plan 模式专用的运行时上下文提示词。

        相比基类的通用上下文，Plan 这里会额外把“必须先初始化页面、必须保存计划、保存后才能收尾”
        这类流程约束写进去，目的是防止模型只做分析不真正产出计划文件。
        """

        extracted_params = state.get("extracted_params", {})
        project_name = self._normalized_project_name(extracted_params.get("project_name")) or ""
        url = self._normalized_runtime_text(extracted_params.get("url")) or ""
        feature_points = extracted_params.get("feature_points", [])
        existing_plan_files = self._normalized_test_plan_files(extracted_params.get("test_plan_files"))

        # 这里既放本次请求的动态参数，也放 Plan 阶段的执行约束，
        # 目的是让模型在一个上下文里同时理解“要做什么”和“必须怎么做完”。
        prompt_sections = [
            "## 本次运行上下文",
            f"- project_name: `{project_name}`",
            f"- url: `{url}`",
            f"- project_dir: `{workspace_dir}`",
            f"- automation_root_dir: `{self._settings.resolved_default_automation_project_root.resolve()}`",
            f"- feature_points: {self._format_prompt_value(feature_points)}",
            f"- existing_test_plan_files: {self._format_prompt_value(existing_plan_files)}",
            "- `planner_save_plan.fileName` 必须是相对 `project_dir` 的路径。",
            "## 完成条件",
            "- 必须先调用一次 `planner_setup_page` 初始化页面。",
            f"- 初始化完成后，必须使用 `browser_navigate` 打开 `{url}` 并开始探索。",
            "- 如果用户提供了 `feature_points`，优先覆盖这些功能点，但仍需结合页面探索补全关键场景。",
            "- 如果 `existing_test_plan_files` 非空，表示当前请求可能是在补充或更新已有计划；优先基于这些计划文件延续，而不是凭空新建无关计划。",
            "- 只能使用当前可见工具；不要尝试调用 `planner_submit_plan` 或任何文件工具绕过 planner 工作流。",
            "- 如确需查询工程文件，先用 `ls` 确认相关目录，再只读取必要文件；不要对整个 `project_dir` 做递归搜索。",
            "- 必须通过 `planner_save_plan` 保存测试计划；只有 `planner_save_plan` 成功才算任务完成。",
            "- `planner_save_plan` 成功后，调用 `browser_run_code` 执行关闭浏览器的函数表达式，然后停止。",
            "- 若关闭浏览器后出现 `Target page, context or browser has been closed` 一类报错，可视为成功收尾。",
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

    async def _run_deep_agent(
        self,
        specialist_agent: Any,
        state: WorkflowState,
        execution_context: SpecialistExecutionContext,
        config: RunnableConfig | None = None,
    ) -> WorkflowState:
        """使用事件流执行 Plan，并强制校验 `planner_save_plan`。

        Plan 之所以不用基类默认的 `ainvoke`，是因为它必须在执行过程中持续观测工具事件，
        确认计划文件真的保存成功，而不是只看模型最终吐出的一条自然语言消息。
        """

        existing_messages = state.get("messages", [])
        # 这里单独维护 `planner_save_succeeded / planner_save_error`，目的是把“是否真正落盘成功”
        # 从自然语言回复中剥离出来，改为基于工具事件做硬判断。
        final_output: dict[str, Any] | None = None
        planner_save_succeeded = False
        planner_save_error: str | None = None
        planner_save_payload: dict[str, Any] | None = None
        stage_artifact: dict[str, Any] | None = None
        extracted_params = state.get("extracted_params", {})
        project_name = self._normalized_project_name(extracted_params.get("project_name")) or (
            execution_context.workspace_dir.name if execution_context.workspace_dir is not None else "unknown-project"
        )
        input_plan_files = self._normalized_test_plan_files(extracted_params.get("test_plan_files"))

        try:
            # TODO(重点流程): Plan 使用事件流执行，是为了在模型推理过程中同步监听关键工具调用结果。
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
                if event.get("name") == "planner_save_plan" and event.get("event") == "on_tool_start":
                    payload = event.get("data", {}).get("input")
                    if isinstance(payload, dict):
                        planner_save_payload = payload
                planner_save_succeeded, planner_save_error, stage_artifact = self._update_planner_save_state(
                    planner_save_succeeded,
                    planner_save_error,
                    stage_artifact,
                    planner_save_payload,
                    execution_context.workspace_dir,
                    project_name,
                    input_plan_files,
                    event,
                )
                self.log_planner_save_state(event, planner_save_succeeded, planner_save_error, execution_context.trace_context)
        except Exception as exc:  # noqa: BLE001
            if planner_save_succeeded and self._is_expected_browser_close_error(exc):
                self.log_browser_close_expected(execution_context.trace_context, exc)
                return {
                    "messages": [AIMessage(content="测试计划已保存，浏览器已按预期关闭。")],
                    "artifact": stage_artifact,
                }
            raise

        log_debug_event(self.log_get_logger(), self._settings, log_title("执行", "事件流"), "plan_final_output", self.log_event_trace_context(execution_context.trace_context, "plan_final_output"), planner_save_succeeded=planner_save_succeeded, planner_save_error=planner_save_error, final_output=final_output)

        # 即使模型说“已经完成”，只要没观察到 `planner_save_plan` 成功事件，这次执行也必须判定失败。
        if not planner_save_succeeded:
            error_suffix = f" 最近一次错误：{planner_save_error}" if planner_save_error else ""
            raise RuntimeError(f"Plan Agent 未成功调用 `planner_save_plan` 保存用例。{error_suffix}")

        if final_output is None:
            return {
                "messages": [AIMessage(content="测试计划已保存。")],
                "artifact": stage_artifact,
            }

        # 最终消息只作为用户可见结果；真正的成功标准前面已经由工具事件验证过。
        all_messages = final_output.get("messages", [])
        if not isinstance(all_messages, list):
            return {
                "messages": [AIMessage(content="测试计划已保存。")],
                "artifact": stage_artifact,
            }

        new_messages = all_messages[len(existing_messages) :]
        if not new_messages:
            new_messages = [AIMessage(content="测试计划已保存。")]
        return {
            "messages": new_messages,
            "artifact": stage_artifact,
        }

    def _capture_final_output(
        self,
        current_output: dict[str, Any] | None,
        event: dict[str, Any],
    ) -> dict[str, Any] | None:
        """从根链路的结束事件中提取最终输出。

        这里只信任根链路的 `on_chain_end`，目的是避免子链路或工具内部的局部输出误伤最终结果。
        """

        if event.get("event") != "on_chain_end" or event.get("parent_ids"):
            return current_output

        output = event.get("data", {}).get("output")
        if isinstance(output, dict):
            return output

        return current_output

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

        这个状态机存在的目的，是把 Plan 的完成标准从“模型口头说完成了”收紧成
        “保存计划工具实际成功执行了”。
        """

        if event.get("name") != "planner_save_plan":
            return planner_save_succeeded, planner_save_error, current_artifact

        # 这里只关注 `planner_save_plan`，其他工具无论成功失败都不改变最终完成判定。
        if event.get("event") == "on_tool_error":
            return False, self.log_truncate(event.get("data", {}).get("error")), current_artifact

        if event.get("event") != "on_tool_end":
            return planner_save_succeeded, planner_save_error, current_artifact

        output = event.get("data", {}).get("output")
        if self._tool_output_is_error(output):
            return False, self.log_truncate(output), current_artifact

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
            return False, self.log_truncate(str(exc)), current_artifact

        return True, None, artifact

    def log_planner_save_state(
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
        self.log_tool_state(
            trace_context=trace_context,
            event_name="planner_save_plan",
            status=status,
            error=planner_save_error,
        )

    def _tool_output_is_error(self, output: Any) -> bool:
        """判断工具输出是否表示失败。

        做这层兼容判断的目的，是适配不同工具实现返回 `status`、`content` 或对象属性的差异，
        避免因为输出形态不同而漏掉真实失败。
        """

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
        """判断异常是否为关闭浏览器后的预期错误。

        Plan 在保存完成后会主动触发浏览器关闭，因此这里要识别“收尾成功后抛出的已关闭异常”，
        避免把正确收尾误判成执行失败。
        """

        text = str(exc).lower()
        expected_fragments = (
            "target page, context or browser has been closed",
            "browsercontext.newpage",
            "browser has been closed",
        )
        return any(fragment in text for fragment in expected_fragments)

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

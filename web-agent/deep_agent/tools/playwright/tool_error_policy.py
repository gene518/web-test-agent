"""Playwright MCP 工具错误分类与恢复策略。"""

from __future__ import annotations

from deep_agent.tools.tool_error_handling import DEFAULT_MCP_TOOL_ERROR_POLICY


_TOOL_ERROR_RECOVERY_INSTRUCTIONS = {
    "POINTER_INTERCEPTED": (
        "不要重复完全相同的 click。先重新 `browser_snapshot` 观察页面；"
        "如果目标是输入框，优先改用 `browser_type` 或其他非指针交互。"
    ),
    "TOOL_TIMEOUT": (
        "不要立刻用同样参数重试。先观察当前页面状态、等待页面稳定，"
        "再修正参数、换工具，或跳过非必要探索步骤。"
    ),
    "PLAYWRIGHT_BROWSER_MISSING": (
        "当前环境缺少 Playwright 浏览器依赖。不要继续重复浏览器类操作；"
        "应报告环境问题，并继续任何不依赖浏览器的规划工作。"
    ),
    "RESOURCE_NOT_FOUND": (
        "先确认目标资源、路径、ref 或 selector 是否真实存在。若该资源不是完成任务所必需，可跳过并继续。"
    ),
    "SELECTOR_AMBIGUOUS": (
        "不要重复同一个 selector 或 ref。先重新观察页面，定位更具体的目标元素后再调用工具。"
    ),
}
_NON_RETRYABLE_TOOL_ERROR_TYPES = frozenset({"PLAYWRIGHT_BROWSER_MISSING"})


class PlaywrightMCPToolErrorPolicy:
    """定义 Playwright 工具族的错误分类和恢复规则。"""

    def classify_tool_error(self, error_message: str) -> str:
        normalized_message = error_message.lower()
        if "intercepts pointer events" in normalized_message or "intercepts pointer" in normalized_message:
            return "POINTER_INTERCEPTED"
        if "strict mode violation" in normalized_message or "matches more than one element" in normalized_message:
            return "SELECTOR_AMBIGUOUS"
        if "timeout" in normalized_message or "timed out" in normalized_message:
            return "TOOL_TIMEOUT"
        if (
            "executable doesn't exist" in normalized_message
            or "download new browsers" in normalized_message
            or "playwright install" in normalized_message
        ):
            return "PLAYWRIGHT_BROWSER_MISSING"
        if (
            "no such file or directory" in normalized_message
            or "does not exist" in normalized_message
            or "cannot find" in normalized_message
            or "not found" in normalized_message
            or "enoent" in normalized_message
        ):
            return "RESOURCE_NOT_FOUND"
        return "UNKNOWN_TOOL_ERROR"

    def recovery_instruction_for(self, error_type: str) -> str:
        return _TOOL_ERROR_RECOVERY_INSTRUCTIONS.get(
            error_type,
            DEFAULT_MCP_TOOL_ERROR_POLICY.recovery_instruction_for(error_type),
        )

    def is_retryable(self, error_type: str) -> bool:
        return error_type not in _NON_RETRYABLE_TOOL_ERROR_TYPES


PLAYWRIGHT_MCP_TOOL_ERROR_POLICY = PlaywrightMCPToolErrorPolicy()

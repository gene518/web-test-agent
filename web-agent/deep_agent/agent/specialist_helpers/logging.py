"""Specialist 运行时日志辅助。

本模块把 Plan / Generator / Healer 共用的日志模板（事件流打点、关键工具状态切换、
浏览器关闭兜底记录等）收敛到一处，避免每个 Specialist 自己维护一套格式。
调用方是 `BaseSpecialistAgent` 通过 mixin 继承。
"""

from __future__ import annotations

import logging
from typing import Any

from deep_agent.core.runtime_logging import (
    debug_max_chars,
    format_value_for_log,
    get_logger,
    log_title,
)


class SpecialistLoggingMixin:
    """把 Specialist 在事件流中要打的日志集中到一个 mixin。

    调用方是 `BaseSpecialistAgent`。让 Plan / Generator / Healer 在事件循环里只负责
    调用 `log_stream_event`、`log_tool_state`、`log_browser_close_expected` 等已对齐
    的日志入口，不再各自拼接 `log_title` 和 `trace_context`。
    """

    agent_type: str
    _settings: Any

    def log_get_logger(self) -> logging.Logger:
        """返回当前 Agent 模块对应的日志对象。"""

        return get_logger(type(self).__module__)

    def log_stream_event(self, event: dict[str, Any], trace_context: dict[str, Any] | None = None) -> None:
        """打印 Specialist 事件流中的关键模型与工具事件。"""

        event_name = event.get("event", "")
        name = event.get("name", "")
        base_trace_context = trace_context or {}
        node_name = base_trace_context.get("node_name") or f"{self.agent_type}_node"
        agent_logger = self.log_get_logger()

        if event_name == "on_chat_model_start":
            agent_logger.info("%s event=model_start trace=%s name=%s input=%s",
                log_title("执行", "事件流", node_name=node_name), self.log_event_trace_context(base_trace_context, "model_start"), name, format_value_for_log(event.get("data", {}).get("input"), self._settings),)
            return

        if event_name == "on_chat_model_end":
            agent_logger.info("%s event=model_end trace=%s name=%s output=%s",
                log_title("执行", "事件流", node_name=node_name), self.log_event_trace_context(base_trace_context, "model_end"), name, format_value_for_log(event.get("data", {}).get("output"), self._settings),)
            return

        if event_name == "on_tool_start":
            agent_logger.info("%s event=tool_start trace=%s name=%s input=%s",
                log_title("执行", "事件流", node_name=node_name), self.log_event_trace_context(base_trace_context, "tool_start"), name, format_value_for_log(event.get("data", {}).get("input"), self._settings),)
            return

        if event_name == "on_tool_end":
            agent_logger.info("%s event=tool_end trace=%s name=%s output=%s",
                log_title("执行", "事件流", node_name=node_name), self.log_event_trace_context(base_trace_context, "tool_end"), name, format_value_for_log(event.get("data", {}).get("output"), self._settings),)
            return

        if event_name == "on_tool_error":
            agent_logger.warning("%s event=tool_error trace=%s name=%s error=%s",
                log_title("执行", "事件流", node_name=node_name), self.log_event_trace_context(base_trace_context, "tool_error"), name, format_value_for_log(event.get("data", {}).get("error"), self._settings),)
            return

        if event_name == "on_chain_end" and not event.get("parent_ids"):
            agent_logger.info("%s event=deep_agent_end trace=%s name=%s output=%s",
                log_title("执行", "事件流", node_name=node_name), self.log_event_trace_context(base_trace_context, "deep_agent_end"), name, format_value_for_log(event.get("data", {}).get("output"), self._settings),)

    def log_browser_close_expected(self, trace_context: dict[str, Any], exc: Exception) -> None:
        """记录浏览器在收尾阶段按预期关闭的异常。"""

        node_name = trace_context.get("node_name") or f"{self.agent_type}_node"
        self.log_get_logger().info("%s event=browser_close_expected trace=%s error=%s",
            log_title("执行", "事件流", node_name=node_name), self.log_event_trace_context(trace_context, "browser_close_expected"), self.log_truncate(str(exc)),)

    def log_tool_state(
        self,
        *,
        trace_context: dict[str, Any],
        event_name: str,
        status: str,
        error: str | None,
    ) -> None:
        """记录关键工具执行状态，便于按 session grep。"""

        node_name = trace_context.get("node_name") or f"{self.agent_type}_node"
        self.log_get_logger().info("%s event=%s trace=%s status=%s error=%s",
            log_title("执行", "事件流", node_name=node_name), event_name, self.log_event_trace_context(trace_context, event_name), status, error,)

    def log_event_trace_context(self, trace_context: dict[str, Any], event_name: str) -> dict[str, Any]:
        """复用节点 trace 标识，只替换当前日志事件名。"""

        event_trace_context = dict(trace_context)
        event_trace_context["event_name"] = event_name
        return event_trace_context

    def log_truncate(self, value: Any, max_length: int | None = None) -> str:
        """压缩日志输出长度。"""

        resolved_max_length = max_length if max_length is not None else debug_max_chars(self._settings)
        text = value if isinstance(value, str) else repr(value)
        if len(text) <= resolved_max_length:
            return text
        return f"{text[:resolved_max_length]}..."

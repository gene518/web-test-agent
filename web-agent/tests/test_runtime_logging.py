from __future__ import annotations

import json
import logging
import unittest
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from deep_agent.core.runtime_logging import configure_logging, build_trace_context, log_debug_event, log_title, serialize_message, serialize_messages, with_trace_context


class RuntimeLoggingTestCase(unittest.TestCase):
    def test_configure_logging_suppresses_watchfiles_change_noise(self) -> None:
        watchfiles_logger = logging.getLogger("watchfiles.main")
        original_level = watchfiles_logger.level

        try:
            configure_logging("INFO")

            self.assertEqual(watchfiles_logger.level, logging.WARNING)
        finally:
            watchfiles_logger.setLevel(original_level)

    def test_configure_logging_formats_structured_args_as_json(self) -> None:
        original_factory = logging.getLogRecordFactory()
        logger = logging.getLogger("deep_agent.tests.runtime_logging.json_args")

        try:
            configure_logging("INFO")

            with self.assertLogs(logger, level="INFO") as log_capture:
                logger.info(
                    "payload=%s trace=%s",
                    {"text": "中文", "missing": None, "items": [1, True]},
                    {"node_name": "master_node", "run_id": None},
                )

            message = log_capture.records[0].getMessage()
            payload_text = message.split("payload=", 1)[1].split(" trace=", 1)[0]
            trace_text = message.split(" trace=", 1)[1]

            self.assertEqual(json.loads(payload_text), {"text": "中文", "missing": None, "items": [1, True]})
            self.assertEqual(json.loads(trace_text), {"node_name": "master_node", "run_id": None})
        finally:
            logging.setLogRecordFactory(original_factory)

    def test_configure_logging_handles_records_without_logger_name(self) -> None:
        original_factory = logging.getLogRecordFactory()

        try:
            configure_logging("INFO")

            record = logging.makeLogRecord({})

            self.assertIsNone(record.name)
        finally:
            logging.setLogRecordFactory(original_factory)

    def test_trace_context_uses_thread_id_as_session_id(self) -> None:
        config = {
            "configurable": {"thread_id": "debug-20260425-120000"},
            "metadata": {"run_id": "run-123"},
        }

        trace_context = build_trace_context(config, node_name="plan_node", event_name="node_enter")

        self.assertEqual(trace_context["session_id"], "debug-20260425-120000")
        self.assertEqual(trace_context["thread_id"], "debug-20260425-120000")
        self.assertEqual(trace_context["run_id"], "run-123")
        self.assertEqual(trace_context["node_name"], "plan_node")
        self.assertEqual(trace_context["event_name"], "node_enter")

    def test_log_title_can_include_node_name_in_prefix(self) -> None:
        self.assertEqual(log_title("执行", "节点入参", node_name="master_node"), "【master_node@@执行@@节点入参】")

    def test_debug_event_puts_node_name_in_title_prefix(self) -> None:
        logger = logging.getLogger("tests.runtime_logging.debug_event")
        settings = SimpleNamespace(agent_debug_trace=True, agent_debug_full_messages=False, agent_debug_max_chars=4000)

        with self.assertLogs(logger, level="INFO") as log_capture:
            log_debug_event(logger, settings, log_title("模型", "调用"), "model_start", {"node_name": "master_node"})

        self.assertIn("【master_node@@模型@@调用】", log_capture.output[0])
        self.assertNotIn("node_name=", log_capture.output[0])

    def test_with_trace_context_merges_metadata_and_configurable(self) -> None:
        merged_config = with_trace_context(
            {"metadata": {"source": "test"}},
            {"session_id": "session-a", "thread_id": "thread-a", "run_id": "run-a"},
            recursion_limit=999,
        )

        self.assertEqual(merged_config["metadata"]["source"], "test")
        self.assertEqual(merged_config["metadata"]["session_id"], "session-a")
        self.assertEqual(merged_config["configurable"]["thread_id"], "thread-a")
        self.assertEqual(merged_config["configurable"]["run_id"], "run-a")
        self.assertEqual(merged_config["recursion_limit"], 999)
        self.assertNotIn("recursion_limit", merged_config["configurable"])

    def test_message_serializer_keeps_message_classes(self) -> None:
        messages = [
            SystemMessage(content="system prompt"),
            HumanMessage(content="user request"),
            AIMessage(content="assistant response"),
            ToolMessage(content="tool output", tool_call_id="tool-call-1"),
        ]

        serialized = serialize_messages(messages, max_text_length=1000)

        self.assertEqual(
            [message["type"] for message in serialized],
            ["SystemMessage", "HumanMessage", "AIMessage", "ToolMessage"],
        )
        self.assertEqual(serialized[3]["tool_call_id"], "tool-call-1")

    def test_message_serializer_truncates_content_by_configured_length(self) -> None:
        serialized = serialize_message(HumanMessage(content="x" * 20), max_text_length=8)

        self.assertEqual(serialized["content"], "xxxxxxxx...")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from langchain_core.messages import AIMessage, ToolMessage

from deep_agent.core.display_message import VisibleTranscriptCollector, build_runtime_message_result, sanitize_display_messages


class FakeCommand:
    def __init__(self, update):  # noqa: ANN001
        self.update = update


class VisibleRuntimeMessagesTestCase(unittest.TestCase):
    def test_collector_extracts_messages_from_stream_and_command_updates(self) -> None:
        collector = VisibleTranscriptCollector()

        collector.consume_event(
            {
                "event": "on_chat_model_end",
                "data": {
                    "output": AIMessage(
                        content="",
                        id="ai-tool-call",
                        tool_calls=[
                            {
                                "name": "write_todos",
                                "args": {"items": ["a", "b"]},
                                "id": "call-1",
                                "type": "tool_call",
                            }
                        ],
                    )
                },
                "parent_ids": [],
            }
        )
        collector.consume_event(
            {
                "event": "on_tool_end",
                "data": {
                    "output": FakeCommand(
                        {
                            "messages": [
                                ToolMessage(
                                    content="todos updated",
                                    id="tool-1",
                                    name="write_todos",
                                    tool_call_id="call-1",
                                )
                            ]
                        }
                    )
                },
                "parent_ids": [],
            }
        )
        collector.consume_event(
            {
                "event": "on_chain_end",
                "data": {"output": {"messages": [AIMessage(content="final", id="ai-final")]}},
                "parent_ids": [],
            }
        )

        self.assertEqual([message.id for message in collector.messages], ["tool-1"])
        self.assertEqual(collector.final_output["messages"][0].id, "ai-final")

    def test_collector_extracts_tool_start_message(self) -> None:
        collector = VisibleTranscriptCollector()

        messages = collector.consume_event(
            {
                "event": "on_tool_start",
                "name": "browser_snapshot",
                "data": {"input": {"random": "input"}},
                "parent_ids": [],
            }
        )

        self.assertEqual(len(messages), 1)
        self.assertIsInstance(messages[0], AIMessage)
        self.assertTrue(messages[0].id.startswith("display-tool-start-browser_snapshot-"))
        self.assertEqual(messages[0].content, "正在调用工具 `browser_snapshot`。")

    def test_build_runtime_message_result_falls_back_to_final_output(self) -> None:
        collector = VisibleTranscriptCollector(
            final_output={
                "messages": [
                    AIMessage(content="existing", id="ai-existing"),
                    AIMessage(content="final", id="ai-final"),
                ]
            }
        )

        result = build_runtime_message_result(
            collector=collector,
            existing_messages=[AIMessage(content="existing", id="ai-existing")],
            fallback_message="fallback",
        )

        self.assertEqual([message.id for message in result["messages"]], ["ai-final"])

    def test_build_runtime_message_result_appends_missing_final_output_messages(self) -> None:
        collector = VisibleTranscriptCollector(
            messages=[AIMessage(content="阶段进展", id="ai-progress")],
            final_output={
                "messages": [
                    AIMessage(content="existing", id="ai-existing"),
                    AIMessage(content="", id="ai-tool-call"),
                    AIMessage(content="final", id="ai-final"),
                ]
            },
        )

        result = build_runtime_message_result(
            collector=collector,
            existing_messages=[AIMessage(content="existing", id="ai-existing")],
            fallback_message="fallback",
        )

        self.assertEqual([message.id for message in result["messages"]], ["ai-progress", "ai-final"])

    def test_build_runtime_message_result_keeps_final_tool_messages(self) -> None:
        collector = VisibleTranscriptCollector(
            final_output={
                "messages": [
                    AIMessage(content="existing", id="ai-existing"),
                    AIMessage(content="", id="ai-tool-call"),
                    ToolMessage(
                        content='{"ok": true}',
                        id="tool-final",
                        name="write_todos",
                        tool_call_id="call-final",
                    ),
                    AIMessage(content="final", id="ai-final"),
                ]
            }
        )

        result = build_runtime_message_result(
            collector=collector,
            existing_messages=[AIMessage(content="existing", id="ai-existing")],
            fallback_message="fallback",
        )

        self.assertEqual([message.id for message in result["messages"]], ["tool-final", "ai-final"])

    def test_sanitize_display_messages_truncates_large_content_and_tool_args(self) -> None:
        large_text = "x" * 13000
        messages = sanitize_display_messages(
            [
                AIMessage(
                    content=large_text,
                    id="ai-large",
                    tool_calls=[
                        {
                            "name": "generator_write_test",
                            "args": {"code": large_text},
                            "id": "call-write",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        )

        self.assertEqual(messages[0].id, "ai-large")
        self.assertLess(len(messages[0].content), len(large_text))
        self.assertIn("UI 展示已截断", messages[0].content)
        self.assertIn("UI 展示已截断", messages[0].tool_calls[0]["args"]["code"])

    def test_sanitize_display_messages_hides_reasoning_but_preserves_answer(self) -> None:
        source = AIMessage(
            content="<think>private chain of thought</think>\n最终答案",
            id="ai-reasoning",
            additional_kwargs={"reasoning_content": "private", "safe_field": "kept"},
        )

        sanitized = sanitize_display_messages([source])[0]

        self.assertEqual(source.content, "<think>private chain of thought</think>\n最终答案")
        self.assertEqual(sanitized.content, "最终答案")
        self.assertNotIn("reasoning_content", sanitized.additional_kwargs)
        self.assertEqual(sanitized.additional_kwargs["safe_field"], "kept")

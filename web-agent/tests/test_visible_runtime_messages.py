from __future__ import annotations

import unittest

from langchain_core.messages import AIMessage, ToolMessage

from deep_agent.core.display_message import VisibleTranscriptCollector, build_runtime_message_result


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

        self.assertEqual([message.id for message in collector.messages], ["ai-tool-call", "tool-1"])
        self.assertEqual(collector.final_output["messages"][0].id, "ai-final")

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
            messages=[AIMessage(content="", id="ai-tool-call")],
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

        self.assertEqual([message.id for message in result["messages"]], ["ai-tool-call", "ai-final"])

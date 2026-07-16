from __future__ import annotations

import unittest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deep_agent.core.model_message_history import normalize_tool_message_history


class ModelMessageHistoryTestCase(unittest.TestCase):
    def test_drops_orphan_tool_results_from_legacy_threads(self) -> None:
        messages = [
            HumanMessage(content="工具展示开关验证", id="human-1"),
            ToolMessage(
                content='{"ok":true}',
                id="tool-1",
                name="browser_test",
                tool_call_id="call-orphan",
            ),
            AIMessage(content="验证完成", id="ai-1"),
            HumanMessage(content="继续聊天", id="human-2"),
        ]

        normalized = normalize_tool_message_history(messages)

        self.assertEqual([message.id for message in normalized], ["human-1", "ai-1", "human-2"])

    def test_keeps_tool_results_with_matching_ai_calls(self) -> None:
        messages = [
            AIMessage(
                content="",
                id="ai-tool",
                tool_calls=[{"id": "call-1", "name": "browser_test", "args": {}}],
            ),
            ToolMessage(content="done", id="tool-1", tool_call_id="call-1"),
            HumanMessage(content="继续", id="human-1"),
        ]

        normalized = normalize_tool_message_history(messages)

        self.assertEqual([message.id for message in normalized], ["ai-tool", "tool-1", "human-1"])

    def test_fills_missing_tool_results_before_the_next_message(self) -> None:
        messages = [
            AIMessage(
                content="",
                id="ai-tool",
                tool_calls=[
                    {"id": "call-1", "name": "first_tool", "args": {}},
                    {"id": "call-2", "name": "second_tool", "args": {}},
                ],
            ),
            ToolMessage(content="done", id="tool-1", tool_call_id="call-1"),
            HumanMessage(content="继续", id="human-1"),
        ]

        normalized = normalize_tool_message_history(messages)

        self.assertEqual([message.type for message in normalized], ["ai", "tool", "tool", "human"])
        synthetic = normalized[2]
        self.assertIsInstance(synthetic, ToolMessage)
        self.assertEqual(synthetic.tool_call_id, "call-2")
        self.assertEqual(synthetic.name, "second_tool")

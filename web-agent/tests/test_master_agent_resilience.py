from __future__ import annotations

import unittest

from langchain_core.messages import AIMessage, HumanMessage
from langgraph_api.errors import UserInterrupt

from deep_agent.agent.master.master_agent import MasterAgent
from deep_agent.core.config import AppSettings


class CapturingModel:
    def __init__(self, response: object) -> None:
        self.response = response
        self.messages: list[object] = []

    async def ainvoke(self, messages, config=None):  # noqa: ANN001
        self.messages = list(messages)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def _master_with_model(model: object, *, max_turns: int = 1) -> MasterAgent:
    master = MasterAgent.__new__(MasterAgent)
    master._settings = AppSettings(_env_file=None, max_conversation_turns=max_turns)
    master._model = model
    return master


class MasterAgentResilienceTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_final_summary_propagates_user_cancellation(self) -> None:
        master = _master_with_model(CapturingModel(UserInterrupt()))

        with self.assertRaises(UserInterrupt):
            await master.summarize_final_response(
                state={"messages": [HumanMessage(content="stop")]},
                stage_name="Plan Agent",
                raw_result={"status": "success"},
            )

    async def test_conversation_summary_only_sends_unsummarized_messages(self) -> None:
        model = CapturingModel(AIMessage(content="updated summary"))
        master = _master_with_model(model)
        messages = [
            HumanMessage(content="old request"),
            AIMessage(content="old response"),
            HumanMessage(content="new request"),
            AIMessage(content="new response"),
        ]

        result = await master.ensure_conversation_summary(
            {
                "messages": messages,
                "conversation_summary": "existing summary",
                "summarized_message_count": 2,
            }
        )

        prompt = str(model.messages[-1].content)
        self.assertNotIn("old request", prompt)
        self.assertNotIn("old response", prompt)
        self.assertIn("new request", prompt)
        self.assertIn("new response", prompt)
        self.assertEqual(result["summarized_message_count"], 4)


if __name__ == "__main__":
    unittest.main()

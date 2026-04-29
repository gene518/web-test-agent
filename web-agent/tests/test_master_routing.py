from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import HumanMessage

from deep_agent.core.config import AppSettings
from deep_agent.agent.master.master_agent import MasterAgent
from deep_agent.agent.master.models.intent import IntentClassification


class MasterRoutingTestCase(unittest.TestCase):
    def test_plan_intent_routes_to_plan(self) -> None:
        agent = object.__new__(MasterAgent)
        classification = IntentClassification(intent_type="plan", project_name="baidu-demo", url="https://example.com")

        self.assertEqual(agent._decide_next_action(classification.intent_type, []), "plan")


class MasterAskParamsTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_master_asks_for_project_name_when_missing(self) -> None:
        agent = object.__new__(MasterAgent)
        agent._settings = AppSettings()

        result = await agent.ask_for_missing_params(
            {
                "messages": [HumanMessage(content="帮我给百度写测试用例")],
                "agent_type": "plan",
                "missing_params": ["project_name", "url"],
                "extracted_params": {"url": "https://www.baidu.com"},
            }
        )

        self.assertIn("自动化工程名字", result["messages"][0].content)
        self.assertIn("url=https://www.baidu.com", result["messages"][0].content)

    async def test_master_execute_adds_ask_params_context_for_classifier(self) -> None:
        agent = object.__new__(MasterAgent)
        agent._settings = AppSettings()
        structured_model = MagicMock()
        structured_model.ainvoke = AsyncMock(
            return_value=IntentClassification(
                intent_type="plan",
                project_name="demo",
                url="https://www.baidu.com",
            )
        )
        agent._model = MagicMock()
        agent._model.with_structured_output.return_value = structured_model

        result = await agent.execute(
            {
                "messages": [HumanMessage(content='"project_name": "demo"')],
                "agent_type": "plan",
                "extracted_params": {"url": "https://www.baidu.com"},
                "missing_params": ["project_name"],
                "next_action": "ask_params",
                "routing_reason": "上一轮缺少 project_name。",
            }
        )

        model_messages = structured_model.ainvoke.await_args.args[0]
        self.assertEqual(len(model_messages), 3)
        self.assertIn("ask_params", model_messages[1].content)
        self.assertIn("https://www.baidu.com", model_messages[1].content)
        self.assertIn("project_name", model_messages[1].content)
        self.assertEqual(result["agent_type"], "plan")
        self.assertEqual(
            result["extracted_params"],
            {
                "project_name": "demo",
                "url": "https://www.baidu.com",
            },
        )
        self.assertEqual(result["missing_params"], [])
        self.assertEqual(result["next_action"], "plan")

    async def test_master_execute_does_not_merge_previous_params_into_model_output(self) -> None:
        agent = object.__new__(MasterAgent)
        agent._settings = AppSettings()
        structured_model = MagicMock()
        structured_model.ainvoke = AsyncMock(
            return_value=IntentClassification(intent_type="plan", project_name="demo", url=None)
        )
        agent._model = MagicMock()
        agent._model.with_structured_output.return_value = structured_model

        result = await agent.execute(
            {
                "messages": [HumanMessage(content='"project_name": "demo"')],
                "agent_type": "plan",
                "extracted_params": {"url": "https://www.baidu.com"},
                "missing_params": ["project_name"],
                "next_action": "ask_params",
            }
        )

        self.assertEqual(result["agent_type"], "plan")
        self.assertEqual(
            result["extracted_params"],
            {
                "project_name": "demo",
            },
        )
        self.assertEqual(result["missing_params"], ["url"])
        self.assertEqual(result["next_action"], "ask_params")

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from deep_agent.agent.master.models.thread_title import ThreadTitleGeneration
from deep_agent.thread_title_workflow import (
    ThreadTitleNode,
    build_thread_title_workflow,
)


class FakeThreadTitleNode:
    async def execute(self, state, config=None):  # noqa: ANN001
        return {"thread_title": f"总结：{state['source_text']}"}


class ThreadTitleWorkflowTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_graph_exposes_only_thread_title_output(self) -> None:
        graph = build_thread_title_workflow(title_node=FakeThreadTitleNode())

        result = await graph.ainvoke({"source_text": "修复会话切换"})

        self.assertEqual(result, {"thread_title": "总结：修复会话切换"})

    async def test_empty_source_skips_model_call(self) -> None:
        node = ThreadTitleNode.__new__(ThreadTitleNode)

        result = await node.execute({"source_text": "  "})

        self.assertEqual(result, {"thread_title": None})

    async def test_node_returns_normalized_structured_title(self) -> None:
        node = ThreadTitleNode.__new__(ThreadTitleNode)
        node._model = object()
        node._capabilities = object()
        node._connection = object()
        structured_result = SimpleNamespace(
            parsed=ThreadTitleGeneration(thread_title="**修复历史标题。**")
        )

        with patch(
            "deep_agent.thread_title_workflow.invoke_structured",
            new=AsyncMock(return_value=structured_result),
        ):
            result = await node.execute({"source_text": "历史列表现在直接展示首句"})

        self.assertEqual(result, {"thread_title": "修复历史标题"})


def test_langgraph_config_exposes_stateless_title_graph() -> None:
    config_path = Path(__file__).resolve().parents[1] / "langgraph.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert (
        config["graphs"]["web-autotest-thread-title"]
        == "./deep_agent/app.py:title_graph"
    )


if __name__ == "__main__":
    unittest.main()

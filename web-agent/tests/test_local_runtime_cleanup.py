from __future__ import annotations

import unittest
from uuid import uuid4

from deep_agent.core.local_runtime_cleanup import mark_stale_runs_interrupted


class LocalRuntimeCleanupTestCase(unittest.TestCase):
    def test_marks_only_target_graph_active_runs_interrupted(self) -> None:
        target_assistant_id = uuid4()
        other_assistant_id = uuid4()
        target_thread_id = uuid4()
        other_thread_id = uuid4()
        completed_thread_id = uuid4()
        store = {
            "assistants": [
                {
                    "assistant_id": target_assistant_id,
                    "graph_id": "web-autotest-agent",
                    "metadata": {},
                },
                {
                    "assistant_id": other_assistant_id,
                    "graph_id": "other-agent",
                    "metadata": {},
                },
            ],
            "runs": [
                {
                    "run_id": uuid4(),
                    "thread_id": target_thread_id,
                    "assistant_id": target_assistant_id,
                    "status": "pending",
                },
                {
                    "run_id": uuid4(),
                    "thread_id": target_thread_id,
                    "assistant_id": target_assistant_id,
                    "status": "running",
                },
                {
                    "run_id": uuid4(),
                    "thread_id": other_thread_id,
                    "assistant_id": other_assistant_id,
                    "status": "pending",
                },
                {
                    "run_id": uuid4(),
                    "thread_id": completed_thread_id,
                    "assistant_id": target_assistant_id,
                    "status": "success",
                },
            ],
            "threads": [
                {"thread_id": target_thread_id, "status": "busy"},
                {"thread_id": other_thread_id, "status": "busy"},
                {"thread_id": completed_thread_id, "status": "idle"},
            ],
        }

        interrupted_count = mark_stale_runs_interrupted(store)

        self.assertEqual(interrupted_count, 2)
        self.assertEqual(store["runs"][0]["status"], "interrupted")
        self.assertEqual(store["runs"][1]["status"], "interrupted")
        self.assertEqual(store["runs"][2]["status"], "pending")
        self.assertEqual(store["runs"][3]["status"], "success")
        self.assertEqual(store["threads"][0]["status"], "idle")
        self.assertEqual(store["threads"][1]["status"], "busy")
        self.assertEqual(len(store["runs"]), 4)
        self.assertEqual(len(store["threads"]), 3)


if __name__ == "__main__":
    unittest.main()

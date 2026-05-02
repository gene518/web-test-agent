from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from deep_agent.core.config import AppSettings
from deep_agent.portal.api import create_app
from deep_agent.portal.events import PortalEventHub
from deep_agent.portal.models import ToolEvent
from deep_agent.portal.runner import PortalRunner
from deep_agent.portal.store import PortalStore


class FakeRunner:
    def __init__(self, settings, store, hub) -> None:  # noqa: ANN001
        self.settings = settings
        self.store = store
        self.hub = hub

    async def start_message(self, session_id: str, *, content: str, selected_file_path: str | None = None) -> None:
        _, turn = self.store.start_turn(session_id, content=content, selected_file_path=selected_file_path)
        await self._publish(session_id, "message_started", turn.turn_id, {"turnId": turn.turn_id})
        tool_event = ToolEvent(event_id="tool-1", name="planner_save_plan", status="completed", output_summary="saved")
        session = self.store.append_tool_event(session_id, turn.turn_id, node_name="plan_node", event=tool_event)
        await self._publish(
            session_id,
            "tool_updated",
            turn.turn_id,
            {
                "nodeName": "plan_node",
                "trace": session.turns[-1].node_traces[-1].model_dump(mode="json", by_alias=True),
            },
        )
        session = self.store.complete_turn(session_id, turn.turn_id, assistant_text="fake complete")
        await self._publish(
            session_id,
            "message_completed",
            turn.turn_id,
            {"snapshot": session.to_snapshot().model_dump(mode="json", by_alias=True)},
        )

    async def shutdown(self) -> None:
        return None

    async def _publish(self, session_id: str, event_type: str, turn_id: str | None, payload: dict[str, Any]) -> None:
        event = self.store.next_event(session_id, event_type, turn_id=turn_id, payload=payload)
        await self.hub.publish(event)


class PortalApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.automation_root = self.root / "projects"
        self.project_dir = self.automation_root / "demo"
        (self.project_dir / "test_case").mkdir(parents=True)
        (self.project_dir / "test_case" / "demo.spec.ts").write_text("test('demo')\n", encoding="utf-8")
        self.settings = AppSettings(default_automation_project_root=str(self.automation_root))
        self.app = create_app(
            settings=self.settings,
            store_path=self.root / "portal" / "sessions.json",
            runner_factory=lambda settings, store, hub: FakeRunner(settings, store, hub),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_session_history_projects_active_project_and_message_flow(self) -> None:
        with TestClient(self.app) as client:
            created = client.post("/api/portal/sessions", json={"title": "Portal 调试"})
            self.assertEqual(created.status_code, 200)
            session_id = created.json()["snapshot"]["sessionId"]

            history = client.get("/api/portal/history")
            self.assertEqual(history.status_code, 200)
            self.assertEqual(history.json()["history"][0]["sessionId"], session_id)

            projects = client.get("/api/portal/projects")
            self.assertEqual(projects.status_code, 200)
            self.assertEqual(projects.json()["projects"][0]["projectName"], "demo")

            active = client.post(f"/api/portal/sessions/{session_id}/active-project", json={"projectName": "demo"})
            self.assertEqual(active.status_code, 200)
            self.assertEqual(active.json()["snapshot"]["activeProject"]["projectName"], "demo")
            self.assertEqual(active.json()["snapshot"]["fileTree"][0]["name"], "test_case")

            selected = client.post(
                f"/api/portal/sessions/{session_id}/selected-file",
                json={"filePath": "test_case/demo.spec.ts"},
            )
            self.assertEqual(selected.status_code, 200)
            self.assertEqual(selected.json()["snapshot"]["selectedFilePath"], "test_case/demo.spec.ts")

            message = client.post(f"/api/portal/sessions/{session_id}/messages", json={"content": "生成测试计划"})
            self.assertEqual(message.status_code, 200)
            self.assertEqual(message.json()["snapshot"]["messages"][-1]["content"], "fake complete")

            detail = client.get(f"/api/portal/sessions/{session_id}")
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.json()["turns"][0]["nodeTraces"][0]["toolEvents"][0]["name"], "planner_save_plan")

    def test_invalid_project_and_selected_file_are_rejected(self) -> None:
        with TestClient(self.app) as client:
            session_id = client.post("/api/portal/sessions", json={}).json()["snapshot"]["sessionId"]

            missing_project = client.post(
                f"/api/portal/sessions/{session_id}/active-project",
                json={"projectName": "missing"},
            )
            self.assertEqual(missing_project.status_code, 404)

            client.post(f"/api/portal/sessions/{session_id}/active-project", json={"projectName": "demo"})
            invalid_file = client.post(
                f"/api/portal/sessions/{session_id}/selected-file",
                json={"filePath": "../escape.txt"},
            )
            self.assertEqual(invalid_file.status_code, 400)


class FakeStateSnapshot:
    def __init__(self, values: dict[str, Any] | None = None, interrupts: tuple[Any, ...] = ()) -> None:
        self.values = values or {}
        self.interrupts = interrupts


class FakeGraph:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    async def astream_events(self, input_data, config=None, version="v2"):  # noqa: ANN001
        yield {"event": "on_chain_start", "name": "plan_node", "metadata": {"langgraph_node": "plan_node"}, "data": {"input": input_data}}
        if self.fail:
            raise RuntimeError("fake graph failed")
        yield {"event": "on_chat_model_start", "name": "model", "metadata": {"langgraph_node": "plan_node"}, "data": {"input": "prompt"}}
        yield {"event": "on_chat_model_end", "name": "model", "metadata": {"langgraph_node": "plan_node"}, "data": {"output": "model output"}}
        yield {
            "event": "on_tool_start",
            "name": "planner_save_plan",
            "metadata": {"langgraph_node": "plan_node"},
            "data": {"input": {"fileName": "test_case/aaaplanning_demo/aaa_demo.md"}},
        }
        yield {
            "event": "on_tool_end",
            "name": "planner_save_plan",
            "metadata": {"langgraph_node": "plan_node"},
            "data": {"output": "saved"},
        }
        yield {"event": "on_chain_end", "name": "plan_node", "metadata": {"langgraph_node": "plan_node"}, "data": {"output": "done"}}

    async def aget_state(self, config=None):  # noqa: ANN001
        return FakeStateSnapshot(
            {
                "final_summary": "真实流程完成",
                "latest_artifacts": {"plan": {"project_name": "demo", "project_dir": "/unused/demo"}},
            }
        )


class PortalRunnerTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.automation_root = self.root / "projects"
        (self.automation_root / "demo").mkdir(parents=True)
        self.settings = AppSettings(default_automation_project_root=str(self.automation_root))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_runner_emits_realtime_event_types_and_project_change(self) -> None:
        store = PortalStore(self.root / "portal" / "sessions.json")
        hub = PortalEventHub()
        runner = PortalRunner(settings=self.settings, store=store, hub=hub, graph=FakeGraph())
        session = store.create_session()

        events_task = self._collect_until(hub, session.session_id, "message_completed")
        await asyncio.sleep(0)
        await runner.start_message(session.session_id, content="生成测试计划")
        event_types = await events_task

        self.assertIn("message_started", event_types)
        self.assertIn("node_updated", event_types)
        self.assertIn("tool_updated", event_types)
        self.assertIn("project_changed", event_types)
        self.assertIn("message_completed", event_types)
        self.assertEqual(store.snapshot(session.session_id).active_project.project_name, "demo")  # type: ignore[union-attr]
        await runner.shutdown()

    async def test_runner_emits_message_failed(self) -> None:
        store = PortalStore(self.root / "portal" / "sessions.json")
        hub = PortalEventHub()
        runner = PortalRunner(settings=self.settings, store=store, hub=hub, graph=FakeGraph(fail=True))
        session = store.create_session()

        events_task = self._collect_until(hub, session.session_id, "message_failed")
        await asyncio.sleep(0)
        await runner.start_message(session.session_id, content="生成测试计划")
        event_types = await events_task

        self.assertIn("message_started", event_types)
        self.assertIn("message_failed", event_types)
        self.assertEqual(store.snapshot(session.session_id).run_status, "failed")
        await runner.shutdown()

    def _collect_until(self, hub: PortalEventHub, session_id: str, final_type: str):
        async def collect() -> list[str]:
            event_types: list[str] = []
            async for event in hub.subscribe(session_id):
                event_types.append(event.type)
                if event.type == final_type:
                    return event_types
            return event_types

        return asyncio.create_task(collect())

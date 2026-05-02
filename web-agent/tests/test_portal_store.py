from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from deep_agent.portal.filesystem import build_active_project, build_file_tree, list_projects, resolve_project_dir
from deep_agent.portal.store import PORTAL_STORE_SCHEMA_VERSION, PortalStore


class PortalStoreTestCase(unittest.TestCase):
    def test_store_creates_and_reloads_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "portal" / "sessions.json"
            store = PortalStore(store_path)

            session = store.create_session(title="调试会话")
            _, turn = store.start_turn(session.session_id, content="生成登录测试计划")
            store.complete_turn(session.session_id, turn.turn_id, assistant_text="计划已生成")

            reloaded = PortalStore(store_path)
            snapshot = reloaded.snapshot(session.session_id)

            self.assertEqual(snapshot.session_id, session.session_id)
            self.assertEqual(snapshot.messages[0].content, "生成登录测试计划")
            self.assertEqual(snapshot.messages[-1].content, "计划已生成")
            self.assertEqual(reloaded.list_history()[0].last_assistant_summary, "计划已生成")

    def test_store_rejects_corrupt_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "sessions.json"
            store_path.write_text("{not-json", encoding="utf-8")

            with self.assertRaises(RuntimeError):
                PortalStore(store_path)

    def test_store_rejects_unknown_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "sessions.json"
            store_path.write_text('{"schemaVersion": 999, "sessions": []}', encoding="utf-8")

            with self.assertRaises(RuntimeError):
                PortalStore(store_path)

    def test_store_writes_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "sessions.json"
            store = PortalStore(store_path)
            store.create_session()

            self.assertIn(f'"schemaVersion": {PORTAL_STORE_SCHEMA_VERSION}', store_path.read_text(encoding="utf-8"))


class PortalFilesystemTestCase(unittest.TestCase):
    def test_project_listing_and_tree_are_limited_to_automation_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "projects"
            project = root / "demo"
            (project / "test_case" / "specs").mkdir(parents=True)
            (project / "node_modules").mkdir()
            (project / "test_case" / "specs" / "demo.spec.ts").write_text("test('demo')\n", encoding="utf-8")
            (project / "node_modules" / "hidden.js").write_text("hidden\n", encoding="utf-8")

            projects = list_projects(root)
            tree = build_file_tree(project)
            active_project = build_active_project(root, "demo")

            self.assertEqual([item.project_name for item in projects], ["demo"])
            self.assertEqual(active_project.project_name, "demo")
            self.assertEqual(resolve_project_dir(root, "demo"), project.resolve())
            self.assertEqual(tree[0].name, "test_case")
            self.assertNotIn("node_modules", [node.name for node in tree])

    def test_project_resolution_rejects_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "projects"
            with self.assertRaises(ValueError):
                resolve_project_dir(root, "../outside")


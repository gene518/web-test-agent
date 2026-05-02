"""基于 JSON 的 Portal 会话存储。"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from deep_agent.portal.filesystem import build_file_tree
from deep_agent.portal.models import (
    ActiveProject,
    FileTreeNode,
    ModelEvent,
    NodeTrace,
    PortalEvent,
    PortalMessage,
    PortalSessionRecord,
    PortalSessionSnapshot,
    PortalSessionSummary,
    PortalTurn,
    ToolEvent,
    utc_now,
)


PORTAL_STORE_SCHEMA_VERSION = 1


class PortalStore:
    """通过原子写入把 Portal 会话持久化到单个 JSON 文件。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._sessions: dict[str, PortalSessionRecord] = {}
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    def create_session(self, *, title: str | None = None) -> PortalSessionRecord:
        with self._lock:
            session_id = uuid4().hex
            now = utc_now()
            session = PortalSessionRecord(
                session_id=session_id,
                thread_id=session_id,
                title=(title or "新会话").strip() or "新会话",
                created_at=now,
                updated_at=now,
                run_status="idle",
            )
            self._sessions[session_id] = session
            self._save_locked()
            return session.model_copy(deep=True)

    def get_session(self, session_id: str) -> PortalSessionRecord:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(session_id)
            return session.model_copy(deep=True)

    def list_history(self) -> list[PortalSessionSummary]:
        with self._lock:
            summaries = [session.to_summary() for session in self._sessions.values()]
        return sorted(summaries, key=lambda item: item.updated_at, reverse=True)

    def snapshot(self, session_id: str, *, read_only: bool = False) -> PortalSessionSnapshot:
        return self.get_session(session_id).to_snapshot(read_only=read_only)

    def set_active_project(
        self,
        session_id: str,
        *,
        active_project: ActiveProject,
        file_tree: list[FileTreeNode] | None = None,
    ) -> PortalSessionRecord:
        return self.update_session(
            session_id,
            lambda session: self._set_active_project(session, active_project=active_project, file_tree=file_tree),
        )

    def set_selected_file(self, session_id: str, file_path: str | None) -> PortalSessionRecord:
        def mutate(session: PortalSessionRecord) -> None:
            session.selected_file_path = file_path
            session.updated_at = utc_now()

        return self.update_session(session_id, mutate)

    def start_turn(self, session_id: str, *, content: str, selected_file_path: str | None = None) -> tuple[PortalSessionRecord, PortalTurn]:
        def mutate(session: PortalSessionRecord) -> PortalTurn:
            now = utc_now()
            turn_id = uuid4().hex
            user_message = PortalMessage(
                message_id=uuid4().hex,
                role="user",
                content=content,
                created_at=now,
                turn_id=turn_id,
            )
            turn = PortalTurn(
                turn_id=turn_id,
                user_message=user_message,
                created_at=now,
                status="running",
            )
            session.messages.append(user_message)
            session.turns.append(turn)
            session.pending_interrupt = None
            session.run_status = "running"
            session.selected_file_path = selected_file_path
            session.title = _derive_title(session.title, content)
            session.updated_at = now
            return turn

        return self.update_session_with_result(session_id, mutate)

    def upsert_node_trace(self, session_id: str, turn_id: str, trace: NodeTrace) -> PortalSessionRecord:
        def mutate(session: PortalSessionRecord) -> None:
            turn = _find_turn(session, turn_id)
            for index, existing_trace in enumerate(turn.node_traces):
                if existing_trace.node_name == trace.node_name:
                    turn.node_traces[index] = _merge_trace(existing_trace, trace)
                    break
            else:
                turn.node_traces.append(trace)
            session.updated_at = utc_now()

        return self.update_session(session_id, mutate)

    def append_model_event(self, session_id: str, turn_id: str, *, node_name: str, event: ModelEvent) -> PortalSessionRecord:
        def mutate(session: PortalSessionRecord) -> None:
            trace = _ensure_trace(_find_turn(session, turn_id), node_name)
            trace.model_events.append(event)
            trace.status = event.status
            if event.status in {"completed", "failed"}:
                trace.finished_at = event.timestamp
            session.updated_at = utc_now()

        return self.update_session(session_id, mutate)

    def append_tool_event(self, session_id: str, turn_id: str, *, node_name: str, event: ToolEvent) -> PortalSessionRecord:
        def mutate(session: PortalSessionRecord) -> None:
            trace = _ensure_trace(_find_turn(session, turn_id), node_name)
            trace.tool_events.append(event)
            trace.status = event.status
            if event.status in {"completed", "failed"}:
                trace.finished_at = event.timestamp
            session.updated_at = utc_now()

        return self.update_session(session_id, mutate)

    def complete_turn(
        self,
        session_id: str,
        turn_id: str,
        *,
        assistant_text: str,
        stage_summaries: list[dict[str, Any]] | None = None,
    ) -> PortalSessionRecord:
        def mutate(session: PortalSessionRecord) -> None:
            now = utc_now()
            turn = _find_turn(session, turn_id)
            assistant_message = PortalMessage(
                message_id=uuid4().hex,
                role="assistant",
                content=assistant_text,
                created_at=now,
                turn_id=turn_id,
            )
            turn.assistant_message = assistant_message
            turn.stage_summaries = stage_summaries or []
            turn.completed_at = now
            turn.status = "completed"
            session.messages.append(assistant_message)
            session.last_assistant_summary = assistant_text
            session.run_status = "completed"
            session.pending_interrupt = None
            session.updated_at = now

        return self.update_session(session_id, mutate)

    def mark_waiting_input(self, session_id: str, turn_id: str, *, interrupt_payload: dict[str, Any]) -> PortalSessionRecord:
        def mutate(session: PortalSessionRecord) -> None:
            now = utc_now()
            question = _interrupt_question(interrupt_payload)
            turn = _find_turn(session, turn_id)
            assistant_message = PortalMessage(
                message_id=uuid4().hex,
                role="assistant",
                content=question,
                created_at=now,
                turn_id=turn_id,
            )
            turn.assistant_message = assistant_message
            turn.completed_at = now
            turn.status = "waiting_input"
            session.messages.append(assistant_message)
            session.last_assistant_summary = question
            session.pending_interrupt = interrupt_payload
            session.run_status = "waiting_input"
            session.updated_at = now

        return self.update_session(session_id, mutate)

    def fail_turn(self, session_id: str, turn_id: str, *, error: str) -> PortalSessionRecord:
        def mutate(session: PortalSessionRecord) -> None:
            now = utc_now()
            turn = _find_turn(session, turn_id)
            assistant_text = f"执行失败：{error}"
            assistant_message = PortalMessage(
                message_id=uuid4().hex,
                role="assistant",
                content=assistant_text,
                created_at=now,
                turn_id=turn_id,
            )
            turn.assistant_message = assistant_message
            turn.completed_at = now
            turn.status = "failed"
            turn.error = error
            session.messages.append(assistant_message)
            session.last_assistant_summary = assistant_text
            session.run_status = "failed"
            session.updated_at = now

        return self.update_session(session_id, mutate)

    def next_event(self, session_id: str, event_type: str, *, turn_id: str | None, payload: dict[str, Any]) -> PortalEvent:
        def mutate(session: PortalSessionRecord) -> PortalEvent:
            session.sequence += 1
            session.updated_at = utc_now()
            return PortalEvent(
                type=event_type,  # type: ignore[arg-type]
                session_id=session_id,
                turn_id=turn_id,
                sequence=session.sequence,
                payload=payload,
            )

        _, event = self.update_session_with_result(session_id, mutate)
        return event

    def refresh_active_project_tree(self, session_id: str) -> PortalSessionRecord:
        def mutate(session: PortalSessionRecord) -> None:
            if session.active_project is None:
                return
            project_dir = Path(session.active_project.project_dir)
            session.active_project.exists = project_dir.is_dir()
            session.file_tree = build_file_tree(project_dir)
            session.updated_at = utc_now()

        return self.update_session(session_id, mutate)

    def update_session(self, session_id: str, mutate: Callable[[PortalSessionRecord], None]) -> PortalSessionRecord:
        session, _ = self.update_session_with_result(session_id, lambda current: mutate(current))
        return session

    def update_session_with_result(
        self,
        session_id: str,
        mutate: Callable[[PortalSessionRecord], Any],
    ) -> tuple[PortalSessionRecord, Any]:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(session_id)
            result = mutate(session)
            self._save_locked()
            return session.model_copy(deep=True), result

    def _set_active_project(
        self,
        session: PortalSessionRecord,
        *,
        active_project: ActiveProject,
        file_tree: list[FileTreeNode] | None,
    ) -> None:
        session.active_project = active_project
        session.file_tree = file_tree if file_tree is not None else build_file_tree(Path(active_project.project_dir))
        session.updated_at = utc_now()

    def _load(self) -> None:
        with self._lock:
            if not self._path.exists():
                self._sessions = {}
                return
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Portal session store is not valid JSON: {self._path}") from exc

            if raw.get("schemaVersion") != PORTAL_STORE_SCHEMA_VERSION:
                raise RuntimeError(f"Unsupported Portal store schema version in {self._path}.")

            sessions = raw.get("sessions", [])
            if not isinstance(sessions, list):
                raise RuntimeError(f"Portal store sessions must be a list: {self._path}.")

            self._sessions = {}
            for session_data in sessions:
                session = PortalSessionRecord.model_validate(session_data)
                self._sessions[session.session_id] = session

    def _save_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schemaVersion": PORTAL_STORE_SCHEMA_VERSION,
            "sessions": [session.model_dump(mode="json", by_alias=True) for session in self._sessions.values()],
        }
        temp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, self._path)


def _derive_title(current_title: str, content: str) -> str:
    if current_title != "新会话":
        return current_title
    compact = " ".join(content.strip().split())
    if not compact:
        return current_title
    return compact[:32]


def _find_turn(session: PortalSessionRecord, turn_id: str) -> PortalTurn:
    for turn in session.turns:
        if turn.turn_id == turn_id:
            return turn
    raise KeyError(turn_id)


def _ensure_trace(turn: PortalTurn, node_name: str) -> NodeTrace:
    for trace in turn.node_traces:
        if trace.node_name == node_name:
            return trace
    trace = NodeTrace(trace_id=uuid4().hex, node_name=node_name, status="running", started_at=utc_now())
    turn.node_traces.append(trace)
    return trace


def _merge_trace(existing: NodeTrace, update: NodeTrace) -> NodeTrace:
    data = existing.model_dump()
    update_data = update.model_dump(exclude_none=True)
    model_events = existing.model_events + update.model_events
    tool_events = existing.tool_events + update.tool_events
    data.update(update_data)
    data["model_events"] = model_events
    data["tool_events"] = tool_events
    return NodeTrace.model_validate(data)


def _interrupt_question(payload: dict[str, Any]) -> str:
    question = payload.get("question") or payload.get("message") or payload.get("content")
    if question:
        return str(question)
    missing_param = payload.get("missing_param") or payload.get("missingParam")
    if missing_param:
        return f"请补充参数：{missing_param}"
    return "请补充缺失信息后继续。"

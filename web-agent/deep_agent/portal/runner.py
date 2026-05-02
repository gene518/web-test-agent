"""把 LangGraph 执行过程适配为 Portal 会话与 SSE 事件的运行器。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from deep_agent.core.config import AppSettings
from deep_agent.portal.events import PortalEventHub
from deep_agent.portal.filesystem import build_active_project, build_file_tree
from deep_agent.portal.models import ModelEvent, NodeTrace, ToolEvent, utc_now
from deep_agent.portal.store import PortalStore
from deep_agent.workflow import build_workflow


class PortalRunner:
    """执行 LangGraph 轮次，并写入面向 Portal 的状态变更。"""

    def __init__(self, *, settings: AppSettings, store: PortalStore, hub: PortalEventHub, graph: Any | None = None) -> None:
        self._settings = settings
        self._store = store
        self._hub = hub
        self._graph = graph or build_workflow(checkpointer=InMemorySaver())
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    async def start_message(self, session_id: str, *, content: str, selected_file_path: str | None = None) -> None:
        """先持久化用户轮次并发出首个事件，再转入后台执行。"""

        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("消息内容不能为空。")

        session_before = self._store.get_session(session_id)
        if session_before.run_status == "running":
            raise RuntimeError("当前会话已有消息正在执行。")

        _, turn = self._store.start_turn(session_id, content=normalized_content, selected_file_path=selected_file_path)
        await self._publish(
            session_id,
            "message_started",
            turn_id=turn.turn_id,
            payload={"turn": turn.model_dump(mode="json", by_alias=True)},
        )

        task = asyncio.create_task(
            self._run_message(
                session_id=session_id,
                turn_id=turn.turn_id,
                content=normalized_content,
                resume_pending=bool(session_before.pending_interrupt),
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def shutdown(self) -> None:
        """在应用关闭时取消仍在执行的后台任务。"""

        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _run_message(self, *, session_id: str, turn_id: str, content: str, resume_pending: bool) -> None:
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            config = {"configurable": {"thread_id": session_id, "session_id": session_id}}
            graph_input: Any = Command(resume=content) if resume_pending else {"messages": [HumanMessage(content=content)]}
            try:
                async for event in self._graph.astream_events(graph_input, config=config, version="v2"):
                    await self._handle_graph_event(session_id=session_id, turn_id=turn_id, event=event)

                state_snapshot = await self._graph.aget_state(config)
                interrupt_payload = self._extract_interrupt_payload(state_snapshot)
                if interrupt_payload is not None:
                    session = self._store.mark_waiting_input(session_id, turn_id, interrupt_payload=interrupt_payload)
                    await self._publish(
                        session_id,
                        "message_completed",
                        turn_id=turn_id,
                        payload={
                            "snapshot": session.to_snapshot().model_dump(mode="json", by_alias=True),
                            "history": [item.model_dump(mode="json", by_alias=True) for item in self._store.list_history()],
                        },
                    )
                    return

                values = getattr(state_snapshot, "values", {}) or {}
                project_changed_payload = self._maybe_update_active_project(session_id, values)
                if project_changed_payload is not None:
                    await self._publish(session_id, "project_changed", turn_id=turn_id, payload=project_changed_payload)

                assistant_text = self._extract_assistant_text(values)
                stage_summaries = self._extract_stage_summaries(values)
                session = self._store.complete_turn(
                    session_id,
                    turn_id,
                    assistant_text=assistant_text,
                    stage_summaries=stage_summaries,
                )
                await self._publish(
                    session_id,
                    "message_completed",
                    turn_id=turn_id,
                    payload={
                        "snapshot": session.to_snapshot().model_dump(mode="json", by_alias=True),
                        "history": [item.model_dump(mode="json", by_alias=True) for item in self._store.list_history()],
                    },
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                session = self._store.fail_turn(session_id, turn_id, error=str(exc))
                await self._publish(
                    session_id,
                    "message_failed",
                    turn_id=turn_id,
                    payload={
                        "error": str(exc),
                        "snapshot": session.to_snapshot().model_dump(mode="json", by_alias=True),
                        "history": [item.model_dump(mode="json", by_alias=True) for item in self._store.list_history()],
                    },
                )

    async def _handle_graph_event(self, *, session_id: str, turn_id: str, event: dict[str, Any]) -> None:
        event_name = str(event.get("event") or "")
        name = str(event.get("name") or "")
        node_name = self._resolve_node_name(event)
        if not node_name:
            return

        if event_name == "on_chain_start":
            trace = NodeTrace(
                trace_id=uuid4().hex,
                node_name=node_name,
                status="running",
                started_at=utc_now(),
                detail=_safe_summary(event.get("data", {}).get("input")),
            )
            session = self._store.upsert_node_trace(session_id, turn_id, trace)
            await self._publish_trace(session_id, turn_id, session, node_name)
            return

        if event_name == "on_chain_end":
            trace = NodeTrace(
                trace_id=uuid4().hex,
                node_name=node_name,
                status="completed",
                finished_at=utc_now(),
                detail=_safe_summary(event.get("data", {}).get("output")),
            )
            session = self._store.upsert_node_trace(session_id, turn_id, trace)
            await self._publish_trace(session_id, turn_id, session, node_name)
            return

        if event_name == "on_chain_error":
            trace = NodeTrace(
                trace_id=uuid4().hex,
                node_name=node_name,
                status="failed",
                finished_at=utc_now(),
                detail=_safe_summary(event.get("data", {}).get("error")),
            )
            session = self._store.upsert_node_trace(session_id, turn_id, trace)
            await self._publish_trace(session_id, turn_id, session, node_name)
            return

        if event_name == "on_chat_model_start":
            model_event = ModelEvent(
                event_id=uuid4().hex,
                name=name or "model",
                status="running",
                input_summary=_safe_summary(event.get("data", {}).get("input")),
            )
            session = self._store.append_model_event(session_id, turn_id, node_name=node_name, event=model_event)
            await self._publish_trace(session_id, turn_id, session, node_name)
            return

        if event_name == "on_chat_model_end":
            model_event = ModelEvent(
                event_id=uuid4().hex,
                name=name or "model",
                status="completed",
                output_summary=_safe_summary(event.get("data", {}).get("output")),
            )
            session = self._store.append_model_event(session_id, turn_id, node_name=node_name, event=model_event)
            await self._publish_trace(session_id, turn_id, session, node_name)
            return

        if event_name == "on_tool_start":
            tool_event = ToolEvent(
                event_id=uuid4().hex,
                name=name or "tool",
                status="running",
                input_summary=_safe_summary(event.get("data", {}).get("input")),
            )
            session = self._store.append_tool_event(session_id, turn_id, node_name=node_name, event=tool_event)
            await self._publish_tool(session_id, turn_id, session, node_name, tool_event)
            return

        if event_name == "on_tool_end":
            tool_event = ToolEvent(
                event_id=uuid4().hex,
                name=name or "tool",
                status="completed",
                output_summary=_safe_summary(event.get("data", {}).get("output")),
            )
            session = self._store.append_tool_event(session_id, turn_id, node_name=node_name, event=tool_event)
            await self._publish_tool(session_id, turn_id, session, node_name, tool_event)
            return

        if event_name == "on_tool_error":
            tool_event = ToolEvent(
                event_id=uuid4().hex,
                name=name or "tool",
                status="failed",
                error_summary=_safe_summary(event.get("data", {}).get("error")),
            )
            session = self._store.append_tool_event(session_id, turn_id, node_name=node_name, event=tool_event)
            await self._publish_tool(session_id, turn_id, session, node_name, tool_event)

    def _resolve_node_name(self, event: dict[str, Any]) -> str | None:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        node_name = metadata.get("langgraph_node")
        if isinstance(node_name, str) and node_name:
            return node_name

        name = event.get("name")
        if isinstance(name, str) and name and name != "LangGraph":
            return name
        return None

    async def _publish_trace(self, session_id: str, turn_id: str, session: Any, node_name: str) -> None:
        turn = next(turn for turn in session.turns if turn.turn_id == turn_id)
        trace = next(trace for trace in turn.node_traces if trace.node_name == node_name)
        await self._publish(
            session_id,
            "node_updated",
            turn_id=turn_id,
            payload={"trace": trace.model_dump(mode="json", by_alias=True)},
        )

    async def _publish_tool(self, session_id: str, turn_id: str, session: Any, node_name: str, tool_event: ToolEvent) -> None:
        turn = next(turn for turn in session.turns if turn.turn_id == turn_id)
        trace = next(trace for trace in turn.node_traces if trace.node_name == node_name)
        await self._publish(
            session_id,
            "tool_updated",
            turn_id=turn_id,
            payload={
                "nodeName": node_name,
                "toolEvent": tool_event.model_dump(mode="json", by_alias=True),
                "trace": trace.model_dump(mode="json", by_alias=True),
            },
        )

    async def _publish(self, session_id: str, event_type: str, *, turn_id: str | None, payload: dict[str, Any]) -> None:
        event = self._store.next_event(session_id, event_type, turn_id=turn_id, payload=payload)
        await self._hub.publish(event)

    def _extract_interrupt_payload(self, state_snapshot: Any) -> dict[str, Any] | None:
        interrupts = getattr(state_snapshot, "interrupts", ()) or ()
        if not interrupts:
            return None
        payload = getattr(interrupts[0], "value", None)
        if isinstance(payload, dict):
            return payload
        return {"question": str(payload)}

    def _extract_assistant_text(self, values: dict[str, Any]) -> str:
        final_summary = values.get("final_summary")
        if isinstance(final_summary, str) and final_summary.strip():
            return final_summary.strip()

        messages = values.get("messages", [])
        if isinstance(messages, list):
            for message in reversed(messages):
                if isinstance(message, AIMessage):
                    return _message_text(message) or "任务已完成。"
                if isinstance(message, BaseMessage) and message.__class__.__name__.lower().startswith("ai"):
                    return _message_text(message) or "任务已完成。"
        return "任务已完成。"

    def _extract_stage_summaries(self, values: dict[str, Any]) -> list[dict[str, Any]]:
        pending = values.get("pending_stage_summaries")
        if isinstance(pending, list):
            return [item for item in pending if isinstance(item, dict)]
        stage_result = values.get("stage_result")
        if isinstance(stage_result, dict) and isinstance(stage_result.get("stage_summary"), dict):
            return [stage_result["stage_summary"]]
        return []

    def _maybe_update_active_project(self, session_id: str, values: dict[str, Any]) -> dict[str, Any] | None:
        project_name = self._extract_project_name(values)
        if not project_name:
            return None

        try:
            active_project = build_active_project(self._settings.resolved_default_automation_project_root, project_name)
        except ValueError:
            return None

        before = self._store.get_session(session_id).active_project
        file_tree = build_file_tree(Path(active_project.project_dir))
        session = self._store.set_active_project(session_id, active_project=active_project, file_tree=file_tree)
        if before and before.project_name == active_project.project_name and before.exists == active_project.exists:
            return None
        return {
            "activeProject": session.active_project.model_dump(mode="json", by_alias=True) if session.active_project else None,
            "fileTree": [node.model_dump(mode="json", by_alias=True) for node in session.file_tree],
        }

    def _extract_project_name(self, values: dict[str, Any]) -> str | None:
        latest_artifacts = values.get("latest_artifacts")
        if isinstance(latest_artifacts, dict):
            for stage_name in ("healer", "generator", "plan"):
                artifact = latest_artifacts.get(stage_name)
                if isinstance(artifact, dict):
                    name = _clean_text(artifact.get("project_name"))
                    if name:
                        return name
                    project_dir = _project_name_from_dir(artifact.get("project_dir"), self._settings.resolved_default_automation_project_root)
                    if project_dir:
                        return project_dir

        extracted_params = values.get("extracted_params")
        if isinstance(extracted_params, dict):
            name = _clean_text(extracted_params.get("project_name"))
            if name:
                return name
            return _project_name_from_dir(extracted_params.get("project_dir"), self._settings.resolved_default_automation_project_root)
        return None


def _safe_summary(value: Any, *, max_chars: int = 1200) -> str | None:
    if value is None:
        return None
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        text = str(value)
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}... [truncated]"


def _message_text(message: BaseMessage) -> str:
    content = message.content
    return content if isinstance(content, str) else str(content)


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _project_name_from_dir(value: Any, automation_root: Path) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    project_dir = Path(text).expanduser().resolve()
    root = automation_root.expanduser().resolve()
    try:
        relative = project_dir.relative_to(root)
    except ValueError:
        return None
    if not relative.parts:
        return None
    return relative.parts[0]

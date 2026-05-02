"""Portal API 的 REST 与 SSE 路由。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

try:  # FastAPI 0.135+ 已直接暴露该类型；这里保留旧版本的兜底导入。
    from fastapi.sse import EventSourceResponse
except Exception:  # pragma: no cover - 兼容旧版本的导入分支
    from sse_starlette.sse import EventSourceResponse

from deep_agent.portal.filesystem import build_active_project, build_file_tree, list_projects, resolve_project_dir
from deep_agent.portal.models import (
    CreateSessionRequest,
    CreateSessionResponse,
    HistoryResponse,
    PortalSessionSnapshot,
    ProjectsResponse,
    SelectFileRequest,
    SelectFileResponse,
    SendMessageRequest,
    SendMessageResponse,
    SetActiveProjectRequest,
    SetActiveProjectResponse,
)


router = APIRouter()


def portal_state(request: Request) -> Any:
    return request.app.state.portal


PortalStateDep = Annotated[Any, Depends(portal_state)]


@router.post("/sessions", response_model=CreateSessionResponse)
async def create_session(payload: CreateSessionRequest, state: PortalStateDep) -> CreateSessionResponse:
    session = state.store.create_session(title=payload.title)
    event = state.store.next_event(session.session_id, "session_created", turn_id=None, payload={})
    await state.hub.publish(event)
    return CreateSessionResponse(snapshot=session.to_snapshot(), history=state.store.list_history())


@router.get("/history", response_model=HistoryResponse)
async def get_history(state: PortalStateDep) -> HistoryResponse:
    return HistoryResponse(history=state.store.list_history())


@router.get("/sessions/{session_id}", response_model=PortalSessionSnapshot)
async def get_session(session_id: str, state: PortalStateDep) -> PortalSessionSnapshot:
    session = _get_session_or_404(state, session_id)
    if session.active_project is not None:
        session = state.store.refresh_active_project_tree(session_id)
    return session.to_snapshot()


@router.get("/sessions/{session_id}/stream")
async def stream_session(session_id: str, state: PortalStateDep) -> EventSourceResponse:
    _get_session_or_404(state, session_id)

    async def stream() -> AsyncIterator[dict[str, str]]:
        async for event in state.hub.subscribe(session_id):
            yield {
                "id": str(event.sequence),
                "event": event.type,
                "data": json.dumps(event.model_dump(mode="json", by_alias=True), ensure_ascii=False),
            }

    return EventSourceResponse(stream())


@router.post("/sessions/{session_id}/messages", response_model=SendMessageResponse)
async def send_message(session_id: str, payload: SendMessageRequest, state: PortalStateDep) -> SendMessageResponse:
    _get_session_or_404(state, session_id)
    try:
        await state.runner.start_message(
            session_id,
            content=payload.content,
            selected_file_path=payload.selected_file_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session = state.store.snapshot(session_id)
    return SendMessageResponse(snapshot=session, history=state.store.list_history())


@router.get("/projects", response_model=ProjectsResponse)
async def get_projects(state: PortalStateDep) -> ProjectsResponse:
    return ProjectsResponse(projects=list_projects(state.settings.resolved_default_automation_project_root))


@router.post("/sessions/{session_id}/active-project", response_model=SetActiveProjectResponse)
async def set_active_project(
    session_id: str,
    payload: SetActiveProjectRequest,
    state: PortalStateDep,
) -> SetActiveProjectResponse:
    _get_session_or_404(state, session_id)
    try:
        project_dir = resolve_project_dir(state.settings.resolved_default_automation_project_root, payload.project_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not project_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"自动化项目不存在：{payload.project_name}")

    active_project = build_active_project(state.settings.resolved_default_automation_project_root, payload.project_name)
    session = state.store.set_active_project(
        session_id,
        active_project=active_project,
        file_tree=build_file_tree(project_dir),
    )
    event = state.store.next_event(
        session_id,
        "project_changed",
        turn_id=None,
        payload={
            "activeProject": session.active_project.model_dump(mode="json", by_alias=True) if session.active_project else None,
            "fileTree": [node.model_dump(mode="json", by_alias=True) for node in session.file_tree],
        },
    )
    await state.hub.publish(event)
    return SetActiveProjectResponse(snapshot=session.to_snapshot(), history=state.store.list_history())


@router.post("/sessions/{session_id}/selected-file", response_model=SelectFileResponse)
async def set_selected_file(session_id: str, payload: SelectFileRequest, state: PortalStateDep) -> SelectFileResponse:
    session = _get_session_or_404(state, session_id)
    if payload.file_path and session.active_project is not None:
        project_dir = Path(session.active_project.project_dir).resolve()
        candidate = (project_dir / payload.file_path).resolve()
        try:
            candidate.relative_to(project_dir)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="选中文件必须位于当前项目目录内。") from exc
    session = state.store.set_selected_file(session_id, payload.file_path)
    return SelectFileResponse(snapshot=session.to_snapshot())


def _get_session_or_404(state: Any, session_id: str):
    try:
        return state.store.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="会话不存在。") from exc

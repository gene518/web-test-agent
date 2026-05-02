"""Portal REST 与 SSE API 的 Pydantic 协议模型。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    """返回用于 Portal 持久化记录的带时区 UTC 时间戳。"""

    return datetime.now(timezone.utc)


def to_camel(value: str) -> str:
    """把 snake_case 字段名转换为 lower camelCase 风格的 API 键名。"""

    parts = value.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


class PortalModel(BaseModel):
    """基础模型：对外输出 camelCase JSON，同时保持 Python 侧写法自然。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


RunStatus = Literal["idle", "running", "waiting_input", "completed", "failed"]
NodeStatus = Literal["running", "completed", "failed"]
FileNodeType = Literal["file", "directory"]
MessageRole = Literal["user", "assistant", "system"]


class ActiveProject(PortalModel):
    project_name: str
    project_dir: str
    exists: bool = True


class FileTreeNode(PortalModel):
    name: str
    path: str
    type: FileNodeType
    children: list["FileTreeNode"] = Field(default_factory=list)


class PortalProjectSummary(PortalModel):
    project_name: str
    project_dir: str
    updated_at: datetime | None = None


class PortalMessage(PortalModel):
    message_id: str
    role: MessageRole
    content: str
    created_at: datetime = Field(default_factory=utc_now)
    turn_id: str | None = None


class ModelEvent(PortalModel):
    event_id: str
    name: str
    status: NodeStatus
    timestamp: datetime = Field(default_factory=utc_now)
    input_summary: str | None = None
    output_summary: str | None = None
    error_summary: str | None = None


class ToolEvent(PortalModel):
    event_id: str
    name: str
    status: NodeStatus
    timestamp: datetime = Field(default_factory=utc_now)
    input_summary: str | None = None
    output_summary: str | None = None
    error_summary: str | None = None


class NodeTrace(PortalModel):
    trace_id: str
    node_name: str
    status: NodeStatus
    routing_reason: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    model_events: list[ModelEvent] = Field(default_factory=list)
    tool_events: list[ToolEvent] = Field(default_factory=list)
    detail: str | None = None


class PortalTurn(PortalModel):
    turn_id: str
    user_message: PortalMessage
    assistant_message: PortalMessage | None = None
    stage_summaries: list[dict[str, Any]] = Field(default_factory=list)
    node_traces: list[NodeTrace] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    status: RunStatus = "running"
    error: str | None = None


class PortalSessionSummary(PortalModel):
    session_id: str
    title: str
    project_name: str | None = None
    updated_at: datetime
    status: RunStatus
    last_assistant_summary: str | None = None


class PortalSessionSnapshot(PortalModel):
    session_id: str
    thread_id: str
    active_project: ActiveProject | None = None
    file_tree: list[FileTreeNode] = Field(default_factory=list)
    messages: list[PortalMessage] = Field(default_factory=list)
    turns: list[PortalTurn] = Field(default_factory=list)
    pending_interrupt: dict[str, Any] | None = None
    run_status: RunStatus = "idle"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    read_only: bool = False
    selected_file_path: str | None = None


class PortalSessionRecord(PortalSessionSnapshot):
    title: str = "新会话"
    sequence: int = 0
    last_assistant_summary: str | None = None

    def to_summary(self) -> PortalSessionSummary:
        """构建历史侧栏使用的精简记录。"""

        return PortalSessionSummary(
            session_id=self.session_id,
            title=self.title,
            project_name=self.active_project.project_name if self.active_project else None,
            updated_at=self.updated_at,
            status=self.run_status,
            last_assistant_summary=self.last_assistant_summary,
        )

    def to_snapshot(self, *, read_only: bool = False) -> PortalSessionSnapshot:
        """构建对外暴露的会话快照。"""

        data = self.model_dump()
        data["read_only"] = read_only
        return PortalSessionSnapshot.model_validate(data)


class PortalEvent(PortalModel):
    type: Literal[
        "session_created",
        "message_started",
        "node_updated",
        "tool_updated",
        "project_changed",
        "message_completed",
        "message_failed",
    ]
    session_id: str
    turn_id: str | None = None
    sequence: int
    timestamp: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)


class CreateSessionRequest(PortalModel):
    title: str | None = None


class CreateSessionResponse(PortalModel):
    snapshot: PortalSessionSnapshot
    history: list[PortalSessionSummary]


class HistoryResponse(PortalModel):
    history: list[PortalSessionSummary]


class ProjectsResponse(PortalModel):
    projects: list[PortalProjectSummary]


class SendMessageRequest(PortalModel):
    content: str
    selected_file_path: str | None = None


class SendMessageResponse(PortalModel):
    snapshot: PortalSessionSnapshot
    history: list[PortalSessionSummary]


class SetActiveProjectRequest(PortalModel):
    project_name: str


class SetActiveProjectResponse(PortalModel):
    snapshot: PortalSessionSnapshot
    history: list[PortalSessionSummary]


class SelectFileRequest(PortalModel):
    file_path: str | None = None


class SelectFileResponse(PortalModel):
    snapshot: PortalSessionSnapshot

export type RunStatus = 'idle' | 'running' | 'waiting_input' | 'completed' | 'failed';
export type NodeStatus = 'running' | 'completed' | 'failed';
export type FileNodeType = 'file' | 'directory';
export type MessageRole = 'user' | 'assistant' | 'system';
export type PortalEventType =
  | 'session_created'
  | 'message_started'
  | 'node_updated'
  | 'tool_updated'
  | 'project_changed'
  | 'message_completed'
  | 'message_failed';

export interface ActiveProject {
  projectName: string;
  projectDir: string;
  exists: boolean;
}

export interface FileTreeNode {
  name: string;
  path: string;
  type: FileNodeType;
  children: FileTreeNode[];
}

export interface PortalProjectSummary {
  projectName: string;
  projectDir: string;
  updatedAt?: string | null;
}

export interface PortalMessage {
  messageId: string;
  role: MessageRole;
  content: string;
  createdAt: string;
  turnId?: string | null;
}

export interface ModelEvent {
  eventId: string;
  name: string;
  status: NodeStatus;
  timestamp: string;
  inputSummary?: string | null;
  outputSummary?: string | null;
  errorSummary?: string | null;
}

export interface ToolEvent {
  eventId: string;
  name: string;
  status: NodeStatus;
  timestamp: string;
  inputSummary?: string | null;
  outputSummary?: string | null;
  errorSummary?: string | null;
}

export interface NodeTrace {
  traceId: string;
  nodeName: string;
  status: NodeStatus;
  routingReason?: string | null;
  startedAt?: string | null;
  finishedAt?: string | null;
  modelEvents: ModelEvent[];
  toolEvents: ToolEvent[];
  detail?: string | null;
}

export interface PortalTurn {
  turnId: string;
  userMessage: PortalMessage;
  assistantMessage?: PortalMessage | null;
  stageSummaries: Array<Record<string, unknown>>;
  nodeTraces: NodeTrace[];
  createdAt: string;
  completedAt?: string | null;
  status: RunStatus;
  error?: string | null;
}

export interface PortalSessionSummary {
  sessionId: string;
  title: string;
  projectName?: string | null;
  updatedAt: string;
  status: RunStatus;
  lastAssistantSummary?: string | null;
}

export interface PortalSessionSnapshot {
  sessionId: string;
  threadId: string;
  activeProject?: ActiveProject | null;
  fileTree: FileTreeNode[];
  messages: PortalMessage[];
  turns: PortalTurn[];
  pendingInterrupt?: Record<string, unknown> | null;
  runStatus: RunStatus;
  createdAt: string;
  updatedAt: string;
  readOnly: boolean;
  selectedFilePath?: string | null;
}

export interface PortalEvent {
  type: PortalEventType;
  sessionId: string;
  turnId?: string | null;
  sequence: number;
  timestamp: string;
  payload: Record<string, unknown>;
}

export interface CreateSessionResponse {
  snapshot: PortalSessionSnapshot;
  history: PortalSessionSummary[];
}

export interface HistoryResponse {
  history: PortalSessionSummary[];
}

export interface ProjectsResponse {
  projects: PortalProjectSummary[];
}

export interface SendMessageResponse {
  snapshot: PortalSessionSnapshot;
  history: PortalSessionSummary[];
}

export interface SetActiveProjectResponse {
  snapshot: PortalSessionSnapshot;
  history: PortalSessionSummary[];
}

export interface SelectFileResponse {
  snapshot: PortalSessionSnapshot;
}


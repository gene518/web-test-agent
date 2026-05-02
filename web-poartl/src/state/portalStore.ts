import { portalApi } from '../api/client';
import type {
  FileTreeNode,
  NodeTrace,
  PortalEvent,
  PortalProjectSummary,
  PortalSessionSnapshot,
  PortalSessionSummary,
  PortalTurn,
} from '../types/portal';

export type ConnectionStatus = 'idle' | 'connecting' | 'open' | 'closed' | 'error';

export interface PortalState {
  activeSessionId: string | null;
  selectedSessionId: string | null;
  snapshot: PortalSessionSnapshot | null;
  history: PortalSessionSummary[];
  projects: PortalProjectSummary[];
  isBootstrapping: boolean;
  isSending: boolean;
  projectPickerOpen: boolean;
  connectionStatus: ConnectionStatus;
  error: string | null;
}

const initialState: PortalState = {
  activeSessionId: null,
  selectedSessionId: null,
  snapshot: null,
  history: [],
  projects: [],
  isBootstrapping: false,
  isSending: false,
  projectPickerOpen: false,
  connectionStatus: 'idle',
  error: null,
};

let state: PortalState = initialState;
const listeners = new Set<() => void>();

function setState(updater: Partial<PortalState> | ((current: PortalState) => PortalState)): void {
  state = typeof updater === 'function' ? updater(state) : { ...state, ...updater };
  listeners.forEach((listener) => listener());
}

function setError(error: unknown): void {
  setState({ error: error instanceof Error ? error.message : String(error) });
}

export const portalStore = {
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  getSnapshot() {
    return state;
  },
  setConnectionStatus(connectionStatus: ConnectionStatus) {
    setState({ connectionStatus });
  },
  async bootstrap() {
    if (state.activeSessionId || state.isBootstrapping) {
      return;
    }
    setState({ isBootstrapping: true, error: null });
    try {
      const response = await portalApi.createSession();
      setState({
        activeSessionId: response.snapshot.sessionId,
        selectedSessionId: response.snapshot.sessionId,
        snapshot: response.snapshot,
        history: response.history,
        isBootstrapping: false,
      });
    } catch (error) {
      setState({ isBootstrapping: false });
      setError(error);
    }
  },
  async refreshSelectedSession() {
    if (!state.selectedSessionId) {
      return;
    }
    try {
      const snapshot = await portalApi.getSession(state.selectedSessionId);
      setState({ snapshot, error: null });
    } catch (error) {
      setError(error);
    }
  },
  async openProjectPicker() {
    try {
      const projects = await portalApi.getProjects();
      setState({ projects, projectPickerOpen: true, error: null });
    } catch (error) {
      setError(error);
    }
  },
  closeProjectPicker() {
    setState({ projectPickerOpen: false });
  },
  async chooseProject(projectName: string) {
    if (!state.activeSessionId) {
      return;
    }
    try {
      const response = await portalApi.setActiveProject(state.activeSessionId, projectName);
      setState({
        snapshot: response.snapshot,
        history: response.history,
        selectedSessionId: state.activeSessionId,
        projectPickerOpen: false,
        error: null,
      });
    } catch (error) {
      setError(error);
    }
  },
  async selectFile(filePath: string | null) {
    const snapshot = state.snapshot;
    if (!snapshot || snapshot.sessionId !== state.activeSessionId) {
      return;
    }
    try {
      const response = await portalApi.setSelectedFile(snapshot.sessionId, filePath);
      setState({ snapshot: response.snapshot, error: null });
    } catch (error) {
      setError(error);
    }
  },
  async selectSession(sessionId: string) {
    try {
      const snapshot = await portalApi.getSession(sessionId);
      setState({ selectedSessionId: sessionId, snapshot, error: null });
    } catch (error) {
      setError(error);
    }
  },
  async returnToActiveSession() {
    if (!state.activeSessionId) {
      return;
    }
    await this.selectSession(state.activeSessionId);
  },
  async sendMessage(content: string) {
    const snapshot = state.snapshot;
    if (!snapshot || snapshot.sessionId !== state.activeSessionId || state.isSending) {
      return;
    }
    const normalized = content.trim();
    if (!normalized) {
      return;
    }
    setState({ isSending: true, error: null });
    try {
      const response = await portalApi.sendMessage(snapshot.sessionId, normalized, snapshot.selectedFilePath);
      setState({
        snapshot: response.snapshot,
        history: response.history,
        selectedSessionId: snapshot.sessionId,
        isSending: false,
      });
    } catch (error) {
      setState({ isSending: false });
      setError(error);
    }
  },
  applyEvent(event: PortalEvent) {
    setState((current) => {
      const next: PortalState = { ...current };
      const payloadSnapshot = readPayloadSnapshot(event);
      const payloadHistory = readPayloadHistory(event);
      if (payloadHistory) {
        next.history = payloadHistory;
      }
      if (payloadSnapshot && current.selectedSessionId === event.sessionId) {
        next.snapshot = payloadSnapshot;
        next.isSending = payloadSnapshot.runStatus === 'running';
        return next;
      }
      if (!current.snapshot || current.selectedSessionId !== event.sessionId) {
        return next;
      }
      if (event.type === 'message_started') {
        next.snapshot = appendTurn(current.snapshot, event);
        next.isSending = true;
      }
      if (event.type === 'node_updated' || event.type === 'tool_updated') {
        next.snapshot = upsertTrace(current.snapshot, event);
      }
      if (event.type === 'project_changed') {
        next.snapshot = applyProjectChanged(current.snapshot, event);
      }
      if (event.type === 'message_failed') {
        next.isSending = false;
      }
      return next;
    });
  },
};

function readPayloadSnapshot(event: PortalEvent): PortalSessionSnapshot | null {
  const snapshot = event.payload.snapshot;
  return isObject(snapshot) ? (snapshot as unknown as PortalSessionSnapshot) : null;
}

function readPayloadHistory(event: PortalEvent): PortalSessionSummary[] | null {
  const history = event.payload.history;
  return Array.isArray(history) ? (history as PortalSessionSummary[]) : null;
}

function appendTurn(snapshot: PortalSessionSnapshot, event: PortalEvent): PortalSessionSnapshot {
  const turn = event.payload.turn;
  if (!isPortalTurn(turn)) {
    return { ...snapshot, runStatus: 'running' };
  }
  if (snapshot.turns.some((existing) => existing.turnId === turn.turnId)) {
    return { ...snapshot, runStatus: 'running' };
  }
  return {
    ...snapshot,
    runStatus: 'running',
    turns: [...snapshot.turns, turn],
    messages: [...snapshot.messages, turn.userMessage],
  };
}

function upsertTrace(snapshot: PortalSessionSnapshot, event: PortalEvent): PortalSessionSnapshot {
  const trace = event.payload.trace;
  if (!isNodeTrace(trace) || !event.turnId) {
    return snapshot;
  }
  return {
    ...snapshot,
    turns: snapshot.turns.map((turn) => {
      if (turn.turnId !== event.turnId) {
        return turn;
      }
      const exists = turn.nodeTraces.some((nodeTrace) => nodeTrace.nodeName === trace.nodeName);
      return {
        ...turn,
        nodeTraces: exists
          ? turn.nodeTraces.map((nodeTrace) => (nodeTrace.nodeName === trace.nodeName ? trace : nodeTrace))
          : [...turn.nodeTraces, trace],
      };
    }),
  };
}

function applyProjectChanged(snapshot: PortalSessionSnapshot, event: PortalEvent): PortalSessionSnapshot {
  const activeProject = event.payload.activeProject;
  const fileTree = event.payload.fileTree;
  return {
    ...snapshot,
    activeProject: isObject(activeProject) ? (activeProject as unknown as PortalSessionSnapshot['activeProject']) : snapshot.activeProject,
    fileTree: Array.isArray(fileTree) ? (fileTree as FileTreeNode[]) : snapshot.fileTree,
  };
}

function isPortalTurn(value: unknown): value is PortalTurn {
  return isObject(value) && typeof value.turnId === 'string' && isObject(value.userMessage);
}

function isNodeTrace(value: unknown): value is NodeTrace {
  return isObject(value) && typeof value.nodeName === 'string' && typeof value.traceId === 'string';
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

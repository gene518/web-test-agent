import type {
  CreateSessionResponse,
  HistoryResponse,
  PortalProjectSummary,
  PortalSessionSnapshot,
  ProjectsResponse,
  SelectFileResponse,
  SendMessageResponse,
  SetActiveProjectResponse,
} from '../types/portal';

const API_BASE = `${import.meta.env.VITE_PORTAL_API_BASE ?? ''}/api/portal`;

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const detail = await readError(response);
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (body.detail) {
      return typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    }
  } catch {
    // 本地调试时，服务端可能返回纯文本错误。
  }
  return `${response.status} ${response.statusText}`;
}

export const portalApi = {
  createSession(title?: string) {
    return requestJson<CreateSessionResponse>('/sessions', {
      method: 'POST',
      body: JSON.stringify({ title }),
    });
  },
  getHistory() {
    return requestJson<HistoryResponse>('/history');
  },
  getSession(sessionId: string) {
    return requestJson<PortalSessionSnapshot>(`/sessions/${sessionId}`);
  },
  getProjects(): Promise<PortalProjectSummary[]> {
    return requestJson<ProjectsResponse>('/projects').then((response) => response.projects);
  },
  setActiveProject(sessionId: string, projectName: string) {
    return requestJson<SetActiveProjectResponse>(`/sessions/${sessionId}/active-project`, {
      method: 'POST',
      body: JSON.stringify({ projectName }),
    });
  },
  setSelectedFile(sessionId: string, filePath: string | null) {
    return requestJson<SelectFileResponse>(`/sessions/${sessionId}/selected-file`, {
      method: 'POST',
      body: JSON.stringify({ filePath }),
    });
  },
  sendMessage(sessionId: string, content: string, selectedFilePath?: string | null) {
    return requestJson<SendMessageResponse>(`/sessions/${sessionId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ content, selectedFilePath }),
    });
  },
  streamUrl(sessionId: string) {
    return `${API_BASE}/sessions/${sessionId}/stream`;
  },
};

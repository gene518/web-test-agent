export type UpdateInfo = {
  current_revision: string;
  latest_revision: string;
  has_update: boolean;
  checked_at?: string;
  run_url?: string;
  operation_id?: string | null;
  maintenance?: boolean;
};

export type UpdateOperation = {
  operation_id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "rolled_back";
  phase: string;
  current_revision?: string;
  target_revision?: string;
  busy_threads?: number;
  scheduler_active?: boolean;
  error?: string;
  rollback_error?: string;
  updated_at?: string;
};

const UPDATE_API = "/api/update";

async function responseJson<T>(response: Response): Promise<T> {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = typeof payload?.error === "string"
      ? payload.error
      : `HTTP ${response.status}`;
    throw new Error(message);
  }
  return payload as T;
}
export async function checkForUpdate(force = false): Promise<UpdateInfo> {
  const response = await fetch(`${UPDATE_API}/${force ? "check" : "status"}`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  return responseJson<UpdateInfo>(response);
}

async function csrfToken(): Promise<string> {
  const response = await fetch(`${UPDATE_API}/csrf`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  const payload = await responseJson<{ token?: unknown }>(response);
  if (typeof payload.token !== "string" || !payload.token) {
    throw new Error("更新服务未返回有效的 CSRF token");
  }
  return payload.token;
}

export async function startUpdate(): Promise<UpdateOperation> {
  const token = await csrfToken();
  const response = await fetch(`${UPDATE_API}/apply`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "X-CSRF-Token": token },
  });
  return responseJson<UpdateOperation>(response);
}

export async function readUpdateOperation(operationId: string): Promise<UpdateOperation> {
  const response = await fetch(
    `${UPDATE_API}/operations/${encodeURIComponent(operationId)}`,
    { credentials: "same-origin", cache: "no-store" },
  );
  return responseJson<UpdateOperation>(response);
}

export function shortRevision(revision: string | undefined): string {
  return revision?.slice(0, 7) || "unknown";
}

export function updatePhaseLabel(operation: UpdateOperation | undefined): string {
  if (!operation) return "";
  if (operation.status === "succeeded") return "更新完成，正在重新连接";
  if (operation.status === "rolled_back") return "更新失败，已恢复上一版本";
  if (operation.status === "failed") return "更新失败";
  switch (operation.phase) {
    case "waiting_for_idle":
      return "正在等待运行中的任务结束";
    case "pulling_images":
      return "正在下载更新";
    case "starting_reconciler":
    case "reconciling":
    case "recreating_services":
      return "正在重启服务";
    case "rolling_back":
      return "健康检查失败，正在回滚";
    default:
      return "正在准备更新";
  }
}

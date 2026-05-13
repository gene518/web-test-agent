const EXPLICIT_NEW_THREAD_KEY = "web-portal:explicit-new-thread";
const ACTIVE_THREAD_RUN_PREFIX = "web-portal:active-thread-run:";
const LATEST_ACTIVE_RUN_KEY = "web-portal:latest-active-run";

function getSessionStorage(): Storage | undefined {
  if (typeof window === "undefined") {
    return undefined;
  }
  return window.sessionStorage;
}

export function markExplicitNewThreadRequested() {
  getSessionStorage()?.setItem(EXPLICIT_NEW_THREAD_KEY, "1");
}

export function clearExplicitNewThreadRequested() {
  getSessionStorage()?.removeItem(EXPLICIT_NEW_THREAD_KEY);
}

export function isExplicitNewThreadRequested(): boolean {
  return getSessionStorage()?.getItem(EXPLICIT_NEW_THREAD_KEY) === "1";
}

export function markActiveThreadRun(threadId: string, runId: string) {
  const storage = getSessionStorage();
  storage?.setItem(`${ACTIVE_THREAD_RUN_PREFIX}${threadId}`, runId);
  storage?.setItem(LATEST_ACTIVE_RUN_KEY, JSON.stringify({ threadId, runId }));
}

export function getActiveThreadRunId(threadId: string): string | null {
  return (
    getSessionStorage()?.getItem(`${ACTIVE_THREAD_RUN_PREFIX}${threadId}`) ??
    null
  );
}

export function clearActiveThreadRun(threadId: string, runId?: string) {
  const storage = getSessionStorage();
  if (!storage) {
    return;
  }

  const key = `${ACTIVE_THREAD_RUN_PREFIX}${threadId}`;
  const currentRunId = storage.getItem(key);
  if (!runId || currentRunId === runId) {
    storage.removeItem(key);
  }

  const latest = getLatestActiveRun();
  if (
    latest &&
    latest.threadId === threadId &&
    (!runId || latest.runId === runId)
  ) {
    storage.removeItem(LATEST_ACTIVE_RUN_KEY);
  }
}

export function getLatestActiveRun(): {
  threadId: string;
  runId: string;
} | null {
  const raw = getSessionStorage()?.getItem(LATEST_ACTIVE_RUN_KEY);
  if (!raw) {
    return null;
  }

  try {
    const parsed = JSON.parse(raw) as { threadId?: unknown; runId?: unknown };
    if (
      typeof parsed.threadId === "string" &&
      typeof parsed.runId === "string"
    ) {
      return {
        threadId: parsed.threadId,
        runId: parsed.runId,
      };
    }
  } catch {
    // 旧数据格式损坏时直接清掉，避免后续取消误用。
  }

  getSessionStorage()?.removeItem(LATEST_ACTIVE_RUN_KEY);
  return null;
}

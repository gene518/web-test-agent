import type { ClientConfig } from "./types";

export type ActiveRun = {
  threadId: string;
  runId: string;
};

export type ClientConfigDraft = {
  projectRoot: string;
  backendPort: string;
};

export function activeRunIdForThread(
  activeRun: ActiveRun | undefined,
  threadId: string | null,
): string | undefined {
  return activeRun?.threadId === threadId ? activeRun.runId : undefined;
}

export function configDraft(config: ClientConfig): ClientConfigDraft {
  return {
    projectRoot: config.projectRoot,
    backendPort: String(config.backendPort),
  };
}

export function backendPortError(value: string): string | null {
  if (!value.trim()) return "请输入后端端口";
  const port = Number(value);
  if (!isValidBackendPort(port)) {
    return "端口必须是 1024 到 65535 之间的整数";
  }
  return null;
}

export function isValidBackendPort(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 1024 && value <= 65535;
}

export function configFromDraft(draft: ClientConfigDraft): ClientConfig | null {
  if (backendPortError(draft.backendPort)) return null;
  return {
    projectRoot: draft.projectRoot.trim(),
    backendPort: Number(draft.backendPort),
  };
}

export function shouldSubmitOnEnter(
  key: string,
  shiftKey: boolean,
  isComposing: boolean,
): boolean {
  return key === "Enter" && !shiftKey && !isComposing;
}

export function cancellationFailureMessages(
  results: PromiseSettledResult<unknown>[],
): string[] {
  return results.flatMap((result) => {
    if (result.status === "fulfilled") return [];
    const reason = result.reason;
    return [reason instanceof Error ? reason.message : String(reason)];
  });
}

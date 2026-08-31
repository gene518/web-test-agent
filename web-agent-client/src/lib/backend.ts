import { isTauri, invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";
import { Client } from "@langchain/langgraph-sdk";
import { isValidBackendPort } from "./client-state";
import type { BackendStatus, ClientConfig } from "./types";
import { DEFAULT_BACKEND_PORT } from "./types";

const CONFIG_KEY = "web-test-agent.client-config.v1";
const INFO_PROBE_TIMEOUT_MS = 2_000;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function isLangGraphInfo(value: unknown): boolean {
  return (
    isRecord(value) &&
    typeof value.langgraph_py_version === "string" &&
    value.langgraph_py_version.trim().length > 0 &&
    isRecord(value.flags)
  );
}

async function fetchBackendInfo(url: string): Promise<{
  response: Response;
  info: unknown;
}> {
  const controller = new AbortController();
  const timeout = globalThis.setTimeout(() => controller.abort(), INFO_PROBE_TIMEOUT_MS);
  try {
    const response = await fetch(`${url}/info`, { signal: controller.signal });
    const info: unknown = response.ok ? await response.json().catch(() => null) : null;
    return { response, info };
  } finally {
    globalThis.clearTimeout(timeout);
  }
}

export function apiUrl(port: number): string {
  return `http://127.0.0.1:${port}`;
}

export function browserLangGraphApiUrl(): string {
  const configured = import.meta.env.VITE_LANGGRAPH_API_URL?.trim();
  if (configured) return configured.replace(/\/+$/, "");
  if (typeof globalThis.location === "undefined") return "/api/langgraph";
  return `${globalThis.location.origin}/api/langgraph`;
}

export function runtimeLangGraphApiUrl(port: number): string {
  return isTauri() ? apiUrl(port) : browserLangGraphApiUrl();
}

export function loadClientConfig(): ClientConfig {
  try {
    const stored = JSON.parse(localStorage.getItem(CONFIG_KEY) ?? "{}") as Partial<ClientConfig>;
    return {
      projectRoot: typeof stored.projectRoot === "string" ? stored.projectRoot : "",
      backendPort:
        isValidBackendPort(stored.backendPort)
          ? Number(stored.backendPort)
          : DEFAULT_BACKEND_PORT,
    };
  } catch {
    return { projectRoot: "", backendPort: DEFAULT_BACKEND_PORT };
  }
}

export function saveClientConfig(config: ClientConfig): void {
  localStorage.setItem(CONFIG_KEY, JSON.stringify(config));
}

export function createAgentClient(url: string): Client {
  return new Client({
    apiUrl: url,
    apiKey: null,
    callerOptions: {
      fetch: isTauri() ? tauriFetch : globalThis.fetch.bind(globalThis),
      maxRetries: 1,
    },
  });
}

export async function getBackendStatus(config: ClientConfig): Promise<BackendStatus> {
  if (isTauri()) {
    return invoke<BackendStatus>("backend_status", {
      projectRoot: config.projectRoot,
      port: config.backendPort,
    });
  }
  const resolvedApiUrl = browserLangGraphApiUrl();
  try {
    const { response, info } = await fetchBackendInfo(resolvedApiUrl);
    if (!response.ok) {
      return {
        state: "error",
        apiUrl: resolvedApiUrl,
        projectRoot: config.projectRoot,
        message: `后端返回 HTTP ${response.status}`,
      };
    }
    if (!isLangGraphInfo(info)) {
      return {
        state: "conflict",
        apiUrl: resolvedApiUrl,
        projectRoot: config.projectRoot,
        message: "当前 H5 地址未连接到有效的 LangGraph 服务。",
      };
    }
    return {
      state: "running",
      apiUrl: resolvedApiUrl,
      projectRoot: "/data/projects",
      message: "H5 服务已连接",
    };
  } catch {
    return {
      state: "stopped",
      apiUrl: resolvedApiUrl,
      projectRoot: config.projectRoot,
      message: "H5 后端暂时不可用，请稍后重试。",
    };
  }
}

export async function restartBackend(config: ClientConfig): Promise<BackendStatus> {
  if (!isTauri()) return getBackendStatus(config);
  return invoke<BackendStatus>("restart_backend", {
    projectRoot: config.projectRoot,
    port: config.backendPort,
  });
}

export async function stopBackend(): Promise<void> {
  if (isTauri()) await invoke("stop_backend");
}

export async function readBackendLog(projectRoot: string, tailLines = 200): Promise<string> {
  if (!isTauri()) return "浏览器预览模式不读取本地日志。";
  return invoke<string>("backend_log", { projectRoot, tailLines });
}

export async function revealPathInFileManager(
  projectRoot: string,
  baseDir: string | undefined,
  path: string,
): Promise<void> {
  if (!isTauri()) {
    if (typeof globalThis.location === "undefined") {
      throw new Error("当前浏览器地址不可用，无法打开产物预览。");
    }
    const previewUrl = new URL("/api/artifacts/preview", globalThis.location.origin);
    previewUrl.searchParams.set("path", path);
    if (baseDir) previewUrl.searchParams.set("base_dir", baseDir);
    globalThis.open(previewUrl.toString(), "_blank", "noopener,noreferrer");
    return;
  }
  await invoke("reveal_path_in_file_manager", { projectRoot, baseDir, path });
}

export async function chooseProjectRoot(defaultPath?: string): Promise<string | null> {
  if (!isTauri()) return null;
  const selected = await open({
    title: "选择 Web Test Agent 仓库根目录",
    directory: true,
    multiple: false,
    defaultPath: defaultPath || undefined,
  });
  return typeof selected === "string" ? selected : null;
}

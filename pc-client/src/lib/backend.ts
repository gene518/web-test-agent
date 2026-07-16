import { isTauri, invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";
import { Client } from "@langchain/langgraph-sdk";
import type { BackendStatus, ClientConfig } from "./types";
import { DEFAULT_BACKEND_PORT } from "./types";

const CONFIG_KEY = "web-test-agent.client-config.v1";

export function apiUrl(port: number): string {
  return `http://127.0.0.1:${port}`;
}

export function loadClientConfig(): ClientConfig {
  try {
    const stored = JSON.parse(localStorage.getItem(CONFIG_KEY) ?? "{}") as Partial<ClientConfig>;
    return {
      projectRoot: typeof stored.projectRoot === "string" ? stored.projectRoot : "",
      backendPort:
        Number.isInteger(stored.backendPort) && Number(stored.backendPort) > 0
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
  try {
    const response = await fetch(`${apiUrl(config.backendPort)}/info`);
    return {
      state: response.ok ? "running" : "error",
      apiUrl: apiUrl(config.backendPort),
      projectRoot: config.projectRoot,
      message: response.ok ? "浏览器预览模式" : `后端返回 HTTP ${response.status}`,
    };
  } catch {
    return {
      state: "stopped",
      apiUrl: apiUrl(config.backendPort),
      projectRoot: config.projectRoot,
      message: "浏览器预览模式不会自动启动后端。",
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

export async function stopBackend(port: number): Promise<void> {
  if (isTauri()) await invoke("stop_backend", { port });
}

export async function readBackendLog(projectRoot: string, tailLines = 200): Promise<string> {
  if (!isTauri()) return "浏览器预览模式不读取本地日志。";
  return invoke<string>("backend_log", { projectRoot, tailLines });
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

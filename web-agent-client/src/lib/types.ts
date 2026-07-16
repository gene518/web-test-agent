import type { Message } from "@langchain/langgraph-sdk";

export const ASSISTANT_ID = "web-autotest-agent";
export const DEFAULT_BACKEND_PORT = 2024;
export const STREAM_MODES = ["values", "messages-tuple", "custom"] as const;

export type AgentState = {
  messages?: Message[];
  display_messages?: Message[];
  __interrupt__?: unknown;
  [key: string]: unknown;
};

export type BackendState =
  | "checking"
  | "stopped"
  | "starting"
  | "running"
  | "conflict"
  | "error";

export type BackendStatus = {
  state: BackendState;
  apiUrl: string;
  projectRoot: string;
  pid?: number;
  message?: string;
};

export type ClientConfig = {
  projectRoot: string;
  backendPort: number;
};

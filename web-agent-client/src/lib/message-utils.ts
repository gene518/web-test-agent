import type { AIMessage, Message, Thread } from "@langchain/langgraph-sdk";
import type { AgentState } from "./types";

type RecordValue = Record<string, unknown>;

export type CanonicalMessage = Message & { type: "human" | "ai" | "tool" };

export type ToolInvocation = {
  id: string;
  name: string;
  args: unknown;
  result?: string;
  status: "running" | "success" | "error";
};

function isRecord(value: unknown): value is RecordValue {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeType(value: unknown): CanonicalMessage["type"] | null {
  if (typeof value !== "string") return null;
  switch (value.toLowerCase()) {
    case "human":
    case "humanmessage":
      return "human";
    case "ai":
    case "aimessage":
      return "ai";
    case "tool":
    case "toolmessage":
      return "tool";
    default:
      return null;
  }
}

export function normalizeMessage(value: unknown): CanonicalMessage | null {
  if (!isRecord(value)) return null;
  const type = normalizeType(value.type ?? value.role);
  if (!type) return null;

  const content =
    typeof value.content === "string" || Array.isArray(value.content)
      ? value.content
      : value.content == null
        ? ""
        : JSON.stringify(value.content);

  return { ...value, type, content } as CanonicalMessage;
}

export function messageText(message: Pick<Message, "content">): string {
  if (typeof message.content === "string") return message.content;
  if (!Array.isArray(message.content)) return "";
  return message.content
    .map((block) => {
      if ("text" in block && typeof block.text === "string") return block.text;
      return isRecord(block) ? JSON.stringify(block) : String(block);
    })
    .filter(Boolean)
    .join("\n");
}

function toolCalls(message: CanonicalMessage): NonNullable<AIMessage["tool_calls"]> {
  if (message.type !== "ai") return [];
  const direct = (message as AIMessage).tool_calls;
  if (Array.isArray(direct)) return direct;
  const additional = isRecord(message.additional_kwargs)
    ? message.additional_kwargs.tool_calls
    : undefined;
  return Array.isArray(additional)
    ? (additional as NonNullable<AIMessage["tool_calls"]>)
    : [];
}

function stableStringify(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  if (isRecord(value)) {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value) ?? String(value);
}

function messageFingerprint(message: CanonicalMessage): string {
  const calls = toolCalls(message).map((call) => ({
    id: call.id,
    name: call.name,
    args: call.args,
  }));
  const toolCallId =
    message.type === "tool" && "tool_call_id" in message
      ? message.tool_call_id
      : undefined;
  return stableStringify({
    type: message.type,
    content: message.content,
    calls,
    toolCallId,
  });
}

function messageScore(message: CanonicalMessage): number {
  return messageText(message).length + toolCalls(message).length * 1000;
}

export function mergeMessages(...sources: unknown[][]): CanonicalMessage[] {
  const result: CanonicalMessage[] = [];
  const indexes = new Map<string, number>();

  for (const source of sources) {
    for (const raw of source) {
      const message = normalizeMessage(raw);
      if (!message) continue;
      const key = message.id ? `id:${message.id}` : `fp:${messageFingerprint(message)}`;
      const index = indexes.get(key);
      if (index == null) {
        indexes.set(key, result.length);
        result.push(message);
      } else if (messageScore(message) >= messageScore(result[index])) {
        result[index] = message;
      }
    }
  }
  return result;
}

export function conversationMessages(
  values: AgentState | undefined,
  liveMessages: unknown[] = [],
): CanonicalMessage[] {
  return mergeMessages(
    values?.display_messages ?? [],
    values?.messages ?? [],
    liveMessages,
  );
}

export function buildToolInvocations(messages: CanonicalMessage[]): ToolInvocation[] {
  const items: ToolInvocation[] = [];
  const byId = new Map<string, ToolInvocation>();

  for (const message of messages) {
    for (const [index, call] of toolCalls(message).entries()) {
      const id = call.id || `${message.id ?? "ai"}-tool-${index}`;
      const item: ToolInvocation = {
        id,
        name: call.name || "tool",
        args: call.args ?? {},
        status: "running",
      };
      items.push(item);
      byId.set(id, item);
    }
    if (message.type === "tool") {
      const id = message.tool_call_id;
      const existing = byId.get(id);
      const result = messageText(message);
      if (existing) {
        existing.result = result;
        existing.status = message.status === "error" ? "error" : "success";
      } else {
        items.push({
          id,
          name: message.name || "tool",
          args: {},
          result,
          status: message.status === "error" ? "error" : "success",
        });
      }
    }
  }
  return items;
}

export function toolsForMessage(
  message: CanonicalMessage,
  invocations: ToolInvocation[],
): ToolInvocation[] {
  const ids = new Set(toolCalls(message).map((call) => call.id).filter(Boolean));
  return invocations.filter((item) => ids.has(item.id));
}

export function summarizeThreadTitle(text: string, limit = 32): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (!normalized) return "新对话";
  return normalized.length <= limit
    ? normalized
    : `${normalized.slice(0, limit - 1)}…`;
}

export function threadTitle(thread: Thread<AgentState>): string {
  const metadata = isRecord(thread.metadata) ? thread.metadata : {};
  if (typeof metadata.thread_title === "string" && metadata.thread_title.trim()) {
    return metadata.thread_title.trim();
  }
  const messages = conversationMessages(thread.values);
  const firstHuman = messages.find((message) => message.type === "human");
  return firstHuman ? summarizeThreadTitle(messageText(firstHuman)) : "未命名对话";
}

export function extractInterruptQuestion(value: unknown): string {
  const candidate = Array.isArray(value) ? value[0] : value;
  if (!isRecord(candidate)) return "Agent 正在等待你的补充信息。";
  const nested = candidate.value;
  if (nested !== undefined && nested !== candidate) {
    const nestedQuestion = extractInterruptQuestion(nested);
    if (nestedQuestion !== "Agent 正在等待你的补充信息。") return nestedQuestion;
  }
  for (const key of ["question", "message", "prompt", "description"]) {
    if (typeof candidate[key] === "string" && candidate[key].trim()) {
      return candidate[key].trim();
    }
  }
  return "Agent 正在等待你的补充信息。";
}

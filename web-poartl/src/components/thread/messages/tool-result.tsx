import type { Message } from "@langchain/langgraph-sdk";
import { useMemo } from "react";

const MAX_STRING_LENGTH = 8_000;
const MAX_JSON_LENGTH = 24_000;
const MAX_COLLECTION_ITEMS = 80;
const MAX_DEPTH = 8;

type ToolMessageLike = Message & {
  artifact?: unknown;
  name?: string;
  status?: string;
  tool_call_id?: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseJsonString(value: string): unknown {
  const trimmed = value.trim();
  if (!trimmed) {
    return value;
  }

  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) {
    return value;
  }

  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
}

function normalizeContentBlocks(value: unknown): unknown {
  if (!Array.isArray(value)) {
    return value;
  }

  const textBlocks = value.filter(
    (block): block is { type: "text"; text: string } =>
      isRecord(block) &&
      block.type === "text" &&
      typeof block.text === "string",
  );

  if (textBlocks.length === value.length) {
    return textBlocks.map((block) => block.text).join("\n\n");
  }

  return value;
}

function parseDisplayValue(value: unknown): unknown {
  const normalized = normalizeContentBlocks(value);
  if (typeof normalized === "string") {
    return parseJsonString(normalized);
  }
  return normalized;
}

function truncateString(value: string): string {
  if (value.length <= MAX_STRING_LENGTH) {
    return value;
  }
  return `${value.slice(0, MAX_STRING_LENGTH)}... [truncated ${value.length - MAX_STRING_LENGTH} chars]`;
}

function sanitizeJsonValue(value: unknown, depth = 0): unknown {
  if (typeof value === "string") {
    return truncateString(value);
  }

  if (
    value == null ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return value;
  }

  if (depth >= MAX_DEPTH) {
    return "[Max depth reached]";
  }

  if (Array.isArray(value)) {
    const visibleItems = value
      .slice(0, MAX_COLLECTION_ITEMS)
      .map((item) => sanitizeJsonValue(item, depth + 1));
    if (value.length > MAX_COLLECTION_ITEMS) {
      visibleItems.push(
        `[Truncated ${value.length - MAX_COLLECTION_ITEMS} array items]`,
      );
    }
    return visibleItems;
  }

  if (isRecord(value)) {
    const entries = Object.entries(value);
    const sanitized = entries
      .slice(0, MAX_COLLECTION_ITEMS)
      .reduce<Record<string, unknown>>((acc, [key, item]) => {
        acc[key] = sanitizeJsonValue(item, depth + 1);
        return acc;
      }, {});
    if (entries.length > MAX_COLLECTION_ITEMS) {
      sanitized.__truncated__ = `${entries.length - MAX_COLLECTION_ITEMS} object keys`;
    }
    return sanitized;
  }

  return String(value);
}

function buildToolJsonPayload(message: ToolMessageLike) {
  return sanitizeJsonValue({
    type: "tool",
    tool_call_id: message.tool_call_id ?? "",
    name: message.name ?? "",
    status: message.status ?? "",
    content: parseDisplayValue(message.content),
    artifact:
      "artifact" in message ? parseDisplayValue(message.artifact) : undefined,
  });
}

function stringifyPayload(payload: unknown): string {
  const text = JSON.stringify(payload, null, 2);
  if (text.length <= MAX_JSON_LENGTH) {
    return text;
  }
  return `${text.slice(0, MAX_JSON_LENGTH)}\n\n[UI display truncated ${text.length - MAX_JSON_LENGTH} chars]`;
}

export function ToolResult({ message }: { message: Message }) {
  const toolMessage = message as ToolMessageLike;
  const payload = useMemo(
    () => buildToolJsonPayload(toolMessage),
    [toolMessage],
  );
  const jsonText = useMemo(() => stringifyPayload(payload), [payload]);
  const toolLabel = toolMessage.name || toolMessage.tool_call_id || "tool";

  return (
    <details className="mx-auto w-full max-w-3xl overflow-hidden rounded-lg border border-gray-200 bg-white">
      <summary className="cursor-pointer border-b border-gray-200 bg-gray-50 px-4 py-2 text-sm font-medium text-gray-900">
        Tool{" "}
        <code className="rounded bg-gray-100 px-2 py-1 text-xs">
          {toolLabel}
        </code>
      </summary>
      <pre className="max-h-[36rem] overflow-auto bg-gray-100 p-3 text-xs whitespace-pre-wrap text-gray-800 [overflow-wrap:anywhere]">
        {jsonText}
      </pre>
    </details>
  );
}

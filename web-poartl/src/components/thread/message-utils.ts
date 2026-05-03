import { parsePartialJson } from "@langchain/core/output_parsers";
import type { AIMessage, Message } from "@langchain/langgraph-sdk";

export const THREAD_STREAM_MODES = [
  "values",
  "messages-tuple",
  "custom",
] as const;

export type CanonicalMessageType = "human" | "ai" | "tool";

export type CanonicalMessage = Message & {
  type: CanonicalMessageType;
};

export type RenderToolCall = Omit<
  NonNullable<AIMessage["tool_calls"]>[number],
  "args"
> & {
  args?: Record<string, any>;
  index?: string | number;
  partialArgsText?: string;
};

type ToolCallArgsState = {
  args?: Record<string, any>;
  completeness: 0 | 1 | 2;
  partialArgsText?: string;
};

function isRecord(value: unknown): value is Record<string, any> {
  return typeof value === "object" && value !== null;
}

function normalizeMessageType(type: unknown): CanonicalMessageType | undefined {
  if (typeof type !== "string") {
    return undefined;
  }

  switch (type.toLowerCase()) {
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
      return undefined;
  }
}

function normalizeMessageContent(content: unknown): Message["content"] {
  if (typeof content === "string" || Array.isArray(content)) {
    return content as Message["content"];
  }
  if (content == null) {
    return "";
  }
  if (isRecord(content)) {
    return [content] as Message["content"];
  }
  return String(content);
}

function isStringOrNumber(value: unknown): value is string | number {
  return typeof value === "string" || typeof value === "number";
}

function hasMeaningfulContent(content: Message["content"]): boolean {
  if (typeof content === "string") {
    return content.trim().length > 0;
  }
  return Array.isArray(content) && content.length > 0;
}

function serializeContent(content: Message["content"]): string {
  if (typeof content === "string") {
    return content;
  }
  try {
    return JSON.stringify(content);
  } catch {
    return String(content);
  }
}

function mergePartialArgsText(
  existing: string | undefined,
  next: string | undefined,
  append: boolean,
): string | undefined {
  if (!next) {
    return existing;
  }
  if (!existing || !append) {
    return next;
  }
  if (next.startsWith(existing) || existing.endsWith(next)) {
    return next.length >= existing.length ? next : existing;
  }
  return `${existing}${next}`;
}

function parseToolCallArgs(value: unknown): ToolCallArgsState {
  if (isRecord(value)) {
    return {
      args: value,
      completeness: Object.keys(value).length > 0 ? 2 : 1,
    };
  }

  if (typeof value !== "string") {
    return { completeness: 0 };
  }

  try {
    const parsed = parsePartialJson(value);
    if (isRecord(parsed)) {
      return {
        args: parsed,
        completeness: Object.keys(parsed).length > 0 ? 2 : 1,
        partialArgsText: value,
      };
    }
  } catch {
    // ignore and keep partial text only
  }

  return {
    completeness: 0,
    partialArgsText: value,
  };
}

function hydrateToolCallArgsState(
  state: ToolCallArgsState,
  partialArgsText?: string,
): ToolCallArgsState {
  const mergedPartialArgsText = mergePartialArgsText(
    state.partialArgsText,
    partialArgsText,
    true,
  );

  if (!mergedPartialArgsText) {
    return state;
  }

  const parsed = parseToolCallArgs(mergedPartialArgsText);
  if (parsed.completeness > state.completeness) {
    return {
      ...parsed,
      partialArgsText: mergedPartialArgsText,
    };
  }

  return {
    ...state,
    partialArgsText: mergedPartialArgsText,
  };
}

function toolCallArgsStateFromRenderToolCall(
  toolCall: Pick<RenderToolCall, "args" | "partialArgsText"> | undefined,
): ToolCallArgsState {
  if (!toolCall) {
    return { completeness: 0 };
  }

  return hydrateToolCallArgsState(
    parseToolCallArgs(toolCall.args),
    toolCall.partialArgsText,
  );
}

function hasNonEmptyToolArgs(args: unknown): args is Record<string, any> {
  return isRecord(args) && Object.keys(args).length > 0;
}

function hasToolCallIdentity(toolCall: {
  id?: string;
  index?: string | number;
  name?: string;
}): boolean {
  return Boolean(toolCall.id || toolCall.name || toolCall.index != null);
}

function hasToolCallPartialArgs(toolCall: {
  partialArgsText?: string;
}): boolean {
  return typeof toolCall.partialArgsText === "string" && toolCall.partialArgsText.trim().length > 0;
}

function finalizeToolCalls(toolCalls: RenderToolCall[]): RenderToolCall[] {
  const identifiedToolCalls = toolCalls.filter(hasToolCallIdentity);

  return toolCalls.filter((toolCall) => {
    if (hasToolCallIdentity(toolCall)) {
      return true;
    }

    if (hasNonEmptyToolArgs(toolCall.args)) {
      return identifiedToolCalls.length === 0;
    }

    if (hasToolCallPartialArgs(toolCall)) {
      return identifiedToolCalls.length === 0;
    }

    return false;
  });
}

function readToolCallName(toolCall: Record<string, any>): string | undefined {
  if (typeof toolCall.name === "string") {
    return toolCall.name;
  }

  const functionPayload = isRecord(toolCall.function) ? toolCall.function : undefined;
  if (typeof functionPayload?.name === "string") {
    return functionPayload.name;
  }

  return undefined;
}

function readToolCallArgs(toolCall: Record<string, any>): unknown {
  const functionPayload = isRecord(toolCall.function) ? toolCall.function : undefined;
  return (
    functionPayload?.arguments ??
    toolCall.args ??
    toolCall.arguments ??
    toolCall.input
  );
}

function toolCallKey(
  toolCall: {
    id?: string;
    name?: string;
    index?: string | number;
  },
  fallbackIndex: number,
): string {
  if (toolCall.id) {
    return `id:${toolCall.id}`;
  }
  if (toolCall.index !== undefined && toolCall.index !== null) {
    return `index:${toolCall.index}`;
  }
  if (!toolCall.name) {
    return "anonymous";
  }
  return `fallback:${toolCall.name ?? "tool"}:${fallbackIndex}`;
}

function mergeToolCallArgs(
  existing: ToolCallArgsState,
  incoming: ToolCallArgsState,
): ToolCallArgsState {
  if (incoming.completeness > existing.completeness) {
    return incoming;
  }

  if (existing.completeness > incoming.completeness) {
    return {
      ...existing,
      partialArgsText: mergePartialArgsText(
        existing.partialArgsText,
        incoming.partialArgsText,
        true,
      ),
    };
  }

  if (existing.completeness === 2 && incoming.completeness === 2) {
    return {
      args: {
        ...(incoming.args ?? {}),
        ...(existing.args ?? {}),
      },
      completeness: 2,
      partialArgsText: mergePartialArgsText(
        existing.partialArgsText,
        incoming.partialArgsText,
        true,
      ),
    };
  }

  if (existing.completeness === 1 && incoming.completeness === 1) {
    return {
      args: existing.args ?? incoming.args ?? {},
      completeness: 1,
      partialArgsText: mergePartialArgsText(
        existing.partialArgsText,
        incoming.partialArgsText,
        true,
      ),
    };
  }

  return {
    ...existing,
    partialArgsText: mergePartialArgsText(
      existing.partialArgsText,
      incoming.partialArgsText,
      true,
    ),
  };
}

export function normalizeToolCalls(
  message:
    | {
        additional_kwargs?: unknown;
        content?: unknown;
        tool_call_chunks?: unknown;
        tool_calls?: unknown;
      }
    | undefined,
): RenderToolCall[] {
  const mergedToolCalls = new Map<string, RenderToolCall>();

  const upsertToolCall = (
    toolCall: {
      args?: unknown;
      id?: string;
      index?: string | number;
      name?: string;
      type?: "tool_call";
    },
    fallbackIndex: number,
    appendPartialText = false,
  ) => {
    const key = toolCallKey(toolCall, fallbackIndex);
    const existing = mergedToolCalls.get(key);
    const mergedArgsState = mergeToolCallArgs(
      toolCallArgsStateFromRenderToolCall(existing),
      parseToolCallArgs(toolCall.args),
    );
    const hydratedArgsState = hydrateToolCallArgsState(mergedArgsState);

    mergedToolCalls.set(key, {
      ...(existing ?? {}),
      ...toolCall,
      args: hydratedArgsState.args,
      id: existing?.id ?? toolCall.id ?? "",
      index: existing?.index ?? toolCall.index,
      name: existing?.name || toolCall.name || "",
      partialArgsText: mergePartialArgsText(
        existing?.partialArgsText,
        hydratedArgsState.partialArgsText,
        appendPartialText || !!existing?.partialArgsText,
      ),
      type: "tool_call",
    });
  };

  if (message && Array.isArray(message.tool_calls)) {
    message.tool_calls.forEach((toolCall, index) => {
      if (isRecord(toolCall)) {
        upsertToolCall(
          {
            args: readToolCallArgs(toolCall),
            id: typeof toolCall.id === "string" ? toolCall.id : undefined,
            index: isStringOrNumber(toolCall.index) ? toolCall.index : undefined,
            name: readToolCallName(toolCall),
            type: "tool_call",
          },
          index,
        );
      }
    });
  }

  if (message && Array.isArray(message.tool_call_chunks)) {
    message.tool_call_chunks.forEach((toolCallChunk, index) => {
      if (isRecord(toolCallChunk)) {
        upsertToolCall(
          {
            args: readToolCallArgs(toolCallChunk),
            id: typeof toolCallChunk.id === "string" ? toolCallChunk.id : undefined,
            index: isStringOrNumber(toolCallChunk.index)
              ? toolCallChunk.index
              : undefined,
            name: readToolCallName(toolCallChunk),
            type: "tool_call",
          },
          index,
          true,
        );
      }
    });
  }

  const additionalKwargs =
    message && isRecord(message.additional_kwargs)
      ? message.additional_kwargs
      : undefined;

  if (Array.isArray(additionalKwargs?.tool_calls)) {
    additionalKwargs.tool_calls.forEach((toolCall, index) => {
      if (isRecord(toolCall)) {
        upsertToolCall(
          {
            args: readToolCallArgs(toolCall),
            id: typeof toolCall.id === "string" ? toolCall.id : undefined,
            index: isStringOrNumber(toolCall.index) ? toolCall.index : undefined,
            name: readToolCallName(toolCall),
            type: "tool_call",
          },
          index,
          typeof readToolCallArgs(toolCall) === "string",
        );
      }
    });
  }

  if (Array.isArray(additionalKwargs?.tool_call_chunks)) {
    additionalKwargs.tool_call_chunks.forEach((toolCallChunk, index) => {
      if (isRecord(toolCallChunk)) {
        upsertToolCall(
          {
            args: readToolCallArgs(toolCallChunk),
            id: typeof toolCallChunk.id === "string" ? toolCallChunk.id : undefined,
            index: isStringOrNumber(toolCallChunk.index)
              ? toolCallChunk.index
              : undefined,
            name: readToolCallName(toolCallChunk),
            type: "tool_call",
          },
          index,
          true,
        );
      }
    });
  }

  if (isRecord(additionalKwargs?.function_call)) {
    const functionCall = additionalKwargs.function_call;
    upsertToolCall(
      {
        args: readToolCallArgs(functionCall),
        name: readToolCallName(functionCall),
        type: "tool_call",
      },
      0,
      true,
    );
  }

  if (!Array.isArray(message?.content)) {
    return finalizeToolCalls(Array.from(mergedToolCalls.values()));
  }

  (message.content as Record<string, any>[]).forEach((block, index) => {
    if (!isRecord(block) || typeof block.type !== "string") {
      return;
    }

    switch (block.type) {
      case "tool_use":
        upsertToolCall(
          {
            args: block.input ?? block.args,
            id: typeof block.id === "string" ? block.id : undefined,
            index: typeof block.index === "number" ? block.index : undefined,
            name: typeof block.name === "string" ? block.name : undefined,
            type: "tool_call",
          },
          index,
        );
        break;
      case "tool_call":
      case "server_tool_call":
        upsertToolCall(
          {
            args: block.args,
            id: typeof block.id === "string" ? block.id : undefined,
            index:
              typeof block.index === "string" || typeof block.index === "number"
                ? block.index
                : undefined,
            name: typeof block.name === "string" ? block.name : undefined,
            type: "tool_call",
          },
          index,
        );
        break;
      case "tool_call_chunk":
      case "server_tool_call_chunk":
        upsertToolCall(
          {
            args: block.args,
            id: typeof block.id === "string" ? block.id : undefined,
            index:
              typeof block.index === "string" || typeof block.index === "number"
                ? block.index
                : undefined,
            name: typeof block.name === "string" ? block.name : undefined,
            type: "tool_call",
          },
          index,
          true,
        );
        break;
      default:
        break;
    }
  });

  return finalizeToolCalls(Array.from(mergedToolCalls.values()));
}

function mergeToolCalls(
  primary: RenderToolCall[],
  secondary: RenderToolCall[],
): RenderToolCall[] {
  const merged = new Map<string, RenderToolCall>();

  const upsert = (toolCall: RenderToolCall, fallbackIndex: number) => {
    const key = toolCallKey(toolCall, fallbackIndex);
    const existing = merged.get(key);
    if (!existing) {
      merged.set(key, toolCall);
      return;
    }

    const mergedArgsState = mergeToolCallArgs(
      toolCallArgsStateFromRenderToolCall(existing),
      toolCallArgsStateFromRenderToolCall(toolCall),
    );
    const hydratedArgsState = hydrateToolCallArgsState(mergedArgsState);

    merged.set(key, {
      ...toolCall,
      ...existing,
      args: hydratedArgsState.args,
      id: existing.id || toolCall.id || "",
      index: existing.index ?? toolCall.index,
      name: existing.name || toolCall.name || "",
      partialArgsText: mergePartialArgsText(
        existing.partialArgsText,
        hydratedArgsState.partialArgsText,
        true,
      ),
      type: "tool_call",
    });
  };

  primary.forEach(upsert);
  secondary.forEach(upsert);
  return finalizeToolCalls(Array.from(merged.values()));
}

function normalizeMessage(message: unknown): CanonicalMessage | undefined {
  if (!isRecord(message)) {
    return undefined;
  }

  const type = normalizeMessageType(message.type);
  if (!type) {
    return undefined;
  }

  const normalizedMessage: Record<string, any> = {
    ...message,
    type,
    content: normalizeMessageContent(message.content),
  };

  if (type === "ai") {
    const toolCalls = normalizeToolCalls(message);
    if (toolCalls.length > 0) {
      normalizedMessage.tool_calls = toolCalls;
    }
  }

  return normalizedMessage as CanonicalMessage;
}

export function normalizeMessages(messages: unknown): CanonicalMessage[] {
  if (Array.isArray(messages)) {
    return messages
      .map(normalizeMessage)
      .filter((message): message is CanonicalMessage => message != null);
  }

  const normalizedMessage = normalizeMessage(messages);
  return normalizedMessage ? [normalizedMessage] : [];
}

function contentFingerprint(message: CanonicalMessage): string {
  const toolCallId =
    "tool_call_id" in message && message.tool_call_id
      ? message.tool_call_id
      : "";
  const name = "name" in message && message.name ? message.name : "";
  const toolCalls = normalizeToolCalls(message)
    .map((toolCall, index) =>
      [
        toolCall.id || index,
        toolCall.name || "",
        toolCall.partialArgsText || "",
        toolCall.args ? JSON.stringify(toolCall.args) : "",
      ].join(":"),
    )
    .join("|");

  return [
    `type:${message.type}`,
    `tool_call_id:${toolCallId}`,
    `name:${name}`,
    `content:${serializeContent(message.content)}`,
    `tool_calls:${toolCalls}`,
  ].join("|");
}

function messageFingerprints(message: CanonicalMessage): string[] {
  const fingerprints = [contentFingerprint(message)];
  if (message.id) {
    fingerprints.unshift(`id:${message.id}`);
  }
  return fingerprints;
}

function mergeCanonicalMessages(
  primary: CanonicalMessage,
  secondary: CanonicalMessage,
): CanonicalMessage {
  const mergedToolCalls = mergeToolCalls(
    normalizeToolCalls(primary),
    normalizeToolCalls(secondary),
  );
  const mergedMessage: Record<string, any> = {
    ...secondary,
    ...primary,
    id: primary.id ?? secondary.id,
    type: primary.type,
    content: hasMeaningfulContent(primary.content)
      ? primary.content
      : secondary.content,
  };

  if (primary.name || secondary.name) {
    mergedMessage.name = primary.name || secondary.name;
  }
  if ("tool_call_id" in primary || "tool_call_id" in secondary) {
    mergedMessage.tool_call_id =
      ("tool_call_id" in primary && primary.tool_call_id) ||
      ("tool_call_id" in secondary && secondary.tool_call_id) ||
      "";
  }
  if ("artifact" in primary || "artifact" in secondary) {
    mergedMessage.artifact =
      ("artifact" in primary ? primary.artifact : undefined) ??
      ("artifact" in secondary ? secondary.artifact : undefined);
  }
  if ("status" in primary || "status" in secondary) {
    mergedMessage.status =
      ("status" in primary ? primary.status : undefined) ??
      ("status" in secondary ? secondary.status : undefined);
  }
  if (mergedToolCalls.length > 0) {
    mergedMessage.tool_calls = mergedToolCalls;
  }

  return mergedMessage as CanonicalMessage;
}

export function mergeVisibleMessages(
  persistedMessages: unknown,
  liveMessages: unknown,
): CanonicalMessage[] {
  const persisted = normalizeMessages(persistedMessages);
  const live = normalizeMessages(liveMessages);

  if (!persisted.length) {
    return live;
  }
  if (!live.length) {
    return persisted;
  }

  const liveByFingerprint = new Map<string, CanonicalMessage>();
  for (const message of live) {
    for (const fingerprint of messageFingerprints(message)) {
      liveByFingerprint.set(fingerprint, message);
    }
  }

  const matchedLive = new Set<CanonicalMessage>();
  const merged = persisted.map((message) => {
    const liveMessage = messageFingerprints(message)
      .map((fingerprint) => liveByFingerprint.get(fingerprint))
      .find((candidate): candidate is CanonicalMessage => candidate != null);

    if (!liveMessage) {
      return message;
    }

    matchedLive.add(liveMessage);
    return mergeCanonicalMessages(liveMessage, message);
  });

  for (const message of live) {
    if (!matchedLive.has(message)) {
      merged.push(message);
    }
  }

  return merged;
}

function normalizeInterruptValue(rawInterrupt: unknown): unknown | undefined {
  if (rawInterrupt == null) {
    return undefined;
  }

  if (Array.isArray(rawInterrupt)) {
    if (rawInterrupt.length === 0) {
      return undefined;
    }
    return rawInterrupt.length === 1 ? rawInterrupt[0] : rawInterrupt;
  }

  if (
    isRecord(rawInterrupt) &&
    rawInterrupt.when === "breakpoint" &&
    !("value" in rawInterrupt)
  ) {
    return undefined;
  }

  return rawInterrupt;
}

export function getActiveInterrupt(
  values: { __interrupt__?: unknown } | undefined,
  fallbackInterrupt?: unknown,
): unknown | undefined {
  if (values == null || !("__interrupt__" in values)) {
    return normalizeInterruptValue(fallbackInterrupt);
  }

  const normalized = normalizeInterruptValue(values.__interrupt__);
  if (normalized !== undefined) {
    return normalized;
  }

  return normalizeInterruptValue(fallbackInterrupt);
}

export function getLastLiveRenderSignal(messages: unknown): string {
  const lastRenderableMessage = normalizeMessages(messages)
    .filter((message) => message.type === "ai" || message.type === "tool")
    .at(-1);

  if (!lastRenderableMessage) {
    return "";
  }

  return [
    lastRenderableMessage.id ?? "",
    lastRenderableMessage.type,
    contentFingerprint(lastRenderableMessage),
  ].join("|");
}

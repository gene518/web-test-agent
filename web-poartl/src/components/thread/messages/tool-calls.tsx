import { ToolMessage } from "@langchain/langgraph-sdk";
import { useMemo, useState } from "react";
import { motion } from "framer-motion";
import { ChevronDown, ChevronUp } from "lucide-react";
import type { RenderToolCall } from "../message-utils";

const PREVIEW_CHAR_LIMIT = 500;
const PREVIEW_LINE_LIMIT = 4;
const STRUCTURED_PREVIEW_ITEMS = 5;

function isComplexValue(value: any): boolean {
  return Array.isArray(value) || (typeof value === "object" && value !== null);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isTextBlock(value: unknown): value is { type: "text"; text: string } {
  return isRecord(value) && value.type === "text" && typeof value.text === "string";
}

function truncatePreviewText(
  text: string,
  charLimit = PREVIEW_CHAR_LIMIT,
  lineLimit = PREVIEW_LINE_LIMIT,
): { text: string; truncated: boolean } {
  const lines = text.split("\n");
  let truncated = false;
  let preview = text;

  if (lines.length > lineLimit) {
    preview = lines.slice(0, lineLimit).join("\n");
    truncated = true;
  }

  if (preview.length > charLimit) {
    preview = preview.slice(0, charLimit);
    truncated = true;
  }

  return {
    text: truncated ? `${preview}...` : preview,
    truncated,
  };
}

function summarizeStructuredValue(value: unknown): string {
  if (value == null) {
    return "null";
  }

  if (Array.isArray(value)) {
    return `Array(${value.length})`;
  }

  if (isRecord(value)) {
    const keys = Object.keys(value);
    const previewKeys = keys.slice(0, STRUCTURED_PREVIEW_ITEMS).join(", ");
    const suffix = keys.length > STRUCTURED_PREVIEW_ITEMS ? ", ..." : "";
    return previewKeys
      ? `Object(${keys.length}) { ${previewKeys}${suffix} }`
      : "Object(0)";
  }

  return String(value);
}

function looksLikeStructuredText(value: string): boolean {
  const trimmed = value.trim();
  return trimmed.startsWith("{") || trimmed.startsWith("[");
}

function normalizeStructuredContent(value: unknown): {
  parsedContent: unknown;
  isStructuredContent: boolean;
} {
  if (Array.isArray(value)) {
    if (value.every(isTextBlock)) {
      return {
        parsedContent: value.map((block) => block.text).join("\n\n"),
        isStructuredContent: false,
      };
    }

    return {
      parsedContent: value,
      isStructuredContent: true,
    };
  }

  if (isComplexValue(value)) {
    return {
      parsedContent: value,
      isStructuredContent: true,
    };
  }

  return {
    parsedContent: value,
    isStructuredContent: false,
  };
}

function resolveToolResultContent(message: ToolMessage): {
  parsedContent: unknown;
  isStructuredContent: boolean;
} {
  const content = message.content;
  const artifact = "artifact" in message ? message.artifact : undefined;

  if (Array.isArray(content) || isComplexValue(content)) {
    return normalizeStructuredContent(content);
  }

  if (typeof content === "string") {
    try {
      const parsed = JSON.parse(content);
      return normalizeStructuredContent(parsed);
    } catch {
      if (content.trim() === "[object Object]" && artifact !== undefined) {
        if (typeof artifact === "string") {
          try {
            return normalizeStructuredContent(JSON.parse(artifact));
          } catch {
            // 忽略。
          }
        }
        return normalizeStructuredContent(artifact);
      }

      return {
        parsedContent: content,
        isStructuredContent: false,
      };
    }
  }

  if (artifact !== undefined) {
    return normalizeStructuredContent(artifact);
  }

  return {
    parsedContent: content,
    isStructuredContent: false,
  };
}

function resolveToolResultPreview(message: ToolMessage): {
  previewText: string;
  canExpand: boolean;
} {
  const content = message.content;
  const artifact = "artifact" in message ? message.artifact : undefined;

  if (Array.isArray(content) || isComplexValue(content)) {
    return {
      previewText: summarizeStructuredValue(content),
      canExpand: true,
    };
  }

  if (typeof content === "string") {
    if (content.trim() === "[object Object]" && artifact !== undefined) {
      return {
        previewText:
          typeof artifact === "string"
            ? truncatePreviewText(artifact).text
            : summarizeStructuredValue(artifact),
        canExpand: true,
      };
    }

    const preview = truncatePreviewText(content);
    return {
      previewText: preview.text,
      canExpand: preview.truncated || looksLikeStructuredText(content),
    };
  }

  if (artifact !== undefined) {
    return {
      previewText:
        typeof artifact === "string"
          ? truncatePreviewText(artifact).text
          : summarizeStructuredValue(artifact),
      canExpand: true,
    };
  }

  return {
    previewText: String(content ?? ""),
    canExpand: false,
  };
}

function StructuredValue({ value }: { value: unknown }) {
  const [isExpanded, setIsExpanded] = useState(false);

  if (value == null) {
    return <code className="rounded bg-gray-50 px-2 py-1 font-mono text-sm">null</code>;
  }

  if (!isComplexValue(value)) {
    return String(value);
  }

  return (
    <div className="flex flex-col gap-2">
      <code className="block rounded bg-gray-50 px-2 py-1 font-mono text-sm whitespace-pre-wrap break-all">
        {isExpanded ? JSON.stringify(value, null, 2) : summarizeStructuredValue(value)}
      </code>
      <button
        type="button"
        onClick={() => setIsExpanded((prev) => !prev)}
        className="flex w-fit items-center gap-1 text-xs text-gray-500 hover:text-gray-700"
      >
        {isExpanded ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />}
        {isExpanded ? "收起" : "展开"}
      </button>
    </div>
  );
}

export function ToolCalls({
  toolCalls,
}: {
  toolCalls: RenderToolCall[] | undefined;
}) {
  if (!toolCalls || toolCalls.length === 0) return null;

  return (
    <div className="mx-auto grid max-w-3xl grid-rows-[1fr_auto] gap-2">
      {toolCalls.map((tc, idx) => {
        const args =
          tc.args && typeof tc.args === "object"
            ? (tc.args as Record<string, any>)
            : undefined;
        const hasArgs = Object.keys(args ?? {}).length > 0;
        const hasExplicitEmptyArgs = !!args && Object.keys(args).length === 0;
        const partialArgsText =
          typeof tc.partialArgsText === "string" ? tc.partialArgsText.trim() : "";
        return (
          <div
            key={tc.id || `${tc.name || "tool"}:${tc.index ?? idx}`}
            className="overflow-hidden rounded-lg border border-gray-200"
          >
            <div className="border-b border-gray-200 bg-gray-50 px-4 py-2">
              <h3 className="font-medium text-gray-900">
                {tc.name}
                {tc.id && (
                  <code className="ml-2 rounded bg-gray-100 px-2 py-1 text-sm">
                    {tc.id}
                  </code>
                )}
              </h3>
            </div>
            {hasArgs ? (
              <table className="min-w-full divide-y divide-gray-200">
                <tbody className="divide-y divide-gray-200">
                  {Object.entries(args ?? {}).map(([key, value], argIdx) => (
                    <tr key={argIdx}>
                      <td className="px-4 py-2 text-sm font-medium whitespace-nowrap text-gray-900">
                        {key}
                      </td>
                      <td className="px-4 py-2 text-sm text-gray-500">
                        <StructuredValue value={value} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : partialArgsText ? (
              <code className="block p-3 text-sm whitespace-pre-wrap break-all">
                {partialArgsText}
              </code>
            ) : hasExplicitEmptyArgs ? (
              <code className="block p-3 text-sm">{"{}"}</code>
            ) : (
              <p className="p-3 text-sm text-amber-700">参数同步中</p>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function ToolResult({ message }: { message: ToolMessage }) {
  const [isExpanded, setIsExpanded] = useState(false);

  const preview = useMemo(() => resolveToolResultPreview(message), [message]);
  const expandedContent = useMemo(
    () => (isExpanded ? resolveToolResultContent(message) : null),
    [isExpanded, message],
  );
  const parsedContent = expandedContent?.parsedContent;
  const isStructuredContent = expandedContent?.isStructuredContent ?? false;
  const structuredEntries = isRecord(parsedContent) ? Object.entries(parsedContent) : [];
  const displayedText =
    expandedContent && !isStructuredContent
      ? String(parsedContent ?? "")
      : preview.previewText;
  const canToggle = preview.canExpand || isExpanded;

  return (
    <div className="mx-auto grid max-w-3xl grid-rows-[1fr_auto] gap-2">
      <div className="overflow-hidden rounded-lg border border-gray-200">
        <div className="border-b border-gray-200 bg-gray-50 px-4 py-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            {message.name ? (
              <h3 className="font-medium text-gray-900">
                工具结果：{" "}
                <code className="rounded bg-gray-100 px-2 py-1">
                  {message.name}
                </code>
              </h3>
            ) : (
              <h3 className="font-medium text-gray-900">工具结果</h3>
            )}
            {message.tool_call_id && (
              <code className="ml-2 rounded bg-gray-100 px-2 py-1 text-sm">
                {message.tool_call_id}
              </code>
            )}
          </div>
        </div>
        <motion.div
          className="min-w-full bg-gray-100"
          initial={false}
          animate={{ height: "auto" }}
          transition={{ duration: 0.3 }}
        >
          <div className="p-3">
            <motion.div
              key={isExpanded ? "expanded" : "collapsed"}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.15 }}
            >
              {isExpanded && isStructuredContent ? (
                <table className="min-w-full divide-y divide-gray-200">
                  <tbody className="divide-y divide-gray-200">
                    {(Array.isArray(parsedContent)
                      ? parsedContent
                      : structuredEntries
                    ).map((item, argIdx) => {
                      const [key, value] = Array.isArray(parsedContent)
                        ? [argIdx, item]
                        : [item[0], item[1]];
                      return (
                        <tr key={argIdx}>
                          <td className="px-4 py-2 text-sm font-medium whitespace-nowrap text-gray-900">
                            {key}
                          </td>
                          <td className="px-4 py-2 text-sm text-gray-500">
                            <StructuredValue value={value} />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              ) : (
                <code className="block text-sm whitespace-pre-wrap break-all">
                  {displayedText}
                </code>
              )}
            </motion.div>
          </div>
          {canToggle && (
            <motion.button
              onClick={() => setIsExpanded(!isExpanded)}
              className="flex w-full cursor-pointer items-center justify-center border-t-[1px] border-gray-200 py-2 text-gray-500 transition-all duration-200 ease-in-out hover:bg-gray-50 hover:text-gray-600"
              initial={{ scale: 1 }}
              whileHover={{ scale: 1.02 }}
              whileTap={{ scale: 0.98 }}
            >
              {isExpanded ? <ChevronUp /> : <ChevronDown />}
            </motion.button>
          )}
        </motion.div>
      </div>
    </div>
  );
}

import { ToolMessage } from "@langchain/langgraph-sdk";
import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ChevronDown, ChevronUp } from "lucide-react";
import type { RenderToolCall } from "../message-utils";

function isComplexValue(value: any): boolean {
  return Array.isArray(value) || (typeof value === "object" && value !== null);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isTextBlock(value: unknown): value is { type: "text"; text: string } {
  return isRecord(value) && value.type === "text" && typeof value.text === "string";
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

function renderStructuredValue(value: unknown): React.ReactNode {
  if (value == null) {
    return <code className="rounded bg-gray-50 px-2 py-1 font-mono text-sm">null</code>;
  }

  if (!isComplexValue(value)) {
    return String(value);
  }

  return (
    <code className="block rounded bg-gray-50 px-2 py-1 font-mono text-sm whitespace-pre-wrap break-all">
      {JSON.stringify(value, null, 2)}
    </code>
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
                        {renderStructuredValue(value)}
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

  const { parsedContent, isStructuredContent } = resolveToolResultContent(message);
  const structuredEntries = isRecord(parsedContent)
    ? Object.entries(parsedContent)
    : [];

  const contentStr = isStructuredContent
    ? JSON.stringify(parsedContent, null, 2)
    : String(parsedContent ?? "");
  const contentLines = contentStr.split("\n");
  const shouldTruncate = contentLines.length > 4 || contentStr.length > 500;
  const displayedContent =
    shouldTruncate && !isExpanded
      ? contentStr.length > 500
        ? contentStr.slice(0, 500) + "..."
        : contentLines.slice(0, 4).join("\n") + "\n..."
      : contentStr;

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
            <AnimatePresence
              mode="wait"
              initial={false}
            >
              <motion.div
                key={isExpanded ? "expanded" : "collapsed"}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -20 }}
                transition={{ duration: 0.2 }}
              >
                {isStructuredContent ? (
                  <table className="min-w-full divide-y divide-gray-200">
                    <tbody className="divide-y divide-gray-200">
                      {(Array.isArray(parsedContent)
                        ? isExpanded
                          ? parsedContent
                          : parsedContent.slice(0, 5)
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
                              {renderStructuredValue(value)}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                ) : (
                  <code className="block text-sm">{displayedContent}</code>
                )}
              </motion.div>
            </AnimatePresence>
          </div>
          {((shouldTruncate && !isStructuredContent) ||
            (isStructuredContent &&
              Array.isArray(parsedContent) &&
              parsedContent.length > 5)) && (
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

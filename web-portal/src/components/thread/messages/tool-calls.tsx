import type { RenderToolCall } from "../message-utils";

function isComplexValue(value: unknown): boolean {
  return Array.isArray(value) || (typeof value === "object" && value !== null);
}

function displayValue(value: unknown): string {
  if (isComplexValue(value)) {
    return JSON.stringify(value, null, 2);
  }
  return String(value);
}

export function ToolCalls({ toolCalls }: { toolCalls: RenderToolCall[] }) {
  if (toolCalls.length === 0) {
    return null;
  }

  return (
    <div className="mx-auto grid w-full max-w-3xl grid-rows-[1fr_auto] gap-2">
      {toolCalls.map((toolCall, index) => {
        const args = toolCall.args ?? {};
        const entries = Object.entries(args);

        return (
          <div
            key={toolCall.id || `${toolCall.name || "tool"}-${index}`}
            className="overflow-hidden rounded-lg border border-gray-200"
          >
            <div className="border-b border-gray-200 bg-gray-50 px-4 py-2">
              <h3 className="font-medium text-gray-900">
                {toolCall.name || "Tool Call"}
                {toolCall.id && (
                  <code className="ml-2 rounded bg-gray-100 px-2 py-1 text-sm">
                    {toolCall.id}
                  </code>
                )}
              </h3>
            </div>
            {entries.length > 0 ? (
              <table className="min-w-full divide-y divide-gray-200">
                <tbody className="divide-y divide-gray-200">
                  {entries.map(([key, value]) => (
                    <tr key={key}>
                      <td className="px-4 py-2 text-sm font-medium whitespace-nowrap text-gray-900">
                        {key}
                      </td>
                      <td className="px-4 py-2 text-sm text-gray-500">
                        {isComplexValue(value) ? (
                          <code className="rounded bg-gray-50 px-2 py-1 font-mono text-sm break-all whitespace-pre-wrap">
                            {displayValue(value)}
                          </code>
                        ) : (
                          displayValue(value)
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <code className="block p-3 text-sm whitespace-pre-wrap">
                {toolCall.partialArgsText || "{}"}
              </code>
            )}
          </div>
        );
      })}
    </div>
  );
}

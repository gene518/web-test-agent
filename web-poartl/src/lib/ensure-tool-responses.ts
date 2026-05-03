import { v4 as uuidv4 } from "uuid";
import { Message, ToolMessage } from "@langchain/langgraph-sdk";

export const DO_NOT_RENDER_ID_PREFIX = "do-not-render-";

export function ensureToolCallsHaveResponses(messages: Message[]): Message[] {
  const newMessages: ToolMessage[] = [];

  messages.forEach((message, index) => {
    if (message.type !== "ai" || message.tool_calls?.length === 0) {
      // 非 AI 消息或没有 tool call 的消息可以忽略。
      return;
    }
    // 如果存在 tool call，确保后续消息是 tool 消息。
    const followingMessage = messages[index + 1];
    if (followingMessage && followingMessage.type === "tool") {
      // 后续消息已经是 tool 消息，可以忽略。
      return;
    }

    // 后续消息不是 tool 消息时，需要补一个新的 tool 消息。
    newMessages.push(
      ...(message.tool_calls?.map((tc) => ({
        type: "tool" as const,
        tool_call_id: tc.id ?? "",
        id: `${DO_NOT_RENDER_ID_PREFIX}${uuidv4()}`,
        name: tc.name,
        content: "工具调用已处理。",
      })) ?? []),
    );
  });

  return newMessages;
}

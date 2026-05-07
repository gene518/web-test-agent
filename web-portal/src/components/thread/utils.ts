import type { Message } from "@langchain/langgraph-sdk";

/**
 * 从消息内容中提取字符串摘要，支持文本、图片、文件等多模态内容。
 * - 如果存在文本，返回拼接后的文本。
 * - 如果没有文本，返回空字符串。
 */
export function getContentString(content: Message["content"]): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  const texts = content
    .filter((c): c is { type: "text"; text: string } => c.type === "text")
    .map((c) => c.text);
  return texts.join(" ");
}

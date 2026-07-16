import { describe, expect, it } from "vitest";
import type { Thread } from "@langchain/langgraph-sdk";
import {
  buildToolInvocations,
  mergeMessages,
  summarizeThreadTitle,
  threadTitle,
} from "./message-utils";
import type { AgentState } from "./types";

describe("message normalization", () => {
  it("normalizes aliases and replaces partial messages with richer versions", () => {
    const messages = mergeMessages(
      [{ id: "a", type: "AIMessage", content: "部" }],
      [{ id: "a", type: "ai", content: "完整回答" }],
      [{ type: "human", content: "问题" }, { type: "human", content: "问题" }],
    );
    expect(messages.map((message) => message.type)).toEqual(["ai", "human"]);
    expect(messages[0].content).toBe("完整回答");
  });
});

describe("thread titles", () => {
  it("summarizes a request and prefers metadata", () => {
    expect(summarizeThreadTitle("  生成   登录页测试  ")).toBe("生成 登录页测试");
    const thread = {
      metadata: { thread_title: "登录测试" },
      values: { messages: [{ type: "human", content: "fallback" }] },
    } as unknown as Thread<AgentState>;
    expect(threadTitle(thread)).toBe("登录测试");
  });
});

describe("tool rendering data", () => {
  it("pairs tool calls and results", () => {
    const messages = mergeMessages([
      {
        id: "ai-1",
        type: "ai",
        content: "",
        tool_calls: [{ id: "call-1", name: "browser_navigate", args: { url: "https://example.com" } }],
      },
      {
        id: "tool-1",
        type: "tool",
        tool_call_id: "call-1",
        content: "Page loaded",
        status: "success",
      },
    ]);
    expect(buildToolInvocations(messages)).toEqual([
      {
        id: "call-1",
        name: "browser_navigate",
        args: { url: "https://example.com" },
        result: "Page loaded",
        status: "success",
      },
    ]);
  });
});

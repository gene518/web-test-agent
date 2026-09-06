import { describe, expect, it } from "vitest";
import type { Thread } from "@langchain/langgraph-sdk";
import {
  buildToolInvocations,
  conversationMessages,
  historicalConversationMessages,
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

  it("keeps selected thread details while the current checkpoint hydrates", () => {
    const messages = historicalConversationMessages(
      {
        display_messages: [
          { id: "human-1", type: "human", content: "历史问题" },
          { id: "ai-1", type: "ai", content: "历史回答" },
        ],
      },
      {},
    );

    expect(messages.map((message) => message.content)).toEqual(["历史问题", "历史回答"]);
  });

  it("keeps display-only intent classification after the triggering user message", () => {
    const messages = conversationMessages({
      messages: [{ id: "human-1", type: "human", content: "帮我生成测试计划" }],
      display_messages: [{
        id: "intent-1",
        type: "ai",
        content: "",
        tool_calls: [{ id: "intent-call", name: "IntentClassification", args: {} }],
      }],
    });

    expect(messages.map((message) => message.id)).toEqual(["human-1", "intent-1"]);
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

  it("prefers a persisted model title over legacy metadata", () => {
    const thread = {
      metadata: { thread_title: "请帮我生成登录页面自动化测试" },
      extracted: { thread_title: "登录自动化测试" },
    } as unknown as Thread<AgentState>;

    expect(threadTitle(thread)).toBe("登录自动化测试");
    expect(threadTitle(thread, { thread_title: "登录回归测试" })).toBe("登录回归测试");
  });

  it("falls back to the first human message for legacy thread titles", () => {
    const thread = {
      metadata: {},
      values: {
        messages: [
          { type: "ai", content: "先前回答" },
          { type: "human", content: "  生成   旧会话测试  " },
        ],
      },
    } as unknown as Thread<AgentState>;

    expect(threadTitle(thread)).toBe("生成 旧会话测试");
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

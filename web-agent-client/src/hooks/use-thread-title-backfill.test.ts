import { describe, expect, it } from "vitest";
import type { ThreadSummary } from "../lib/message-utils";
import {
  normalizedBackfillTitle,
  threadNeedsTitleBackfill,
} from "./use-thread-title-backfill";

function summary(overrides: Partial<ThreadSummary> = {}): ThreadSummary {
  return {
    thread_id: "thread-1",
    created_at: "2026-08-30T00:00:00Z",
    updated_at: "2026-08-30T00:00:00Z",
    metadata: { graph_id: "web-autotest-agent" },
    status: "idle",
    extracted: { first_message: { type: "human", content: "生成登录测试" } },
    ...overrides,
  };
}

describe("legacy title backfill", () => {
  it("queues legacy first-sentence titles but skips model titles", () => {
    expect(threadNeedsTitleBackfill(summary({ metadata: { thread_title: "生成登录测试" } }))).toBe(true);
    expect(threadNeedsTitleBackfill(summary({
      metadata: { thread_title: "登录流程测试", thread_title_source: "model-v1" },
    }))).toBe(false);
    expect(threadNeedsTitleBackfill(summary({
      extracted: { thread_title: "登录流程测试" },
    }))).toBe(false);
  });

  it("normalizes and bounds model output", () => {
    expect(normalizedBackfillTitle("  登录   流程测试  ")).toBe("登录 流程测试");
    expect(normalizedBackfillTitle("x".repeat(40))).toHaveLength(32);
    expect(normalizedBackfillTitle(null)).toBeUndefined();
  });
});

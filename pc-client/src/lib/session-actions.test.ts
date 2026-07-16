import { describe, expect, it } from "vitest";
import { activeRunIds, buildSubmitRequest } from "./session-actions";

describe("submission routing", () => {
  it("uses command.resume while interrupted", () => {
    const request = buildSubmitRequest("补充路径", {
      interrupted: true,
      newThread: false,
      id: "human-1",
    });
    expect(request.values).toEqual({});
    expect(request.options.command).toEqual({ resume: { text: "补充路径" } });
  });

  it("adds title metadata for the first message", () => {
    const request = buildSubmitRequest("生成登录测试", {
      interrupted: false,
      newThread: true,
      id: "human-1",
    });
    expect(request.options.metadata).toMatchObject({
      graph_id: "web-autotest-agent",
      thread_title: "生成登录测试",
    });
  });
});

describe("run cancellation", () => {
  it("deduplicates running, pending and locally known runs", () => {
    expect(
      activeRunIds(
        [{ run_id: "run-a" }],
        [{ run_id: "run-b" }, { run_id: "run-a" }],
        "run-local",
      ),
    ).toEqual(["run-local", "run-a", "run-b"]);
  });
});

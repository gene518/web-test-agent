import { describe, expect, it } from "vitest";
import { activeRunIds, buildSubmitRequest } from "./session-actions";

describe("submission routing", () => {
  it("uses command.resume while interrupted", () => {
    const request = buildSubmitRequest("补充路径", {
      interrupted: true,
      id: "human-1",
    });
    expect(request.values).toEqual({});
    expect(request.options.command).toEqual({ resume: { text: "补充路径" } });
  });

  it("keeps title generation out of client metadata", () => {
    const request = buildSubmitRequest("生成登录测试", {
      interrupted: false,
      id: "human-1",
    });
    expect(request.options.metadata).toEqual({ graph_id: "web-autotest-agent" });
    expect(request.values).toEqual({
      messages: [{ id: "human-1", type: "human", content: "生成登录测试" }],
    });
  });

  it("seeds a model-generated legacy title into state", () => {
    const request = buildSubmitRequest("继续", {
      interrupted: false,
      existingThreadTitle: "登录测试回归",
      id: "human-2",
    });
    expect(request.values).toMatchObject({ thread_title: "登录测试回归" });
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

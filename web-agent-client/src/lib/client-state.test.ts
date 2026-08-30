import { describe, expect, it } from "vitest";
import {
  activeRunIdForThread,
  backendPortError,
  cancellationFailureMessages,
  configDraft,
  configFromDraft,
  shouldSubmitOnEnter,
} from "./client-state";

describe("thread-bound run state", () => {
  it("only exposes a run to the thread that owns it", () => {
    const activeRun = { threadId: "thread-a", runId: "run-a" };
    expect(activeRunIdForThread(activeRun, "thread-a")).toBe("run-a");
    expect(activeRunIdForThread(activeRun, "thread-b")).toBeUndefined();
  });
});

describe("settings draft", () => {
  it("round-trips a valid client config", () => {
    const draft = configDraft({ projectRoot: "/repo", backendPort: 2024 });
    expect(configFromDraft(draft)).toEqual({ projectRoot: "/repo", backendPort: 2024 });
  });

  it.each(["", "1023", "65536", "2024.5", "not-a-port"])(
    "rejects invalid port %j",
    (port) => {
      expect(backendPortError(port)).not.toBeNull();
      expect(configFromDraft({ projectRoot: "/repo", backendPort: port })).toBeNull();
    },
  );
});

describe("composer keyboard handling", () => {
  it("does not submit while an IME composition is active", () => {
    expect(shouldSubmitOnEnter("Enter", false, true)).toBe(false);
    expect(shouldSubmitOnEnter("Enter", false, false)).toBe(true);
    expect(shouldSubmitOnEnter("Enter", true, false)).toBe(false);
  });
});

describe("run cancellation", () => {
  it("keeps every rejected cancellation visible to the caller", () => {
    const failures = cancellationFailureMessages([
      { status: "fulfilled", value: undefined },
      { status: "rejected", reason: new Error("run-a failed") },
      { status: "rejected", reason: "run-b failed" },
    ]);
    expect(failures).toEqual(["run-a failed", "run-b failed"]);
  });
});

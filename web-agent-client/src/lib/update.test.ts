import { describe, expect, it } from "vitest";
import { shortRevision, updatePhaseLabel, type UpdateOperation } from "./update";

function operation(overrides: Partial<UpdateOperation>): UpdateOperation {
  return {
    operation_id: "operation",
    status: "running",
    phase: "waiting_for_idle",
    ...overrides,
  };
}

describe("update presentation", () => {
  it("uses short immutable revisions", () => {
    expect(shortRevision("abcdef0123456789")).toBe("abcdef0");
    expect(shortRevision(undefined)).toBe("unknown");
  });

  it("describes drain, restart and rollback states", () => {
    expect(updatePhaseLabel(operation({ phase: "waiting_for_idle" }))).toContain("等待");
    expect(updatePhaseLabel(operation({ phase: "recreating_services" }))).toContain("重启");
    expect(updatePhaseLabel(operation({ status: "rolled_back" }))).toContain("恢复");
  });
});

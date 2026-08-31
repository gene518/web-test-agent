import { describe, expect, it } from "vitest";
import { actionableInterrupt, isActiveRunPhase, runPhaseLabel } from "./thread-runtime";

describe("actionable interrupts", () => {
  it("accepts only the structured missing-parameter contract", () => {
    expect(
      actionableInterrupt({
        id: "interrupt-1",
        value: { question: "请提供 URL", missing_param: "url" },
      }),
    ).toMatchObject({ question: "请提供 URL", missing_param: "url" });
    expect(actionableInterrupt({ when: "breakpoint" })).toBeUndefined();
    expect(actionableInterrupt([])).toBeUndefined();
    expect(actionableInterrupt({ value: { question: "缺少字段" } })).toBeUndefined();
  });
});

describe("run phases", () => {
  it("separates queued work from active execution", () => {
    expect(isActiveRunPhase("queued")).toBe(true);
    expect(isActiveRunPhase("awaiting_input")).toBe(false);
    expect(runPhaseLabel("queued")).toBe("任务排队中");
    expect(runPhaseLabel("running")).toBe("Agent 正在执行任务");
  });
});

import { describe, expect, it } from "vitest";
import {
  actionableInterrupt,
  checkpointInterrupt,
  isActiveRunPhase,
  runPhaseLabel,
} from "./thread-runtime";

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

  it("uses only task interrupts from the final checkpoint", () => {
    expect(checkpointInterrupt({ tasks: [] })).toBeUndefined();
    expect(checkpointInterrupt({
      tasks: [{
        interrupts: [{
          value: { question: "请提供账号", missing_param: "account" },
        }],
      }],
    })).toMatchObject({ question: "请提供账号", missing_param: "account" });
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

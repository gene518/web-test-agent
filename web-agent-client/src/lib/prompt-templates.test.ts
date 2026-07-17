import { describe, expect, it } from "vitest";
import { AGENT_INTRO, PROMPT_TEMPLATES } from "./prompt-templates";

describe("prompt templates", () => {
  it("keeps the four requested shortcuts in workflow order", () => {
    expect(PROMPT_TEMPLATES.map((template) => template.title)).toEqual([
      "plan+generator+healer",
      "独立 Plan",
      "独立 Generator",
      "独立 Healer",
    ]);
  });

  it("keeps the start-page Agent introduction at 50 characters", () => {
    expect(Array.from(AGENT_INTRO)).toHaveLength(50);
    expect(AGENT_INTRO).toContain("网页测试智能体");
  });

  it("fills each shortcut with its complete workflow constraints", () => {
    const [full, plan, generator, healer] = PROMPT_TEMPLATES;

    expect(full.content).toContain("plan → generator → healer");
    expect(full.content).toContain("{project_name}");
    expect(plan.content).toContain("真实探索页面");
    expect(plan.content).toContain("aaaplanning");
    expect(generator.content).toContain("IMBaseFlow.openNewConversation(page)");
    expect(generator.content).toContain("不要出现 test_plan_files");
    expect(healer.content).toContain("不使用 networkidle");
    expect(healer.content).toContain("[UPDATED]");
  });
});

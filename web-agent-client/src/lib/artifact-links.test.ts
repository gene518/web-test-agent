import { describe, expect, it } from "vitest";
import {
  artifactKindHint,
  artifactPathLabel,
  isArtifactPath,
  stageSummaryBaseDir,
  stageSummarySegments,
} from "./artifact-links";

const SUMMARY = `**Generator 阶段**
- 状态：成功
- 项目目录：\`/repo/web-agent/demo\`
- 已生成脚本：共 2 个，\`test_case/login/a.spec.ts\`, \`test_case/login/b.spec.ts:20:4\`
- 下一阶段建议输入：\`healer\``;

const HEALER_SUMMARY = `**Healer 阶段**
- 状态：成功
- 项目目录：\`/repo/web-agent/demo\`
- 调试目标脚本：共 1 个，\`test_case/login/a_login.spec.ts\`
- 实际变更文件：共 1 个，\`test_case/login/a_login.spec.ts\`
- 验证运行目标：共 1 个，\`test_case/login/a_login.validation.spec.ts\`
- 脚本明细：共 1 条
- 调试对象 1：\`test_case/login/a_login.spec.ts\`，覆盖标题 \`登录流程\`
- 下一阶段建议输入：如需继续复测或追加修复，可继续提供 \`test_case/login/a_login.spec.ts\`；如需重新生成为其他用例写脚本，可回复“继续生成测试脚本”，并按需补充 \`test_plan_files\` / \`test_cases\`。`;

function pathValues(text: string): string[] {
  return stageSummarySegments(text)
    .filter((segment) => segment.type === "path")
    .map((segment) => segment.value);
}

describe("stage summary artifact links", () => {
  it.each(["Plan", "Generator", "Healer", "Scheduler"])(
    "recognizes %s stage summaries",
    (stage) => {
      const text = `**${stage} 阶段**\n- 项目目录：\`/repo/project\``;
      expect(stageSummarySegments(text)).toContainEqual({
        type: "path",
        value: "/repo/project",
        kindHint: "directory",
        label: "project",
      });
    },
  );

  it("extracts only path-like inline code from a stage summary", () => {
    expect(pathValues(SUMMARY)).toEqual([
      "/repo/web-agent/demo",
      "test_case/login/a.spec.ts",
      "test_case/login/b.spec.ts:20:4",
    ]);
    expect(stageSummaryBaseDir(SUMMARY)).toBe("/repo/web-agent/demo");
  });

  it("parses validation run targets from a production Healer summary", () => {
    expect(
      pathValues(HEALER_SUMMARY),
    ).toEqual([
      "/repo/web-agent/demo",
      "test_case/login/a_login.spec.ts",
      "test_case/login/a_login.spec.ts",
      "test_case/login/a_login.validation.spec.ts",
      "test_case/login/a_login.spec.ts",
    ]);
  });

  it("does not add links to ordinary messages or URLs", () => {
    expect(stageSummarySegments("请执行 `test_case/a.spec.ts`")).toEqual([
      { type: "text", value: "请执行 `test_case/a.spec.ts`" },
    ]);
    expect(isArtifactPath("https://example.com/a.spec.ts")).toBe(false);
    expect(isArtifactPath("generator")).toBe(false);
  });

  it("links relative artifact directories without linking stage/status words", () => {
    const text = `**Scheduler 阶段**
- 产物目录：\`test_case\`, \`test-results\`, \`reports/20260829\`
- 下一阶段建议输入：\`generator\`
- 状态标识：\`success\`
- 日志状态：\`success\``;

    expect(pathValues(text)).toEqual(["test_case", "test-results", "reports/20260829"]);
  });

  it("recognizes POSIX, Windows and filename-only paths", () => {
    expect(isArtifactPath("/repo/test_case")).toBe(true);
    expect(isArtifactPath("/Users/me/My Project/report.md")).toBe(true);
    expect(isArtifactPath("C:\\repo\\case.spec.ts")).toBe(true);
    expect(isArtifactPath("C:\\Program Files\\report folder")).toBe(true);
    expect(isArtifactPath("aaa_plan.md")).toBe(true);
    expect(isArtifactPath("npm test")).toBe(false);
    expect(isArtifactPath("npm run test/e2e")).toBe(false);
  });

  it("links a bare directory containing spaces only in an artifact field", () => {
    const text = `**Generator 阶段**
- 产物目录：\`My Project\`
- 下一阶段建议输入：\`npm test\``;

    expect(pathValues(text)).toEqual(["My Project"]);
  });

  it("links Scheduler test-scope directories including the current project", () => {
    const text = `**Scheduler 阶段**
- 状态：成功
- 项目目录：\`/repo/project\`
- 测试范围：\`test_case\`、\`.\``;

    expect(pathValues(text)).toEqual(["/repo/project", "test_case", "."]);
  });

  it("does not treat test titles or model explanation text as paths", () => {
    const text = `**Generator 阶段**
- 状态：成功
- 项目目录：\`/repo/project\`
- 脚本 1：\`test_case/login.spec.ts\`，覆盖标题 \`登录/退出流程\`，来源计划 \`test_case/login.md\`
- 用例 1：\`支付/退款流程\`，所属分组 \`账户/订单\`，3 步，计划生成 \`test_case/payment.spec.ts\`
- 说明：请查看 \`/tmp/not-an-artifact\``;

    expect(pathValues(text)).toEqual([
      "/repo/project",
      "test_case/login.spec.ts",
      "test_case/login.md",
      "test_case/payment.spec.ts",
    ]);
  });

  it("adds compact labels and file/directory hints for the UI", () => {
    expect(artifactPathLabel("/repo/My Project/report.md:20:4")).toBe("report.md");
    expect(artifactPathLabel("C:\\repo\\test_case\\")).toBe("test_case");
    expect(artifactKindHint("test_case", "产物目录")).toBe("directory");
    expect(artifactKindHint("test_case/login.spec.ts", "脚本")).toBe("file");
    expect(artifactKindHint("test_case/login.spec.ts", "测试范围")).toBe("file");
    expect(artifactKindHint("test_case/login", "测试范围")).toBe("directory");
    expect(artifactKindHint("/tmp/result", "产物")).toBe("unknown");

    const paths = stageSummarySegments(SUMMARY).filter((segment) => segment.type === "path");
    expect(paths[1]).toMatchObject({
      value: "test_case/login/a.spec.ts",
      kindHint: "file",
      label: "a.spec.ts",
    });
  });
});

export type StageSummarySegment =
  | { type: "text"; value: string }
  | { type: "path"; value: string };

const STAGE_SUMMARY_HEADING = /\*\*(?:Plan|Generator|Healer|Scheduler) 阶段\*\*/;
const INLINE_CODE = /`([^`\n]+)`/g;
const PATH_EXTENSION = /\.(?:css|html?|jsx?|json|log|md|mjs|mts|png|tsx?|txt|ya?ml)(?::\d+(?::\d+)?)?$/i;
const ARTIFACT_FIELD = /(?:验证运行目标|目录|文件|脚本|测试计划|测试范围|报告|日志|产物)/;
const BARE_DIRECTORY_NAME = /^[\p{L}\p{N}][\p{L}\p{N}._ -]*$/u;
const COMMAND_TEXT = /^(?:cargo|git|node|npm|npx|pnpm|python|uv|yarn)\s+.+$/i;
const NON_PATH_TOKEN = /^(?:error|failed|failure|generator|healer|passed|plan|retry|scheduler|success|succeeded|失败|成功|无|未知)$/i;

export function isStageSummary(text: string): boolean {
  return STAGE_SUMMARY_HEADING.test(text);
}

export function isArtifactPath(value: string): boolean {
  const candidate = value.trim();
  if (
    !candidate ||
    COMMAND_TEXT.test(candidate) ||
    /^[a-z][a-z\d+.-]*:\/\//i.test(candidate)
  ) {
    return false;
  }
  return (
    candidate.startsWith("/") ||
    candidate.startsWith("~/") ||
    candidate.startsWith("./") ||
    candidate.startsWith("../") ||
    candidate.startsWith("\\\\") ||
    /^[a-z]:[\\/]/i.test(candidate) ||
    candidate.includes("/") ||
    candidate.includes("\\") ||
    PATH_EXTENSION.test(candidate)
  );
}

export function stageSummaryBaseDir(text: string): string | undefined {
  if (!isStageSummary(text)) return undefined;
  const match = text.match(/^- 项目目录：`([^`\n]+)`/m);
  return match?.[1].trim() || undefined;
}

function isArtifactFieldPath(
  text: string,
  inlineCodeStart: number,
  value: string,
): boolean {
  const candidate = value.trim();
  if (NON_PATH_TOKEN.test(candidate) || candidate === "..") {
    return false;
  }
  const lineStart = text.lastIndexOf("\n", inlineCodeStart - 1) + 1;
  const beforeInlineCode = text.slice(lineStart, inlineCodeStart);
  const field = beforeInlineCode.match(/^-\s*([^：:\n]+)[：:]/)?.[1]?.trim() ?? "";
  if (!field || field.includes("下一阶段") || field === "说明") return false;

  const trailingLabel = beforeInlineCode.split(/[，,：:]/).pop()?.trim() ?? "";
  if (/^(?:覆盖标题|所属分组)$/.test(trailingLabel)) return false;
  if (/^用例\s*\d+$/i.test(field)) return trailingLabel === "计划生成";
  if (/^(?:脚本|调试对象)\s*\d+$/i.test(field)) {
    return trailingLabel === "" || trailingLabel === "来源计划";
  }
  if (!ARTIFACT_FIELD.test(field)) return false;

  return (
    isArtifactPath(candidate) ||
    candidate === "." ||
    BARE_DIRECTORY_NAME.test(candidate)
  );
}

export function stageSummarySegments(text: string): StageSummarySegment[] {
  if (!isStageSummary(text)) return [{ type: "text", value: text }];

  const segments: StageSummarySegment[] = [];
  let previousEnd = 0;
  for (const match of text.matchAll(INLINE_CODE)) {
    const start = match.index ?? 0;
    if (start > previousEnd) {
      segments.push({ type: "text", value: text.slice(previousEnd, start) });
    }
    const value = match[1];
    segments.push(
      isArtifactFieldPath(text, start, value)
        ? { type: "path", value }
        : { type: "text", value: match[0] },
    );
    previousEnd = start + match[0].length;
  }
  if (previousEnd < text.length) {
    segments.push({ type: "text", value: text.slice(previousEnd) });
  }
  return segments.length > 0 ? segments : [{ type: "text", value: text }];
}

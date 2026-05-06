export function truncateThreadTitle(value: string, maxLength = 32): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (normalized.length <= maxLength) {
    return normalized;
  }
  return `${normalized.slice(0, maxLength - 1)}…`;
}

export function summarizeThreadRequestTitle(
  rawText: string,
  maxLength = 32,
): string {
  const normalized = rawText.replace(/\s+/g, " ").trim();
  if (!normalized) {
    return "";
  }

  const strippedPrefix = normalized.replace(
    /^(请|帮我|麻烦|需要|想要|给我|为我)\s*/u,
    "",
  );
  const firstSentence =
    strippedPrefix.split(/[。！？!?]/u).find((segment) => segment.trim()) ??
    strippedPrefix;
  const firstClause =
    firstSentence.split(/[，,；;]/u).find((segment) => segment.trim()) ??
    firstSentence;
  const candidate =
    firstClause.trim().length >= 6 ? firstClause.trim() : firstSentence.trim();

  return truncateThreadTitle(candidate || normalized, maxLength);
}

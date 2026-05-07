const RESUME_SUBMIT_LOCK_TTL_MS = 60_000;
const KEY_PART_LIMIT = 2_000;

const activeResumeSubmitLocks = new Map<
  string,
  ReturnType<typeof setTimeout>
>();

function truncateKeyPart(value: string): string {
  if (value.length <= KEY_PART_LIMIT) {
    return value;
  }
  return `${value.slice(0, KEY_PART_LIMIT)}[truncated:${value.length - KEY_PART_LIMIT}]`;
}

function stableStringify(value: unknown): string {
  const seen = new WeakSet<object>();

  try {
    return truncateKeyPart(
      JSON.stringify(value, (_key, nestedValue) => {
        if (typeof nestedValue === "string") {
          return truncateKeyPart(nestedValue);
        }

        if (
          typeof nestedValue !== "object" ||
          nestedValue === null ||
          Array.isArray(nestedValue)
        ) {
          return nestedValue;
        }

        if (seen.has(nestedValue)) {
          return "[Circular]";
        }
        seen.add(nestedValue);

        return Object.keys(nestedValue)
          .sort()
          .reduce<Record<string, unknown>>((acc, key) => {
            acc[key] = (nestedValue as Record<string, unknown>)[key];
            return acc;
          }, {});
      }) ?? "",
    );
  } catch {
    return truncateKeyPart(String(value));
  }
}

export function buildResumeSubmitKey({
  threadId,
  interrupt,
  text,
}: {
  threadId: string | null | undefined;
  interrupt: unknown;
  text: string;
}): string {
  return [
    `thread:${threadId ?? "new"}`,
    `interrupt:${stableStringify(interrupt)}`,
    `text:${text.trim()}`,
  ].join("|");
}

export function tryLockResumeSubmit(key: string): boolean {
  if (activeResumeSubmitLocks.has(key)) {
    return false;
  }

  const timeout = setTimeout(() => {
    activeResumeSubmitLocks.delete(key);
  }, RESUME_SUBMIT_LOCK_TTL_MS);
  activeResumeSubmitLocks.set(key, timeout);
  return true;
}

export function unlockResumeSubmit(key: string) {
  const timeout = activeResumeSubmitLocks.get(key);
  if (timeout) {
    clearTimeout(timeout);
  }
  activeResumeSubmitLocks.delete(key);
}

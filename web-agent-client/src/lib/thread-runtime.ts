type RecordValue = Record<string, unknown>;

export type RunPhase =
  | "idle"
  | "submitting"
  | "queued"
  | "running"
  | "awaiting_input"
  | "cancelling"
  | "failed";

export type ActionableInterrupt = RecordValue & {
  question: string;
  missing_param: string;
};

function isRecord(value: unknown): value is RecordValue {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function findInterruptPayload(value: unknown): ActionableInterrupt | undefined {
  if (Array.isArray(value)) {
    for (const item of value) {
      const payload = findInterruptPayload(item);
      if (payload) return payload;
    }
    return undefined;
  }
  if (!isRecord(value)) return undefined;

  if (
    typeof value.question === "string" &&
    value.question.trim() &&
    typeof value.missing_param === "string" &&
    value.missing_param.trim()
  ) {
    return {
      ...value,
      question: value.question.trim(),
      missing_param: value.missing_param.trim(),
    };
  }
  return value.value === value ? undefined : findInterruptPayload(value.value);
}

export function actionableInterrupt(value: unknown): ActionableInterrupt | undefined {
  return findInterruptPayload(value);
}

export function isActiveRunPhase(phase: RunPhase | undefined): boolean {
  return phase === "submitting" ||
    phase === "queued" ||
    phase === "running" ||
    phase === "cancelling";
}

export function runPhaseLabel(phase: RunPhase | undefined): string {
  switch (phase) {
    case "submitting":
      return "正在提交任务";
    case "queued":
      return "任务排队中";
    case "running":
      return "Agent 正在执行任务";
    case "awaiting_input":
      return "等待补充信息";
    case "cancelling":
      return "正在取消任务";
    case "failed":
      return "任务执行失败";
    default:
      return "Web 自动化测试 Agent";
  }
}

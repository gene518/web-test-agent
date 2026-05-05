import { type ReactNode, useMemo, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  LoaderCircle,
  TriangleAlert,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { MarkdownText } from "./markdown-text";

const STAGE_SEQUENCE = ["plan", "generator", "healer"] as const;

type StageName = (typeof STAGE_SEQUENCE)[number];
type StageDisplayStatus = "success" | "running" | "queued" | "error" | "idle";

type StageSummaryEntry = {
  artifact_id?: string | null;
  stage?: string;
  status?: string;
  text?: string;
};

type StageArtifact = {
  artifact_id?: string;
  stage?: string;
  status?: string;
  project_dir?: string;
  message?: string;
  input_files?: string[];
  output_files?: string[];
  touched_files?: string[];
  test_plan_files?: string[];
  test_scripts?: string[];
  planned_test_case_files?: string[];
  validation_runs?: string[];
};

type WorkflowPipelineValues = {
  agent_type?: unknown;
  completed_stage_summaries?: unknown;
  latest_artifacts?: unknown;
  pending_stage_summaries?: unknown;
  pipeline_cursor?: unknown;
  requested_pipeline?: unknown;
};

type StageSection = {
  artifact?: StageArtifact;
  stage: StageName;
  status: StageDisplayStatus;
  summaryText?: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function normalizeStageName(value: unknown): StageName | undefined {
  switch (value) {
    case "plan":
    case "generator":
    case "healer":
      return value;
    default:
      return undefined;
  }
}

function normalizeStageLabel(stage: StageName): string {
  switch (stage) {
    case "plan":
      return "Plan";
    case "generator":
      return "Generator";
    case "healer":
      return "Healer";
  }
}

function normalizeStageStatus(value: unknown): StageDisplayStatus {
  switch (value) {
    case "success":
      return "success";
    case "exception":
    case "error":
    case "validation_error":
      return "error";
    default:
      return "idle";
  }
}

function normalizeStageList(value: unknown): StageName[] {
  if (!Array.isArray(value)) {
    return [];
  }

  const seen = new Set<StageName>();
  const stages: StageName[] = [];
  for (const candidate of value) {
    const stage = normalizeStageName(candidate);
    if (!stage || seen.has(stage)) {
      continue;
    }
    seen.add(stage);
    stages.push(stage);
  }
  return stages;
}

function normalizeStageSummaries(value: unknown): StageSummaryEntry[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.filter((item): item is StageSummaryEntry => isRecord(item));
}

function normalizeLatestArtifacts(value: unknown): Partial<Record<StageName, StageArtifact>> {
  if (!isRecord(value)) {
    return {};
  }

  const entries = Object.entries(value)
    .map(([stage, artifact]) => [normalizeStageName(stage), artifact] as const)
    .filter(
      (entry): entry is [StageName, StageArtifact] =>
        entry[0] != null && isRecord(entry[1]),
    );

  return Object.fromEntries(entries);
}

function dedupeStages(stages: StageName[]): StageName[] {
  const seen = new Set<StageName>();
  const deduped: StageName[] = [];
  for (const stage of stages) {
    if (seen.has(stage)) {
      continue;
    }
    seen.add(stage);
    deduped.push(stage);
  }
  return deduped;
}

function selectVisibleFileGroups(artifact?: StageArtifact): Array<[string, string[]]> {
  if (!artifact) {
    return [];
  }

  const groups: Array<[string, string[] | undefined]> = [
    ["项目目录", artifact.project_dir ? [artifact.project_dir] : undefined],
    ["输入文件", artifact.input_files],
    ["输出文件", artifact.output_files],
    ["变更文件", artifact.touched_files],
    ["计划文件", artifact.test_plan_files],
    ["目标脚本", artifact.test_scripts],
    ["规划脚本", artifact.planned_test_case_files],
    ["验证目标", artifact.validation_runs],
  ];

  return groups.filter(
    (group): group is [string, string[]] =>
      Array.isArray(group[1]) && group[1].length > 0,
  );
}

function statusMeta(status: StageDisplayStatus): {
  badgeClassName: string;
  icon: ReactNode;
  label: string;
} {
  switch (status) {
    case "success":
      return {
        badgeClassName: "bg-emerald-50 text-emerald-700 border-emerald-200",
        icon: <CheckCircle2 className="size-4" />,
        label: "成功",
      };
    case "running":
      return {
        badgeClassName: "bg-sky-50 text-sky-700 border-sky-200",
        icon: <LoaderCircle className="size-4 animate-spin" />,
        label: "进行中",
      };
    case "queued":
      return {
        badgeClassName: "bg-amber-50 text-amber-700 border-amber-200",
        icon: <Clock3 className="size-4" />,
        label: "排队中",
      };
    case "error":
      return {
        badgeClassName: "bg-rose-50 text-rose-700 border-rose-200",
        icon: <TriangleAlert className="size-4" />,
        label: "失败",
      };
    default:
      return {
        badgeClassName: "bg-slate-50 text-slate-600 border-slate-200",
        icon: <Clock3 className="size-4" />,
        label: "待执行",
      };
  }
}

function buildStageSections(
  values: WorkflowPipelineValues,
  isLoading: boolean,
): StageSection[] {
  const requestedPipeline = normalizeStageList(values.requested_pipeline);
  const pendingSummaries = normalizeStageSummaries(values.pending_stage_summaries);
  const completedSummaries = normalizeStageSummaries(
    values.completed_stage_summaries,
  );
  const visibleSummaries =
    pendingSummaries.length > 0 ? pendingSummaries : completedSummaries;
  const latestArtifacts = normalizeLatestArtifacts(values.latest_artifacts);
  const summaryByStage = new Map<StageName, StageSummaryEntry>();

  for (const summary of visibleSummaries) {
    const stage = normalizeStageName(summary.stage);
    if (!stage) {
      continue;
    }
    summaryByStage.set(stage, summary);
  }

  const stageList = dedupeStages([
    ...requestedPipeline,
    ...STAGE_SEQUENCE.filter(
      (stage) => summaryByStage.has(stage) || latestArtifacts[stage] != null,
    ),
  ]);

  if (stageList.length === 0) {
    return [];
  }

  const pipelineCursor =
    typeof values.pipeline_cursor === "number" ? values.pipeline_cursor : 0;
  const completedCount = pendingSummaries.length;
  const activeStage = isLoading
    ? normalizeStageName(values.agent_type) ??
      requestedPipeline[Math.min(pipelineCursor, requestedPipeline.length - 1)]
    : undefined;

  return stageList.map((stage) => {
    const summary = summaryByStage.get(stage);
    const artifact = latestArtifacts[stage];
    const requestedIndex = requestedPipeline.indexOf(stage);
    let status: StageDisplayStatus = "idle";

    if (summary) {
      status = normalizeStageStatus(summary.status);
    } else if (isLoading && requestedIndex !== -1) {
      if (stage === activeStage || requestedIndex === completedCount) {
        status = "running";
      } else if (requestedIndex > completedCount) {
        status = "queued";
      }
    } else if (artifact) {
      status = normalizeStageStatus(artifact.status);
    }

    return {
      artifact,
      stage,
      status,
      summaryText:
        typeof summary?.text === "string" && summary.text.trim().length > 0
          ? summary.text
          : undefined,
    };
  });
}

function FileGroup({ label, files }: { label: string; files: string[] }) {
  const visibleFiles = files.slice(0, 4);
  const hiddenCount = Math.max(0, files.length - visibleFiles.length);

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-slate-200 bg-slate-50/70 p-3">
      <div className="text-xs font-medium tracking-wide text-slate-500 uppercase">
        {label}
      </div>
      <div className="flex flex-col gap-1.5">
        {visibleFiles.map((file) => (
          <code
            key={file}
            className="rounded bg-white px-2 py-1 text-[12px] break-all text-slate-700"
          >
            {file}
          </code>
        ))}
        {hiddenCount > 0 && (
          <div className="text-xs text-slate-500">另有 {hiddenCount} 个文件未展开</div>
        )}
      </div>
    </div>
  );
}

export function StageProgressPanel({
  isLoading,
  values,
}: {
  isLoading: boolean;
  values: WorkflowPipelineValues;
}) {
  const [expandedStages, setExpandedStages] = useState<Record<string, boolean>>(
    {},
  );
  const stageSections = useMemo(
    () => buildStageSections(values, isLoading),
    [isLoading, values],
  );

  if (stageSections.length === 0) {
    return null;
  }

  return (
    <section className="mb-2 rounded-2xl border border-slate-200 bg-slate-50/70 p-4 shadow-sm">
      <div className="mb-4 flex flex-col gap-1">
        <div className="text-sm font-semibold text-slate-900">阶段执行</div>
        <div className="text-xs text-slate-500">
          主时间线已裁剪为轻量消息，详细结果按阶段折叠显示。
        </div>
      </div>

      <div className="flex flex-col gap-3">
        {stageSections.map((section) => {
          const meta = statusMeta(section.status);
          const isExpanded =
            expandedStages[section.stage] ??
            (section.status === "running" || section.status === "error");
          const fileGroups = selectVisibleFileGroups(section.artifact);
          const artifactMessage =
            typeof section.artifact?.message === "string"
              ? section.artifact.message.trim()
              : "";

          return (
            <div
              key={section.stage}
              className="overflow-hidden rounded-2xl border border-slate-200 bg-white"
            >
              <button
                type="button"
                onClick={() =>
                  setExpandedStages((prev) => ({
                    ...prev,
                    [section.stage]: !isExpanded,
                  }))
                }
                className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
              >
                <div className="flex min-w-0 items-center gap-3">
                  <div
                    className={cn(
                      "flex size-9 shrink-0 items-center justify-center rounded-full border",
                      meta.badgeClassName,
                    )}
                  >
                    {meta.icon}
                  </div>
                  <div className="min-w-0">
                    <div className="font-medium text-slate-900">
                      {normalizeStageLabel(section.stage)}
                    </div>
                    <div className="text-xs text-slate-500">
                      {section.summaryText
                        ? "已生成阶段摘要"
                        : section.status === "queued"
                          ? "等待上游阶段完成"
                          : section.status === "running"
                            ? "正在执行当前阶段"
                            : "暂无阶段摘要"}
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-3">
                  <span
                    className={cn(
                      "rounded-full border px-2.5 py-1 text-xs font-medium",
                      meta.badgeClassName,
                    )}
                  >
                    {meta.label}
                  </span>
                  {isExpanded ? (
                    <ChevronDown className="size-4 text-slate-400" />
                  ) : (
                    <ChevronRight className="size-4 text-slate-400" />
                  )}
                </div>
              </button>

              {isExpanded && (
                <div className="border-t border-slate-100 px-4 py-4">
                  <div className="flex flex-col gap-4">
                    {section.summaryText && (
                      <div className="rounded-xl border border-slate-200 bg-slate-50/80 p-4">
                        <MarkdownText>{section.summaryText}</MarkdownText>
                      </div>
                    )}

                    {!section.summaryText && artifactMessage && (
                      <div className="rounded-xl border border-slate-200 bg-slate-50/80 p-4 text-sm text-slate-700">
                        {artifactMessage}
                      </div>
                    )}

                    {fileGroups.length > 0 && (
                      <div className="grid gap-3 md:grid-cols-2">
                        {fileGroups.map(([label, files]) => (
                          <FileGroup
                            key={`${section.stage}-${label}`}
                            label={label}
                            files={files}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

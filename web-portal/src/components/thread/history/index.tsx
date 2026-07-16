import { Button } from "@/components/ui/button";
import { useThreads } from "@/providers/Thread";
import { Thread, type Message } from "@langchain/langgraph-sdk";
import { useEffect } from "react";
import { format, isThisYear, isToday } from "date-fns";

import { getContentString } from "../utils";
import { useQueryState, parseAsBoolean } from "nuqs";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { PanelRightOpen } from "lucide-react";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import {
  summarizeThreadRequestTitle,
  truncateThreadTitle,
} from "@/lib/thread-title";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function truncateText(value: string, maxLength = 32): string {
  return truncateThreadTitle(value, maxLength);
}

function basename(value: string): string {
  const trimmed = value.trim().replace(/\/+$/, "");
  if (!trimmed) {
    return "";
  }
  const parts = trimmed.split(/[\\/]/);
  return parts.at(-1) ?? trimmed;
}

function simplifyUrlLabel(rawUrl: string): string {
  const trimmed = rawUrl.trim();
  if (!trimmed) {
    return "";
  }

  const candidate = /^https?:\/\//i.test(trimmed)
    ? trimmed
    : `https://${trimmed}`;
  try {
    const parsed = new URL(candidate);
    return parsed.hostname.replace(/^www\./i, "");
  } catch {
    return trimmed.replace(/^https?:\/\//i, "").replace(/^www\./i, "");
  }
}

function normalizeStageLabel(stage: unknown): string | undefined {
  if (typeof stage !== "string") {
    return undefined;
  }

  switch (stage) {
    case "plan":
      return "测试计划";
    case "generator":
      return "脚本生成";
    case "healer":
      return "脚本调试";
    case "scheduler":
      return "定时任务";
    case "general":
      return "通用任务";
    default:
      return undefined;
  }
}

function getThreadValues(thread: Thread): Record<string, unknown> {
  return isRecord(thread.values) ? thread.values : {};
}

function getThreadMessages(thread: Thread): Record<string, unknown>[] {
  const values = getThreadValues(thread);
  const candidates = [values.display_messages, values.messages];

  for (const candidate of candidates) {
    if (Array.isArray(candidate)) {
      const messages = candidate.filter(isRecord);
      if (messages.length > 0) {
        return messages;
      }
    }
  }

  return [];
}

function getFirstHumanText(thread: Thread): string {
  const firstHumanMessage = getThreadMessages(thread).find((message) => {
    if (typeof message.type !== "string") {
      return false;
    }
    const normalizedType = message.type.toLowerCase();
    return normalizedType === "human" || normalizedType === "humanmessage";
  });

  if (!firstHumanMessage) {
    return "";
  }

  return getContentString(firstHumanMessage.content as Message["content"]);
}

function getStringField(record: Record<string, unknown>, key: string): string {
  const value = record[key];
  return typeof value === "string" ? value.trim() : "";
}

function getThreadMetadata(thread: Thread): Record<string, unknown> {
  return isRecord(thread.metadata) ? thread.metadata : {};
}

function getThreadTimestamp(thread: Thread): Date | null {
  // 历史列表以 `updated_at` 作为时间排序依据，它代表 thread 最后一次有活动的时间；
  // 拿不到更新时间时回退到创建时间，保证仍能展示时间信息。
  const raw = thread.updated_at || thread.created_at;
  if (!raw) {
    return null;
  }

  const parsed = new Date(raw);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function formatThreadTimestamp(timestamp: Date): string {
  // 今天 -> HH:mm；本年 -> MM-dd HH:mm；跨年 -> yyyy-MM-dd。
  // 这样既能让今天的记录时间一眼可见，又避免列表里塞进过长的完整时间戳。
  if (isToday(timestamp)) {
    return format(timestamp, "HH:mm");
  }
  if (isThisYear(timestamp)) {
    return format(timestamp, "MM-dd HH:mm");
  }
  return format(timestamp, "yyyy-MM-dd");
}

function getFirstString(values: unknown): string {
  if (!Array.isArray(values)) {
    return "";
  }

  const first = values.find(
    (value) => typeof value === "string" && value.trim(),
  );
  return typeof first === "string" ? first.trim() : "";
}

function buildThreadTitle(thread: Thread): {
  title: string;
  subtitle?: string;
} {
  const values = getThreadValues(thread);
  const extractedParams = isRecord(values.extracted_params)
    ? values.extracted_params
    : {};
  const requestedPipeline = Array.isArray(values.requested_pipeline)
    ? values.requested_pipeline
    : [];
  const stage =
    normalizeStageLabel(requestedPipeline[0]) ??
    normalizeStageLabel(values.agent_type) ??
    "对话";

  const urlLabel = simplifyUrlLabel(getStringField(extractedParams, "url"));
  const projectName = getStringField(extractedParams, "project_name");
  const testPlanFile = basename(
    getFirstString(extractedParams.test_plan_files),
  );
  const testScriptFile = basename(getFirstString(extractedParams.test_scripts));
  const scheduleTaskId = getStringField(extractedParams, "schedule_task_id");
  const firstHumanText = getFirstHumanText(thread);
  const metadataTitle = getStringField(
    getThreadMetadata(thread),
    "thread_title",
  );
  const summarizedHumanTitle =
    metadataTitle || summarizeThreadRequestTitle(firstHumanText);

  const primaryTarget =
    urlLabel || projectName || testScriptFile || testPlanFile || scheduleTaskId;
  const subtitleParts = [primaryTarget, stage !== "对话" ? stage : ""].filter(
    Boolean,
  );
  const stageSubtitle = subtitleParts.length
    ? truncateText(subtitleParts.join(" · "), 40)
    : undefined;

  if (summarizedHumanTitle) {
    return {
      title: summarizedHumanTitle,
      subtitle: stageSubtitle,
    };
  }

  if (primaryTarget) {
    return {
      title: truncateText(primaryTarget),
      subtitle: stage !== "对话" ? truncateText(stage, 40) : undefined,
    };
  }

  return {
    title: truncateText(thread.thread_id),
    subtitle: stage !== "对话" ? stage : undefined,
  };
}

function ThreadList({
  threads,
  onThreadClick,
}: {
  threads: Thread[];
  onThreadClick?: (threadId: string) => void;
}) {
  const [threadId, setThreadId] = useQueryState("threadId");

  return (
    <div className="flex h-full w-full flex-col items-start justify-start gap-2 overflow-y-scroll [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-gray-300 [&::-webkit-scrollbar-track]:bg-transparent">
      {threads.map((t) => {
        const { title, subtitle } = buildThreadTitle(t);
        const timestamp = getThreadTimestamp(t);
        const timeLabel = timestamp ? formatThreadTimestamp(timestamp) : "";
        return (
          <div
            key={t.thread_id}
            className="w-full px-1"
          >
            <Button
              variant="ghost"
              className="h-auto w-full items-start justify-start px-3 py-2 text-left font-normal"
              aria-current={t.thread_id === threadId ? "true" : undefined}
              onClick={(e) => {
                e.preventDefault();
                if (t.thread_id !== threadId) {
                  setThreadId(t.thread_id);
                }
                onThreadClick?.(t.thread_id);
              }}
            >
              <div className="flex w-full flex-col items-start gap-0.5">
                <div className="flex w-full items-start justify-between gap-2">
                  <p className="min-w-0 flex-1 truncate text-sm font-medium text-slate-900">
                    {title}
                  </p>
                  {timeLabel ? (
                    <span
                      className="shrink-0 text-xs text-slate-400 tabular-nums"
                      title={timestamp ? timestamp.toLocaleString() : undefined}
                    >
                      {timeLabel}
                    </span>
                  ) : null}
                </div>
                {subtitle ? (
                  <p className="w-full truncate text-xs text-slate-500">
                    {subtitle}
                  </p>
                ) : null}
              </div>
            </Button>
          </div>
        );
      })}
    </div>
  );
}

function ThreadHistoryLoading() {
  return (
    <div className="flex h-full w-full flex-col items-start justify-start gap-2 overflow-y-scroll [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-gray-300 [&::-webkit-scrollbar-track]:bg-transparent">
      {Array.from({ length: 30 }).map((_, i) => (
        <Skeleton
          key={`skeleton-${i}`}
          className="h-10 w-[280px]"
        />
      ))}
    </div>
  );
}

export default function ThreadHistory() {
  const isLargeScreen = useMediaQuery("(min-width: 1024px)");
  const [chatHistoryOpen, setChatHistoryOpen] = useQueryState(
    "chatHistoryOpen",
    parseAsBoolean.withDefault(false),
  );

  const { getThreads, threads, setThreads, threadsLoading, setThreadsLoading } =
    useThreads();

  useEffect(() => {
    if (typeof window === "undefined") return;
    setThreadsLoading(true);
    getThreads()
      .then(setThreads)
      .catch(console.error)
      .finally(() => setThreadsLoading(false));
  }, [getThreads, setThreads, setThreadsLoading]);

  return (
    <>
      {chatHistoryOpen && isLargeScreen ? (
        <div className="shadow-inner-right fixed inset-y-0 left-0 z-30 hidden w-[300px] flex-col items-start justify-start gap-6 overflow-hidden border-r border-[#cbd5e1] bg-[#ffffff] lg:flex">
          <div className="flex w-full items-center justify-between px-4 pt-1.5">
            <Button
              type="button"
              size="icon"
              className="hover:bg-gray-100"
              variant="ghost"
              aria-label="关闭对话历史"
              onClick={() => setChatHistoryOpen(false)}
            >
              <PanelRightOpen className="size-5" />
            </Button>
            <h1 className="text-xl font-semibold tracking-tight">对话历史</h1>
          </div>
          {threadsLoading ? (
            <ThreadHistoryLoading />
          ) : (
            <ThreadList threads={threads} />
          )}
        </div>
      ) : null}
      <div className="lg:hidden">
        <Sheet
          open={!!chatHistoryOpen && !isLargeScreen}
          onOpenChange={(open) => {
            if (isLargeScreen) return;
            setChatHistoryOpen(open);
          }}
        >
          <SheetContent
            side="left"
            className="flex lg:hidden"
          >
            <SheetHeader>
              <SheetTitle>对话历史</SheetTitle>
            </SheetHeader>
            {threadsLoading ? (
              <ThreadHistoryLoading />
            ) : (
              <ThreadList
                threads={threads}
                onThreadClick={() => setChatHistoryOpen(false)}
              />
            )}
          </SheetContent>
        </Sheet>
      </div>
    </>
  );
}

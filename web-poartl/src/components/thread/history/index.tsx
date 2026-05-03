import { Button } from "@/components/ui/button";
import { useThreads } from "@/providers/Thread";
import { Thread, type Message } from "@langchain/langgraph-sdk";
import { useEffect } from "react";

import { getContentString } from "../utils";
import { useQueryState, parseAsBoolean } from "nuqs";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { PanelRightOpen, PanelRightClose } from "lucide-react";
import { useMediaQuery } from "@/hooks/useMediaQuery";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function truncateText(value: string, maxLength = 32): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (normalized.length <= maxLength) {
    return normalized;
  }
  return `${normalized.slice(0, maxLength - 1)}…`;
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

  const candidate = /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
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
      return candidate.filter(isRecord);
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

function getFirstString(values: unknown): string {
  if (!Array.isArray(values)) {
    return "";
  }

  const first = values.find((value) => typeof value === "string" && value.trim());
  return typeof first === "string" ? first.trim() : "";
}

function buildThreadTitle(thread: Thread): { title: string; subtitle?: string } {
  const values = getThreadValues(thread);
  const extractedParams = isRecord(values.extracted_params) ? values.extracted_params : {};
  const requestedPipeline = Array.isArray(values.requested_pipeline)
    ? values.requested_pipeline
    : [];
  const stage =
    normalizeStageLabel(requestedPipeline[0]) ??
    normalizeStageLabel(values.agent_type) ??
    "对话";

  const urlLabel = simplifyUrlLabel(getStringField(extractedParams, "url"));
  const projectName = getStringField(extractedParams, "project_name");
  const testPlanFile = basename(getFirstString(extractedParams.test_plan_files));
  const testScriptFile = basename(getFirstString(extractedParams.test_scripts));
  const scheduleTaskId = getStringField(extractedParams, "schedule_task_id");
  const firstHumanText = getFirstHumanText(thread);

  const primaryTarget =
    urlLabel ||
    projectName ||
    testScriptFile ||
    testPlanFile ||
    scheduleTaskId;

  if (primaryTarget) {
    return {
      title: truncateText(`${primaryTarget} · ${stage}`),
      subtitle:
        firstHumanText && firstHumanText !== primaryTarget
          ? truncateText(firstHumanText, 40)
          : undefined,
    };
  }

  if (firstHumanText) {
    return {
      title: truncateText(firstHumanText),
      subtitle: stage !== "对话" ? stage : undefined,
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
        return (
          <div
            key={t.thread_id}
            className="w-full px-1"
          >
            <Button
              variant="ghost"
              className="h-auto w-full items-start justify-start px-3 py-2 text-left font-normal"
              onClick={(e) => {
                e.preventDefault();
                onThreadClick?.(t.thread_id);
                if (t.thread_id === threadId) return;
                setThreadId(t.thread_id);
              }}
            >
              <div className="flex w-full flex-col items-start gap-0.5">
                <p className="w-full truncate text-sm font-medium text-slate-900">
                  {title}
                </p>
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
      <div className="shadow-inner-right hidden h-screen w-[300px] shrink-0 flex-col items-start justify-start gap-6 border-r-[1px] border-slate-300 lg:flex">
        <div className="flex w-full items-center justify-between px-4 pt-1.5">
          <Button
            className="hover:bg-gray-100"
            variant="ghost"
            onClick={() => setChatHistoryOpen((p) => !p)}
          >
            {chatHistoryOpen ? (
              <PanelRightOpen className="size-5" />
            ) : (
              <PanelRightClose className="size-5" />
            )}
          </Button>
          <h1 className="text-xl font-semibold tracking-tight">对话历史</h1>
        </div>
        {threadsLoading ? (
          <ThreadHistoryLoading />
        ) : (
          <ThreadList threads={threads} />
        )}
      </div>
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
            <ThreadList
              threads={threads}
              onThreadClick={() => setChatHistoryOpen((o) => !o)}
            />
          </SheetContent>
        </Sheet>
      </div>
    </>
  );
}

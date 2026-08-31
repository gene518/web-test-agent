import { useEffect, useRef, useState } from "react";
import type { Client } from "@langchain/langgraph-sdk";
import {
  threadFirstHumanText,
  threadModelTitle,
  type ThreadSummary,
} from "../lib/message-utils";
import { THREAD_TITLE_ASSISTANT_ID } from "../lib/types";

type BackfillResult = { thread_title?: unknown };

type UseThreadTitleBackfillOptions = {
  client: Client;
  threads: ThreadSummary[];
  enabled: boolean;
  foregroundActive: boolean;
  onUpdated: (threadId: string, metadata: Record<string, unknown>) => void;
};

export function normalizedBackfillTitle(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const normalized = value.replace(/\s+/g, " ").trim();
  return normalized ? normalized.slice(0, 32) : undefined;
}

export function threadNeedsTitleBackfill(thread: ThreadSummary): boolean {
  return !threadModelTitle(thread) && Boolean(threadFirstHumanText(thread));
}

export function useThreadTitleBackfill({
  client,
  threads,
  enabled,
  foregroundActive,
  onUpdated,
}: UseThreadTitleBackfillOptions): void {
  const attemptedRef = useRef(new Set<string>());
  const inFlightRef = useRef<{
    threadId: string;
    controller: AbortController;
  } | undefined>(undefined);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    if (enabled && !foregroundActive) return;
    const active = inFlightRef.current;
    if (!active) return;
    attemptedRef.current.delete(active.threadId);
    active.controller.abort();
  }, [enabled, foregroundActive]);

  useEffect(() => () => {
    const active = inFlightRef.current;
    if (!active) return;
    attemptedRef.current.delete(active.threadId);
    active.controller.abort();
  }, [client]);

  useEffect(() => {
    if (!enabled || foregroundActive || inFlightRef.current) return;
    const thread = threads.find(
      (candidate) =>
        !attemptedRef.current.has(candidate.thread_id) &&
        threadNeedsTitleBackfill(candidate),
    );
    if (!thread) return;

    const sourceText = threadFirstHumanText(thread);
    if (!sourceText) return;
    const controller = new AbortController();
    inFlightRef.current = { threadId: thread.thread_id, controller };
    attemptedRef.current.add(thread.thread_id);

    const backfill = async () => {
      try {
        const result = await client.runs.wait(null, THREAD_TITLE_ASSISTANT_ID, {
          input: { source_text: sourceText.slice(0, 6_000) },
          signal: controller.signal,
        }) as BackfillResult;
        const title = normalizedBackfillTitle(result?.thread_title);
        if (!title || controller.signal.aborted) return;
        const metadata = {
          ...thread.metadata,
          thread_title: title,
          thread_title_source: "model-v1",
        };
        await client.threads.update(thread.thread_id, {
          metadata,
          returnMinimal: true,
          signal: controller.signal,
        });
        if (!controller.signal.aborted) onUpdated(thread.thread_id, metadata);
      } catch (error) {
        if (error instanceof Error && error.name === "AbortError") {
          attemptedRef.current.delete(thread.thread_id);
        }
      } finally {
        if (inFlightRef.current?.threadId === thread.thread_id) {
          inFlightRef.current = undefined;
        }
        setRevision((current) => current + 1);
      }
    };
    void backfill();
  }, [client, enabled, foregroundActive, onUpdated, revision, threads]);
}

import { useCallback, useEffect, useRef, useState } from "react";
import type { Client } from "@langchain/langgraph-sdk";
import { errorMessage } from "../lib/errors";
import type { ThreadSummary } from "../lib/message-utils";
import { ASSISTANT_ID } from "../lib/types";

const THREAD_PAGE_SIZE = 50;

type UseThreadHistoryOptions = {
  client: Client;
  enabled: boolean;
  onError: (message: string) => void;
};

export function useThreadHistory({
  client,
  enabled,
  onError,
}: UseThreadHistoryOptions) {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const nextOffsetRef = useRef(0);
  const requestRevisionRef = useRef(0);
  const requestInFlightRef = useRef(false);

  const fetchPage = useCallback(
    async (offset: number, append: boolean, pageSize = THREAD_PAGE_SIZE) => {
      if (!enabled || requestInFlightRef.current) return;
      requestInFlightRef.current = true;
      const requestRevision = ++requestRevisionRef.current;
      const showLoading = append || nextOffsetRef.current === 0;
      if (showLoading) setLoading(true);
      try {
        const result = await client.threads.search({
          limit: pageSize + 1,
          offset,
          sortBy: "updated_at",
          sortOrder: "desc",
          select: ["thread_id", "created_at", "updated_at", "state_updated_at", "metadata", "status"],
          extract: {
            thread_title: "values.thread_title",
            first_message: "values.messages[0]",
          },
        });
        if (requestRevision !== requestRevisionRef.current) return;
        const rawPage = (result as ThreadSummary[]).slice(0, pageSize);
        const filtered = rawPage
          .filter((thread) => {
            const graphId = thread.metadata?.graph_id ?? thread.metadata?.assistant_id;
            return !graphId || graphId === ASSISTANT_ID;
          })
          .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at));
        setThreads((current) => {
          const combined = append ? [...current, ...filtered] : filtered;
          return [...new Map(combined.map((thread) => [thread.thread_id, thread])).values()].sort(
            (a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at),
          );
        });
        nextOffsetRef.current = append ? offset + rawPage.length : rawPage.length;
        setHasMore(result.length > pageSize);
      } catch (error) {
        if (requestRevision === requestRevisionRef.current) {
          onError(`读取历史对话失败：${errorMessage(error)}`);
        }
      } finally {
        if (requestRevision === requestRevisionRef.current) {
          requestInFlightRef.current = false;
          if (showLoading) setLoading(false);
        }
      }
    },
    [client, enabled, onError],
  );

  const refresh = useCallback(
    () => fetchPage(0, false, Math.max(nextOffsetRef.current, THREAD_PAGE_SIZE)),
    [fetchPage],
  );
  const loadMore = useCallback(
    () => fetchPage(nextOffsetRef.current, true),
    [fetchPage],
  );
  const patchThread = useCallback(
    (threadId: string, patch: Partial<ThreadSummary>) => {
      setThreads((current) => {
        const index = current.findIndex((thread) => thread.thread_id === threadId);
        if (index < 0) return current;
        const next = [...current];
        next[index] = { ...next[index], ...patch };
        return next;
      });
    },
    [],
  );
  const upsertThread = useCallback((thread: ThreadSummary) => {
    setThreads((current) => {
      const next = [thread, ...current.filter((item) => item.thread_id !== thread.thread_id)];
      return next.sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at));
    });
  }, []);

  useEffect(() => {
    requestRevisionRef.current += 1;
    requestInFlightRef.current = false;
    nextOffsetRef.current = 0;
    setThreads([]);
    setHasMore(false);
    setLoading(false);
    if (enabled) void refresh();
    return () => {
      requestRevisionRef.current += 1;
      requestInFlightRef.current = false;
    };
  }, [client, enabled, refresh]);

  useEffect(() => {
    if (!enabled) return;
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    const timer = window.setInterval(refreshWhenVisible, 5_000);
    window.addEventListener("focus", refreshWhenVisible);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refreshWhenVisible);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [enabled, refresh]);

  return {
    threads,
    loading,
    hasMore,
    refresh,
    loadMore,
    patchThread,
    upsertThread,
  };
}

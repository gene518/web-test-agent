import { useCallback, useEffect, useState } from "react";
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
  const [nextOffset, setNextOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);

  const fetchPage = useCallback(
    async (offset: number, append: boolean) => {
      if (!enabled) return;
      setLoading(true);
      try {
        const result = await client.threads.search({
          limit: THREAD_PAGE_SIZE,
          offset,
          sortBy: "updated_at",
          sortOrder: "desc",
          select: ["thread_id", "created_at", "updated_at", "state_updated_at", "metadata", "status"],
          extract: {
            thread_title: "values.thread_title",
            first_message: "values.messages[0]",
          },
        });
        const filtered = (result as ThreadSummary[])
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
        setNextOffset(offset + result.length);
        setHasMore(result.length === THREAD_PAGE_SIZE);
      } catch (error) {
        onError(`读取历史对话失败：${errorMessage(error)}`);
      } finally {
        setLoading(false);
      }
    },
    [client, enabled, onError],
  );

  const refresh = useCallback(() => fetchPage(0, false), [fetchPage]);
  const loadMore = useCallback(() => fetchPage(nextOffset, true), [fetchPage, nextOffset]);
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
    if (enabled) void refresh();
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

import { validate } from "uuid";
import { getApiKey } from "@/lib/api-key";
import { Thread } from "@langchain/langgraph-sdk";
import { useQueryState } from "nuqs";
import {
  createContext,
  useContext,
  ReactNode,
  useCallback,
  useState,
  Dispatch,
  SetStateAction,
} from "react";
import { createClient } from "./client";

const DEFAULT_API_URL = "http://127.0.0.1:2024";
const DEFAULT_ASSISTANT_ID = "web-autotest-agent";

interface ThreadContextType {
  getThreads: () => Promise<Thread[]>;
  threads: Thread[];
  setThreads: Dispatch<SetStateAction<Thread[]>>;
  threadsLoading: boolean;
  setThreadsLoading: Dispatch<SetStateAction<boolean>>;
}

const ThreadContext = createContext<ThreadContextType | undefined>(undefined);

function getThreadSearchMetadata(
  assistantId: string,
): { graph_id: string } | { assistant_id: string } {
  if (validate(assistantId)) {
    return { assistant_id: assistantId };
  } else {
    return { graph_id: assistantId };
  }
}

function isThreadForCurrentAssistant(
  thread: Thread,
  expectedMetadata: { graph_id: string } | { assistant_id: string },
): boolean {
  const metadata =
    thread.metadata &&
    typeof thread.metadata === "object" &&
    !Array.isArray(thread.metadata)
      ? (thread.metadata as Record<string, unknown>)
      : {};
  const hasGraphMarker =
    typeof metadata.graph_id === "string" ||
    typeof metadata.assistant_id === "string";

  // 旧 thread 创建时没有写入 graph 元数据，不能因为缺少标记就从历史列表里消失。
  if (!hasGraphMarker) {
    return true;
  }

  return Object.entries(expectedMetadata).every(
    ([key, value]) => metadata[key] === value,
  );
}

export function ThreadProvider({ children }: { children: ReactNode }) {
  const envApiUrl: string | undefined = process.env.NEXT_PUBLIC_API_URL;
  const envAssistantId: string | undefined =
    process.env.NEXT_PUBLIC_ASSISTANT_ID;
  const envAuthScheme: string | undefined = process.env.NEXT_PUBLIC_AUTH_SCHEME;

  const [apiUrl] = useQueryState("apiUrl", {
    defaultValue: envApiUrl || DEFAULT_API_URL,
  });
  const [assistantId] = useQueryState("assistantId", {
    defaultValue: envAssistantId || DEFAULT_ASSISTANT_ID,
  });
  const [authScheme] = useQueryState("authScheme", {
    defaultValue: envAuthScheme || "",
  });
  const [threads, setThreads] = useState<Thread[]>([]);
  const [threadsLoading, setThreadsLoading] = useState(false);

  const getThreads = useCallback(async (): Promise<Thread[]> => {
    const resolvedAssistantId =
      assistantId || envAssistantId || DEFAULT_ASSISTANT_ID;
    if (!apiUrl || !resolvedAssistantId) return [];
    const client = createClient(
      apiUrl,
      getApiKey() ?? undefined,
      authScheme || undefined,
    );

    const expectedMetadata = getThreadSearchMetadata(resolvedAssistantId);
    const threads = await client.threads.search({
      limit: 100,
      sortBy: "updated_at",
      sortOrder: "desc",
      select: [
        "thread_id",
        "created_at",
        "updated_at",
        "metadata",
        "status",
        "values",
      ],
    });

    // 这里再做一次前端保底排序：
    // LangGraph server 不同版本对 `sortBy`/`sortOrder` 参数的支持并不一致，
    // 某些旧版本会直接忽略排序参数，导致历史列表顺序不可控。
    // 为了保证 UI 稳定按最近活跃倒序展示，统一在前端按 `updated_at` 再排一次。
    return threads
      .filter((thread) => isThreadForCurrentAssistant(thread, expectedMetadata))
      .sort((a, b) => {
        const aTime = Date.parse(a.updated_at || a.created_at || "");
        const bTime = Date.parse(b.updated_at || b.created_at || "");
        // Date.parse 解析失败会得到 NaN，NaN 参与比较结果不稳定，
        // 这里把无法解析的时间视为最小值，避免破坏整体倒序。
        const safeA = Number.isNaN(aTime) ? -Infinity : aTime;
        const safeB = Number.isNaN(bTime) ? -Infinity : bTime;
        return safeB - safeA;
      });
  }, [apiUrl, assistantId, authScheme, envAssistantId]);

  const value = {
    getThreads,
    threads,
    setThreads,
    threadsLoading,
    setThreadsLoading,
  };

  return (
    <ThreadContext.Provider value={value}>{children}</ThreadContext.Provider>
  );
}

export function useThreads() {
  const context = useContext(ThreadContext);
  if (context === undefined) {
    throw new Error("useThreads 必须在 ThreadProvider 内使用");
  }
  return context;
}

import React, {
  createContext,
  ReactNode,
  useState,
  useEffect,
  useRef,
} from "react";
import { useStream } from "@langchain/langgraph-sdk/react";
import { type Message } from "@langchain/langgraph-sdk";
import {
  uiMessageReducer,
  isUIMessage,
  isRemoveUIMessage,
  type UIMessage,
  type RemoveUIMessage,
} from "@langchain/langgraph-sdk/react-ui";
import { useQueryState } from "nuqs";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { LangGraphLogoSVG } from "@/components/icons/langgraph";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { ArrowRight } from "lucide-react";
import { PasswordInput } from "@/components/ui/password-input";
import { getApiKey } from "@/lib/api-key";
import { useThreads } from "./Thread";
import { toast } from "sonner";
import { mergeVisibleMessages } from "@/components/thread/message-utils";

export type StateType = {
  messages: Message[];
  display_messages?: Message[];
  ui?: UIMessage[];
  __interrupt__?: unknown[];
  agent_type?: string | null;
  next_action?: string;
  requested_pipeline?: string[];
  pipeline_cursor?: number;
  final_summary?: string;
  extracted_params?: Record<string, unknown>;
  stage_result?: Record<string, unknown>;
  latest_artifacts?: Record<string, Record<string, unknown>>;
  pending_stage_summaries?: Record<string, unknown>[];
  completed_stage_summaries?: Record<string, unknown>[];
};

type DisplayMessagesCustomEvent = {
  type: "display_messages";
  messages: unknown[];
};

const useTypedStream = useStream<
  StateType,
  {
    UpdateType: {
      messages?: Message[] | Message | string;
      display_messages?: Message[] | Message | string;
      ui?: (UIMessage | RemoveUIMessage)[] | UIMessage | RemoveUIMessage;
      context?: Record<string, unknown>;
    };
    CustomEventType: UIMessage | RemoveUIMessage | DisplayMessagesCustomEvent;
  }
>;

export type StreamContextType = ReturnType<typeof useTypedStream>;
const StreamContext = createContext<StreamContextType | undefined>(undefined);

function isDisplayMessagesEvent(
  event: unknown,
): event is DisplayMessagesCustomEvent {
  return (
    typeof event === "object" &&
    event !== null &&
    "type" in event &&
    (event as { type?: unknown }).type === "display_messages" &&
    Array.isArray((event as { messages?: unknown }).messages)
  );
}

async function sleep(ms = 4000) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function checkGraphStatus(
  apiUrl: string,
  apiKey: string | null,
  authScheme?: string,
): Promise<boolean> {
  try {
    const headers = new Headers();
    if (apiKey) headers.set("X-Api-Key", apiKey);
    if (authScheme) headers.set("X-Auth-Scheme", authScheme);

    const res = await fetch(`${apiUrl}/info`, {
      headers,
    });

    return res.ok;
  } catch (e) {
    console.error(e);
    return false;
  }
}

function isThreadNotFoundErrorMessage(message: string | null | undefined): boolean {
  if (!message) {
    return false;
  }

  return (
    /Thread with ID .+ not found/i.test(message) ||
    /HTTP 404:.*Thread with ID .+ not found/i.test(message)
  );
}

const StreamSession = ({
  children,
  apiKey,
  apiUrl,
  assistantId,
  authScheme,
}: {
  children: ReactNode;
  apiKey: string | null;
  apiUrl: string;
  assistantId: string;
  authScheme?: string;
}) => {
  const [threadId, setThreadId] = useQueryState("threadId");
  const { getThreads, setThreads } = useThreads();
  const pendingDisplayMessagesRef = useRef<unknown[]>([]);
  const displayMessagesFrameRef = useRef<number | null>(null);
  const initialThreadIdRef = useRef(threadId ?? null);
  const locallyCreatedThreadIdsRef = useRef<Set<string>>(new Set());
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [initialThreadValidated, setInitialThreadValidated] = useState(
    () => !threadId,
  );

  const scheduleDisplayMessagesFlush = (
    messages: unknown[],
    mutate: (update: Partial<StateType> | ((prev: StateType) => Partial<StateType>)) => void,
  ) => {
    pendingDisplayMessagesRef.current.push(...messages);
    if (displayMessagesFrameRef.current != null) {
      return;
    }

    const flush = () => {
      displayMessagesFrameRef.current = null;
      const batch = pendingDisplayMessagesRef.current.splice(0);
      if (!batch.length) {
        return;
      }

      mutate((prev) => ({
        ...prev,
        display_messages: mergeVisibleMessages(
          prev.display_messages ?? prev.messages,
          batch,
        ),
      }));
    };

    displayMessagesFrameRef.current =
      typeof window !== "undefined" && window.requestAnimationFrame
        ? window.requestAnimationFrame(flush)
        : window.setTimeout(flush, 16);
  };

  const streamValue = useTypedStream({
    apiUrl,
    apiKey: apiKey ?? undefined,
    assistantId,
    ...(authScheme && {
      defaultHeaders: {
        "X-Auth-Scheme": authScheme,
      },
    }),
    threadId: activeThreadId,
    messagesKey: "display_messages",
    fetchStateHistory: true,
    onCustomEvent: (event, options) => {
      if (isUIMessage(event) || isRemoveUIMessage(event)) {
        options.mutate((prev) => {
          const ui = uiMessageReducer(prev.ui ?? [], event);
          return { ...prev, ui };
        });
        return;
      }

      if (isDisplayMessagesEvent(event)) {
        scheduleDisplayMessagesFlush(event.messages, options.mutate);
      }
    },
    onThreadId: (id) => {
      locallyCreatedThreadIdsRef.current.add(id);
      setActiveThreadId(id);
      setInitialThreadValidated(true);
      setThreadId(id);
      // thread ID 变化后重新拉取 thread 列表。
      // 延迟几秒后再拉取，确保新创建的 thread 已可见。
      sleep().then(() =>
        getThreads()
          .then((threads) => {
            setThreads(threads);
            if (threads.some((thread) => thread.thread_id === id)) {
              locallyCreatedThreadIdsRef.current.delete(id);
            }
          })
          .catch(console.error),
      );
    },
  });

  useEffect(() => {
    const candidate = initialThreadIdRef.current;
    if (!candidate) {
      setInitialThreadValidated(true);
      return;
    }

    let cancelled = false;
    getThreads()
      .then((threads) => {
        if (cancelled) {
          return;
        }

        setThreads(threads);
        if (threads.some((thread) => thread.thread_id === candidate)) {
          setActiveThreadId(candidate);
        } else {
          setActiveThreadId(null);
          setThreadId(null);
          toast.error("历史 thread 不存在，已切换到新对话。", {
            description: (
              <p>
                <strong>threadId：</strong> <code>{candidate}</code>
              </p>
            ),
            richColors: true,
            closeButton: true,
          });
        }
        setInitialThreadValidated(true);
      })
      .catch((error) => {
        console.error(error);
        if (cancelled) {
          return;
        }
        setActiveThreadId(candidate);
        setInitialThreadValidated(true);
      });

    return () => {
      cancelled = true;
    };
  }, [getThreads, setThreadId, setThreads]);

  useEffect(() => {
    if (!initialThreadValidated) {
      return;
    }

    if (threadId && locallyCreatedThreadIdsRef.current.has(threadId)) {
      setActiveThreadId(threadId);
      return;
    }

    setActiveThreadId(threadId ?? null);
  }, [initialThreadValidated, threadId]);

  useEffect(() => {
    checkGraphStatus(apiUrl, apiKey, authScheme).then((ok) => {
      if (!ok) {
        toast.error("无法连接 LangGraph server", {
          description: () => (
            <p>
              请确认 graph 已在 <code>{apiUrl}</code> 运行，并且 API key
              配置正确（连接已部署 graph 时需要）。
            </p>
          ),
          duration: 10000,
          richColors: true,
          closeButton: true,
        });
      }
    });
  }, [apiKey, apiUrl, authScheme]);

  useEffect(() => {
    const message = (streamValue.error as { message?: string } | undefined)?.message;
    if (!isThreadNotFoundErrorMessage(message) || !threadId) {
      return;
    }

    setActiveThreadId(null);
    setThreadId(null);
    toast.error("当前 thread 已失效，已切换到新对话。", {
      description: (
        <p>
          <strong>错误：</strong> <code>{message}</code>
        </p>
      ),
      richColors: true,
      closeButton: true,
    });
  }, [streamValue.error, threadId, setThreadId]);

  useEffect(() => {
    return () => {
      if (displayMessagesFrameRef.current == null) {
        return;
      }
      if (typeof window !== "undefined" && window.cancelAnimationFrame) {
        window.cancelAnimationFrame(displayMessagesFrameRef.current);
      } else {
        window.clearTimeout(displayMessagesFrameRef.current);
      }
    };
  }, []);

  return (
    <StreamContext.Provider value={streamValue}>
      {children}
    </StreamContext.Provider>
  );
};

// 本地默认值指向本仓库的 LangGraph dev server 和 graph id。
const DEFAULT_API_URL = "http://127.0.0.1:2024";
const DEFAULT_ASSISTANT_ID = "master";
const AGENT_BUILDER_AUTH_SCHEME = "langsmith-api-key";

export const StreamProvider: React.FC<{ children: ReactNode }> = ({
  children,
}) => {
  // 读取环境变量。
  const envApiUrl: string | undefined = process.env.NEXT_PUBLIC_API_URL;
  const envAssistantId: string | undefined =
    process.env.NEXT_PUBLIC_ASSISTANT_ID;
  const envAuthScheme: string | undefined = process.env.NEXT_PUBLIC_AUTH_SCHEME;

  // URL 参数优先，环境变量作为兜底。
  const [apiUrl, setApiUrl] = useQueryState("apiUrl", {
    defaultValue: envApiUrl || DEFAULT_API_URL,
  });
  const [assistantId, setAssistantId] = useQueryState("assistantId", {
    defaultValue: envAssistantId || DEFAULT_ASSISTANT_ID,
  });
  const [authScheme, setAuthScheme] = useQueryState("authScheme", {
    defaultValue: envAuthScheme || "",
  });
  const [isAgentBuilder, setIsAgentBuilder] = useState(
    () =>
      (authScheme || envAuthScheme || "").toLowerCase() ===
      AGENT_BUILDER_AUTH_SCHEME,
  );

  // API key 优先读取 localStorage，环境变量作为兜底。
  const [apiKey, _setApiKey] = useState(() => {
    const storedKey = getApiKey();
    return storedKey || "";
  });

  const setApiKey = (key: string) => {
    window.localStorage.setItem("lg:chat:apiKey", key);
    _setApiKey(key);
  };

  // 计算最终配置值：URL 参数优先，其次环境变量，最后使用本地默认值。
  const finalApiUrl = apiUrl || envApiUrl || DEFAULT_API_URL;
  const finalAssistantId =
    assistantId || envAssistantId || DEFAULT_ASSISTANT_ID;
  const finalAuthScheme = authScheme || envAuthScheme || "";

  // 缺少 API URL 或 assistant ID 时展示配置表单。
  if (!finalApiUrl || !finalAssistantId) {
    return (
      <div className="flex min-h-screen w-full items-center justify-center p-4">
        <div className="animate-in fade-in-0 zoom-in-95 bg-background flex max-w-3xl flex-col rounded-lg border shadow-lg">
          <div className="mt-14 flex flex-col gap-2 border-b p-6">
            <div className="flex flex-col items-start gap-2">
              <LangGraphLogoSVG className="h-7" />
              <h1 className="text-xl font-semibold tracking-tight">
                Agent Chat
              </h1>
            </div>
            <p className="text-muted-foreground">
              欢迎使用 Agent Chat。开始前，请输入部署地址和 assistant / graph
              ID。
            </p>
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();

              const form = e.target as HTMLFormElement;
              const formData = new FormData(form);
              const apiUrl = formData.get("apiUrl") as string;
              const assistantId = formData.get("assistantId") as string;
              const apiKey = formData.get("apiKey") as string;

              setApiUrl(apiUrl);
              setApiKey(apiKey);
              setAssistantId(assistantId);
              setAuthScheme(isAgentBuilder ? AGENT_BUILDER_AUTH_SCHEME : "");

              form.reset();
            }}
            className="bg-muted/50 flex flex-col gap-6 p-6"
          >
            <div className="flex flex-col gap-2">
              <Label htmlFor="apiUrl">
                部署地址<span className="text-rose-500">*</span>
              </Label>
              <p className="text-muted-foreground text-sm">
                这是 LangGraph 部署地址，可以是本地地址或生产部署地址。
              </p>
              <Input
                id="apiUrl"
                name="apiUrl"
                className="bg-background"
                defaultValue={apiUrl || DEFAULT_API_URL}
                required
              />
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="assistantId">
                Assistant / Graph ID<span className="text-rose-500">*</span>
              </Label>
              <p className="text-muted-foreground text-sm">
                这是用于拉取 thread 并执行操作的 graph 名称或 assistant ID。
              </p>
              <Input
                id="assistantId"
                name="assistantId"
                className="bg-background"
                defaultValue={assistantId || DEFAULT_ASSISTANT_ID}
                required
              />
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="apiKey">LangSmith API Key</Label>
              <p className="text-muted-foreground text-sm">
                使用本地 LangGraph server 时<strong>不需要</strong>
                填写。该值会保存在浏览器 localStorage 中，仅用于认证发送到
                LangGraph server 的请求。
              </p>
              <PasswordInput
                id="apiKey"
                name="apiKey"
                defaultValue={apiKey ?? ""}
                className="bg-background"
                placeholder="lsv2_pt_..."
              />
            </div>

            <div className="flex flex-col gap-3">
              <div className="flex items-center justify-between gap-4">
                <div className="flex flex-col gap-1">
                  <Label htmlFor="agentBuilderEnabled">
                    使用 Agent Builder 构建
                  </Label>
                  <p className="text-muted-foreground text-sm">
                    连接 Agent Builder 部署时启用。
                  </p>
                </div>
                <Switch
                  id="agentBuilderEnabled"
                  checked={isAgentBuilder}
                  onCheckedChange={setIsAgentBuilder}
                />
              </div>
            </div>

            <div className="mt-2 flex justify-end">
              <Button
                type="submit"
                size="lg"
              >
                继续
                <ArrowRight className="size-5" />
              </Button>
            </div>
          </form>
        </div>
      </div>
    );
  }

  return (
    <StreamSession
      apiKey={apiKey}
      apiUrl={finalApiUrl}
      assistantId={finalAssistantId}
      authScheme={finalAuthScheme || undefined}
    >
      {children}
    </StreamSession>
  );
};

export default StreamContext;

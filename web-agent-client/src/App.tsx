import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { isTauri } from "@tauri-apps/api/core";
import { useStream } from "@langchain/langgraph-sdk/react";
import type { Message } from "@langchain/langgraph-sdk";
import {
  AlertTriangle,
  CircleStop,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  X,
} from "lucide-react";
import "./App.css";
import {
  LOG_THEME_STORAGE_KEY,
  LogModal,
  SettingsModal,
  loadLogTheme,
  type LogTheme,
} from "./components/AppModals";
import { BackendBadge, BackendBanner } from "./components/BackendStatus";
import { Composer } from "./components/Composer";
import { MessageTimeline } from "./components/MessageTimeline";
import { Sidebar } from "./components/Sidebar";
import { useThreadHistory } from "./hooks/use-thread-history";
import {
  chooseProjectRoot,
  createAgentClient,
  getBackendStatus,
  loadClientConfig,
  readBackendLog,
  revealPathInFileManager,
  restartBackend,
  saveClientConfig,
} from "./lib/backend";
import {
  historicalConversationMessages,
  mergeMessages,
  threadTitle,
} from "./lib/message-utils";
import {
  activeRunIdForThread,
  backendPortError,
  cancellationFailureMessages,
  configDraft,
  configFromDraft,
  type ActiveRun,
  type ClientConfigDraft,
} from "./lib/client-state";
import { errorMessage } from "./lib/errors";
import { activeRunIds, buildSubmitRequest } from "./lib/session-actions";
import {
  ASSISTANT_ID,
  STREAM_MODES,
  type AgentState,
  type BackendStatus,
  type ClientConfig,
} from "./lib/types";

type DisplayMessagesEvent = { type: "display_messages"; messages: unknown[] };
type PendingSubmit = {
  threadId: string | null;
  selectionRevision: number;
};

const INITIAL_STATUS: BackendStatus = {
  state: "checking",
  apiUrl: "http://127.0.0.1:2024",
  projectRoot: "",
  message: "正在检查本地后端...",
};

function isDisplayMessagesEvent(value: unknown): value is DisplayMessagesEvent {
  return (
    typeof value === "object" &&
    value !== null &&
    "type" in value &&
    (value as { type?: unknown }).type === "display_messages" &&
    Array.isArray((value as { messages?: unknown }).messages)
  );
}

function App() {
  const [config, setConfig] = useState<ClientConfig>(() => loadClientConfig());
  const [settingsDraft, setSettingsDraft] = useState<ClientConfigDraft>(() => configDraft(config));
  const [backend, setBackend] = useState<BackendStatus>(INITIAL_STATUS);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [backendBusy, setBackendBusy] = useState(false);
  const [cancellingThreadIds, setCancellingThreadIds] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  const [backendLog, setBackendLog] = useState("");
  const [logTheme, setLogTheme] = useState<LogTheme>(loadLogTheme);
  const [notice, setNotice] = useState<string | null>(null);
  const [activeRun, setActiveRun] = useState<ActiveRun | undefined>();
  const [reconnectRevision, setReconnectRevision] = useState(0);
  const composerInputRef = useRef<HTMLTextAreaElement>(null);
  const threadIdRef = useRef<string | null>(null);
  const selectionRevisionRef = useRef(0);
  const joiningRunsRef = useRef(new Map<string, ActiveRun>());
  const pendingSubmitsRef = useRef(new Set<PendingSubmit>());
  const reconnectAttemptsRef = useRef(new Map<string, number>());
  const reconnectTimersRef = useRef(new Map<string, number>());

  const client = useMemo(() => createAgentClient(backend.apiUrl), [backend.apiUrl]);
  const handleHistoryError = useCallback((message: string) => setNotice(message), []);
  const {
    threads,
    loading: historyLoading,
    hasMore: hasMoreThreads,
    refresh: refreshThreads,
    loadMore: loadMoreThreads,
  } = useThreadHistory({
    client,
    enabled: backend.state === "running",
    onError: handleHistoryError,
  });

  const scheduleStreamReconnect = useCallback((targetThreadId: string) => {
    if (reconnectTimersRef.current.has(targetThreadId)) return;
    const attempt = reconnectAttemptsRef.current.get(targetThreadId) ?? 0;
    reconnectAttemptsRef.current.set(targetThreadId, attempt + 1);
    const timer = window.setTimeout(() => {
      reconnectTimersRef.current.delete(targetThreadId);
      if (threadIdRef.current === targetThreadId) {
        setReconnectRevision((current) => current + 1);
      }
    }, Math.min(1_000 * 2 ** attempt, 8_000));
    reconnectTimersRef.current.set(targetThreadId, timer);
  }, []);

  const stream = useStream<
    AgentState,
    {
      UpdateType: AgentState;
      CustomEventType: DisplayMessagesEvent;
    }
  >({
    assistantId: ASSISTANT_ID,
    client,
    threadId,
    messagesKey: "display_messages",
    // 当前界面只需要最新 checkpoint。false 会调用 getState；true 会改走完整
    // history 接口，某些本地 LangGraph 运行时会因此出现列表可见但详情为空。
    fetchStateHistory: false,
    onThreadId: (id) => {
      const unboundSubmits = [...pendingSubmitsRef.current].filter(
        (pending) => pending.threadId === null,
      );
      const pendingSubmit = unboundSubmits.length === 1 ? unboundSubmits[0] : undefined;
      if (pendingSubmit) pendingSubmit.threadId = id;
      if (
        !pendingSubmit ||
        pendingSubmit.selectionRevision === selectionRevisionRef.current
      ) {
        threadIdRef.current = id;
        setThreadId(id);
      }
      window.setTimeout(() => void refreshThreads(), 700);
    },
    onCreated: (run) => {
      setActiveRun({ threadId: run.thread_id, runId: run.run_id });
    },
    onFinish: (...args) => {
      const run = args[1];
      if (run) {
        joiningRunsRef.current.delete(`${run.thread_id}:${run.run_id}`);
        const timer = reconnectTimersRef.current.get(run.thread_id);
        if (timer !== undefined) window.clearTimeout(timer);
        reconnectTimersRef.current.delete(run.thread_id);
        reconnectAttemptsRef.current.delete(run.thread_id);
        setActiveRun((current) =>
          current?.threadId === run.thread_id && current.runId === run.run_id
            ? undefined
            : current,
        );
        setNotice((current) =>
          threadIdRef.current === run.thread_id && current?.startsWith("恢复执行流失败")
            ? null
            : current,
        );
      }
      window.setTimeout(() => void refreshThreads(), 300);
    },
    onError: (error, run) => {
      const runKey = run ? `${run.thread_id}:${run.run_id}` : undefined;
      const joiningRun =
        runKey
          ? joiningRunsRef.current.get(runKey)
          : joiningRunsRef.current.size === 1
            ? joiningRunsRef.current.values().next().value
            : undefined;
      const pendingSubmit =
        !run && !joiningRun && pendingSubmitsRef.current.size === 1
          ? pendingSubmitsRef.current.values().next().value
          : undefined;
      if (run || joiningRun) {
        const failedRun = joiningRun ?? {
          threadId: run!.thread_id,
          runId: run!.run_id,
        };
        setActiveRun((current) =>
          current?.threadId === failedRun.threadId && current.runId === failedRun.runId
            ? undefined
            : current,
        );
      }
      if (joiningRun) {
        joiningRunsRef.current.delete(`${joiningRun.threadId}:${joiningRun.runId}`);
        if (threadIdRef.current === joiningRun.threadId) {
          setNotice(`恢复执行流失败，将自动重试：${errorMessage(error)}`);
        }
        scheduleStreamReconnect(joiningRun.threadId);
      } else {
        const failedThreadId = run?.thread_id ?? pendingSubmit?.threadId;
        if (
          failedThreadId !== undefined &&
          threadIdRef.current === failedThreadId
        ) {
          setNotice(`Agent 执行失败：${errorMessage(error)}`);
        }
      }
    },
    onCustomEvent: (event, options) => {
      if (!isDisplayMessagesEvent(event)) return;
      options.mutate((previous) => ({
        ...previous,
        display_messages: mergeMessages(
          previous.display_messages ?? previous.messages ?? [],
          event.messages,
        ) as Message[],
      }));
    },
  });

  const selectedThread = threads.find((thread) => thread.thread_id === threadId);
  const messages = useMemo(
    () => historicalConversationMessages(selectedThread?.values, stream.values, stream.messages),
    [selectedThread?.values, stream.messages, stream.values],
  );
  const interrupt = stream.interrupt ?? stream.values.__interrupt__;
  const selectedRunId = activeRunIdForThread(activeRun, threadId);
  const selectedThreadBusy = selectedThread?.status === "busy";
  const isRunning = stream.isLoading || Boolean(selectedRunId) || selectedThreadBusy;
  const composerDisabled = backend.state !== "running" || isRunning || stream.isThreadLoading;
  const cancelBusy = threadId ? cancellingThreadIds.has(threadId) : false;
  const settingsPortError = backendPortError(settingsDraft.backendPort);

  useEffect(() => {
    let cancelled = false;
    const initialize = async () => {
      setBackend(INITIAL_STATUS);
      try {
        let status = await getBackendStatus(config);
        if (cancelled) return;
        if (status.projectRoot && status.projectRoot !== config.projectRoot) {
          const detected = { ...config, projectRoot: status.projectRoot };
          setConfig(detected);
          saveClientConfig(detected);
        }
        if (isTauri()) {
          if (!status.projectRoot) {
            setBackend(status);
            return;
          }
          setBackend({ ...status, state: "starting", message: "正在重启本地 LangGraph 后端..." });
          status = await restartBackend({ ...config, projectRoot: status.projectRoot });
        }
        if (!cancelled) setBackend(status);
      } catch (error) {
        if (!cancelled) {
          setBackend({
            state: "error",
            apiUrl: `http://127.0.0.1:${config.backendPort}`,
            projectRoot: config.projectRoot,
            message: errorMessage(error),
          });
        }
      }
    };
    void initialize();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(
    () => () => {
      for (const timer of reconnectTimersRef.current.values()) {
        window.clearTimeout(timer);
      }
      reconnectTimersRef.current.clear();
    },
    [],
  );

  useEffect(() => {
    if (
      !threadId ||
      backend.state !== "running" ||
      stream.isLoading ||
      stream.isThreadLoading
    ) {
      return;
    }
    let cancelled = false;
    let joiningRunKey: string | undefined;

    const reconnect = async () => {
      try {
        const [running, pending] = await Promise.all([
          client.runs.list(threadId, { limit: 1, status: "running" }),
          client.runs.list(threadId, { limit: 1, status: "pending" }),
        ]);
        const run = running[0] ?? pending[0];
        if (!run || cancelled) return;
        const runKey = `${threadId}:${run.run_id}`;
        if (joiningRunsRef.current.has(runKey)) {
          scheduleStreamReconnect(threadId);
          return;
        }

        joiningRunKey = runKey;
        joiningRunsRef.current.set(runKey, { threadId, runId: run.run_id });
        setActiveRun({ threadId, runId: run.run_id });
        await stream.joinStream(run.run_id, undefined, { streamMode: [...STREAM_MODES] });
      } catch (error) {
        if (joiningRunKey) joiningRunsRef.current.delete(joiningRunKey);
        setActiveRun((current) =>
          current?.threadId === threadId ? undefined : current,
        );
        if (!cancelled) {
          setNotice(`恢复执行流失败，将自动重试：${errorMessage(error)}`);
          scheduleStreamReconnect(threadId);
        }
      }
    };
    void reconnect();
    return () => {
      cancelled = true;
      if (joiningRunKey) joiningRunsRef.current.delete(joiningRunKey);
    };
  }, [
    backend.state,
    client,
    reconnectRevision,
    scheduleStreamReconnect,
    stream.isThreadLoading,
    threadId,
  ]);

  const handleRestart = async (nextConfig = config): Promise<boolean> => {
    setBackendBusy(true);
    setNotice(null);
    setBackend((previous) => ({ ...previous, state: "starting", message: "正在重启本地后端..." }));
    try {
      const status = await restartBackend(nextConfig);
      setBackend(status);
      if (status.state === "running") {
        const saved = {
          ...nextConfig,
          projectRoot: status.projectRoot || nextConfig.projectRoot,
        };
        setConfig(saved);
        saveClientConfig(saved);
        return true;
      }
      return false;
    } catch (error) {
      setBackend({
        state: "error",
        apiUrl: `http://127.0.0.1:${nextConfig.backendPort}`,
        projectRoot: nextConfig.projectRoot,
        message: errorMessage(error),
      });
      return false;
    } finally {
      setBackendBusy(false);
    }
  };

  const handleChooseRoot = async () => {
    const root = await chooseProjectRoot(config.projectRoot);
    if (!root) return;
    const next = { ...config, projectRoot: root };
    await handleRestart(next);
  };

  const handleChooseSettingsRoot = async () => {
    const root = await chooseProjectRoot(settingsDraft.projectRoot);
    if (!root) return;
    setSettingsDraft((current) => ({ ...current, projectRoot: root }));
  };

  const handleOpenSettings = () => {
    setSettingsDraft(configDraft(config));
    setSettingsOpen(true);
  };

  const handleSaveSettings = async () => {
    const nextConfig = configFromDraft(settingsDraft);
    if (!nextConfig || !nextConfig.projectRoot) return;
    if (await handleRestart(nextConfig)) setSettingsOpen(false);
  };

  const handleSubmit = async () => {
    const text = input.trim();
    if (!text || composerDisabled) return;
    setNotice(null);
    const request = buildSubmitRequest(text, {
      interrupted: Boolean(interrupt),
      newThread: !threadId,
    });
    const pendingSubmit: PendingSubmit = {
      threadId,
      selectionRevision: selectionRevisionRef.current,
    };
    pendingSubmitsRef.current.add(pendingSubmit);
    setInput("");
    try {
      await stream.submit(request.values as AgentState, {
        ...(request.options as Parameters<typeof stream.submit>[1]),
        optimisticValues: (previous) => ({
          ...previous,
          messages: [...(previous.messages ?? []), request.message],
          display_messages: [
            ...(previous.display_messages ?? previous.messages ?? []),
            request.message,
          ],
        }),
      });
    } catch (error) {
      if (threadIdRef.current === pendingSubmit.threadId) {
        setInput(text);
        setNotice(`发送失败：${errorMessage(error)}`);
      }
    } finally {
      pendingSubmitsRef.current.delete(pendingSubmit);
    }
  };

  const handlePromptTemplate = (content: string) => {
    setInput(content);
    window.requestAnimationFrame(() => {
      if (composerInputRef.current) composerInputRef.current.scrollTop = 0;
    });
  };

  const handleCancel = async () => {
    if (!threadId || cancelBusy) return;
    const targetThreadId = threadId;
    const targetRunId = activeRunIdForThread(activeRun, targetThreadId);
    setCancellingThreadIds((current) => new Set(current).add(targetThreadId));
    try {
      const [running, pending] = await Promise.all([
        client.runs.list(targetThreadId, { limit: 20, status: "running" }),
        client.runs.list(targetThreadId, { limit: 20, status: "pending" }),
      ]);
      const ids = activeRunIds(running, pending, targetRunId);
      const results = await Promise.allSettled(
        ids.map((runId) =>
          client.runs.cancel(targetThreadId, runId, true, "interrupt"),
        ),
      );
      const failures = cancellationFailureMessages(results);
      if (failures.length > 0) {
        throw new Error(
          `${failures.length}/${ids.length} 个运行取消失败：${failures.join("；")}`,
        );
      }
      setActiveRun((current) =>
        current?.threadId === targetThreadId ? undefined : current,
      );
      if (threadIdRef.current === targetThreadId) await stream.stop();
      await refreshThreads();
    } catch (error) {
      if (threadIdRef.current === targetThreadId) {
        setNotice(`取消任务失败：${errorMessage(error)}`);
      }
    } finally {
      setCancellingThreadIds((current) => {
        const next = new Set(current);
        next.delete(targetThreadId);
        return next;
      });
    }
  };

  const handleNewThread = () => {
    selectionRevisionRef.current += 1;
    threadIdRef.current = null;
    stream.switchThread(null);
    setThreadId(null);
    setInput("");
    setNotice(null);
    setMobileSidebarOpen(false);
  };

  const handleSelectThread = (id: string) => {
    selectionRevisionRef.current += 1;
    threadIdRef.current = id;
    stream.switchThread(id);
    setThreadId(id);
    setNotice(null);
    setMobileSidebarOpen(false);
  };

  const handleShowLog = async () => {
    setLogOpen(true);
    setBackendLog("正在读取日志...");
    try {
      setBackendLog(await readBackendLog(config.projectRoot));
    } catch (error) {
      setBackendLog(`读取日志失败：${errorMessage(error)}`);
    }
  };

  const handleOpenArtifactPath = async (path: string, baseDir?: string) => {
    setNotice(null);
    try {
      await revealPathInFileManager(
        backend.projectRoot || config.projectRoot,
        baseDir,
        path,
      );
    } catch (error) {
      setNotice(`无法打开路径：${errorMessage(error)}`);
    }
  };

  const handleLogThemeChange = (theme: LogTheme) => {
    setLogTheme(theme);
    try {
      localStorage.setItem(LOG_THEME_STORAGE_KEY, theme);
    } catch {
      // WebView 存储不可用时仍允许本次会话切换主题。
    }
  };

  return (
    <main className="app-shell">
      <Sidebar
        threads={threads}
        selectedThreadId={threadId}
        open={sidebarOpen}
        mobileOpen={mobileSidebarOpen}
        historyLoading={historyLoading}
        hasMoreThreads={hasMoreThreads}
        onCloseMobile={() => setMobileSidebarOpen(false)}
        onNewThread={handleNewThread}
        onSelectThread={handleSelectThread}
        onRefreshThreads={refreshThreads}
        onLoadMoreThreads={loadMoreThreads}
        onOpenSettings={handleOpenSettings}
        onShowLog={handleShowLog}
      />

      <section className="workspace">
        <header className="workspace-header">
          <div className="header-left">
            <button className="icon-button mobile-menu" onClick={() => setMobileSidebarOpen(true)} title="打开会话列表">
              <Menu size={19} />
            </button>
            <button className="icon-button desktop-sidebar-toggle" onClick={() => setSidebarOpen((open) => !open)} title={sidebarOpen ? "收起侧栏" : "展开侧栏"}>
              {sidebarOpen ? <PanelLeftClose size={18} /> : <PanelLeftOpen size={18} />}
            </button>
            <div className="conversation-heading">
              <strong>{selectedThread ? threadTitle(selectedThread) : "新对话"}</strong>
              <span>{stream.isThreadLoading ? "正在加载对话" : isRunning ? "Agent 正在执行任务" : interrupt ? "等待补充信息" : "Web 自动化测试 Agent"}</span>
            </div>
          </div>
          <div className="header-actions">
            <BackendBadge status={backend} />
            {isRunning && (
              <button className="cancel-button" onClick={() => void handleCancel()} disabled={cancelBusy}>
                <CircleStop size={16} />
                <span>{cancelBusy ? "取消中" : "取消任务"}</span>
              </button>
            )}
          </div>
        </header>

        <BackendBanner
          status={backend}
          busy={backendBusy}
          onChooseRoot={handleChooseRoot}
          onRestart={handleRestart}
        />

        {notice && (
          <div className="notice" role="alert">
            <AlertTriangle size={16} />
            <span>{notice}</span>
            <button className="icon-button small" onClick={() => setNotice(null)} title="关闭提示"><X size={14} /></button>
          </div>
        )}

        <MessageTimeline
          messages={messages}
          interrupt={interrupt}
          isLoading={stream.isLoading}
          isThreadLoading={stream.isThreadLoading}
          onPromptTemplate={handlePromptTemplate}
          onOpenPath={(path, baseDir) => void handleOpenArtifactPath(path, baseDir)}
        />

        <Composer
          value={input}
          disabled={composerDisabled}
          threadLoading={stream.isThreadLoading}
          interrupted={Boolean(interrupt)}
          apiUrl={backend.apiUrl}
          inputRef={composerInputRef}
          onChange={setInput}
          onSubmit={handleSubmit}
        />
      </section>

      <SettingsModal
        open={settingsOpen}
        draft={settingsDraft}
        portError={settingsPortError}
        backend={backend}
        busy={backendBusy}
        onDraftChange={setSettingsDraft}
        onChooseRoot={handleChooseSettingsRoot}
        onSave={handleSaveSettings}
        onClose={() => setSettingsOpen(false)}
      />
      <LogModal
        open={logOpen}
        content={backendLog}
        theme={logTheme}
        onThemeChange={handleLogThemeChange}
        onRefresh={handleShowLog}
        onClose={() => setLogOpen(false)}
      />
    </main>
  );
}

export default App;

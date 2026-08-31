import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
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
import {
  ThreadSessionController,
  type ThreadSessionHandle,
  type ThreadSessionSnapshot,
} from "./components/ThreadSessionController";
import { useThreadHistory } from "./hooks/use-thread-history";
import { useThreadTitleBackfill } from "./hooks/use-thread-title-backfill";
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
  messageText,
  summarizeThreadTitle,
  threadModelTitle,
  threadTitle,
} from "./lib/message-utils";
import {
  backendPortError,
  configDraft,
  configFromDraft,
  type ClientConfigDraft,
} from "./lib/client-state";
import { errorMessage } from "./lib/errors";
import { isActiveRunPhase, runPhaseLabel } from "./lib/thread-runtime";
import {
  ASSISTANT_ID,
  type AgentState,
  type BackendStatus,
  type ClientConfig,
} from "./lib/types";

type SessionDescriptor = {
  sessionKey: string;
  threadId: string | null;
  lastUsedAt: number;
};

const MAX_CACHED_IDLE_SESSIONS = 8;

const INITIAL_STATUS: BackendStatus = {
  state: "checking",
  apiUrl: "http://127.0.0.1:2024",
  projectRoot: "",
  message: "正在检查本地后端...",
};

function createSession(threadId: string | null = null): SessionDescriptor {
  return {
    sessionKey: crypto.randomUUID(),
    threadId,
    lastUsedAt: Date.now(),
  };
}

function firstMessageTitle(snapshot: ThreadSessionSnapshot | undefined): string | undefined {
  const firstHuman = snapshot?.messages.find((message) => message.type === "human");
  return firstHuman ? summarizeThreadTitle(messageText(firstHuman)) : undefined;
}

function App() {
  const initialSessionRef = useRef<SessionDescriptor | undefined>(undefined);
  if (!initialSessionRef.current) initialSessionRef.current = createSession();

  const [config, setConfig] = useState<ClientConfig>(() => loadClientConfig());
  const [settingsDraft, setSettingsDraft] = useState<ClientConfigDraft>(() => configDraft(config));
  const [backend, setBackend] = useState<BackendStatus>(INITIAL_STATUS);
  const [sessions, setSessions] = useState<SessionDescriptor[]>([initialSessionRef.current]);
  const [selectedSessionKey, setSelectedSessionKey] = useState(initialSessionRef.current.sessionKey);
  const [sessionSnapshots, setSessionSnapshots] = useState<Record<string, ThreadSessionSnapshot>>({});
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [backendBusy, setBackendBusy] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  const [backendLog, setBackendLog] = useState("");
  const [logTheme, setLogTheme] = useState<LogTheme>(loadLogTheme);
  const [systemNotice, setSystemNotice] = useState<string | null>(null);
  const sessionHandlesRef = useRef(new Map<string, ThreadSessionHandle>());
  const composerInputRef = useRef<HTMLTextAreaElement>(null);

  const client = useMemo(() => createAgentClient(backend.apiUrl), [backend.apiUrl]);
  const handleHistoryError = useCallback((message: string) => setSystemNotice(message), []);
  const {
    threads,
    loading: historyLoading,
    hasMore: hasMoreThreads,
    refresh: refreshThreads,
    loadMore: loadMoreThreads,
    patchThread,
    upsertThread,
  } = useThreadHistory({
    client,
    enabled: backend.state === "running",
    onError: handleHistoryError,
  });

  const selectedSession = sessions.find((session) => session.sessionKey === selectedSessionKey);
  const selectedThreadId = selectedSession?.threadId ?? null;
  const selectedSnapshot = sessionSnapshots[selectedSessionKey];
  const selectedThread = threads.find((thread) => thread.thread_id === selectedThreadId);
  const selectedHandle = sessionHandlesRef.current.get(selectedSessionKey);
  const selectedDraft = drafts[selectedSessionKey] ?? "";
  const foregroundActive = Object.values(sessionSnapshots).some((snapshot) => (
    isActiveRunPhase(snapshot.phase)
  )) || threads.some((thread) => thread.status === "busy");

  const handleBackfillUpdated = useCallback((threadId: string, metadata: Record<string, unknown>) => {
    patchThread(threadId, { metadata });
  }, [patchThread]);

  useThreadTitleBackfill({
    client,
    threads,
    enabled: backend.state === "running",
    foregroundActive,
    onUpdated: handleBackfillUpdated,
  });

  const handleSessionRegister = useCallback((sessionKey: string, handle?: ThreadSessionHandle) => {
    if (handle) sessionHandlesRef.current.set(sessionKey, handle);
    else sessionHandlesRef.current.delete(sessionKey);
  }, []);

  const handleSnapshot = useCallback((snapshot: ThreadSessionSnapshot) => {
    setSessionSnapshots((current) => (
      current[snapshot.sessionKey] === snapshot
        ? current
        : { ...current, [snapshot.sessionKey]: snapshot }
    ));
  }, []);

  const handleRestoreDraft = useCallback((sessionKey: string, text: string) => {
    setDrafts((current) => ({
      ...current,
      [sessionKey]: current[sessionKey]?.trim() ? current[sessionKey] : text,
    }));
  }, []);

  const handleRefreshRequested = useCallback(() => {
    void refreshThreads();
  }, [refreshThreads]);

  const handleThreadBound = useCallback((sessionKey: string, threadId: string) => {
    setSessions((current) => current.map((session) => (
      session.sessionKey === sessionKey
        ? { ...session, threadId, lastUsedAt: Date.now() }
        : session
    )));
    const snapshot = sessionSnapshots[sessionKey];
    const now = new Date().toISOString();
    upsertThread({
      thread_id: threadId,
      created_at: now,
      updated_at: now,
      metadata: { graph_id: ASSISTANT_ID },
      status: "busy",
      extracted: snapshot?.messages[0]
        ? { first_message: snapshot.messages[0] }
        : undefined,
    });
    window.setTimeout(() => void refreshThreads(), 700);
  }, [refreshThreads, sessionSnapshots, upsertThread]);

  useEffect(() => {
    for (const snapshot of Object.values(sessionSnapshots)) {
      if (!snapshot.threadId || !snapshot.values.thread_title?.trim()) continue;
      const thread = threads.find((item) => item.thread_id === snapshot.threadId);
      if (!thread) continue;
      if (thread?.extracted?.thread_title === snapshot.values.thread_title.trim()) continue;
      patchThread(snapshot.threadId, {
        extracted: {
          ...thread?.extracted,
          thread_title: snapshot.values.thread_title.trim(),
        },
      });
    }
  }, [patchThread, sessionSnapshots, threads]);

  useEffect(() => {
    const inactive = sessions
      .filter((session) => (
        session.sessionKey !== selectedSessionKey &&
        !isActiveRunPhase(sessionSnapshots[session.sessionKey]?.phase)
      ))
      .sort((a, b) => b.lastUsedAt - a.lastUsedAt);
    if (inactive.length <= MAX_CACHED_IDLE_SESSIONS) return;
    const removed = new Set(inactive.slice(MAX_CACHED_IDLE_SESSIONS).map((session) => session.sessionKey));
    setSessions((current) => current.filter((session) => !removed.has(session.sessionKey)));
    setSessionSnapshots((current) => Object.fromEntries(
      Object.entries(current).filter(([key]) => !removed.has(key)),
    ));
  }, [selectedSessionKey, sessionSnapshots, sessions]);

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

  const handleRestart = async (nextConfig = config): Promise<boolean> => {
    setBackendBusy(true);
    setSystemNotice(null);
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
    await handleRestart({ ...config, projectRoot: root });
  };

  const handleChooseSettingsRoot = async () => {
    const root = await chooseProjectRoot(settingsDraft.projectRoot);
    if (root) setSettingsDraft((current) => ({ ...current, projectRoot: root }));
  };

  const handleSaveSettings = async () => {
    const nextConfig = configFromDraft(settingsDraft);
    if (!nextConfig?.projectRoot) return;
    if (await handleRestart(nextConfig)) setSettingsOpen(false);
  };

  const handleSubmit = () => {
    const text = selectedDraft.trim();
    if (!text || !selectedHandle) return;
    const existingTitle = threadModelTitle(selectedThread, selectedSnapshot?.values);
    if (!selectedHandle.submit(text, existingTitle)) return;
    setSystemNotice(null);
    setDrafts((current) => ({ ...current, [selectedSessionKey]: "" }));
  };

  const handlePromptTemplate = (content: string) => {
    setDrafts((current) => ({ ...current, [selectedSessionKey]: content }));
    window.requestAnimationFrame(() => {
      if (composerInputRef.current) composerInputRef.current.scrollTop = 0;
    });
  };

  const handleNewThread = () => {
    const session = createSession();
    setSessions((current) => [...current, session]);
    setSelectedSessionKey(session.sessionKey);
    setDrafts((current) => ({ ...current, [session.sessionKey]: "" }));
    setSystemNotice(null);
    setMobileSidebarOpen(false);
  };

  const handleSelectThread = (threadId: string) => {
    const existing = sessions.find((session) => session.threadId === threadId);
    const session = existing ?? createSession(threadId);
    setSessions((current) => existing
      ? current.map((item) => item.sessionKey === existing.sessionKey
        ? { ...item, lastUsedAt: Date.now() }
        : item)
      : [...current, session]);
    setSelectedSessionKey(session.sessionKey);
    setSystemNotice(null);
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
    setSystemNotice(null);
    try {
      await revealPathInFileManager(
        backend.projectRoot || config.projectRoot,
        baseDir,
        path,
      );
    } catch (error) {
      setSystemNotice(`无法打开路径：${errorMessage(error)}`);
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

  const serverBusyBeforeHydration = selectedThread?.status === "busy" && !selectedSnapshot?.checkedRuns;
  const selectedRunning = isActiveRunPhase(selectedSnapshot?.phase) || serverBusyBeforeHydration;
  const composerDisabled = backend.state !== "running" ||
    !selectedHandle ||
    selectedRunning ||
    Boolean(selectedSnapshot?.isThreadLoading);
  const notice = systemNotice ?? selectedSnapshot?.notice ?? null;
  const headingTitle = selectedThread
    ? threadTitle(selectedThread, selectedSnapshot?.values)
    : selectedSnapshot?.values.thread_title?.trim() || firstMessageTitle(selectedSnapshot) || "新对话";
  const headingStatus = selectedSnapshot?.isThreadLoading
    ? "正在加载对话"
    : serverBusyBeforeHydration
      ? "Agent 正在执行任务"
      : runPhaseLabel(selectedSnapshot?.phase);
  const settingsPortError = backendPortError(settingsDraft.backendPort);

  return (
    <main className="app-shell">
      {backend.state === "running" && sessions.map((session) => (
        <ThreadSessionController
          key={session.sessionKey}
          sessionKey={session.sessionKey}
          threadId={session.threadId}
          client={client}
          enabled
          initialValues={sessionSnapshots[session.sessionKey]?.values as AgentState | undefined}
          onThreadBound={handleThreadBound}
          onSnapshot={handleSnapshot}
          onRegister={handleSessionRegister}
          onRefreshThreads={handleRefreshRequested}
          onRestoreDraft={handleRestoreDraft}
        />
      ))}

      <Sidebar
        threads={threads}
        selectedThreadId={selectedThreadId}
        open={sidebarOpen}
        mobileOpen={mobileSidebarOpen}
        historyLoading={historyLoading}
        hasMoreThreads={hasMoreThreads}
        onCloseMobile={() => setMobileSidebarOpen(false)}
        onNewThread={handleNewThread}
        onSelectThread={handleSelectThread}
        onRefreshThreads={refreshThreads}
        onLoadMoreThreads={loadMoreThreads}
        onOpenSettings={() => {
          setSettingsDraft(configDraft(config));
          setSettingsOpen(true);
        }}
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
              <strong>{headingTitle}</strong>
              <span>{headingStatus}</span>
            </div>
          </div>
          <div className="header-actions">
            <BackendBadge status={backend} />
            {selectedRunning && selectedThreadId && (
              <button className="cancel-button" onClick={() => void selectedHandle?.cancel()} disabled={selectedSnapshot?.phase === "cancelling"}>
                <CircleStop size={16} />
                <span>{selectedSnapshot?.phase === "cancelling" ? "取消中" : "取消任务"}</span>
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
            <button
              className="icon-button small"
              onClick={() => {
                if (systemNotice) setSystemNotice(null);
                else selectedHandle?.clearNotice();
              }}
              title="关闭提示"
            >
              <X size={14} />
            </button>
          </div>
        )}

        <MessageTimeline
          threadKey={selectedSessionKey}
          messages={selectedSnapshot?.messages ?? []}
          interrupt={selectedSnapshot?.interrupt}
          isLoading={selectedSnapshot?.phase === "queued" || selectedSnapshot?.phase === "running"}
          isThreadLoading={Boolean(selectedSnapshot?.isThreadLoading)}
          onPromptTemplate={handlePromptTemplate}
          onOpenPath={handleOpenArtifactPath}
        />

        <Composer
          value={selectedDraft}
          disabled={composerDisabled}
          threadLoading={Boolean(selectedSnapshot?.isThreadLoading)}
          interrupted={selectedSnapshot?.phase === "awaiting_input"}
          apiUrl={backend.apiUrl}
          inputRef={composerInputRef}
          onChange={(value) => setDrafts((current) => ({
            ...current,
            [selectedSessionKey]: value,
          }))}
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

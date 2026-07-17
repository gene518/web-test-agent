import {
  type FormEvent,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { isTauri } from "@tauri-apps/api/core";
import { useStream } from "@langchain/langgraph-sdk/react";
import type { Message, Thread } from "@langchain/langgraph-sdk";
import Ansi from "ansi-to-react";
import {
  Activity,
  AlertTriangle,
  Bot,
  Check,
  ChevronDown,
  CircleStop,
  Clock3,
  FolderOpen,
  Menu,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Palette,
  Plus,
  RefreshCw,
  Send,
  Settings,
  TerminalSquare,
  UserRound,
  Wrench,
  X,
} from "lucide-react";
import "./App.css";
import {
  chooseProjectRoot,
  createAgentClient,
  getBackendStatus,
  loadClientConfig,
  readBackendLog,
  restartBackend,
  saveClientConfig,
} from "./lib/backend";
import {
  buildToolInvocations,
  extractInterruptQuestion,
  historicalConversationMessages,
  mergeMessages,
  messageText,
  threadTitle,
  toolsForMessage,
  type CanonicalMessage,
  type ToolInvocation,
} from "./lib/message-utils";
import { activeRunIds, buildSubmitRequest } from "./lib/session-actions";
import { AGENT_INTRO, PROMPT_TEMPLATES } from "./lib/prompt-templates";
import {
  ASSISTANT_ID,
  STREAM_MODES,
  type AgentState,
  type BackendStatus,
  type ClientConfig,
} from "./lib/types";

type DisplayMessagesEvent = { type: "display_messages"; messages: unknown[] };
type LogTheme = "macos" | "dark" | "light";

const LOG_THEME_STORAGE_KEY = "web-test-agent.log-theme.v1";
const MAX_COMPOSER_ROWS = 5;
const LOG_THEME_OPTIONS: readonly { value: LogTheme; label: string }[] = [
  { value: "macos", label: "macOS 控制台" },
  { value: "dark", label: "深色" },
  { value: "light", label: "浅色" },
];

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

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function loadLogTheme(): LogTheme {
  try {
    const stored = localStorage.getItem(LOG_THEME_STORAGE_KEY);
    return LOG_THEME_OPTIONS.some((theme) => theme.value === stored)
      ? (stored as LogTheme)
      : "macos";
  } catch {
    return "macos";
  }
}

function formatTime(value?: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const today = new Date();
  if (date.toDateString() === today.toDateString()) {
    return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  }
  return date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

function stringify(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function ToolRow({ item }: { item: ToolInvocation }) {
  return (
    <details className="tool-row">
      <summary>
        <span className={`tool-status tool-status-${item.status}`} aria-hidden="true">
          {item.status === "running" ? <Activity size={14} /> : <Check size={14} />}
        </span>
        <span className="tool-name">{item.name}</span>
        <span className="tool-state">
          {item.status === "running" ? "执行中" : item.status === "error" ? "失败" : "已完成"}
        </span>
        <ChevronDown className="tool-chevron" size={15} aria-hidden="true" />
      </summary>
      <div className="tool-detail">
        <div>
          <span>参数</span>
          <pre>{stringify(item.args)}</pre>
        </div>
        {item.result !== undefined && (
          <div>
            <span>结果</span>
            <pre>{item.result || "(空结果)"}</pre>
          </div>
        )}
      </div>
    </details>
  );
}

function TimelineMessage({
  message,
  tools,
}: {
  message: CanonicalMessage;
  tools: ToolInvocation[];
}) {
  if (message.type === "tool") {
    if (tools.length === 0) return null;
    return (
      <article className="timeline-message timeline-tool">
        <div className="message-avatar" aria-hidden="true"><Wrench size={16} /></div>
        <div className="message-body">
          <div className="message-role">工具</div>
          <div className="tool-list standalone-tool">
            {tools.map((tool) => <ToolRow item={tool} key={tool.id} />)}
          </div>
        </div>
      </article>
    );
  }
  const text = messageText(message).trim();

  return (
    <article className={`timeline-message timeline-${message.type}`}>
      <div className="message-avatar" aria-hidden="true">
        {message.type === "human" ? <UserRound size={16} /> : <Bot size={17} />}
      </div>
      <div className="message-body">
        <div className="message-role">{message.type === "human" ? "你" : "Agent"}</div>
        {text && <div className="message-content">{text}</div>}
        {tools.length > 0 && (
          <div className="tool-list">
            {tools.map((tool) => (
              <ToolRow item={tool} key={tool.id} />
            ))}
          </div>
        )}
      </div>
    </article>
  );
}

function BackendBadge({ status }: { status: BackendStatus }) {
  const label = {
    checking: "检查中",
    starting: "启动中",
    running: "已连接",
    stopped: "未启动",
    conflict: "端口冲突",
    error: "异常",
  }[status.state];
  return (
    <span className={`backend-badge backend-${status.state}`} title={status.message}>
      <span />
      {label}
    </span>
  );
}

function App() {
  const [config, setConfig] = useState<ClientConfig>(() => loadClientConfig());
  const [backend, setBackend] = useState<BackendStatus>(INITIAL_STATUS);
  const [threads, setThreads] = useState<Thread<AgentState>[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [historyLoading, setHistoryLoading] = useState(false);
  const [backendBusy, setBackendBusy] = useState(false);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  const [backendLog, setBackendLog] = useState("");
  const [logTheme, setLogTheme] = useState<LogTheme>(loadLogTheme);
  const [notice, setNotice] = useState<string | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | undefined>();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const composerInputRef = useRef<HTMLTextAreaElement>(null);
  const joinedRunsRef = useRef(new Set<string>());

  const client = useMemo(() => createAgentClient(backend.apiUrl), [backend.apiUrl]);

  const refreshThreads = useCallback(async () => {
    if (backend.state !== "running") return;
    setHistoryLoading(true);
    try {
      const result = await client.threads.search<AgentState>({
        limit: 100,
        sortBy: "updated_at",
        sortOrder: "desc",
        select: ["thread_id", "created_at", "updated_at", "metadata", "status", "values", "interrupts"],
      });
      const filtered = result
        .filter((thread) => {
          const graphId = thread.metadata?.graph_id ?? thread.metadata?.assistant_id;
          return !graphId || graphId === ASSISTANT_ID;
        })
        .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at));
      setThreads(filtered);
    } catch (error) {
      setNotice(`读取历史对话失败：${errorMessage(error)}`);
    } finally {
      setHistoryLoading(false);
    }
  }, [backend.state, client]);

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
      setThreadId(id);
      window.setTimeout(() => void refreshThreads(), 700);
    },
    onCreated: (run) => setActiveRunId(run.run_id),
    onFinish: () => {
      setActiveRunId(undefined);
      window.setTimeout(() => void refreshThreads(), 300);
    },
    onError: (error) => {
      setActiveRunId(undefined);
      setNotice(`Agent 执行失败：${errorMessage(error)}`);
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
  const toolInvocations = useMemo(() => buildToolInvocations(messages), [messages]);
  const linkedToolIds = useMemo(
    () =>
      new Set(
        messages
          .filter((message) => message.type === "ai")
          .flatMap((message) => toolsForMessage(message, toolInvocations).map((tool) => tool.id)),
      ),
    [messages, toolInvocations],
  );
  const interrupt = stream.interrupt ?? stream.values.__interrupt__;
  const isRunning = stream.isLoading || Boolean(activeRunId);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, stream.isLoading, interrupt]);

  useLayoutEffect(() => {
    const textarea = composerInputRef.current;
    if (!textarea) return;

    textarea.style.height = "auto";
    textarea.style.overflowY = "hidden";
    const style = window.getComputedStyle(textarea);
    const lineHeight = Number.parseFloat(style.lineHeight) || 20;
    const verticalPadding =
      (Number.parseFloat(style.paddingTop) || 0) + (Number.parseFloat(style.paddingBottom) || 0);
    const maxHeight = lineHeight * MAX_COMPOSER_ROWS + verticalPadding;
    const contentHeight = textarea.scrollHeight;
    textarea.style.height = `${Math.min(contentHeight, maxHeight)}px`;
    textarea.style.overflowY = contentHeight > maxHeight ? "auto" : "hidden";
  }, [input]);

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

  useEffect(() => {
    if (backend.state === "running") void refreshThreads();
  }, [backend.state, refreshThreads]);

  useEffect(() => {
    if (!threadId || backend.state !== "running" || stream.isLoading) return;
    let cancelled = false;
    const reconnect = async () => {
      try {
        const [running, pending] = await Promise.all([
          client.runs.list(threadId, { limit: 1, status: "running" }),
          client.runs.list(threadId, { limit: 1, status: "pending" }),
        ]);
        const run = running[0] ?? pending[0];
        if (!run || cancelled || joinedRunsRef.current.has(run.run_id)) return;
        joinedRunsRef.current.add(run.run_id);
        setActiveRunId(run.run_id);
        await stream.joinStream(run.run_id, undefined, { streamMode: [...STREAM_MODES] });
      } catch (error) {
        if (!cancelled) setNotice(`恢复执行流失败：${errorMessage(error)}`);
      }
    };
    const timeout = window.setTimeout(() => void reconnect(), 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timeout);
    };
  }, [backend.state, client, threadId]);

  const handleRestart = async (nextConfig = config) => {
    setBackendBusy(true);
    setNotice(null);
    setBackend((previous) => ({ ...previous, state: "starting", message: "正在重启本地后端..." }));
    try {
      const status = await restartBackend(nextConfig);
      setBackend(status);
      if (status.projectRoot) {
        const saved = { ...nextConfig, projectRoot: status.projectRoot };
        setConfig(saved);
        saveClientConfig(saved);
      }
      if (status.state === "running") await refreshThreads();
    } catch (error) {
      setBackend({
        state: "error",
        apiUrl: `http://127.0.0.1:${nextConfig.backendPort}`,
        projectRoot: nextConfig.projectRoot,
        message: errorMessage(error),
      });
    } finally {
      setBackendBusy(false);
    }
  };

  const handleChooseRoot = async () => {
    const root = await chooseProjectRoot(config.projectRoot);
    if (!root) return;
    const next = { ...config, projectRoot: root };
    setConfig(next);
    saveClientConfig(next);
    await handleRestart(next);
  };

  const handleSubmit = async (event?: FormEvent) => {
    event?.preventDefault();
    const text = input.trim();
    if (!text || isRunning || backend.state !== "running") return;
    setNotice(null);
    const request = buildSubmitRequest(text, {
      interrupted: Boolean(interrupt),
      newThread: !threadId,
    });
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
      setInput(text);
      setNotice(`发送失败：${errorMessage(error)}`);
    }
  };

  const handleInputKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSubmit();
    }
  };

  const handlePromptTemplate = (content: string) => {
    setInput(content);
    window.requestAnimationFrame(() => {
      if (composerInputRef.current) composerInputRef.current.scrollTop = 0;
    });
  };

  const handleCancel = async () => {
    if (!threadId || cancelBusy) {
      await stream.stop();
      return;
    }
    setCancelBusy(true);
    try {
      const [running, pending] = await Promise.all([
        client.runs.list(threadId, { limit: 20, status: "running" }),
        client.runs.list(threadId, { limit: 20, status: "pending" }),
      ]);
      const ids = activeRunIds(running, pending, activeRunId);
      await Promise.allSettled(
        ids.map((runId) => client.runs.cancel(threadId, runId, true, "interrupt")),
      );
      setActiveRunId(undefined);
      await stream.stop();
      await refreshThreads();
    } catch (error) {
      setNotice(`取消任务失败：${errorMessage(error)}`);
      await stream.stop();
    } finally {
      setCancelBusy(false);
    }
  };

  const handleNewThread = () => {
    stream.switchThread(null);
    setThreadId(null);
    setInput("");
    setNotice(null);
    setMobileSidebarOpen(false);
  };

  const handleSelectThread = (id: string) => {
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
      <aside className={`sidebar ${sidebarOpen ? "" : "sidebar-collapsed"} ${mobileSidebarOpen ? "sidebar-mobile-open" : ""}`}>
        <div className="sidebar-brand">
          <div className="brand-mark"><Activity size={19} /></div>
          {sidebarOpen && (
            <div>
              <strong>Web Test Agent</strong>
              <span>Desktop</span>
            </div>
          )}
          <button className="icon-button mobile-close" onClick={() => setMobileSidebarOpen(false)} title="关闭会话列表">
            <X size={18} />
          </button>
        </div>

        <button
          className="new-chat-button"
          onClick={handleNewThread}
          title="新建对话"
        >
          <Plus size={18} />
          {sidebarOpen && <span>新建对话</span>}
        </button>

        {sidebarOpen && (
          <div className="history-section">
            <div className="section-label">
              <span>历史对话</span>
              <button className={`icon-button small ${historyLoading ? "spin" : ""}`} onClick={() => void refreshThreads()} title="刷新历史对话">
                <RefreshCw size={14} />
              </button>
            </div>
            <div className="thread-list">
              {threads.map((thread) => (
                <button
                  className={`thread-item ${thread.thread_id === threadId ? "selected" : ""}`}
                  key={thread.thread_id}
                  onClick={() => handleSelectThread(thread.thread_id)}
                >
                  <MessageSquare size={15} />
                  <span className="thread-copy">
                    <strong>{threadTitle(thread)}</strong>
                    <span>{thread.status === "busy" ? "正在运行" : formatTime(thread.updated_at)}</span>
                  </span>
                  {thread.status === "busy" && <span className="busy-dot" title="正在运行" />}
                </button>
              ))}
              {!historyLoading && threads.length === 0 && (
                <div className="history-empty">暂无历史对话</div>
              )}
            </div>
          </div>
        )}

        <div className="sidebar-footer">
          <button className="sidebar-action" onClick={() => setSettingsOpen(true)} title="客户端设置">
            <Settings size={17} />
            {sidebarOpen && <span>设置</span>}
          </button>
          <button className="sidebar-action" onClick={() => void handleShowLog()} title="查看后端日志">
            <TerminalSquare size={17} />
            {sidebarOpen && <span>后端日志</span>}
          </button>
        </div>
      </aside>
      {mobileSidebarOpen && <button className="sidebar-scrim" onClick={() => setMobileSidebarOpen(false)} aria-label="关闭会话列表" />}

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
              <span>{isRunning ? "Agent 正在执行任务" : interrupt ? "等待补充信息" : "Web 自动化测试 Agent"}</span>
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

        {backend.state !== "running" && (
          <div className={`backend-banner banner-${backend.state}`}>
            <div>
              {backend.state === "starting" || backend.state === "checking" ? <RefreshCw className="spin" size={18} /> : <AlertTriangle size={18} />}
              <span>{backend.message || "本地后端尚未就绪。"}</span>
            </div>
            {isTauri() && !backend.projectRoot && (
              <button onClick={() => void handleChooseRoot()} disabled={backendBusy}>
                <FolderOpen size={16} />选择项目目录
              </button>
            )}
            {backend.projectRoot && backend.state !== "starting" && backend.state !== "checking" && (
              <button onClick={() => void handleRestart()} disabled={backendBusy}>
                <RefreshCw size={16} />重新启动
              </button>
            )}
          </div>
        )}

        {notice && (
          <div className="notice" role="alert">
            <AlertTriangle size={16} />
            <span>{notice}</span>
            <button className="icon-button small" onClick={() => setNotice(null)} title="关闭提示"><X size={14} /></button>
          </div>
        )}

        <div className="message-viewport">
          <div className="message-column">
            {messages.length === 0 && !interrupt ? (
              <div className="empty-state">
                <div className="empty-icon"><Bot size={25} /></div>
                <h1>开始一个测试任务</h1>
                <p>{AGENT_INTRO}</p>
                <div className="prompt-examples">
                  {PROMPT_TEMPLATES.map((template) => (
                    <button
                      key={template.id}
                      type="button"
                      onClick={() => handlePromptTemplate(template.content)}
                    >
                      {template.title}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <>
                {messages.map((message, index) => (
                  <TimelineMessage
                    key={message.id ?? `${message.type}-${index}`}
                    message={message}
                    tools={
                      message.type === "tool"
                        ? linkedToolIds.has(message.tool_call_id)
                          ? []
                          : toolInvocations.filter((tool) => tool.id === message.tool_call_id)
                        : toolsForMessage(message, toolInvocations)
                    }
                  />
                ))}
                {interrupt && (
                  <div className="interrupt-panel">
                    <div className="interrupt-icon"><Clock3 size={18} /></div>
                    <div>
                      <strong>需要补充信息</strong>
                      <p>{extractInterruptQuestion(interrupt)}</p>
                    </div>
                  </div>
                )}
                {stream.isLoading && (
                  <div className="running-indicator">
                    <span /><span /><span />
                    Agent 正在处理
                  </div>
                )}
              </>
            )}
            <div ref={messagesEndRef} />
          </div>
        </div>

        <footer className="composer-area">
          <form className="composer" onSubmit={(event) => void handleSubmit(event)}>
            <textarea
              ref={composerInputRef}
              aria-label="对话输入框"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleInputKeyDown}
              placeholder={interrupt ? "输入补充信息并继续..." : "向 Agent 描述测试任务..."}
              rows={1}
              disabled={backend.state !== "running" || isRunning}
            />
            <button className="send-button" type="submit" disabled={!input.trim() || isRunning || backend.state !== "running"} title="发送">
              <Send size={18} />
            </button>
          </form>
          <div className="composer-meta">
            <span>{interrupt ? "回复将恢复当前任务" : "Enter 发送，Shift + Enter 换行"}</span>
            <span>{backend.apiUrl}</span>
          </div>
        </footer>
      </section>

      {settingsOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setSettingsOpen(false)}>
          <section className="modal" role="dialog" aria-modal="true" aria-labelledby="settings-title" onMouseDown={(event) => event.stopPropagation()}>
            <header>
              <div><Settings size={19} /><h2 id="settings-title">客户端设置</h2></div>
              <button className="icon-button" onClick={() => setSettingsOpen(false)} title="关闭设置"><X size={18} /></button>
            </header>
            <div className="settings-content">
              <label>
                <span>项目根目录</span>
                <div className="path-input">
                  <input value={config.projectRoot} readOnly placeholder="请选择仓库根目录" />
                  <button className="icon-button" onClick={() => void handleChooseRoot()} title="选择项目根目录"><FolderOpen size={18} /></button>
                </div>
              </label>
              <label>
                <span>后端端口</span>
                <input
                  type="number"
                  min={1024}
                  max={65535}
                  value={config.backendPort}
                  onChange={(event) => setConfig((current) => ({ ...current, backendPort: Number(event.target.value) }))}
                />
              </label>
              <div className="settings-status">
                <BackendBadge status={backend} />
                <span>{backend.message || backend.apiUrl}</span>
              </div>
            </div>
            <footer>
              <button className="secondary-button" onClick={() => setSettingsOpen(false)}>取消</button>
              <button className="primary-button" onClick={() => { saveClientConfig(config); void handleRestart(); }} disabled={backendBusy || !config.projectRoot}>
                <RefreshCw size={16} className={backendBusy ? "spin" : ""} />
                {backendBusy ? "启动中" : "保存并重启"}
              </button>
            </footer>
          </section>
        </div>
      )}

      {logOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setLogOpen(false)}>
          <section className="modal log-modal" role="dialog" aria-modal="true" aria-labelledby="log-title" onMouseDown={(event) => event.stopPropagation()}>
            <header>
              <div><TerminalSquare size={19} /><h2 id="log-title">后端日志</h2></div>
              <div className="modal-header-actions">
                <label className="log-theme-picker">
                  <Palette size={15} aria-hidden="true" />
                  <select
                    aria-label="日志颜色主题"
                    value={logTheme}
                    onChange={(event) => handleLogThemeChange(event.target.value as LogTheme)}
                  >
                    {LOG_THEME_OPTIONS.map((theme) => (
                      <option key={theme.value} value={theme.value}>{theme.label}</option>
                    ))}
                  </select>
                </label>
                <button className="icon-button" onClick={() => void handleShowLog()} title="刷新日志"><RefreshCw size={17} /></button>
                <button className="icon-button" onClick={() => setLogOpen(false)} title="关闭日志"><X size={18} /></button>
              </div>
            </header>
            <div className={`log-content log-theme-${logTheme}`} role="log" aria-label="后端日志内容">
              <Ansi useClasses>{backendLog}</Ansi>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}

export default App;

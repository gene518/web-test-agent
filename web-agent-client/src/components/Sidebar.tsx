import type { Thread } from "@langchain/langgraph-sdk";
import {
  Activity,
  MessageSquare,
  Plus,
  RefreshCw,
  Settings,
  TerminalSquare,
  X,
} from "lucide-react";
import { threadTitle } from "../lib/message-utils";
import type { AgentState } from "../lib/types";

function formatThreadTime(value?: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const today = new Date();
  if (date.toDateString() === today.toDateString()) {
    return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  }
  return date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

type SidebarProps = {
  threads: Thread<AgentState>[];
  selectedThreadId: string | null;
  open: boolean;
  mobileOpen: boolean;
  historyLoading: boolean;
  hasMoreThreads: boolean;
  onCloseMobile: () => void;
  onNewThread: () => void;
  onSelectThread: (threadId: string) => void;
  onRefreshThreads: () => void | Promise<void>;
  onLoadMoreThreads: () => void | Promise<void>;
  onOpenSettings: () => void;
  onShowLog: () => void | Promise<void>;
};

export function Sidebar({
  threads,
  selectedThreadId,
  open,
  mobileOpen,
  historyLoading,
  hasMoreThreads,
  onCloseMobile,
  onNewThread,
  onSelectThread,
  onRefreshThreads,
  onLoadMoreThreads,
  onOpenSettings,
  onShowLog,
}: SidebarProps) {
  return (
    <>
      <aside
        className={`sidebar ${open ? "" : "sidebar-collapsed"} ${mobileOpen ? "sidebar-mobile-open" : ""}`}
      >
        <div className="sidebar-brand">
          <div className="brand-mark"><Activity size={19} /></div>
          {open && (
            <div>
              <strong>Web Test Agent</strong>
              <span>Desktop</span>
            </div>
          )}
          <button
            className="icon-button mobile-close"
            onClick={onCloseMobile}
            title="关闭会话列表"
          >
            <X size={18} />
          </button>
        </div>

        <button className="new-chat-button" onClick={onNewThread} title="新建对话">
          <Plus size={18} />
          {open && <span>新建对话</span>}
        </button>

        {open && (
          <div className="history-section">
            <div className="section-label">
              <span>历史对话</span>
              <button
                className={`icon-button small ${historyLoading ? "spin" : ""}`}
                onClick={() => void onRefreshThreads()}
                title="刷新历史对话"
              >
                <RefreshCw size={14} />
              </button>
            </div>
            <div className="thread-list">
              {threads.map((thread) => (
                <button
                  className={`thread-item ${thread.thread_id === selectedThreadId ? "selected" : ""}`}
                  key={thread.thread_id}
                  onClick={() => onSelectThread(thread.thread_id)}
                >
                  <MessageSquare size={15} />
                  <span className="thread-copy">
                    <strong>{threadTitle(thread)}</strong>
                    <span>
                      {thread.status === "busy"
                        ? "正在运行"
                        : formatThreadTime(thread.updated_at)}
                    </span>
                  </span>
                  {thread.status === "busy" && <span className="busy-dot" title="正在运行" />}
                </button>
              ))}
              {!historyLoading && threads.length === 0 && (
                <div className="history-empty">暂无历史对话</div>
              )}
              {hasMoreThreads && (
                <button
                  className="history-more"
                  type="button"
                  onClick={() => void onLoadMoreThreads()}
                  disabled={historyLoading}
                >
                  {historyLoading ? "加载中" : "加载更多"}
                </button>
              )}
            </div>
          </div>
        )}

        <div className="sidebar-footer">
          <button className="sidebar-action" onClick={onOpenSettings} title="客户端设置">
            <Settings size={17} />
            {open && <span>设置</span>}
          </button>
          <button
            className="sidebar-action"
            onClick={() => void onShowLog()}
            title="查看后端日志"
          >
            <TerminalSquare size={17} />
            {open && <span>后端日志</span>}
          </button>
        </div>
      </aside>
      {mobileOpen && (
        <button className="sidebar-scrim" onClick={onCloseMobile} aria-label="关闭会话列表" />
      )}
    </>
  );
}

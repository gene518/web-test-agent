import Ansi from "ansi-to-react";
import { FolderOpen, Palette, RefreshCw, Settings, TerminalSquare, X } from "lucide-react";
import type { ClientConfigDraft } from "../lib/client-state";
import type { BackendStatus } from "../lib/types";
import { BackendBadge } from "./BackendStatus";

export type LogTheme = "macos" | "dark" | "light";

export const LOG_THEME_STORAGE_KEY = "web-test-agent.log-theme.v1";
export const LOG_THEME_OPTIONS: readonly { value: LogTheme; label: string }[] = [
  { value: "macos", label: "macOS 控制台" },
  { value: "dark", label: "深色" },
  { value: "light", label: "浅色" },
];

export function loadLogTheme(): LogTheme {
  try {
    const stored = localStorage.getItem(LOG_THEME_STORAGE_KEY);
    return LOG_THEME_OPTIONS.some((theme) => theme.value === stored)
      ? (stored as LogTheme)
      : "macos";
  } catch {
    return "macos";
  }
}

type SettingsModalProps = {
  open: boolean;
  draft: ClientConfigDraft;
  portError: string | null;
  backend: BackendStatus;
  busy: boolean;
  onDraftChange: (draft: ClientConfigDraft) => void;
  onChooseRoot: () => void | Promise<void>;
  onSave: () => void | Promise<void>;
  onClose: () => void;
};

export function SettingsModal({
  open,
  draft,
  portError,
  backend,
  busy,
  onDraftChange,
  onChooseRoot,
  onSave,
  onClose,
}: SettingsModalProps) {
  if (!open) return null;

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div><Settings size={19} /><h2 id="settings-title">客户端设置</h2></div>
          <button className="icon-button" onClick={onClose} title="关闭设置"><X size={18} /></button>
        </header>
        <div className="settings-content">
          <label>
            <span>项目根目录</span>
            <div className="path-input">
              <input value={draft.projectRoot} readOnly placeholder="请选择仓库根目录" />
              <button
                className="icon-button"
                onClick={() => void onChooseRoot()}
                title="选择项目根目录"
              >
                <FolderOpen size={18} />
              </button>
            </div>
          </label>
          <label>
            <span>后端端口</span>
            <input
              type="number"
              min={1024}
              max={65535}
              value={draft.backendPort}
              aria-invalid={Boolean(portError)}
              aria-describedby={portError ? "backend-port-error" : undefined}
              onChange={(event) =>
                onDraftChange({ ...draft, backendPort: event.target.value })
              }
            />
            {portError && (
              <span className="field-error" id="backend-port-error">{portError}</span>
            )}
          </label>
          <div className="settings-status">
            <BackendBadge status={backend} />
            <span>{backend.message || backend.apiUrl}</span>
          </div>
        </div>
        <footer>
          <button className="secondary-button" onClick={onClose}>取消</button>
          <button
            className="primary-button"
            onClick={() => void onSave()}
            disabled={busy || !draft.projectRoot.trim() || Boolean(portError)}
          >
            <RefreshCw size={16} className={busy ? "spin" : ""} />
            {busy ? "启动中" : "保存并重启"}
          </button>
        </footer>
      </section>
    </div>
  );
}

type LogModalProps = {
  open: boolean;
  content: string;
  theme: LogTheme;
  onThemeChange: (theme: LogTheme) => void;
  onRefresh: () => void | Promise<void>;
  onClose: () => void;
};

export function LogModal({
  open,
  content,
  theme,
  onThemeChange,
  onRefresh,
  onClose,
}: LogModalProps) {
  if (!open) return null;

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="modal log-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="log-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div><TerminalSquare size={19} /><h2 id="log-title">后端日志</h2></div>
          <div className="modal-header-actions">
            <label className="log-theme-picker">
              <Palette size={15} aria-hidden="true" />
              <select
                aria-label="日志颜色主题"
                value={theme}
                onChange={(event) => onThemeChange(event.target.value as LogTheme)}
              >
                {LOG_THEME_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </label>
            <button className="icon-button" onClick={() => void onRefresh()} title="刷新日志">
              <RefreshCw size={17} />
            </button>
            <button className="icon-button" onClick={onClose} title="关闭日志"><X size={18} /></button>
          </div>
        </header>
        <div className={`log-content log-theme-${theme}`} role="log" aria-label="后端日志内容">
          <Ansi useClasses>{content}</Ansi>
        </div>
      </section>
    </div>
  );
}

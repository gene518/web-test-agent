import { isTauri } from "@tauri-apps/api/core";
import { AlertTriangle, FolderOpen, RefreshCw } from "lucide-react";
import type { BackendStatus } from "../lib/types";

export function BackendBadge({ status }: { status: BackendStatus }) {
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

type BackendBannerProps = {
  status: BackendStatus;
  busy: boolean;
  onChooseRoot: () => unknown | Promise<unknown>;
  onRestart: () => unknown | Promise<unknown>;
};

export function BackendBanner({
  status,
  busy,
  onChooseRoot,
  onRestart,
}: BackendBannerProps) {
  if (status.state === "running") return null;

  const isPending = status.state === "starting" || status.state === "checking";

  return (
    <div className={`backend-banner banner-${status.state}`}>
      <div>
        {isPending ? (
          <RefreshCw className="spin" size={18} />
        ) : (
          <AlertTriangle size={18} />
        )}
        <span>{status.message || "本地后端尚未就绪。"}</span>
      </div>
      {isTauri() && !status.projectRoot && (
        <button onClick={() => void onChooseRoot()} disabled={busy}>
          <FolderOpen size={16} />选择项目目录
        </button>
      )}
      {isTauri() && status.projectRoot && !isPending && (
        <button onClick={() => void onRestart()} disabled={busy}>
          <RefreshCw size={16} />重新启动
        </button>
      )}
    </div>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  Download,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  XCircle,
} from "lucide-react";
import {
  checkForUpdate,
  readUpdateOperation,
  shortRevision,
  startUpdate,
  updatePhaseLabel,
  type UpdateInfo,
  type UpdateOperation,
} from "../lib/update";

const UPDATE_CHECK_INTERVAL_MS = 30 * 60 * 1_000;
const OPERATION_POLL_INTERVAL_MS = 2_000;

type UpdateBadgeProps = {
  enabled: boolean;
};

export function UpdateBadge({ enabled }: UpdateBadgeProps) {
  const [open, setOpen] = useState(false);
  const [checking, setChecking] = useState(false);
  const [applying, setApplying] = useState(false);
  const [info, setInfo] = useState<UpdateInfo>();
  const [operation, setOperation] = useState<UpdateOperation>();
  const [error, setError] = useState<string>();
  const pollingRef = useRef<number | undefined>(undefined);
  const controlRef = useRef<HTMLDivElement>(null);

  const check = useCallback(async (force = false) => {
    if (!enabled) return;
    setChecking(true);
    setError(undefined);
    try {
      const next = await checkForUpdate(force);
      setInfo(next);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setChecking(false);
    }
  }, [enabled]);

  const pollOperation = useCallback((operationId: string) => {
    if (pollingRef.current !== undefined) window.clearTimeout(pollingRef.current);
    const poll = async () => {
      try {
        const next = await readUpdateOperation(operationId);
        setOperation(next);
        if (next.status === "succeeded") {
          window.setTimeout(() => window.location.reload(), 1_000);
          return;
        }
        if (next.status === "failed" || next.status === "rolled_back") return;
      } catch {
        // 服务重建期间连接短暂失败是预期状态，继续轮询同一个 operation id。
      }
      pollingRef.current = window.setTimeout(poll, OPERATION_POLL_INTERVAL_MS);
    };
    void poll();
  }, []);

  useEffect(() => {
    if (info?.operation_id && !operation) {
      pollOperation(info.operation_id);
    }
  }, [info?.operation_id, operation, pollOperation]);

  useEffect(() => {
    if (!enabled) return;
    void check();
    const interval = window.setInterval(() => void check(), UPDATE_CHECK_INTERVAL_MS);
    const onFocus = () => void check();
    window.addEventListener("focus", onFocus);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener("focus", onFocus);
      if (pollingRef.current !== undefined) window.clearTimeout(pollingRef.current);
    };
  }, [check, enabled]);

  useEffect(() => {
    if (!open) return;
    const closeOnPointerDown = (event: PointerEvent) => {
      if (!controlRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", closeOnPointerDown);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnPointerDown);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  if (!enabled) return null;

  const updating = applying || Boolean(
    operation && !["succeeded", "failed", "rolled_back"].includes(operation.status),
  );
  const hasUpdate = Boolean(info?.has_update);
  const phase = updatePhaseLabel(operation);

  return (
    <div className="update-control" ref={controlRef}>
      <button
        className={`update-badge ${hasUpdate ? "update-badge-available" : ""}`}
        type="button"
        title={hasUpdate ? "有可用更新" : "查看版本"}
        aria-label={hasUpdate ? "有可用更新" : "查看版本"}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-controls="update-popover"
        onClick={() => setOpen((value) => !value)}
      >
        {updating || checking
          ? <LoaderCircle className="spin" size={14} />
          : <Download size={14} />}
        <span>{shortRevision(info?.current_revision)}</span>
        {hasUpdate && <span className="update-dot" aria-hidden="true" />}
      </button>

      {open && (
        <div id="update-popover" className="update-popover" role="dialog" aria-label="应用更新">
          <div className="update-popover-header">
            <strong>版本更新</strong>
            <button
              className="icon-button small"
              type="button"
              title="重新检查"
              aria-label="重新检查更新"
              disabled={checking || Boolean(updating)}
              onClick={() => void check(true)}
            >
              <RefreshCw className={checking ? "spin" : ""} size={14} />
            </button>
          </div>

          <div className="update-version-row">
            <span>当前</span>
            <code>{shortRevision(info?.current_revision)}</code>
          </div>
          <div className="update-version-row">
            <span>最新</span>
            <code>{shortRevision(info?.latest_revision)}</code>
          </div>

          {(phase || applying) && (
            <div
              className={`update-state update-state-${operation?.status ?? "queued"}`}
              role="status"
              aria-live="polite"
            >
              {operation?.status === "succeeded" ? <Check size={15} /> :
                operation?.status === "rolled_back" ? <RotateCcw size={15} /> :
                  operation?.status === "failed" ? <XCircle size={15} /> :
                    <LoaderCircle className="spin" size={15} />}
              <span>{phase || "正在准备更新"}</span>
            </div>
          )}

          {(error || operation?.error || operation?.rollback_error) && (
            <p className="update-error" role="alert">
              {error || operation?.rollback_error || operation?.error}
            </p>
          )}

          {hasUpdate && !updating && operation?.status !== "succeeded" && (
            <button
              className="update-apply-button"
              type="button"
              disabled={applying}
              onClick={() => {
                setError(undefined);
                setApplying(true);
                void startUpdate()
                  .then((next) => {
                    setOperation(next);
                    pollOperation(next.operation_id);
                  })
                  .catch((cause) => setError(
                    cause instanceof Error ? cause.message : String(cause),
                  ))
                  .finally(() => setApplying(false));
              }}
            >
              <Download size={15} />
              立即更新
            </button>
          )}
        </div>
      )}
    </div>
  );
}

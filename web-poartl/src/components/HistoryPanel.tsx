import { portalStore, type PortalState } from '../state/portalStore';
import type { PortalSessionSummary } from '../types/portal';

interface HistoryPanelProps {
  state: PortalState;
}

export function HistoryPanel({ state }: HistoryPanelProps) {
  return (
    <aside className="panel history-panel">
      <header className="panel-header">
        <span className="eyebrow">历史会话</span>
        <strong>{state.history.length}</strong>
      </header>
      <div className="history-list">
        {state.history.map((item) => (
          <HistoryItem
            key={item.sessionId}
            item={item}
            selected={item.sessionId === state.selectedSessionId}
            active={item.sessionId === state.activeSessionId}
          />
        ))}
      </div>
    </aside>
  );
}

interface HistoryItemProps {
  item: PortalSessionSummary;
  selected: boolean;
  active: boolean;
}

function HistoryItem({ item, selected, active }: HistoryItemProps) {
  return (
    <button className={`history-item ${selected ? 'selected' : ''}`} type="button" onClick={() => void portalStore.selectSession(item.sessionId)}>
      <span className="history-topline">
        <strong>{item.title}</strong>
        <span className={`status-pill ${item.status}`}>{active ? '当前' : statusText(item.status)}</span>
      </span>
      <span>{item.projectName ?? '未关联项目'}</span>
      <small>{formatTime(item.updatedAt)}</small>
      {item.lastAssistantSummary ? <p>{item.lastAssistantSummary}</p> : null}
    </button>
  );
}

function statusText(status: PortalSessionSummary['status']) {
  const map: Record<PortalSessionSummary['status'], string> = {
    idle: '空闲',
    running: '运行中',
    waiting_input: '待补参',
    completed: '已完成',
    failed: '失败',
  };
  return map[status];
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
}


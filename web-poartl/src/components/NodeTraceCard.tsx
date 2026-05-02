import type { ModelEvent, NodeTrace, ToolEvent } from '../types/portal';

interface NodeTraceCardProps {
  trace: NodeTrace;
}

export function NodeTraceCard({ trace }: NodeTraceCardProps) {
  return (
    <details className={`trace-card ${trace.status}`} open={trace.status === 'running'}>
      <summary>
        <span className="node-name">{trace.nodeName}</span>
        <span className={`status-pill ${trace.status}`}>{statusText(trace.status)}</span>
      </summary>
      <div className="trace-body">
        <div className="trace-grid">
          <span>开始</span>
          <strong>{trace.startedAt ? formatTime(trace.startedAt) : '-'}</strong>
          <span>结束</span>
          <strong>{trace.finishedAt ? formatTime(trace.finishedAt) : '-'}</strong>
        </div>
        {trace.routingReason ? <TraceText title="决策原因" value={trace.routingReason} /> : null}
        {trace.detail ? <TraceText title="节点摘要" value={trace.detail} /> : null}
        <EventList title="模型调用" events={trace.modelEvents} />
        <EventList title="工具调用" events={trace.toolEvents} />
      </div>
    </details>
  );
}

function EventList({ title, events }: { title: string; events: Array<ModelEvent | ToolEvent> }) {
  if (events.length === 0) {
    return null;
  }
  return (
    <section className="trace-events">
      <h4>{title}</h4>
      {events.map((event) => (
        <div key={event.eventId} className={`trace-event ${event.status}`}>
          <span>
            {event.name} · {statusText(event.status)}
          </span>
          <TraceText title="输入" value={event.inputSummary} />
          <TraceText title="输出" value={event.outputSummary} />
          <TraceText title="错误" value={event.errorSummary} />
        </div>
      ))}
    </section>
  );
}

function TraceText({ title, value }: { title: string; value?: string | null }) {
  if (!value) {
    return null;
  }
  return (
    <label className="trace-text">
      <span>{title}</span>
      <code>{value}</code>
    </label>
  );
}

function statusText(status: NodeTrace['status']) {
  const map: Record<NodeTrace['status'], string> = {
    running: '运行中',
    completed: '完成',
    failed: '失败',
  };
  return map[status];
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value));
}


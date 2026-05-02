import { FormEvent, useState } from 'react';
import { portalStore, type PortalState } from '../state/portalStore';
import type { PortalMessage, PortalTurn } from '../types/portal';
import { NodeTraceCard } from './NodeTraceCard';

interface MainPanelProps {
  state: PortalState;
}

export function MainPanel({ state }: MainPanelProps) {
  const snapshot = state.snapshot;
  const readOnly = Boolean(snapshot && snapshot.sessionId !== state.activeSessionId);

  return (
    <section className="main-panel">
      <header className="session-header">
        <div>
          <span className="eyebrow">当前会话</span>
          <h2>{snapshot ? sessionTitle(snapshot.messages) : '初始化中'}</h2>
        </div>
        <div className="session-meta">
          <span className={`connection ${state.connectionStatus}`}>{connectionText(state.connectionStatus)}</span>
          {readOnly ? (
            <button className="ghost-button" type="button" onClick={() => void portalStore.returnToActiveSession()}>
              返回活跃会话
            </button>
          ) : null}
        </div>
      </header>

      {state.error ? <div className="error-banner">{state.error}</div> : null}

      <div className="conversation">
        {state.isBootstrapping ? <div className="empty-state large">正在创建 Portal 会话...</div> : null}
        {snapshot && snapshot.turns.length === 0 ? (
          <div className="welcome-card">
            <span className="eyebrow">从一个任务开始</span>
            <h3>描述你要生成、补全或修复的 Web 自动化测试。</h3>
            <p>未选择项目时，服务端会在执行后尝试识别或创建目标自动化项目，并刷新左侧文件树。</p>
          </div>
        ) : null}
        {snapshot?.turns.map((turn) => (
          <TurnBlock key={turn.turnId} turn={turn} />
        ))}
      </div>

      <Composer state={state} readOnly={readOnly} />
    </section>
  );
}

function TurnBlock({ turn }: { turn: PortalTurn }) {
  return (
    <article className="turn-block">
      <MessageBubble message={turn.userMessage} />
      {turn.nodeTraces.length > 0 ? (
        <div className="trace-stack">
          {turn.nodeTraces.map((trace) => (
            <NodeTraceCard key={trace.traceId} trace={trace} />
          ))}
        </div>
      ) : null}
      {turn.assistantMessage ? <MessageBubble message={turn.assistantMessage} /> : null}
      {turn.error ? <div className="error-banner compact">{turn.error}</div> : null}
    </article>
  );
}

function MessageBubble({ message }: { message: PortalMessage }) {
  return (
    <div className={`message ${message.role}`}>
      <span>{message.role === 'user' ? 'You' : 'Agent'}</span>
      <p>{message.content}</p>
    </div>
  );
}

function Composer({ state, readOnly }: { state: PortalState; readOnly: boolean }) {
  const [value, setValue] = useState('');
  const snapshot = state.snapshot;
  const disabled = readOnly || !snapshot || state.isSending || snapshot.runStatus === 'running';
  const pendingInterrupt = snapshot?.pendingInterrupt;

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const message = value.trim();
    if (!message || disabled) {
      return;
    }
    setValue('');
    void portalStore.sendMessage(message);
  };

  return (
    <form className="composer" onSubmit={handleSubmit}>
      <div className="context-row">
        <span>{snapshot?.selectedFilePath ? `上下文文件：${snapshot.selectedFilePath}` : '未选择上下文文件'}</span>
        {pendingInterrupt ? <strong>等待补参</strong> : null}
      </div>
      <div className="input-row">
        <textarea
          value={value}
          placeholder={placeholder(readOnly, Boolean(pendingInterrupt))}
          onChange={(event) => setValue(event.target.value)}
          disabled={disabled}
          rows={3}
        />
        <button type="submit" disabled={disabled || !value.trim()}>
          {pendingInterrupt ? '提交补参' : '发送'}
        </button>
      </div>
    </form>
  );
}

function sessionTitle(messages: PortalMessage[]) {
  const firstUserMessage = messages.find((message) => message.role === 'user');
  return firstUserMessage ? firstUserMessage.content.slice(0, 42) : '新会话';
}

function placeholder(readOnly: boolean, pendingInterrupt: boolean) {
  if (readOnly) {
    return '历史会话只读。返回当前活跃会话后继续输入。';
  }
  if (pendingInterrupt) {
    return '补充缺失参数，例如项目名、URL、计划文件或脚本路径。';
  }
  return '输入自动化测试任务，例如：为 demo 项目的登录页生成测试计划...';
}

function connectionText(status: PortalState['connectionStatus']) {
  const map: Record<PortalState['connectionStatus'], string> = {
    idle: '未连接',
    connecting: '连接中',
    open: '实时连接',
    closed: '已断开',
    error: '连接异常',
  };
  return map[status];
}


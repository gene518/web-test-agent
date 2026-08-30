import { useEffect, useMemo, useRef } from "react";
import {
  Activity,
  Bot,
  Check,
  ChevronDown,
  Clock3,
  FolderOpen,
  RefreshCw,
  UserRound,
  Wrench,
} from "lucide-react";
import { stageSummaryBaseDir, stageSummarySegments } from "../lib/artifact-links";
import {
  buildToolInvocations,
  extractInterruptQuestion,
  messageText,
  toolsForMessage,
  type CanonicalMessage,
  type ToolInvocation,
} from "../lib/message-utils";
import { AGENT_INTRO, PROMPT_TEMPLATES } from "../lib/prompt-templates";

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
  onOpenPath,
}: {
  message: CanonicalMessage;
  tools: ToolInvocation[];
  onOpenPath: (path: string, baseDir?: string) => void;
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
  const summaryBaseDir = message.type === "ai" ? stageSummaryBaseDir(text) : undefined;
  const summarySegments =
    message.type === "ai"
      ? stageSummarySegments(text)
      : [{ type: "text" as const, value: text }];

  return (
    <article className={`timeline-message timeline-${message.type}`}>
      <div className="message-avatar" aria-hidden="true">
        {message.type === "human" ? <UserRound size={16} /> : <Bot size={17} />}
      </div>
      <div className="message-body">
        <div className="message-role">{message.type === "human" ? "你" : "Agent"}</div>
        {text && (
          <div className="message-content">
            {summarySegments.map((segment, index) =>
              segment.type === "path" ? (
                <button
                  className="artifact-path-link"
                  type="button"
                  key={`${segment.value}-${index}`}
                  title="在系统文件管理器中显示"
                  aria-label={`在文件管理器中打开 ${segment.value}`}
                  onClick={() => onOpenPath(segment.value, summaryBaseDir)}
                >
                  <FolderOpen size={13} aria-hidden="true" />
                  <span>{segment.value}</span>
                </button>
              ) : (
                segment.value
              ),
            )}
          </div>
        )}
        {tools.length > 0 && (
          <div className="tool-list">
            {tools.map((tool) => <ToolRow item={tool} key={tool.id} />)}
          </div>
        )}
      </div>
    </article>
  );
}

type MessageTimelineProps = {
  messages: CanonicalMessage[];
  interrupt: unknown;
  isLoading: boolean;
  isThreadLoading: boolean;
  onPromptTemplate: (content: string) => void;
  onOpenPath: (path: string, baseDir?: string) => void;
};

export function MessageTimeline({
  messages,
  interrupt,
  isLoading,
  isThreadLoading,
  onPromptTemplate,
  onOpenPath,
}: MessageTimelineProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null);
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

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({
      behavior: isLoading ? "auto" : "smooth",
      block: "end",
    });
  }, [messages, isLoading, interrupt]);

  return (
    <div className="message-viewport">
      <div className="message-column">
        {isThreadLoading ? (
          <div className="empty-state history-hydrating" aria-label="正在加载对话">
            <RefreshCw className="spin" size={22} />
            <p>正在加载对话...</p>
          </div>
        ) : messages.length === 0 && !interrupt ? (
          <div className="empty-state">
            <div className="empty-icon"><Bot size={25} /></div>
            <h1>开始一个测试任务</h1>
            <p>{AGENT_INTRO}</p>
            <div className="prompt-examples">
              {PROMPT_TEMPLATES.map((template) => (
                <button
                  key={template.id}
                  type="button"
                  onClick={() => onPromptTemplate(template.content)}
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
                onOpenPath={onOpenPath}
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
            {isLoading && (
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
  );
}

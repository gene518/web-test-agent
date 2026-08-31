import {
  useCallback,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Activity,
  ArrowDown,
  Bot,
  Check,
  ChevronDown,
  Clock3,
  File,
  FileCode2,
  FileText,
  Folder,
  LoaderCircle,
  RefreshCw,
  UserRound,
  Wrench,
} from "lucide-react";
import {
  stageSummaryBaseDir,
  stageSummarySegments,
  type ArtifactKind,
  type StageSummarySegment,
} from "../lib/artifact-links";
import {
  buildToolInvocations,
  extractInterruptQuestion,
  messageText,
  toolsForMessage,
  type CanonicalMessage,
  type ToolInvocation,
} from "../lib/message-utils";
import { AGENT_INTRO, PROMPT_TEMPLATES } from "../lib/prompt-templates";
import {
  groupMessagesIntoTurns,
  initialVisibleTurnIndex,
  isTimelineNearBottom,
  previousVisibleTurnIndex,
  TIMELINE_TOP_THRESHOLD,
} from "../lib/timeline";

const TIMELINE_SCROLL_DURATION = 260;
const TIMELINE_SCROLL_CACHE_LIMIT = 32;
const CODE_FILE = /\.(?:css|html?|jsx?|json|mjs|mts|tsx?|ya?ml)(?::\d+(?::\d+)?)?$/i;
const TEXT_FILE = /\.(?:log|md|txt)(?::\d+(?::\d+)?)?$/i;

type TimelineAnchor = {
  key: string;
  offset: number;
};

type TimelineScrollSnapshot = TimelineAnchor & {
  visibleStart: number;
  following: boolean;
};

const timelineScrollCache = new Map<string, TimelineScrollSnapshot>();

function cachedTimelineState(threadKey: string): TimelineScrollSnapshot | undefined {
  const snapshot = timelineScrollCache.get(threadKey);
  if (!snapshot) return undefined;
  timelineScrollCache.delete(threadKey);
  timelineScrollCache.set(threadKey, snapshot);
  return snapshot;
}

function cacheTimelineState(threadKey: string, snapshot: TimelineScrollSnapshot): void {
  timelineScrollCache.delete(threadKey);
  timelineScrollCache.set(threadKey, snapshot);
  while (timelineScrollCache.size > TIMELINE_SCROLL_CACHE_LIMIT) {
    const oldest = timelineScrollCache.keys().next().value;
    if (typeof oldest !== "string") break;
    timelineScrollCache.delete(oldest);
  }
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

function ArtifactIcon({ kind, path }: { kind: ArtifactKind; path: string }) {
  if (kind === "directory") return <Folder size={14} aria-hidden="true" />;
  if (CODE_FILE.test(path)) return <FileCode2 size={14} aria-hidden="true" />;
  if (TEXT_FILE.test(path)) return <FileText size={14} aria-hidden="true" />;
  return <File size={14} aria-hidden="true" />;
}

function ArtifactPathLink({
  segment,
  baseDir,
  onOpenPath,
}: {
  segment: Extract<StageSummarySegment, { type: "path" }>;
  baseDir?: string;
  onOpenPath: (path: string, baseDir?: string) => void | Promise<void>;
}) {
  const [opening, setOpening] = useState(false);
  const kindLabel = segment.kindHint === "directory"
    ? "文件夹"
    : segment.kindHint === "file"
      ? "文件"
      : "路径";

  const open = async () => {
    if (opening) return;
    setOpening(true);
    try {
      await onOpenPath(segment.value, baseDir);
    } finally {
      setOpening(false);
    }
  };

  return (
    <button
      className={`artifact-path-link artifact-path-${segment.kindHint}`}
      type="button"
      title={`在系统文件管理器中显示：${segment.value}`}
      aria-label={`在系统文件管理器中显示${kindLabel} ${segment.value}`}
      aria-busy={opening}
      disabled={opening}
      onClick={() => void open()}
    >
      <span className="artifact-path-icon">
        {opening ? (
          <LoaderCircle className="spin" size={14} aria-hidden="true" />
        ) : (
          <ArtifactIcon kind={segment.kindHint} path={segment.value} />
        )}
      </span>
      <span className="artifact-path-label">{segment.label}</span>
    </button>
  );
}

function TimelineMessage({
  message,
  tools,
  onOpenPath,
}: {
  message: CanonicalMessage;
  tools: ToolInvocation[];
  onOpenPath: (path: string, baseDir?: string) => void | Promise<void>;
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
                <ArtifactPathLink
                  key={`${segment.value}-${index}`}
                  segment={segment}
                  baseDir={summaryBaseDir}
                  onOpenPath={onOpenPath}
                />
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

export type MessageTimelineProps = {
  messages: CanonicalMessage[];
  interrupt: unknown;
  isLoading: boolean;
  isThreadLoading: boolean;
  threadKey?: string;
  onPromptTemplate: (content: string) => void;
  onOpenPath: (path: string, baseDir?: string) => void | Promise<void>;
};

type MessageTimelineContentProps = Omit<MessageTimelineProps, "threadKey"> & {
  threadKey: string;
};

function MessageTimelineContent({
  messages,
  interrupt,
  isLoading,
  isThreadLoading,
  threadKey,
  onPromptTemplate,
  onOpenPath,
}: MessageTimelineContentProps) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const columnRef = useRef<HTMLDivElement>(null);
  const animationFrameRef = useRef<number | undefined>(undefined);
  const touchYRef = useRef<number | undefined>(undefined);
  const pendingAnchorRef = useRef<TimelineAnchor | undefined>(undefined);
  const currentAnchorRef = useRef<TimelineAnchor | undefined>(undefined);
  const loadingOlderRef = useRef(false);
  const initialPositionedRef = useRef(false);
  const cachedStateRef = useRef(cachedTimelineState(threadKey));
  const toolInvocations = useMemo(() => buildToolInvocations(messages), [messages]);
  const turns = useMemo(() => groupMessagesIntoTurns(messages), [messages]);
  const latestHumanKey = useMemo(() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if (message.type === "human") return String(message.id ?? `human-${index}`);
    }
    return undefined;
  }, [messages]);
  const previousHumanKeyRef = useRef(latestHumanKey);
  const [visibleStart, setVisibleStart] = useState(() =>
    turns.length > 0
      ? initialVisibleTurnIndex(turns.length, cachedStateRef.current?.visibleStart)
      : cachedStateRef.current?.visibleStart ?? 0,
  );
  const visibleStartRef = useRef(visibleStart);
  const [following, setFollowing] = useState(cachedStateRef.current?.following ?? true);
  const followingRef = useRef(following);
  const userDetachedRef = useRef(cachedStateRef.current?.following === false);
  const lastScrollTopRef = useRef(0);

  const linkedToolIds = useMemo(
    () =>
      new Set(
        messages
          .filter((message) => message.type === "ai")
          .flatMap((message) => toolsForMessage(message, toolInvocations).map((tool) => tool.id)),
      ),
    [messages, toolInvocations],
  );

  const setFollowingState = useCallback((next: boolean) => {
    followingRef.current = next;
    setFollowing((current) => (current === next ? current : next));
  }, []);

  const cancelScrollAnimation = useCallback(() => {
    if (animationFrameRef.current !== undefined) {
      window.cancelAnimationFrame(animationFrameRef.current);
      animationFrameRef.current = undefined;
    }
  }, []);

  const findAnchor = useCallback((): TimelineAnchor | undefined => {
    const viewport = viewportRef.current;
    const column = columnRef.current;
    if (!viewport || !column) return undefined;
    const viewportTop = viewport.getBoundingClientRect().top;
    const elements = Array.from(column.querySelectorAll<HTMLElement>("[data-turn-key]"));
    const element = elements.find((candidate) => candidate.getBoundingClientRect().bottom > viewportTop);
    if (!element?.dataset.turnKey) return undefined;
    return {
      key: element.dataset.turnKey,
      offset: element.getBoundingClientRect().top - viewportTop,
    };
  }, []);

  const findTurn = useCallback((key: string): HTMLElement | undefined => {
    const elements = columnRef.current?.querySelectorAll<HTMLElement>("[data-turn-key]");
    return elements ? Array.from(elements).find((element) => element.dataset.turnKey === key) : undefined;
  }, []);

  const rememberPosition = useCallback(() => {
    const anchor = findAnchor();
    if (!anchor) return;
    currentAnchorRef.current = anchor;
    cacheTimelineState(threadKey, {
      ...anchor,
      visibleStart: visibleStartRef.current,
      following: followingRef.current,
    });
  }, [findAnchor, threadKey]);

  const scrollToBottomImmediately = useCallback(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    viewport.scrollTop = Math.max(0, viewport.scrollHeight - viewport.clientHeight);
    lastScrollTopRef.current = viewport.scrollTop;
    userDetachedRef.current = false;
    setFollowingState(true);
    currentAnchorRef.current = findAnchor();
  }, [findAnchor, setFollowingState]);

  const detachFromLatest = useCallback(() => {
    cancelScrollAnimation();
    userDetachedRef.current = true;
    setFollowingState(false);
  }, [cancelScrollAnimation, setFollowingState]);

  const loadOlderTurns = useCallback(() => {
    if (loadingOlderRef.current || visibleStartRef.current <= 0) return;
    const anchor = findAnchor();
    if (!anchor) return;
    loadingOlderRef.current = true;
    pendingAnchorRef.current = anchor;
    const nextStart = previousVisibleTurnIndex(visibleStartRef.current);
    visibleStartRef.current = nextStart;
    setVisibleStart(nextStart);
  }, [findAnchor]);

  const handleScroll = useCallback(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    if (viewport.scrollTop <= TIMELINE_TOP_THRESHOLD) loadOlderTurns();
    const movedTowardLatest = viewport.scrollTop > lastScrollTopRef.current;
    const nearBottom = isTimelineNearBottom(viewport);
    if (nearBottom && (!userDetachedRef.current || movedTowardLatest)) {
      userDetachedRef.current = false;
      setFollowingState(true);
    } else {
      setFollowingState(false);
    }
    lastScrollTopRef.current = viewport.scrollTop;
    rememberPosition();
  }, [loadOlderTurns, rememberPosition, setFollowingState]);

  const scrollToLatest = useCallback(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    cancelScrollAnimation();
    userDetachedRef.current = false;
    setFollowingState(true);
    const start = viewport.scrollTop;
    const target = Math.max(0, viewport.scrollHeight - viewport.clientHeight);
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduceMotion || Math.abs(target - start) < 1) {
      scrollToBottomImmediately();
      rememberPosition();
      return;
    }

    const startedAt = window.performance.now();
    const animate = (now: number) => {
      const progress = Math.min(1, (now - startedAt) / TIMELINE_SCROLL_DURATION);
      const eased = 1 - (1 - progress) ** 3;
      viewport.scrollTop = start + (target - start) * eased;
      if (progress < 1) {
        animationFrameRef.current = window.requestAnimationFrame(animate);
      } else {
        animationFrameRef.current = undefined;
        scrollToBottomImmediately();
        rememberPosition();
      }
    };
    animationFrameRef.current = window.requestAnimationFrame(animate);
  }, [cancelScrollAnimation, rememberPosition, scrollToBottomImmediately, setFollowingState]);

  useLayoutEffect(() => {
    const pendingAnchor = pendingAnchorRef.current;
    if (!pendingAnchor) return;
    const viewport = viewportRef.current;
    const element = findTurn(pendingAnchor.key);
    if (viewport && element) {
      const currentOffset = element.getBoundingClientRect().top - viewport.getBoundingClientRect().top;
      viewport.scrollTop += currentOffset - pendingAnchor.offset;
    }
    currentAnchorRef.current = pendingAnchor;
    pendingAnchorRef.current = undefined;
    rememberPosition();
    window.requestAnimationFrame(() => {
      loadingOlderRef.current = false;
    });
  }, [findTurn, rememberPosition, visibleStart]);

  useLayoutEffect(() => {
    if (isThreadLoading || initialPositionedRef.current || turns.length === 0) return;
    const cached = cachedStateRef.current;
    const targetStart = initialVisibleTurnIndex(turns.length, cached?.visibleStart);
    if (visibleStartRef.current !== targetStart) {
      visibleStartRef.current = targetStart;
      setVisibleStart(targetStart);
      return;
    }

    const viewport = viewportRef.current;
    const cachedElement = cached && !cached.following ? findTurn(cached.key) : undefined;
    if (viewport && cached && !cached.following && cachedElement) {
      const currentOffset = cachedElement.getBoundingClientRect().top - viewport.getBoundingClientRect().top;
      viewport.scrollTop += currentOffset - cached.offset;
      lastScrollTopRef.current = viewport.scrollTop;
      currentAnchorRef.current = cached;
      userDetachedRef.current = true;
      setFollowingState(false);
    } else {
      scrollToBottomImmediately();
    }
    initialPositionedRef.current = true;
    rememberPosition();
  }, [
    findTurn,
    isThreadLoading,
    rememberPosition,
    scrollToBottomImmediately,
    setFollowingState,
    turns.length,
    visibleStart,
  ]);

  useLayoutEffect(() => {
    if (latestHumanKey === previousHumanKeyRef.current) return;
    const hadHumanMessage = previousHumanKeyRef.current !== undefined;
    previousHumanKeyRef.current = latestHumanKey;
    if (!latestHumanKey || !hadHumanMessage) return;
    const nextStart = initialVisibleTurnIndex(turns.length);
    visibleStartRef.current = nextStart;
    setVisibleStart(nextStart);
    setFollowingState(true);
  }, [latestHumanKey, setFollowingState, turns.length]);

  useLayoutEffect(() => {
    if (!initialPositionedRef.current || !followingRef.current || isThreadLoading) return;
    scrollToBottomImmediately();
    rememberPosition();
  }, [
    interrupt,
    isLoading,
    isThreadLoading,
    messages,
    rememberPosition,
    scrollToBottomImmediately,
    visibleStart,
  ]);

  useLayoutEffect(() => {
    const column = columnRef.current;
    if (!column || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      const viewport = viewportRef.current;
      if (!viewport || !initialPositionedRef.current) return;
      if (followingRef.current) {
        scrollToBottomImmediately();
        return;
      }
      const anchor = currentAnchorRef.current;
      const element = anchor ? findTurn(anchor.key) : undefined;
      if (!anchor || !element) return;
      const currentOffset = element.getBoundingClientRect().top - viewport.getBoundingClientRect().top;
      const delta = currentOffset - anchor.offset;
      if (Math.abs(delta) >= 1) viewport.scrollTop += delta;
    });
    observer.observe(column);
    return () => observer.disconnect();
  }, [findTurn, scrollToBottomImmediately]);

  useLayoutEffect(() => {
    return () => {
      cancelScrollAnimation();
      rememberPosition();
    };
  }, [cancelScrollAnimation, rememberPosition]);

  const visibleTurns = turns.slice(visibleStart);

  return (
    <div className="message-viewport-shell">
      <div
        ref={viewportRef}
        className="message-viewport"
        aria-label="对话消息"
        tabIndex={0}
        onScroll={handleScroll}
        onWheel={(event) => {
          if (event.deltaY < 0 && event.currentTarget.scrollHeight > event.currentTarget.clientHeight) {
            detachFromLatest();
          }
        }}
        onTouchStart={(event) => {
          touchYRef.current = event.touches[0]?.clientY;
        }}
        onTouchMove={(event) => {
          const nextY = event.touches[0]?.clientY;
          if (nextY !== undefined && touchYRef.current !== undefined && nextY > touchYRef.current) {
            if (event.currentTarget.scrollHeight > event.currentTarget.clientHeight) {
              detachFromLatest();
            }
          }
          touchYRef.current = nextY;
        }}
        onKeyDown={(event) => {
          if (["ArrowUp", "PageUp", "Home"].includes(event.key) || (event.key === " " && event.shiftKey)) {
            if (event.currentTarget.scrollHeight > event.currentTarget.clientHeight) {
              detachFromLatest();
            }
          }
        }}
      >
        <div ref={columnRef} className="message-column">
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
              {visibleTurns.map((turn) => (
                <div className="timeline-turn" data-turn-key={turn.key} key={turn.key}>
                  {turn.messages.map(({ message, sourceIndex }) => (
                    <TimelineMessage
                      key={message.id ?? `${message.type}-${sourceIndex}`}
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
                </div>
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
        </div>
      </div>
      {!following && (messages.length > 0 || Boolean(interrupt)) && (
        <button
          className="timeline-to-latest"
          type="button"
          title="回到最新消息"
          aria-label="回到最新消息"
          onClick={scrollToLatest}
        >
          <ArrowDown size={17} aria-hidden="true" />
          {isLoading && <span className="timeline-to-latest-running" aria-hidden="true" />}
        </button>
      )}
    </div>
  );
}

export function MessageTimeline({ threadKey = "new-thread", ...props }: MessageTimelineProps) {
  return <MessageTimelineContent key={threadKey} threadKey={threadKey} {...props} />;
}

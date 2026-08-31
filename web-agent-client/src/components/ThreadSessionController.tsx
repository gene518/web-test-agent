import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Client, Message } from "@langchain/langgraph-sdk";
import { useStream } from "@langchain/langgraph-sdk/react";
import { activeRunIds } from "../lib/session-actions";
import { buildSubmitRequest } from "../lib/session-actions";
import { cancellationFailureMessages } from "../lib/client-state";
import { errorMessage } from "../lib/errors";
import { conversationMessages, mergeMessages, type CanonicalMessage } from "../lib/message-utils";
import {
  actionableInterrupt,
  checkpointInterrupt,
  isActiveRunPhase,
  type ActionableInterrupt,
  type RunPhase,
} from "../lib/thread-runtime";
import { ASSISTANT_ID, STREAM_MODES, type AgentState } from "../lib/types";

type DisplayMessagesEvent = { type: "display_messages"; messages: unknown[] };
const EMPTY_AGENT_STATE: AgentState = Object.freeze({});

export type ThreadSessionSnapshot = {
  sessionKey: string;
  threadId: string | null;
  values: AgentState;
  messages: CanonicalMessage[];
  interrupt?: ActionableInterrupt;
  phase: RunPhase;
  runId?: string;
  notice?: string;
  isThreadLoading: boolean;
  checkedRuns: boolean;
};

export type ThreadSessionHandle = {
  submit: (text: string, existingThreadTitle?: string) => boolean;
  cancel: () => Promise<void>;
  clearNotice: () => void;
};

type ThreadSessionControllerProps = {
  sessionKey: string;
  threadId: string | null;
  client: Client;
  enabled: boolean;
  initialValues?: AgentState;
  onThreadBound: (sessionKey: string, threadId: string) => void;
  onSnapshot: (snapshot: ThreadSessionSnapshot) => void;
  onRegister: (sessionKey: string, handle?: ThreadSessionHandle) => void;
  onRefreshThreads: () => void;
  onRestoreDraft: (sessionKey: string, text: string) => void;
};

function isDisplayMessagesEvent(value: unknown): value is DisplayMessagesEvent {
  return typeof value === "object" &&
    value !== null &&
    "type" in value &&
    (value as { type?: unknown }).type === "display_messages" &&
    Array.isArray((value as { messages?: unknown }).messages);
}

export function ThreadSessionController({
  sessionKey,
  threadId,
  client,
  enabled,
  initialValues,
  onThreadBound,
  onSnapshot,
  onRegister,
  onRefreshThreads,
  onRestoreDraft,
}: ThreadSessionControllerProps) {
  const [phase, setPhaseState] = useState<RunPhase>("idle");
  const [runId, setRunId] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [checkedRuns, setCheckedRuns] = useState(threadId === null);
  const [finishedInterrupt, setFinishedInterrupt] = useState<{
    known: boolean;
    value?: ActionableInterrupt;
  }>({ known: false });
  const [reconnectRevision, setReconnectRevision] = useState(0);
  const phaseRef = useRef<RunPhase>(phase);
  const threadIdRef = useRef(threadId);
  const interruptRef = useRef<ActionableInterrupt | undefined>(undefined);
  const pendingTextRef = useRef<string | undefined>(undefined);
  const joiningRef = useRef(false);
  const discoveryCompleteRef = useRef(threadId === null);
  const knownRunIdsRef = useRef(new Set<string>());
  const reconnectAttemptRef = useRef(0);
  const reconnectTimerRef = useRef<number | undefined>(undefined);

  threadIdRef.current = threadId;

  const setPhase = useCallback((next: RunPhase) => {
    phaseRef.current = next;
    setPhaseState(next);
  }, []);

  const markRunning = useCallback(() => {
    if (phaseRef.current === "queued" || phaseRef.current === "submitting") {
      setPhase("running");
    }
  }, [setPhase]);

  const scheduleReconnect = useCallback(() => {
    if (reconnectTimerRef.current !== undefined) return;
    const attempt = reconnectAttemptRef.current;
    reconnectAttemptRef.current += 1;
    reconnectTimerRef.current = window.setTimeout(() => {
      reconnectTimerRef.current = undefined;
      setReconnectRevision((current) => current + 1);
    }, Math.min(1_000 * 2 ** attempt, 8_000));
  }, []);

  const stream = useStream<
    AgentState,
    { UpdateType: AgentState; CustomEventType: DisplayMessagesEvent }
  >({
    assistantId: ASSISTANT_ID,
    client,
    threadId,
    messagesKey: "display_messages",
    fetchStateHistory: false,
    initialValues,
    onThreadId: (id) => onThreadBound(sessionKey, id),
    onCreated: (run) => {
      discoveryCompleteRef.current = true;
      knownRunIdsRef.current.add(run.run_id);
      setRunId(run.run_id);
      setPhase("queued");
      window.setTimeout(onRefreshThreads, 250);
    },
    onMetadataEvent: markRunning,
    onUpdateEvent: markRunning,
    onToolEvent: markRunning,
    onCustomEvent: (event, options) => {
      markRunning();
      if (!isDisplayMessagesEvent(event)) return;
      options.mutate((previous) => ({
        ...previous,
        display_messages: mergeMessages(
          previous.display_messages ?? previous.messages ?? [],
          event.messages,
        ) as Message[],
      }));
    },
    onFinish: (state, run) => {
      joiningRef.current = false;
      discoveryCompleteRef.current = true;
      if (run) knownRunIdsRef.current.delete(run.run_id);
      reconnectAttemptRef.current = 0;
      if (reconnectTimerRef.current !== undefined) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = undefined;
      }
      const remainingRunId = knownRunIdsRef.current.values().next().value as string | undefined;
      setRunId(remainingRunId);
      setNotice((current) => (
        current?.startsWith("恢复执行流失败") ? undefined : current
      ));
      const finalInterrupt = checkpointInterrupt(state);
      interruptRef.current = finalInterrupt;
      setFinishedInterrupt({ known: true, value: finalInterrupt });
      setPhase(
        finalInterrupt
          ? "awaiting_input"
          : remainingRunId
            ? "running"
            : "idle",
      );
      if (run) window.setTimeout(onRefreshThreads, 250);
    },
    onError: (error) => {
      const wasJoining = joiningRef.current;
      joiningRef.current = false;
      if (wasJoining) discoveryCompleteRef.current = false;
      setRunId(undefined);
      setPhase("failed");
      if (wasJoining) {
        setNotice(`恢复执行流失败，将自动重试：${errorMessage(error)}`);
        scheduleReconnect();
      } else {
        setNotice(`Agent 执行失败：${errorMessage(error)}`);
        if (pendingTextRef.current) onRestoreDraft(sessionKey, pendingTextRef.current);
      }
      window.setTimeout(onRefreshThreads, 250);
    },
  });

  const streamInterrupt = actionableInterrupt(stream.interrupt);
  // The SDK can retain an earlier stream interrupt after a final checkpoint lands.
  // Once a run has finished, the checkpoint is authoritative until the next submit.
  const interrupt = finishedInterrupt.known
    ? finishedInterrupt.value
    : streamInterrupt;
  interruptRef.current = interrupt;
  const streamValues = stream.values;
  const values = Object.keys(streamValues).length > 0
    ? streamValues
    : initialValues ?? EMPTY_AGENT_STATE;
  const messages = useMemo(
    () => conversationMessages(values),
    [values],
  );

  useEffect(() => {
    if (stream.isLoading) return;
    if (interrupt && !isActiveRunPhase(phaseRef.current)) {
      setPhase("awaiting_input");
    } else if (!interrupt && phaseRef.current === "awaiting_input") {
      setPhase("idle");
    }
  }, [interrupt, setPhase, stream.isLoading]);

  useEffect(() => () => {
    if (reconnectTimerRef.current !== undefined) {
      window.clearTimeout(reconnectTimerRef.current);
    }
  }, []);

  useEffect(() => {
    discoveryCompleteRef.current = threadId === null;
    knownRunIdsRef.current.clear();
    setCheckedRuns(threadId === null);
    setFinishedInterrupt({ known: false });
  }, [threadId]);

  useEffect(() => {
    if (
      !enabled ||
      !threadId ||
      stream.isLoading ||
      stream.isThreadLoading ||
      joiningRef.current ||
      discoveryCompleteRef.current ||
      reconnectTimerRef.current !== undefined ||
      isActiveRunPhase(phaseRef.current)
    ) {
      return;
    }
    let cancelled = false;

    const reconnect = async () => {
      try {
        const [running, pending] = await Promise.all([
          client.runs.list(threadId, { limit: 1, status: "running" }),
          client.runs.list(threadId, { limit: 1, status: "pending" }),
        ]);
        if (cancelled) return;
        discoveryCompleteRef.current = true;
        setCheckedRuns(true);
        knownRunIdsRef.current = new Set(
          [...running, ...pending].map((candidate) => candidate.run_id),
        );
        const run = running[0] ?? pending[0];
        if (!run) {
          if (phaseRef.current !== "failed" && !interruptRef.current) setPhase("idle");
          return;
        }
        joiningRef.current = true;
        setRunId(run.run_id);
        setPhase(running[0] ? "running" : "queued");
        await stream.joinStream(run.run_id, undefined, { streamMode: [...STREAM_MODES] });
      } catch (error) {
        if (cancelled) return;
        joiningRef.current = false;
        discoveryCompleteRef.current = false;
        setCheckedRuns(true);
        setPhase("failed");
        setNotice(`恢复执行流失败，将自动重试：${errorMessage(error)}`);
        scheduleReconnect();
      }
    };
    void reconnect();
    return () => {
      cancelled = true;
    };
  }, [
    client,
    enabled,
    reconnectRevision,
    scheduleReconnect,
    setPhase,
    stream.isLoading,
    stream.isThreadLoading,
    threadId,
  ]);

  const submit = useCallback((text: string, existingThreadTitle?: string): boolean => {
    if (!text.trim() || isActiveRunPhase(phaseRef.current) || stream.isThreadLoading) return false;
    const request = buildSubmitRequest(text, {
      interrupted: Boolean(interruptRef.current),
      existingThreadTitle,
    });
    pendingTextRef.current = text;
    setNotice(undefined);
    setFinishedInterrupt({ known: false });
    setPhase("submitting");
    void stream.submit(request.values as AgentState, {
      ...(request.options as Parameters<typeof stream.submit>[1]),
      optimisticValues: (previous) => ({
        ...previous,
        ...request.values,
        messages: [...(previous.messages ?? []), request.message],
        display_messages: [
          ...(previous.display_messages ?? previous.messages ?? []),
          request.message,
        ],
      }),
    }).catch((error) => {
      setPhase("failed");
      setNotice(`发送失败：${errorMessage(error)}`);
      onRestoreDraft(sessionKey, text);
    }).finally(() => {
      pendingTextRef.current = undefined;
    });
    return true;
  }, [onRestoreDraft, sessionKey, setPhase, stream]);

  const cancel = useCallback(async () => {
    const targetThreadId = threadIdRef.current;
    if (!targetThreadId || phaseRef.current === "cancelling") return;
    const previousPhase = phaseRef.current;
    setPhase("cancelling");
    setNotice(undefined);
    try {
      const [running, pending] = await Promise.all([
        client.runs.list(targetThreadId, { limit: 20, status: "running" }),
        client.runs.list(targetThreadId, { limit: 20, status: "pending" }),
      ]);
      const ids = activeRunIds(running, pending, runId);
      const results = await Promise.allSettled(
        ids.map((id) => client.runs.cancel(targetThreadId, id, true, "interrupt")),
      );
      const failures = cancellationFailureMessages(results);
      if (failures.length > 0) {
        knownRunIdsRef.current = new Set(
          ids.filter((_, index) => results[index]?.status === "rejected"),
        );
        setRunId(knownRunIdsRef.current.values().next().value as string | undefined);
        throw new Error(`${failures.length}/${ids.length} 个运行取消失败：${failures.join("；")}`);
      }
      await stream.stop();
      knownRunIdsRef.current.clear();
      setRunId(undefined);
      setPhase("idle");
      onRefreshThreads();
    } catch (error) {
      setPhase(knownRunIdsRef.current.size > 0 ? "running" : previousPhase);
      setNotice(`取消任务失败：${errorMessage(error)}`);
    }
  }, [client, onRefreshThreads, runId, setPhase, stream]);

  const handle = useMemo<ThreadSessionHandle>(() => ({
    submit,
    cancel,
    clearNotice: () => setNotice(undefined),
  }), [cancel, submit]);

  useEffect(() => {
    onRegister(sessionKey, handle);
    return () => onRegister(sessionKey, undefined);
  }, [handle, onRegister, sessionKey]);

  const snapshot = useMemo<ThreadSessionSnapshot>(() => ({
    sessionKey,
    threadId,
    values,
    messages,
    interrupt,
    phase,
    runId,
    notice,
    isThreadLoading: stream.isThreadLoading,
    checkedRuns,
  }), [
    checkedRuns,
    interrupt,
    messages,
    notice,
    phase,
    runId,
    sessionKey,
    stream.isThreadLoading,
    threadId,
    values,
  ]);

  useEffect(() => onSnapshot(snapshot), [onSnapshot, snapshot]);
  return null;
}

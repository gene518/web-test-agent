import { validate as isUuid, v4 as uuidv4 } from "uuid";
import {
  ReactNode,
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";
import { useStreamContext } from "@/providers/useStreamContext";
import { Button } from "../ui/button";
import { Checkpoint, Message } from "@langchain/langgraph-sdk";
import { AssistantMessage, AssistantMessageLoading } from "./messages/ai";
import { HumanMessage } from "./messages/human";
import {
  DO_NOT_RENDER_ID_PREFIX,
  ensureToolCallsHaveResponses,
} from "@/lib/ensure-tool-responses";
import { LangGraphLogoSVG } from "../icons/langgraph";
import { TooltipIconButton } from "./tooltip-icon-button";
import {
  ArrowDown,
  LoaderCircle,
  PanelRightOpen,
  PanelRightClose,
  SquarePen,
  XIcon,
  Plus,
} from "lucide-react";
import { useQueryState, parseAsBoolean } from "nuqs";
import { StickToBottom, useStickToBottomContext } from "use-stick-to-bottom";
import ThreadHistory from "./history";
import { toast } from "sonner";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { Label } from "../ui/label";
import { GitHubSVG } from "../icons/github";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../ui/tooltip";
import { useFileUpload } from "@/hooks/use-file-upload";
import { ContentBlocksPreview } from "./ContentBlocksPreview";
import {
  useArtifactOpen,
  ArtifactContent,
  ArtifactTitle,
  useArtifactContext,
} from "./artifact";
import {
  filterConversationMessages,
  getActiveInterrupt,
  getHumanMessages,
  getLastLiveRenderSignal,
  mergeVisibleMessages,
  THREAD_STREAM_MODES,
} from "./message-utils";
import { isAgentInboxInterruptSchema } from "@/lib/agent-inbox-interrupt";
import {
  buildResumeSubmitKey,
  tryLockResumeSubmit,
  unlockResumeSubmit,
} from "./resume-submit-guard";
import {
  clearActiveThreadRun,
  clearExplicitNewThreadRequested,
  getActiveThreadRunId,
  getLatestActiveRun,
  markExplicitNewThreadRequested,
} from "@/lib/thread-session";
import { summarizeThreadRequestTitle } from "@/lib/thread-title";
import { CONTINUE_ON_DISCONNECT_RUN_OPTIONS } from "@/lib/run-submit-options";

function isThreadNotFoundError(message: string | undefined): boolean {
  if (!message) {
    return false;
  }

  return (
    /Thread with ID .+ not found/i.test(message) ||
    /HTTP 404:.*Thread with ID .+ not found/i.test(message)
  );
}

function getThreadMetadataForAssistant(
  assistantId: string,
): { graph_id: string } | { assistant_id: string } {
  return isUuid(assistantId)
    ? { assistant_id: assistantId }
    : { graph_id: assistantId };
}

function StickyToBottomContent(props: {
  content: ReactNode;
  footer?: ReactNode;
  className?: string;
  contentClassName?: string;
}) {
  const context = useStickToBottomContext();
  return (
    <div
      ref={context.scrollRef}
      style={{ width: "100%", height: "100%" }}
      className={props.className}
    >
      <div
        ref={context.contentRef}
        className={props.contentClassName}
      >
        {props.content}
      </div>

      {props.footer}
    </div>
  );
}

function ScrollToBottom(props: { className?: string }) {
  const { isAtBottom, scrollToBottom } = useStickToBottomContext();

  if (isAtBottom) return null;
  return (
    <Button
      variant="outline"
      className={props.className}
      onClick={() => scrollToBottom()}
    >
      <ArrowDown className="h-4 w-4" />
      <span>滚动到底部</span>
    </Button>
  );
}

function OpenGitHubRepo() {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <a
            href="https://github.com/langchain-ai/agent-chat-ui"
            target="_blank"
            className="flex items-center justify-center"
          >
            <GitHubSVG
              width="24"
              height="24"
            />
          </a>
        </TooltipTrigger>
        <TooltipContent side="left">
          <p>打开 GitHub 仓库</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export function Thread() {
  const [artifactContext, setArtifactContext] = useArtifactContext();
  const [artifactOpen, closeArtifact] = useArtifactOpen();

  const [threadId, _setThreadId] = useQueryState("threadId");
  const [chatHistoryOpen, setChatHistoryOpen] = useQueryState(
    "chatHistoryOpen",
    parseAsBoolean.withDefault(false),
  );
  const [input, setInput] = useState("");
  const {
    contentBlocks,
    setContentBlocks,
    handleFileUpload,
    dropRef,
    removeBlock,
    resetBlocks: _resetBlocks,
    dragOver,
    handlePaste,
  } = useFileUpload();
  const [firstTokenReceived, setFirstTokenReceived] = useState(false);
  const isLargeScreen = useMediaQuery("(min-width: 1024px)");

  const stream = useStreamContext();
  const displayMessages = stream.values.display_messages;
  const stateHumanMessages = useMemo(
    () => getHumanMessages(stream.values.messages),
    [stream.values.messages],
  );
  const persistedMessages = useMemo(
    () =>
      mergeVisibleMessages(
        filterConversationMessages(displayMessages),
        stateHumanMessages,
      ),
    [displayMessages, stateHumanMessages],
  );
  const visibleLiveMessages = useMemo(
    () => filterConversationMessages(stream.messages),
    [stream.messages],
  );
  const messages = useMemo(
    () =>
      persistedMessages === visibleLiveMessages
        ? visibleLiveMessages
        : mergeVisibleMessages(persistedMessages, visibleLiveMessages),
    [persistedMessages, visibleLiveMessages],
  );
  const isLoading = stream.isLoading;
  const [cancelSubmitting, setCancelSubmitting] = useState(false);
  const [genericResumeSubmitting, setGenericResumeSubmitting] = useState(false);
  const pendingGenericResumeKeyRef = useRef<{
    key: string;
    sawLoading: boolean;
  } | null>(null);
  const activeInterrupt = getActiveInterrupt(stream.values, stream.interrupt);
  const activeGenericInterrupt =
    activeInterrupt && !isAgentInboxInterruptSchema(activeInterrupt)
      ? activeInterrupt
      : undefined;
  const hasGenericInterrupt = !!activeGenericInterrupt;
  const lastLiveRenderSignal = getLastLiveRenderSignal(visibleLiveMessages);

  const lastError = useRef<string | undefined>(undefined);

  const setThreadId = (id: string | null) => {
    if (id) {
      clearExplicitNewThreadRequested();
    } else {
      markExplicitNewThreadRequested();
    }
    _setThreadId(id);

    // 关闭 artifact 并重置 artifact 上下文。
    closeArtifact();
    setArtifactContext({});
  };

  const clearPendingGenericResume = useCallback(() => {
    const pending = pendingGenericResumeKeyRef.current;
    if (!pending) {
      return;
    }
    unlockResumeSubmit(pending.key);
    pendingGenericResumeKeyRef.current = null;
    setGenericResumeSubmitting(false);
  }, []);

  useEffect(() => {
    const pending = pendingGenericResumeKeyRef.current;
    if (!pending) {
      return;
    }

    if (isLoading) {
      pending.sawLoading = true;
      return;
    }

    if (pending.sawLoading || !hasGenericInterrupt) {
      clearPendingGenericResume();
    }
  }, [clearPendingGenericResume, hasGenericInterrupt, isLoading]);

  useEffect(() => {
    return () => {
      clearPendingGenericResume();
    };
  }, [clearPendingGenericResume]);

  useEffect(() => {
    if (!stream.error) {
      lastError.current = undefined;
      return;
    }
    try {
      const message = (stream.error as any).message;
      if (!message || lastError.current === message) {
        // 消息已经记录过，不再修改 ref，直接返回。
        return;
      }
      if (isThreadNotFoundError(message)) {
        return;
      }

      // 消息已定义且尚未记录，先保存再发送错误。
      lastError.current = message;
      toast.error("发生错误，请重试。", {
        description: (
          <p>
            <strong>错误：</strong> <code>{message}</code>
          </p>
        ),
        richColors: true,
        closeButton: true,
      });
    } catch {
      // 无需处理。
    }
  }, [stream.error]);

  // TODO: 这段逻辑应并入 useStream hook。
  const prevLiveRenderSignal = useRef("");
  useEffect(() => {
    if (!isLoading) {
      prevLiveRenderSignal.current = lastLiveRenderSignal;
      return;
    }

    if (
      lastLiveRenderSignal &&
      lastLiveRenderSignal !== prevLiveRenderSignal.current
    ) {
      setFirstTokenReceived(true);
    }

    prevLiveRenderSignal.current = lastLiveRenderSignal;
  }, [isLoading, lastLiveRenderSignal]);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (
      (input.trim().length === 0 && contentBlocks.length === 0) ||
      isLoading ||
      genericResumeSubmitting
    )
      return;
    setFirstTokenReceived(false);

    const newHumanMessage: Message = {
      id: uuidv4(),
      type: "human",
      content: [
        ...(input.trim().length > 0 ? [{ type: "text", text: input }] : []),
        ...contentBlocks,
      ] as Message["content"],
    };

    const toolMessages = ensureToolCallsHaveResponses(
      stream.values.messages ?? [],
    );
    const threadTitle = !threadId ? summarizeThreadRequestTitle(input) : "";

    const context =
      Object.keys(artifactContext).length > 0 ? artifactContext : undefined;

    if (hasGenericInterrupt) {
      const resumeText = input.trim();
      if (contentBlocks.length > 0) {
        toast.error("缺参补全只支持文本回复。", {
          richColors: true,
          closeButton: true,
        });
        return;
      }

      const resumeKey = buildResumeSubmitKey({
        threadId,
        interrupt: activeGenericInterrupt,
        text: resumeText,
      });
      if (!tryLockResumeSubmit(resumeKey)) {
        toast.info("补参正在提交，请稍候。", {
          richColors: true,
          closeButton: true,
        });
        return;
      }
      pendingGenericResumeKeyRef.current = {
        key: resumeKey,
        sawLoading: false,
      };
      setGenericResumeSubmitting(true);

      try {
        stream.submit(
          {},
          {
            command: {
              resume: {
                text: resumeText,
              },
            },
            multitaskStrategy: "reject",
            streamMode: [...THREAD_STREAM_MODES],
            streamSubgraphs: true,
            ...CONTINUE_ON_DISCONNECT_RUN_OPTIONS,
            optimisticValues: (prev) => ({
              ...prev,
              messages: [...(prev.messages ?? []), newHumanMessage],
              display_messages: [
                ...(prev.display_messages ?? prev.messages ?? []),
                newHumanMessage,
              ],
            }),
          },
        );
      } catch (error) {
        clearPendingGenericResume();
        console.error("提交补参回复失败", error);
        toast.error("提交补参回复失败。", {
          richColors: true,
          closeButton: true,
        });
        return;
      }

      setInput("");
      setContentBlocks([]);
      return;
    }

    stream.submit(
      { messages: [...toolMessages, newHumanMessage], context },
      {
        ...(threadTitle
          ? {
              metadata: {
                ...getThreadMetadataForAssistant(stream.assistantId),
                thread_title: threadTitle,
              },
            }
          : {
              metadata: getThreadMetadataForAssistant(stream.assistantId),
            }),
        streamMode: [...THREAD_STREAM_MODES],
        streamSubgraphs: true,
        ...CONTINUE_ON_DISCONNECT_RUN_OPTIONS,
        optimisticValues: (prev) => ({
          ...prev,
          context,
          requested_pipeline: [],
          pipeline_cursor: 0,
          pending_stage_summaries: [],
          completed_stage_summaries: [],
          final_summary: "",
          messages: [
            ...(prev.messages ?? []),
            ...toolMessages,
            newHumanMessage,
          ],
          display_messages: [
            ...(prev.display_messages ?? prev.messages ?? []),
            ...toolMessages,
            newHumanMessage,
          ],
        }),
      },
    );

    setInput("");
    setContentBlocks([]);
  };

  const handleRegenerate = (
    parentCheckpoint: Checkpoint | null | undefined,
  ) => {
    setFirstTokenReceived(false);
    stream.submit(undefined, {
      checkpoint: parentCheckpoint,
      streamMode: [...THREAD_STREAM_MODES],
      streamSubgraphs: true,
      ...CONTINUE_ON_DISCONNECT_RUN_OPTIONS,
    });
  };

  const handleCancelRun = useCallback(() => {
    if (cancelSubmitting) {
      return;
    }

    setCancelSubmitting(true);
    const latestActiveRun = getLatestActiveRun();
    const currentThreadId = threadId ?? latestActiveRun?.threadId ?? null;
    const currentRunId =
      (threadId ? getActiveThreadRunId(threadId) : null) ??
      (latestActiveRun?.threadId === currentThreadId
        ? latestActiveRun.runId
        : null);

    if (!currentThreadId) {
      stream.stop();
      setCancelSubmitting(false);
      return;
    }

    const cancelServerRuns = async () => {
      try {
        const activeRunIds = new Set<string>();
        if (currentRunId) {
          activeRunIds.add(currentRunId);
        }

        try {
          const runningRuns = await stream.client.runs.list(currentThreadId, {
            limit: 10,
            status: "running",
            select: ["run_id", "status"],
          });
          const pendingRuns = await stream.client.runs.list(currentThreadId, {
            limit: 10,
            status: "pending",
            select: ["run_id", "status"],
          });
          const activeRuns = [...runningRuns, ...pendingRuns].filter(
            (run, index, runs) =>
              runs.findIndex((candidate) => candidate.run_id === run.run_id) ===
              index,
          );
          activeRuns.forEach((run) => activeRunIds.add(run.run_id));
        } catch (error) {
          console.error("查询当前执行失败，改用已记录的 run 取消。", error);
        }

        if (activeRunIds.size === 0) {
          stream.stop();
          return;
        }

        await Promise.allSettled(
          [...activeRunIds].map((runId) =>
            stream.client.runs.cancel(
              currentThreadId,
              runId,
              true,
              "interrupt",
            ),
          ),
        );
        activeRunIds.forEach((runId) =>
          clearActiveThreadRun(currentThreadId, runId),
        );
      } catch (error) {
        console.error("取消当前执行失败", error);
        toast.error("取消当前执行失败。", {
          richColors: true,
          closeButton: true,
        });
      } finally {
        stream.stop();
        setCancelSubmitting(false);
      }
    };

    void cancelServerRuns();
  }, [cancelSubmitting, stream, threadId]);

  const chatStarted = !!threadId || !!messages.length;
  const hasNoAIOrToolMessages = !messages.find(
    (m) => m.type === "ai" || m.type === "tool",
  );
  const lastVisibleMessageId = messages[messages.length - 1]?.id;
  const lastVisibleMessageType = messages[messages.length - 1]?.type;
  const shouldRenderStandaloneInterrupt =
    !!activeInterrupt &&
    (hasNoAIOrToolMessages ||
      !lastVisibleMessageType ||
      lastVisibleMessageType === "human");

  return (
    <div className="flex h-screen w-full overflow-hidden">
      <div className="relative hidden lg:flex">
        <motion.div
          className="absolute z-20 h-full overflow-hidden border-r bg-white"
          style={{ width: 300 }}
          animate={
            isLargeScreen
              ? { x: chatHistoryOpen ? 0 : -300 }
              : { x: chatHistoryOpen ? 0 : -300 }
          }
          initial={{ x: -300 }}
          transition={
            isLargeScreen
              ? { type: "spring", stiffness: 300, damping: 30 }
              : { duration: 0 }
          }
        >
          <div
            className="relative h-full"
            style={{ width: 300 }}
          >
            <ThreadHistory />
          </div>
        </motion.div>
      </div>

      <div
        className={cn(
          "grid w-full grid-cols-[1fr_0fr] transition-all duration-500",
          artifactOpen && "grid-cols-[3fr_2fr]",
        )}
      >
        <motion.div
          className={cn(
            "relative flex min-w-0 flex-1 flex-col overflow-hidden",
            !chatStarted && "grid-rows-[1fr]",
          )}
          layout={isLargeScreen}
          animate={{
            marginLeft: chatHistoryOpen ? (isLargeScreen ? 300 : 0) : 0,
            width: chatHistoryOpen
              ? isLargeScreen
                ? "calc(100% - 300px)"
                : "100%"
              : "100%",
          }}
          transition={
            isLargeScreen
              ? { type: "spring", stiffness: 300, damping: 30 }
              : { duration: 0 }
          }
        >
          {!chatStarted && (
            <div className="absolute top-0 left-0 z-10 flex w-full items-center justify-between gap-3 p-2 pl-4">
              <div>
                {(!chatHistoryOpen || !isLargeScreen) && (
                  <Button
                    className="hover:bg-gray-100"
                    variant="ghost"
                    onClick={() => setChatHistoryOpen((p) => !p)}
                  >
                    {chatHistoryOpen ? (
                      <PanelRightOpen className="size-5" />
                    ) : (
                      <PanelRightClose className="size-5" />
                    )}
                  </Button>
                )}
              </div>
              <div className="absolute top-2 right-4 flex items-center">
                <OpenGitHubRepo />
              </div>
            </div>
          )}
          {chatStarted && (
            <div className="relative z-10 flex items-center justify-between gap-3 p-2">
              <div className="relative flex items-center justify-start gap-2">
                <div className="absolute left-0 z-10">
                  {(!chatHistoryOpen || !isLargeScreen) && (
                    <Button
                      className="hover:bg-gray-100"
                      variant="ghost"
                      onClick={() => setChatHistoryOpen((p) => !p)}
                    >
                      {chatHistoryOpen ? (
                        <PanelRightOpen className="size-5" />
                      ) : (
                        <PanelRightClose className="size-5" />
                      )}
                    </Button>
                  )}
                </div>
                <motion.button
                  className="flex cursor-pointer items-center gap-2"
                  onClick={() => setThreadId(null)}
                  animate={{
                    marginLeft: !chatHistoryOpen ? 48 : 0,
                  }}
                  transition={{
                    type: "spring",
                    stiffness: 300,
                    damping: 30,
                  }}
                >
                  <LangGraphLogoSVG
                    width={32}
                    height={32}
                  />
                  <span className="text-xl font-semibold tracking-tight">
                    Agent Chat
                  </span>
                </motion.button>
              </div>

              <div className="flex items-center gap-4">
                <div className="flex items-center">
                  <OpenGitHubRepo />
                </div>
                <TooltipIconButton
                  size="lg"
                  className="p-4"
                  tooltip="新建 thread"
                  variant="ghost"
                  onClick={() => setThreadId(null)}
                >
                  <SquarePen className="size-5" />
                </TooltipIconButton>
              </div>

              <div className="from-background to-background/0 absolute inset-x-0 top-full h-5 bg-gradient-to-b" />
            </div>
          )}

          <StickToBottom className="relative flex-1 overflow-hidden">
            <StickyToBottomContent
              className={cn(
                "absolute inset-0 overflow-y-scroll px-4 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-gray-300 [&::-webkit-scrollbar-track]:bg-transparent",
                !chatStarted && "mt-[25vh] flex flex-col items-stretch",
                chatStarted && "grid grid-rows-[1fr_auto]",
              )}
              contentClassName="pt-8 pb-16 max-w-3xl mx-auto flex flex-col gap-4 w-full"
              content={
                <>
                  {messages
                    .filter((m) => !m.id?.startsWith(DO_NOT_RENDER_ID_PREFIX))
                    .map((message, index) =>
                      message.type === "human" ? (
                        <HumanMessage
                          key={message.id || `${message.type}-${index}`}
                          message={message}
                          isLoading={isLoading}
                        />
                      ) : (
                        <AssistantMessage
                          key={message.id || `${message.type}-${index}`}
                          message={message}
                          isLoading={isLoading}
                          interrupt={activeInterrupt}
                          isLastMessage={message.id === lastVisibleMessageId}
                          hasNoAIOrToolMessages={hasNoAIOrToolMessages}
                          handleRegenerate={handleRegenerate}
                        />
                      ),
                    )}
                  {/* interrupt 可能发生在最后一条用户消息之后，此时没有后续 AI/tool 消息承载补参 UI。 */}
                  {shouldRenderStandaloneInterrupt && (
                    <AssistantMessage
                      key="interrupt-msg"
                      message={undefined}
                      isLoading={isLoading}
                      interrupt={activeInterrupt}
                      isLastMessage={true}
                      hasNoAIOrToolMessages={hasNoAIOrToolMessages}
                      handleRegenerate={handleRegenerate}
                    />
                  )}
                  {isLoading && !firstTokenReceived && (
                    <AssistantMessageLoading />
                  )}
                </>
              }
              footer={
                <div className="sticky bottom-0 flex flex-col items-center gap-8 bg-white">
                  {!chatStarted && (
                    <div className="flex items-center gap-3">
                      <LangGraphLogoSVG className="h-8 flex-shrink-0" />
                      <h1 className="text-2xl font-semibold tracking-tight">
                        Agent Chat
                      </h1>
                    </div>
                  )}

                  <ScrollToBottom className="animate-in fade-in-0 zoom-in-95 absolute bottom-full left-1/2 mb-4 -translate-x-1/2" />

                  <div
                    ref={dropRef}
                    className={cn(
                      "bg-muted relative z-10 mx-auto mb-8 w-full max-w-3xl rounded-2xl shadow-xs transition-all",
                      dragOver
                        ? "border-primary border-2 border-dotted"
                        : "border border-solid",
                    )}
                  >
                    <form
                      onSubmit={handleSubmit}
                      className="mx-auto grid max-w-3xl grid-rows-[1fr_auto] gap-2"
                    >
                      <ContentBlocksPreview
                        blocks={contentBlocks}
                        onRemove={removeBlock}
                      />
                      <textarea
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        onPaste={handlePaste}
                        onKeyDown={(e) => {
                          if (
                            e.key === "Enter" &&
                            !e.shiftKey &&
                            !e.metaKey &&
                            !e.nativeEvent.isComposing
                          ) {
                            e.preventDefault();
                            const el = e.target as HTMLElement | undefined;
                            const form = el?.closest("form");
                            form?.requestSubmit();
                          }
                        }}
                        placeholder="输入消息..."
                        className="field-sizing-content resize-none border-none bg-transparent p-3.5 pb-0 shadow-none ring-0 outline-none focus:ring-0 focus:outline-none"
                      />

                      <div className="flex items-center gap-4 p-2 pt-4">
                        <Label
                          htmlFor="file-input"
                          className="flex cursor-pointer items-center gap-2"
                        >
                          <Plus className="size-5 text-gray-600" />
                          <span className="text-sm text-gray-600">
                            Upload PDF or Image
                          </span>
                        </Label>
                        <input
                          id="file-input"
                          type="file"
                          onChange={handleFileUpload}
                          multiple
                          accept="image/jpeg,image/png,image/gif,image/webp,application/pdf"
                          className="hidden"
                        />
                        {stream.isLoading ? (
                          <Button
                            key="stop"
                            type="button"
                            onClick={handleCancelRun}
                            className="ml-auto"
                            disabled={cancelSubmitting}
                          >
                            <LoaderCircle className="h-4 w-4 animate-spin" />
                            取消
                          </Button>
                        ) : (
                          <Button
                            type="submit"
                            className="ml-auto shadow-md transition-all"
                            disabled={
                              isLoading ||
                              genericResumeSubmitting ||
                              (!input.trim() && contentBlocks.length === 0)
                            }
                          >
                            Send
                          </Button>
                        )}
                      </div>
                    </form>
                  </div>
                </div>
              }
            />
          </StickToBottom>
        </motion.div>
        <div className="relative flex flex-col border-l">
          <div className="absolute inset-0 flex min-w-[30vw] flex-col">
            <div className="grid grid-cols-[1fr_auto] border-b p-4">
              <ArtifactTitle className="truncate overflow-hidden" />
              <button
                onClick={closeArtifact}
                className="cursor-pointer"
              >
                <XIcon className="size-5" />
              </button>
            </div>
            <ArtifactContent className="relative flex-grow" />
          </div>
        </div>
      </div>
    </div>
  );
}

import type { Message, Run } from "@langchain/langgraph-sdk";
import { ASSISTANT_ID, STREAM_MODES } from "./types";
import { summarizeThreadTitle } from "./message-utils";

type SubmitRequest = {
  values: Record<string, unknown>;
  options: Record<string, unknown>;
  message: Message;
};

export function buildSubmitRequest(
  text: string,
  options: { interrupted: boolean; newThread: boolean; id?: string },
): SubmitRequest {
  const message: Message = {
    id: options.id ?? crypto.randomUUID(),
    type: "human",
    content: text.trim(),
  };
  const shared = {
    multitaskStrategy: "reject",
    streamMode: [...STREAM_MODES],
    streamSubgraphs: true,
    onDisconnect: "continue",
    streamResumable: true,
  };

  if (options.interrupted) {
    return {
      values: {},
      message,
      options: {
        ...shared,
        command: { resume: { text: text.trim() } },
      },
    };
  }

  return {
    values: { messages: [message] },
    message,
    options: {
      ...shared,
      metadata: {
        graph_id: ASSISTANT_ID,
        ...(options.newThread
          ? { thread_title: summarizeThreadTitle(text) }
          : {}),
      },
    },
  };
}

export function activeRunIds(
  running: Pick<Run, "run_id">[],
  pending: Pick<Run, "run_id">[],
  knownRunId?: string,
): string[] {
  return [knownRunId, ...running.map((run) => run.run_id), ...pending.map((run) => run.run_id)]
    .filter((id): id is string => Boolean(id))
    .filter((id, index, all) => all.indexOf(id) === index);
}

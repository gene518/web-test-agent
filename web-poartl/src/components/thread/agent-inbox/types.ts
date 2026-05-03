import { BaseMessage } from "@langchain/core/messages";
import { Interrupt, Thread, ThreadStatus } from "@langchain/langgraph-sdk";

export type DecisionType = "approve" | "edit" | "reject";

export interface Action {
  name: string;
  args: Record<string, unknown>;
}

export interface ActionRequest {
  name: string;
  args: Record<string, unknown>;
  description?: string;
}

export interface ReviewConfig {
  action_name: string;
  allowed_decisions: DecisionType[];
  args_schema?: Record<string, unknown>;
}

export interface HITLRequest {
  action_requests: ActionRequest[];
  review_configs: ReviewConfig[];
}

export type Decision =
  | { type: "approve" }
  | { type: "reject"; message?: string }
  | { type: "edit"; edited_action: Action };

export type DecisionWithEdits =
  | { type: "approve" }
  | { type: "reject"; message?: string }
  | {
      type: "edit";
      edited_action: Action;
      acceptAllowed?: boolean;
      editsMade?: boolean;
    };

export type Email = {
  id: string;
  thread_id: string;
  from_email: string;
  to_email: string;
  subject: string;
  page_content: string;
  send_time: string | undefined;
  read?: boolean;
  status?: "in-queue" | "processing" | "hitl" | "done";
};

export interface ThreadValues {
  email: Email;
  messages: BaseMessage[];
  triage: {
    logic: string;
    response: string;
  };
}

export type ThreadData<
  ThreadValues extends Record<string, any> = Record<string, any>,
> = {
  thread: Thread<ThreadValues>;
} & (
  | {
      status: "interrupted";
      interrupts: Interrupt<HITLRequest>[] | undefined;
    }
  | {
      status: "idle" | "busy" | "error";
      interrupts?: never;
    }
);

export type ThreadStatusWithAll = ThreadStatus | "all";

export type SubmitType = DecisionType;

export interface AgentInbox {
  /**
   * inbox 的唯一标识。
   */
  id: string;
  /**
   * graph 的 ID。
   */
  graphId: string;
  /**
   * 部署地址。可以是 localhost 地址，也可以是正式部署地址。
   */
  deploymentUrl: string;
  /**
   * inbox 的可选名称，用于在 UI 中标记 inbox。
   */
  name?: string;
  /**
   * inbox 是否处于选中状态。
   */
  selected: boolean;
}

import { useStreamContext } from "@/providers/useStreamContext";
import { END } from "@langchain/langgraph/web";
import { Interrupt } from "@langchain/langgraph-sdk";
import { toast } from "sonner";
import {
  Dispatch,
  KeyboardEvent,
  MutableRefObject,
  SetStateAction,
  useEffect,
  useRef,
  useState,
} from "react";
import { Decision, DecisionWithEdits, HITLRequest, SubmitType } from "../types";
import { buildDecisionFromState, createDefaultHumanResponse } from "../utils";

interface UseInterruptedActionsInput {
  interrupt: Interrupt<HITLRequest>;
}

interface UseInterruptedActionsValue {
  handleSubmit: (
    e: React.MouseEvent<HTMLButtonElement, MouseEvent> | KeyboardEvent,
  ) => Promise<void>;
  handleResolve: (
    e: React.MouseEvent<HTMLButtonElement, MouseEvent>,
  ) => Promise<void>;
  streaming: boolean;
  streamFinished: boolean;
  loading: boolean;
  supportsMultipleMethods: boolean;
  hasEdited: boolean;
  hasAddedResponse: boolean;
  approveAllowed: boolean;
  humanResponse: DecisionWithEdits[];
  selectedSubmitType: SubmitType | undefined;
  setSelectedSubmitType: Dispatch<SetStateAction<SubmitType | undefined>>;
  setHumanResponse: Dispatch<SetStateAction<DecisionWithEdits[]>>;
  setHasAddedResponse: Dispatch<SetStateAction<boolean>>;
  setHasEdited: Dispatch<SetStateAction<boolean>>;
  initialHumanInterruptEditValue: MutableRefObject<Record<string, string>>;
}

export default function useInterruptedActions({
  interrupt,
}: UseInterruptedActionsInput): UseInterruptedActionsValue {
  const thread = useStreamContext();
  const [humanResponse, setHumanResponse] = useState<DecisionWithEdits[]>([]);
  const [loading, setLoading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [streamFinished, setStreamFinished] = useState(false);
  const [selectedSubmitType, setSelectedSubmitType] = useState<SubmitType>();
  const [hasEdited, setHasEdited] = useState(false);
  const [hasAddedResponse, setHasAddedResponse] = useState(false);
  const [approveAllowed, setApproveAllowed] = useState(false);
  const initialHumanInterruptEditValue = useRef<Record<string, string>>({});

  useEffect(() => {
    const hitlValue = interrupt.value as HITLRequest | undefined;
    initialHumanInterruptEditValue.current = {};

    if (!hitlValue) {
      setHumanResponse([]);
      setSelectedSubmitType(undefined);
      setApproveAllowed(false);
      setHasEdited(false);
      setHasAddedResponse(false);
      return;
    }

    try {
      const { responses, defaultSubmitType, hasApprove } =
        createDefaultHumanResponse(hitlValue, initialHumanInterruptEditValue);
      setHumanResponse(responses);
      setSelectedSubmitType(defaultSubmitType);
      setApproveAllowed(hasApprove);
      setHasEdited(false);
      setHasAddedResponse(false);
    } catch (error) {
      console.error("格式化并设置人工回复状态失败", error);
      setHumanResponse([]);
      setSelectedSubmitType(undefined);
      setApproveAllowed(false);
    }
  }, [interrupt]);

  const resumeRun = (decisions: Decision[]): boolean => {
    try {
      thread.submit(
        {},
        {
          command: {
            resume: {
              decisions,
            },
          },
        },
      );
      return true;
    } catch (error) {
      console.error("发送人工回复失败", error);
      return false;
    }
  };

  const handleSubmit = async (
    e: React.MouseEvent<HTMLButtonElement, MouseEvent> | KeyboardEvent,
  ) => {
    e.preventDefault();
    const { decision, error } = buildDecisionFromState(
      humanResponse,
      selectedSubmitType,
    );

    if (!decision) {
      toast.error("错误", {
        description: error ?? "不支持的回复类型。",
        duration: 5000,
        richColors: true,
        closeButton: true,
      });
      return;
    }

    if (error) {
      toast.error("错误", {
        description: error,
        duration: 5000,
        richColors: true,
        closeButton: true,
      });
      return;
    }

    let errorOccurred = false;
    initialHumanInterruptEditValue.current = {};

    try {
      setLoading(true);
      setStreaming(true);

      const resumedSuccessfully = resumeRun([decision]);
      if (!resumedSuccessfully) {
        errorOccurred = true;
        return;
      }

      toast("成功", {
        description: "回复已提交。",
        duration: 5000,
      });

      setStreamFinished(true);
    } catch (error: any) {
      console.error("发送人工回复失败", error);
      errorOccurred = true;

      if ("message" in error && error.message.includes("Invalid assistant")) {
        toast("错误：assistant ID 无效", {
          description:
            "当前 graph 中找不到提供的 assistant ID。请在设置中更新 assistant ID 后重试。",
          richColors: true,
          closeButton: true,
          duration: 5000,
        });
      } else {
        toast.error("错误", {
          description: "提交回复失败。",
          richColors: true,
          closeButton: true,
          duration: 5000,
        });
      }
    } finally {
      setStreaming(false);
      setLoading(false);
      if (errorOccurred) {
        setStreamFinished(false);
      }
    }
  };

  const handleResolve = async (
    e: React.MouseEvent<HTMLButtonElement, MouseEvent>,
  ) => {
    e.preventDefault();
    setLoading(true);
    initialHumanInterruptEditValue.current = {};

    try {
      thread.submit(
        {},
        {
          command: {
            goto: END,
          },
        },
      );

      toast("成功", {
        description: "thread 已标记为已解决。",
        duration: 3000,
      });
    } catch (error) {
      console.error("标记 thread 为已解决失败", error);
      toast.error("错误", {
        description: "标记 thread 为已解决失败。",
        richColors: true,
        closeButton: true,
        duration: 3000,
      });
    } finally {
      setLoading(false);
    }
  };

  const supportsMultipleMethods =
    humanResponse.filter((response) =>
      ["edit", "approve", "reject"].includes(response.type),
    ).length > 1;

  return {
    handleSubmit,
    handleResolve,
    humanResponse,
    selectedSubmitType,
    streaming,
    streamFinished,
    loading,
    supportsMultipleMethods,
    hasEdited,
    hasAddedResponse,
    approveAllowed,
    setSelectedSubmitType,
    setHumanResponse,
    setHasAddedResponse,
    setHasEdited,
    initialHumanInterruptEditValue,
  };
}

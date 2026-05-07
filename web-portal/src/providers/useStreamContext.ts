import { useContext } from "react";
import StreamContext, { type StreamContextType } from "./Stream";

export const useStreamContext = (): StreamContextType => {
  const context = useContext(StreamContext);
  if (context === undefined) {
    throw new Error("useStreamContext 必须在 StreamProvider 内使用");
  }
  return context;
};

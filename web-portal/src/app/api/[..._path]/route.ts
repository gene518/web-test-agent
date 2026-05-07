import { initApiPassthrough } from "langgraph-nextjs-api-passthrough";

// 该文件把请求代理到 LangGraph server。
// 更多说明见上游文档的 Going to Production 部分：
// https://github.com/langchain-ai/agent-chat-ui?tab=readme-ov-file#going-to-production

export const { GET, POST, PUT, PATCH, DELETE, OPTIONS, runtime } =
  initApiPassthrough({
    apiUrl: process.env.LANGGRAPH_API_URL ?? "remove-me", // 未配置时库内部也会尝试读取 LANGGRAPH_API_URL。
    apiKey: process.env.LANGSMITH_API_KEY ?? "remove-me", // 未配置时库内部也会尝试读取 LANGSMITH_API_KEY。
    runtime: "edge", // 使用默认 edge runtime。
  });

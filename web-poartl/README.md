# Web AutoTest Agent Chat UI

此前端用 `langchain-ai/agent-chat-ui` 的上游 Agent Chat UI 替换旧的自定义 Portal UI。

- 上游仓库：https://github.com/langchain-ai/agent-chat-ui
- 引入提交：`5201e3ab6d35b22c86c1aad34acd32d1032e5279`
- 本地默认 LangGraph API：`http://127.0.0.1:2024`
- 本地默认 graph id：`master`

## 安装

使用模板声明的包管理器：

```bash
corepack enable
corepack prepare pnpm@10.5.1 --activate
pnpm install
```

## 启动

先从 `web-agent/` 启动 LangGraph server，再启动前端：

```bash
pnpm dev
```

应用运行在 `http://localhost:3000`。

## 配置

本地默认值已写入前端代码。如需覆盖，复制 `.env.example` 为 `.env` 后修改：

```bash
NEXT_PUBLIC_API_URL=http://127.0.0.1:2024
NEXT_PUBLIC_ASSISTANT_ID=master
NEXT_PUBLIC_AUTH_SCHEME=
```

如果使用部署环境的代理模式，请在 Next.js 服务端环境中设置 `LANGGRAPH_API_URL` 和 `LANGSMITH_API_KEY`。

不要把 `OPENAI_API_KEY` 等模型服务密钥放在本目录。浏览器可见配置必须使用 `NEXT_PUBLIC_` 前缀；服务端密钥应放在 `web-agent/.env` 或部署专用的代理环境中。

# Web AutoTest Agent Portal 调试指南

本仓库包含两部分：

- `web-agent/`: LangGraph + Deep Agents 后端能力，以及 Portal REST/SSE API。
- `web-poartl/`: 独立 Vite React TypeScript Portal 前端。目录名保持 PRD 中的 `web-poartl`。

## 1. 后端环境

```bash
cd /Users/jin/Documents/code/github/web-test-agent
uv sync --project web-agent --extra dev
```

后端读取 `web-agent/.env`。最少需要配置：

```bash
OPENAI_API_KEY=...
OPENAI_BASE_URL=...
MASTER_MODEL=openai:gpt-5.4
SPECIALIST_MODEL=openai:gpt-5.4
DEFAULT_AUTOMATION_PROJECT_ROOT=~/webautotest
```

不要把 `OPENAI_API_KEY`、`LANGSMITH_API_KEY` 或其他服务端密钥放到 `web-poartl/.env`。Vite 只会把 `VITE_` 前缀变量暴露给浏览器端代码。

## 2. 启动 Portal API 后端

```bash
cd /Users/jin/Documents/code/github/web-test-agent/web-agent
uv run --extra dev uvicorn deep_agent.portal.api:app --reload --host 127.0.0.1 --port 8000
```

Portal API 默认地址：

- REST: `http://127.0.0.1:8000/api/portal`
- SSE: `http://127.0.0.1:8000/api/portal/sessions/{sessionId}/stream`
- API docs: `http://127.0.0.1:8000/docs`

会话历史使用 JSON 持久化到 `web-agent/runtime/portal/sessions.json`。运行中的 SSE 连接和 interrupt 状态只保证当前进程内可用；服务重启后前端会通过重新拉取 session snapshot 恢复已持久化历史。

## 3. 启动 Portal 前端

```bash
cd /Users/jin/Documents/code/github/web-test-agent/web-poartl
npm install
npm run dev
```

前端默认地址：

```text
http://127.0.0.1:5173
```

Vite 已配置 `/api` proxy 到 `http://127.0.0.1:8000`。如果要直连其他后端地址，可以创建 `web-poartl/.env.local`：

```bash
VITE_PORTAL_API_BASE=http://127.0.0.1:8000
```

只允许在前端 env 中放无敏感信息的浏览器配置。

## 4. 常用验证

后端测试：

```bash
cd /Users/jin/Documents/code/github/web-test-agent/web-agent
uv run --extra dev pytest tests
```

前端构建：

```bash
cd /Users/jin/Documents/code/github/web-test-agent/web-poartl
npm run build
```

## 5. 端口与 SSE 排障

- 如果 `8000` 被占用，换端口启动后端，并同步设置 `VITE_PORTAL_API_BASE`。
- 如果 `5173` 被占用，使用 `npm run dev -- --port 5174`。
- 如果页面显示实时连接异常，先确认后端 `/api/portal/history` 可访问，再刷新页面或切回当前活跃会话。
- SSE 断开不会删除已完成消息；前端可通过会话详情接口重新拉取快照。

## 6. Portal API 与 LangGraph Studio

Portal API 是面向业务用户的前后端分离入口，负责会话历史、文件树、SSE 事件和三栏 UI。

LangGraph Studio 仍可用于调试原始图执行链路：

```bash
cd /Users/jin/Documents/code/github/web-test-agent/web-agent
tests/debug/dev.sh
```

两者不是同一个入口。Portal 后端通过 `deep_agent.workflow.build_workflow()` 复用现有图能力，但不依赖 LangGraph Studio 接口。

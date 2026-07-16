# MCP Inspector 使用指南

MCP Inspector 是用于查看、测试和调试 MCP Server 的开发工具。它提供网页界面，可以连接 MCP Server、查看 Server 声明的能力，并直接调用 Tools、读取 Resources、测试 Prompts。

本文先介绍 MCP Inspector 的通用用法，最后以 Playwright Test MCP 为示例演示完整调试流程。

## 1. MCP Inspector 是什么

MCP Inspector 由两个本地组件组成：

- **Inspector Client**：浏览器中的调试界面，默认监听 `http://localhost:6274`。
- **Inspector Proxy**：连接网页和 MCP Server 的本地代理，默认监听 `6277` 端口。

Proxy 不是抓包代理。它实际充当 MCP Client，通过以下 transport 之一连接 MCP Server：

- `STDIO`：Inspector 负责启动本地 MCP Server 子进程，通过标准输入和输出通信。
- `Streamable HTTP`：连接已经运行的 HTTP MCP Server。
- `SSE`：连接使用旧版 SSE transport 的 MCP Server。

在网页中通常可以完成以下操作：

- 检查 MCP 初始化结果和 Server capabilities。
- 查看 Tool 名称、说明和输入 Schema，并填写参数调用 Tool。
- 列出和读取 Resources。
- 查看 Prompts，填写参数并获取生成结果。
- 查看调用结果、协议错误和 Server 日志。
- 导出当前 Server 的 `mcp.json` 配置。

## 2. 前置条件

MCP Inspector 通过 npm 发布，不需要全局安装。先确认 Node.js 和 npm 可用：

```bash
node --version
npm --version
```

当前 `@modelcontextprotocol/inspector` 包要求 Node.js `22.7.5` 或更高的 Node.js 22 版本。以后如遇版本兼容问题，以 npm 包和官方文档的最新要求为准。

## 3. 快速启动网页界面

只启动 Inspector，不预先指定 MCP Server：

```bash
npx -y @modelcontextprotocol/inspector
```

启动后终端会打印一个带 session token 的完整地址，并通常自动打开浏览器，例如：

```text
http://localhost:6274/?MCP_PROXY_AUTH_TOKEN=...
```

应使用终端输出的完整地址。只打开 `http://localhost:6274` 可能因为缺少 Proxy Session Token 而无法连接。

## 4. 连接 MCP Server

### 4.1 STDIO Server

通用格式：

```bash
npx -y @modelcontextprotocol/inspector [Inspector参数] \
  <Server启动命令> [Server参数...]
```

调试 Node.js MCP Server：

```bash
npx -y @modelcontextprotocol/inspector node build/index.js
```

调试 Python MCP Server：

```bash
npx -y @modelcontextprotocol/inspector python server.py
```

向 MCP Server 传递环境变量：

```bash
npx -y @modelcontextprotocol/inspector \
  -e API_KEY=your-key \
  -e DEBUG=true \
  node build/index.js
```

如果 Server 参数与 Inspector 参数重名，使用 `--` 明确分隔：

```bash
npx -y @modelcontextprotocol/inspector \
  -e DEBUG=true \
  -- node build/index.js -e server-value
```

STDIO Server 应由 Inspector 拉起，不要先在另一个终端单独启动。执行 Inspector 命令时的当前目录通常也会成为 Server 的工作目录，因此应先进入 MCP Server 所需的项目目录。

### 4.2 Streamable HTTP Server

先启动目标 MCP Server，再启动 Inspector：

```bash
npx -y @modelcontextprotocol/inspector
```

在网页侧栏中：

1. 选择 `Streamable HTTP` transport。
2. 填写 MCP 地址，例如 `http://localhost:3000/mcp`。
3. 如果 Server 需要认证，填写 Bearer Token 或自定义 Header。
4. 点击 `Connect`。

### 4.3 SSE Server

启动 Inspector 后，在网页中选择 `SSE` transport，填写 Server 的 SSE 地址，例如：

```text
http://localhost:3000/sse
```

SSE 主要用于兼容旧版 MCP Server。新 Server 通常优先使用 Streamable HTTP。

## 5. 网页调试流程

连接成功后，建议按以下顺序检查：

1. 查看连接状态和初始化结果，确认 Server 名称、版本及 capabilities。
2. 打开 `Tools`，执行工具列表刷新，确认工具名称和输入 Schema。
3. 选择一个 Tool，填写必填参数后执行，检查返回内容和错误信息。
4. 如果 Server 声明了 Resources，打开 `Resources` 列表并读取具体资源。
5. 如果 Server 声明了 Prompts，打开 `Prompts`，填写参数并获取 Prompt。
6. 查看 Notifications、日志和错误，确认 Server 是否正确处理请求。

不是所有 MCP Server 都会同时提供 Tools、Resources 和 Prompts。某个页面为空不一定是故障，应以 Server 声明的 capabilities 为准。

## 6. 使用配置文件

多个 Server 或启动参数较多时，可以保存为 `mcp.json`：

```json
{
  "mcpServers": {
    "local-server": {
      "type": "stdio",
      "command": "node",
      "args": ["build/index.js"],
      "env": {
        "DEBUG": "true"
      }
    },
    "remote-server": {
      "type": "streamable-http",
      "url": "http://localhost:3000/mcp"
    }
  }
}
```

指定配置和 Server 启动：

```bash
npx -y @modelcontextprotocol/inspector \
  --config ./mcp.json \
  --server local-server
```

如果配置文件只有一个 Server，或者存在名为 `default-server` 的配置，可以省略 `--server`。Inspector 网页也可以通过 `Server Entry` 或 `Servers File` 按钮导出配置。

不要把 API Key、Token 等敏感信息提交到 Git。需要保存配置时，优先在 `env` 中引用运行环境提供的值。

## 7. CLI 模式

Inspector 也支持不打开网页的 CLI 模式，适合脚本和自动检查。

列出 Tools：

```bash
npx -y @modelcontextprotocol/inspector --cli \
  node build/index.js \
  --method tools/list
```

调用 Tool：

```bash
npx -y @modelcontextprotocol/inspector --cli \
  node build/index.js \
  --method tools/call \
  --tool-name my-tool \
  --tool-arg key=value
```

列出 Resources 或 Prompts：

```bash
npx -y @modelcontextprotocol/inspector --cli \
  node build/index.js \
  --method resources/list

npx -y @modelcontextprotocol/inspector --cli \
  node build/index.js \
  --method prompts/list
```

交互调试优先使用网页模式；需要重复执行、自动化验证或接入 CI 时使用 CLI 模式。

## 8. 端口与安全

修改 Client 和 Proxy 端口：

```bash
CLIENT_PORT=6275 SERVER_PORT=6278 \
npx -y @modelcontextprotocol/inspector node build/index.js
```

Inspector Proxy 能够启动本地进程，并以当前用户权限访问本机资源。使用时需要注意：

- 保留默认 session token 认证。
- 使用终端输出的完整 Inspector URL。
- 不要将 Inspector 暴露到不可信网络。
- 不要设置 `DANGEROUSLY_OMIT_AUTH=true`，除非已经理解其安全风险。
- 调试结束后在终端按 `Ctrl+C`，同时停止 Client、Proxy 和 STDIO Server。

## 9. 示例：调试 Playwright Test MCP

以下示例使用 Playwright Test `1.58.0` 提供的 `run-test-mcp-server`。

### 9.1 进入 Playwright 项目

MCP Server 会使用当前目录中的 Playwright 配置、测试文件和测试结果，因此先进入实际项目根目录：

```bash
cd /path/to/your/playwright-project
```

### 9.2 查看当前版本

```bash
npx playwright --version
npm list @playwright/test
```

只输出项目安装的精确版本：

```bash
node -p "require('@playwright/test/package.json').version"
```

### 9.3 临时指定 1.58.0 启动

不修改项目依赖，临时下载并使用 `@playwright/test@1.58.0`：

```bash
npx -y @modelcontextprotocol/inspector \
  -e PWTEST_HEADED=1 \
  npx -y --package=@playwright/test@1.58.0 \
  playwright run-test-mcp-server
```

这里各部分的作用是：

- `@modelcontextprotocol/inspector`：启动网页客户端和 Proxy。
- `-e PWTEST_HEADED=1`：向 MCP Server 传入有头运行环境变量。
- `npx --package=@playwright/test@1.58.0`：临时提供指定版本的 Playwright。
- `playwright run-test-mcp-server`：启动 STDIO MCP Server。

### 9.4 使用项目锁定版本启动

为了让 MCP Server 与项目测试代码使用相同依赖，推荐先锁定项目版本：

```bash
npm install --save-dev --save-exact @playwright/test@1.58.0
```

然后使用项目本地的 Playwright：

```bash
npx -y @modelcontextprotocol/inspector \
  -e PWTEST_HEADED=1 \
  npx playwright run-test-mcp-server
```

将 `PWTEST_HEADED` 改为 `0` 可以使用无头模式：

```bash
npx -y @modelcontextprotocol/inspector \
  -e PWTEST_HEADED=0 \
  npx playwright run-test-mcp-server
```

### 9.5 在网页中验证

1. 使用终端自动打开的带 token 地址进入 Inspector。
2. 确认 transport 为 `STDIO`，然后点击 `Connect`。
3. 打开 `Tools` 并刷新工具列表。
4. 选择一个 Playwright Tool，检查参数 Schema 并执行。
5. 观察返回结果、浏览器行为和终端日志。

如果浏览器无法启动，可以安装 Playwright 浏览器：

```bash
npx playwright install
```

如果找不到 `run-test-mcp-server`，重新确认实际使用的是 `@playwright/test@1.58.0`，并确认命令在 Playwright 项目根目录执行。

## 10. 官方资料

- [MCP Inspector GitHub](https://github.com/modelcontextprotocol/inspector)
- [MCP Inspector 文档](https://modelcontextprotocol.io/docs/tools/inspector)
- [MCP 调试指南](https://modelcontextprotocol.io/docs/tools/debugging)

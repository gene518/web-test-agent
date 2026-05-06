# Web AutoTest Agent 一键启动

这个目录是给最终用户使用的启动入口，不影响仓库里原有的开发脚本。

## 第一次使用

1. 复制配置模板：

   ```bash
   cp start/config.env.example start/config.env
   ```

2. 打开 `start/config.env`，填写模型服务信息：

   ```env
   OPENAI_API_KEY=
   OPENAI_BASE_URL=
   MASTER_MODEL=openai:gpt-4.1
   SPECIALIST_MODEL=openai:gpt-5.4
   DEFAULT_AUTOMATION_PROJECT_ROOT=~/webautotest
   PWTEST_HEADED=true
   ```

3. 启动：

   - macOS：双击 `start/start.command`
   - Windows：双击 `start/start.bat`

首次启动会自动准备 Python 依赖、前端依赖和 Playwright 浏览器。依赖已存在时会直接复用。

## 启动后

脚本会自动打开浏览器。如果没有自动打开，请在终端输出里复制“前端地址”手动访问。

关闭启动脚本窗口，或在窗口里按 `Ctrl+C`，会停止本地后端和前端服务。

## 日志

启动相关日志都在 `start/logs/`：

- `setup.log`：依赖安装和启动准备日志。
- `backend.log`：LangGraph 后端日志。
- `frontend.log`：Next.js 前端日志。

排查问题时，优先查看 `setup.log`，再看后端或前端日志。

## 常见问题

### 没有 `config.env`

脚本会自动从 `config.env.example` 生成一份 `config.env`，然后退出。填写配置后重新启动即可。

### 未找到 Node.js

请先安装 Node.js 22 LTS 或更高版本。脚本会自动准备 `pnpm`，但不会安装系统级 Node.js。

### 端口被占用

默认端口是后端 `2024`、前端 `3000`。如果端口被占用，脚本会自动换到可用端口，并把实际地址打印在窗口里。

### 历史对话不见了

脚本不会清理 `web-agent/.langgraph_api`。如果你把整个项目移动到新目录，或删除了该目录，LangGraph 本地历史可能会变化。

### 想强制重新安装依赖

在 `start/config.env` 里设置：

```env
START_FORCE_SETUP=1
```

重新启动后会重新同步 Python 与前端依赖。

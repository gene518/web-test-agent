# 容器启动

`start/container/` 是单机 VPS 的 Docker Compose 入口。桌面源码启动、Windows 便携包构建和本地日志位于相邻的 [`../desktop/`](../desktop/) 目录。

容器启动按顺序读取：

- `web-agent/.env`：Master、Specialist、Scheduler 等应用配置的唯一来源。
- `deploy/.env`：H5 监听地址和两个宿主机数据目录。

完整的权限、安全边界、持久化和手工升级说明见 [`../../deploy/README.md`](../../deploy/README.md)。

## 首次启动

```bash
cp web-agent/.env.example web-agent/.env
cp deploy/.env.example deploy/.env
chmod 600 web-agent/.env deploy/.env
```

模型地址、模型名和密钥只填入 `web-agent/.env`。H5 监听地址与数据目录填写到 `deploy/.env`，目录初始化命令见部署 README。随后执行：

```bash
bash start/container/start-container.sh bootstrap
```

`bootstrap` 会校验两份配置、从当前源码构建镜像，并等待三个服务健康。

## 服务结构

| 分组 | 容器 | 职责 |
| --- | --- | --- |
| 核心运行组 | `agent`、`scheduler` | 对话执行与定时任务常驻调度，共用 Agent 镜像 |
| 访问入口组 | `web` | H5 和同源 API 代理，使用 Web 镜像 |

当前共三个常驻容器、两个镜像。没有 Updater、Docker socket 或浏览器内升级入口；`status` 按以上两组展示。

## 日常操作

```bash
bash start/container/start-container.sh up
bash start/container/start-container.sh status
bash start/container/start-container.sh logs
bash start/container/start-container.sh logs agent scheduler
bash start/container/start-container.sh down
```

版本升级时由部署人员先更新源码，再重新构建：

```bash
git pull --ff-only origin main
bash start/container/start-container.sh bootstrap
```

需要使用临时配置校验 Compose 时，可以分别覆盖两类配置：

```bash
WEB_TEST_AGENT_MODEL_ENV_FILE=/path/to/model.env \
WEB_TEST_AGENT_CONTAINER_ENV_FILE=/path/to/deploy.env \
  bash start/container/start-container.sh config
```

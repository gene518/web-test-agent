# 单机 VPS H5 部署

本目录提供“不使用 Nginx、单台 VPS、Docker Compose”的私有化方案。Caddy 是唯一公开服务，监听 `8080` 并提供 H5 静态文件和两个同源代理：

- `/api/langgraph/*` -> `agent:2024`
- `/api/artifacts/*` -> Agent 的只读产物预览与下载路由

正常运行时只有三个常驻容器和两个镜像：

| 分组 | 容器 | 镜像与职责 |
| --- | --- | --- |
| 核心运行组 | `agent`、`scheduler` | 共用 Agent 镜像；分别运行 LangGraph 服务和定时调度进程 |
| 访问入口组 | `web` | 使用 Web 镜像；提供 H5 和反向代理 |

该方案没有 Updater 容器、Docker socket、浏览器内版本检查或自动重启流程。版本升级由部署人员明确更新源码后重新构建，避免把宿主机 Docker 控制权交给 Web 可达服务。

## H5 网络边界

H5 可以启动 Agent、消耗模型额度并查看测试产物，但 Web 入口不提供登录或其他访问认证。默认 `H5_BIND_ADDRESS=127.0.0.1`，仅允许部署主机本机访问。

需要从其他设备访问时，可将 `H5_BIND_ADDRESS` 改为 `0.0.0.0`，但只能部署在 VPS 防火墙白名单、可信 VPN 或其他受控网络中。当前目标使用 HTTP 且没有入口认证；直接暴露到不可信公网前必须增加 HTTPS 和外层访问控制。

Artifact HTTP 接口只读，并将目标约束到 `/data/projects` 的真实路径；目录穿越、符号链接逃逸、隐藏目录、`node_modules`、认证状态和常见密钥文件均返回 404。HTML、SVG 等主动内容不会直接以内联原始响应执行。

### 腾讯云域名入口

2026-09-06，按部署所有者明确要求，`https://tencent.geneecho.top` 向所有 IPv4 来源开放，未添加登录认证。这是共享实例：所有访问者可以使用同一模型额度、访问共享对话与产物；HTTPS 只保护浏览器到入口的传输，不提供用户隔离。该实例是上述默认私有部署边界的显式例外。

服务器源码位于 `/home/ubuntu/web-test-agent`。三个业务容器保持不变；宿主机另运行 Ubuntu 软件源安装的 `caddy.service`，代理到 `127.0.0.1:8080`。远端 `deploy/.env` 使用 `H5_BIND_ADDRESS=127.0.0.1`、`H5_PORT=8080`。腾讯云防火墙对全部 IPv4 放行 TCP 80/443，不放行 8080；80 自动跳转 HTTPS。无需 SSH 隧道，访问地址不带端口。

宿主机 `/etc/caddy/Caddyfile` 内容如下（与容器内的 `deploy/Caddyfile` 职责不同）：

```caddyfile
{
    admin 127.0.0.1:2019
}

tencent.geneecho.top {
    encode zstd gzip
    header Strict-Transport-Security "max-age=31536000"
    reverse_proxy 127.0.0.1:8080 {
        flush_interval -1
    }
}
```

Caddy 自动申请并续期证书，证书和 ACME 状态持久化到 `/var/lib/caddy/.local/share/caddy`；应与 `/etc/caddy/Caddyfile` 一同备份，保持域名解析及 80/443 可达。该 systemd 服务独立于 Compose，日常检查和重载：

```bash
sudo caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo systemctl status caddy --no-pager
sudo journalctl -u caddy -n 50 --no-pager
curl -fsS https://tencent.geneecho.top/health
curl -fsS https://tencent.geneecho.top/api/langgraph/info
```

2026-09-06，Master 和 Specialist 均切换为 `gpt-5.6-terra`，已从运行容器验证真实模型响应、JSON 输出和工具调用续接。模型凭据仅保留在 `web-agent/.env` 与 Agent 容器环境中，不在文档中记录。

## 首次启动

要求 Docker Engine 和 Compose v2，宿主机 CPU 为 x86_64。Agent 使用 `linux/amd64`，Playwright 与 MCP 固定为 `1.61.1`，容器内以非 root `pwuser` 运行。

```bash
# 在仓库根目录执行。`web-agent/.env` 包含模型凭据，两份部署配置均只允许当前部署用户读取。
umask 077
cp web-agent/.env.example web-agent/.env
cp deploy/.env.example deploy/.env
chmod 600 web-agent/.env deploy/.env
cd deploy

# UID/GID 1001 是 Agent 镜像内 pwuser 的数字身份。
sudo install -d -o 1001 -g 1001 -m 0750 data data/projects data/config
sudo install -o 1001 -g 1001 -m 0640 \
  ../web-agent/scheduler_tasks.example.json data/config/scheduler_tasks.json

# 输出需要写入 deploy/.env 的两个宿主机绝对路径。
deploy_dir="$(pwd -P)"
echo "PROJECTS_HOST_PATH=$deploy_dir/data/projects"
echo "CONFIG_HOST_PATH=$deploy_dir/data/config"

```

模型连接只在 `web-agent/.env` 中配置。Master 和 Specialist 各自保留完整的 family、channel、model、API key、base URL 和 thinking 字段；两者可以使用同一服务和密钥。

仅修改模型配置后，在仓库根目录执行 `bash start/container/start-container.sh up`，Compose 会重新创建配置变化的 Agent 容器并等待健康检查；直接执行 `docker compose restart` 不会重新读取环境变量。同步维护脱敏的 `web-agent/.env.example`，并核对新容器实际加载的模型名和真实模型响应。

`deploy/.env` 只需要配置：

- `H5_BIND_ADDRESS`、`H5_PORT`：H5 的宿主机监听地址和端口。
- `PROJECTS_HOST_PATH`：测试工程、脚本和报告的宿主机绝对路径。
- `CONFIG_HOST_PATH`：Scheduler 配置的宿主机绝对路径。

Compose 只把模型连接传入 Agent；Scheduler 获得运行所需的非密钥配置，并通过内部地址 `http://agent:2024` 创建监控对话。两份配置文件仅用于 Compose 插值，不作为广域 `env_file` 注入。

两份 `.env` 必须保持 `0600`。`PROJECTS_HOST_PATH` 必须允许 UID 1001 读写，`CONFIG_HOST_PATH` 由 Agent 读写、Scheduler 只读挂载；否则生成脚本、报告或定时配置会失败。

随后回到仓库根目录启动：

```bash
cd ..
bash start/container/start-container.sh bootstrap
bash start/container/start-container.sh status
```

## 运行、升级与持久化

- `web-test-agent-langgraph-state` 保存 checkpoint、线程和 store。
- `PROJECTS_HOST_PATH` 保存测试工程、Playwright 脚本及分析报告。
- `CONFIG_HOST_PATH/scheduler_tasks.json` 由 Agent 写入、Scheduler 读取。
- Agent 与 Scheduler 共用项目目录和同一版本的 Agent 镜像。

版本升级由部署人员在维护窗口中执行：

```bash
git pull --ff-only origin main
bash start/container/start-container.sh bootstrap
```

`bootstrap` 会从当前检出的源码重新构建镜像并等待三个服务健康。CI 仍为 `main` 的 Agent/Web 发布镜像生成不可变 digest 和 cosign 签名，但本地启动不自动拉取或安装任何远程版本。

常用运维命令：

```bash
bash start/container/start-container.sh logs
bash start/container/start-container.sh logs agent scheduler
bash start/container/start-container.sh status
bash start/container/start-container.sh down
```

删除 named volume 会丢失会话状态，启动脚本不提供该破坏性操作。备份时至少保存 `web-test-agent-langgraph-state`、`deploy/data/`、`web-agent/.env` 和 `deploy/.env`。

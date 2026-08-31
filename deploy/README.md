# 单机 VPS H5 部署

本目录提供“不使用 Nginx、单台 VPS、Docker Compose”的私有化方案。Caddy 是唯一公开服务，监听 `8080` 并提供 H5 静态文件、Basic Auth 和三个同源代理：

- `/api/langgraph/*` -> `agent:2024`
- `/api/artifacts/*` -> Agent 的只读产物预览/下载路由
- `/api/update/*` -> `updater:8090`

Agent、scheduler 和 updater 不发布宿主机端口，只加入 `web-test-agent-internal` bridge 网络。Tauri 桌面启动与本地端口逻辑保持不变。

## 安全警告

当前目标明确要求公网 HTTP，因此 Basic Auth 的用户名和密码可被链路上的第三方窃听。它只阻止无意访问，**不提供传输安全**。只应在额外受控网络、VPS 防火墙或可信 VPN 内使用；真正暴露公网前应改用 HTTPS。

Artifact HTTP 接口只读，并将目标约束到 `/data/projects` 的真实路径；目录穿越、符号链接逃逸、隐藏目录、`node_modules`、认证状态和常见密钥文件均返回 404。HTML、SVG 等主动内容不会直接以内联原始响应执行。

## Docker socket 权限（重要）

在线更新器挂载了宿主机的 `/var/run/docker.sock`，以便拉取已签名镜像并重建 Compose 服务。这等同于授予 updater **宿主机级 Docker 控制权**：一旦 updater 进程或其可达控制面被攻破，攻击者可通过 Docker 创建高权限容器、挂载宿主机文件系统并取得主机控制。`read_only`、`cap_drop` 和 `no-new-privileges` 不会限制 Docker socket 所赋予的能力，不能把它们当作隔离边界。

因此应把该部署放在专用 VPS 上，并限制管理员、网络和 Docker daemon 访问。若需要强隔离，必须移除原始 Docker socket，改为单独部署的、严格只允许预定义更新操作的宿主机更新 helper 或 socket proxy；当前 Compose 方案尚未提供这样的 helper。

## 首次启动

要求 Docker Engine 和 Compose v2，宿主机 CPU 为 x86_64。Agent 明确使用 `linux/amd64`，Playwright 与 MCP 均固定为 `1.61.1`，容器内以非 root `pwuser` 运行。

```bash
cd deploy
# `.env` 含模型、GitHub 和更新密钥；创建时就限制为当前部署用户可读。
umask 077
cp .env.example .env
chmod 600 .env

# 容器内 Agent 使用 UID/GID 1001。目录和 scheduler 配置不应向其他宿主机用户开放。
sudo install -d -o 1001 -g 1001 -m 0750 data data/projects data/config
sudo install -o 1001 -g 1001 -m 0640 \
  ../web-agent/scheduler_tasks.example.json data/config/scheduler_tasks.json

# 把下面三个绝对路径写入 .env，不能保留相对路径。
deploy_dir="$(pwd -P)"
echo "DEPLOY_HOST_PATH=$deploy_dir"
echo "PROJECTS_HOST_PATH=$deploy_dir/data/projects"
echo "CONFIG_HOST_PATH=$deploy_dir/data/config"

# 生成 Basic Auth hash；写进 .env 时把每个 $ 改成 $$。
docker run --rm caddy:2.10.2-alpine \
  caddy hash-password --plaintext '替换为强密码'

# 为 UPDATE_INTERNAL_TOKEN 和 UPDATE_CSRF_SECRET 分别生成不同值。
openssl rand -hex 32
openssl rand -hex 32
```

编辑 `.env`，至少填写：

- `PUBLIC_ORIGIN`：浏览器实际访问的完整 HTTP origin，例如 `http://203.0.113.10:8080`，不能带结尾 `/`。
- `DEPLOY_HOST_PATH`、`PROJECTS_HOST_PATH`、`CONFIG_HOST_PATH`：上一步输出的宿主机绝对路径。在线更新的 reconciler 通过 Docker socket 调用宿主 daemon，相对路径会绑定到错误位置。
- `H5_BASIC_AUTH_USER`、`H5_BASIC_AUTH_HASH`。
- 两组模型的 family、channel、model、API key 和 base URL。
- `UPDATE_INTERNAL_TOKEN`、`UPDATE_CSRF_SECRET`。

Compose 只把模型连接传入 Agent；scheduler 仅获得运行时配置并通过内部 API 请求 Agent 执行分析。Basic Auth、`UPDATE_*`、GitHub 和 GHCR 凭据不会进入可执行浏览器与模型工具的容器。`.env` 由 Compose 用于插值，不作为广域 `env_file` 注入。

`.env` 必须保持 `0600`，部署目录建议仅部署用户可访问（例如 `chmod 750 deploy`）。Playwright 官方镜像中的 `pwuser` 是 UID/GID `1001:1001`。首次启动命令将 `data/`、`data/projects/`、`data/config/` 设为该用户拥有的 `0750` 目录，并把 `scheduler_tasks.json` 设为 `0640`。`PROJECTS_HOST_PATH` 必须允许该用户读写，`CONFIG_HOST_PATH` 必须允许 Agent 读写（scheduler 以只读方式挂载）；否则生成脚本、报告或更新定时配置会失败。需要让另一位宿主机运维用户管理这些文件时，应通过受控组或 `sudo` 授权，而不是放宽为 world-readable 或 world-writable。

随后启动：

```bash
docker compose config --quiet
docker compose build
docker compose up -d --wait
docker compose ps
curl http://127.0.0.1:8080/health
```

访问 `PUBLIC_ORIGIN` 并输入 Basic Auth 凭据。`/health` 仅供本机/容器健康检查，不返回业务数据，因此不要求认证。

## 运行与持久化

- `web-test-agent-langgraph-state` 保存 `.langgraph_api` checkpoint、线程和 store。这里使用无需 LangSmith 部署许可证的 `langgraph dev --no-reload`，只支持单机单 Agent 副本。
- `PROJECTS_HOST_PATH` 保存测试工程、脚本、Playwright 报告和 scheduler 分析报告。
- `CONFIG_HOST_PATH/scheduler_tasks.json` 由 Agent 写入、scheduler 读取。
- `web-test-agent-update-state` 保存更新操作、部署 revision 和 maintenance gate；scheduler 只读挂载该卷。
- `web-test-agent-scheduler-state` 仅由 scheduler 写入活动状态，updater 只读挂载，避免浏览器执行容器修改更新控制状态。Agent 镜像预置由非 root `pwuser` 持有的 `/scheduler-state`，首次创建空 named volume 时也具备正确写权限。
- Agent 与 scheduler 使用共享项目目录和同一镜像；scheduler 通过内部地址 `http://agent:2024` 创建监控对话。

更新开始后 updater 创建 maintenance gate。scheduler 从只读控制卷观察 gate，让当前执行自然结束、暂停启动后续任务，并向独立状态卷原子发布 `scheduler-status.json`；活动 LangGraph 与 scheduler 任务均清空后，updater 才重建服务。更新器只接受同一 `CI` 工作流中、`main` push 的成功发布 job 产生的签名 GHCR 镜像，失败时恢复上一组不可变镜像。

GitHub 侧应保护 `main`，仅允许受控主体推送并要求 CI 通过。容器发布是 `CI` 内的最后一个 job，依赖后端、前端、Windows 启动和 Windows 客户端检查全部成功；它固定检出当前 CI run 的精确 SHA，而非可变的 `main` 分支名。Cosign keyless 签名身份固定为 `.github/workflows/ci.yml@refs/heads/main`。

常用运维命令：

```bash
docker compose logs -f agent scheduler web updater
docker compose restart scheduler
docker compose down                 # 保留 named volumes 和宿主机数据
docker compose down --volumes       # 会删除会话和更新状态，慎用
```

不要横向扩容 `agent`：本方案的 `.langgraph_api` 是单进程本地持久层。备份时至少保存三个 named volume、`data/` 和 `.env`；`.env` 含密钥，不得提交 Git。

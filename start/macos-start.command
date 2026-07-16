#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
START_DIR="$PROJECT_ROOT/start"
BACKEND_DIR="$PROJECT_ROOT/web-agent"
CLIENT_DIR="$PROJECT_ROOT/web-agent-client"
PLAYWRIGHT_PROJECT_DIR="$BACKEND_DIR/deep_agent/assets/demo"
BACKEND_ENV_FILE="$BACKEND_DIR/.env"
START_SCRIPT_PATH="$SCRIPT_DIR/macos-start.command"
# 平台相关的持久化状态目录：遵循 macOS 约定放在 ~/Library/Application Support，
# 不再污染项目 start/ 目录。
APP_STATE_DIR="${APP_STATE_DIR:-$HOME/Library/Application Support/WebAutoTestAgent}"
BACKEND_LOG_FILE="$START_DIR/backend.log"

BACKEND_HOST="127.0.0.1"
BACKEND_PORT="${BACKEND_PORT:-2024}"
STARTUP_WAIT_SECONDS="${STARTUP_WAIT_SECONDS:-90}"
NO_RELOAD="${NO_RELOAD:-1}"
SERVER_LOG_LEVEL="${SERVER_LOG_LEVEL:-}"

MODE="${1:-start}"
ENTRY_DISPLAY_NAME="${WEB_AUTOTEST_ENTRY:-$0}"
BACKEND_PID=""
CLEANED_UP=0
STARTUP_STEP_INDEX=0
STARTUP_TOTAL_STEPS=6
STOP_STEP_INDEX=0
STOP_TOTAL_STEPS=3
PYTHON_BIN=""

mkdir -p "$APP_STATE_DIR"

log() {
  printf "%s\n" "$*"
}

setup_log() {
  printf "%s\n" "$*"
}

start_step() {
  local title="$1"
  STARTUP_STEP_INDEX=$((STARTUP_STEP_INDEX + 1))
  setup_log
  setup_log "[$STARTUP_STEP_INDEX/$STARTUP_TOTAL_STEPS] $title"
}

finish_step() {
  local title="$1"
  setup_log "[$STARTUP_STEP_INDEX/$STARTUP_TOTAL_STEPS] $title 完成"
}

run_logged_command() {
  "$@"
}

run_logged_command_in_dir() {
  local workdir="$1"
  shift

  (
    cd "$workdir"
    "$@"
  )
}

fail() {
  setup_log "启动失败：$*"
  exit 1
}

ensure_backend_env_file() {
  if [ -f "$BACKEND_ENV_FILE" ]; then
    return
  fi

  fail "未找到项目配置文件：${BACKEND_ENV_FILE}。请先参考 ${BACKEND_DIR}/.env.example 创建并填写它。"
}

warn_if_missing_config() {
  local key="$1"
  local hint="$2"

  if [ -n "${!key:-}" ]; then
    return
  fi

  setup_log "提示：$BACKEND_ENV_FILE 中 $key 为空；$hint"
}

import_project_env_file() {
  set -a
  # 项目配置使用 shell .env 语法，直接注入当前启动进程。
  # shellcheck disable=SC1090
  . "$BACKEND_ENV_FILE"
  set +a

  setup_log "已加载项目配置：$BACKEND_ENV_FILE"
}

load_backend_env_file() {
  import_project_env_file
  warn_if_missing_config "MASTER_MODEL" "将使用项目默认值。"
  warn_if_missing_config "SPECIALIST_MODEL" "将使用项目默认值。"

  if [ -z "${OPENAI_API_KEY:-}" ] && [ -z "${OPENAI_BASE_URL:-}" ]; then
    setup_log "提示：$BACKEND_ENV_FILE 中 OPENAI_API_KEY 和 OPENAI_BASE_URL 都为空，请确认模型服务配置。"
  elif [ -z "${OPENAI_API_KEY:-}" ]; then
    setup_log "提示：$BACKEND_ENV_FILE 中 OPENAI_API_KEY 为空；如果你的模型服务需要 Key，请先补齐。"
  fi
}

append_path_if_exists() {
  local path_value="$1"
  if [ -d "$path_value" ]; then
    PATH="$path_value:$PATH"
    export PATH
  fi
}

check_git() {
  # 仅检查 Git 是否存在，不做自动安装。Git 是拉取仓库和部分工具脚本的基础依赖。
  if command -v git >/dev/null 2>&1; then
    setup_log "Git 已就绪：$(command -v git) ($(git --version 2>/dev/null))"
    return
  fi

  fail "未找到 Git。请先安装 Git 后再运行本脚本。推荐方式：通过 Homebrew 安装 \`brew install git\`，或访问 https://git-scm.com/download/mac 下载安装包。"
}

check_node() {
  # 仅检查 Node.js 是否存在且版本满足要求。项目需要 Node.js 22 LTS 或更高版本。
  if ! command -v node >/dev/null 2>&1; then
    fail "未找到 Node.js。请先安装 Node.js 22 LTS 或更高版本后重试。推荐方式：通过 Homebrew 安装 \`brew install node@22\`，或访问 https://nodejs.org/ 下载安装包。"
  fi

  local node_version_raw
  node_version_raw="$(node --version 2>/dev/null | sed 's/^v//')"
  local node_major
  node_major="${node_version_raw%%.*}"

  if ! [[ "$node_major" =~ ^[0-9]+$ ]]; then
    fail "无法解析 Node.js 版本号：${node_version_raw}。请确认已正确安装 Node.js 22 LTS 或更高版本。"
  fi

  if [ "$node_major" -lt 22 ]; then
    fail "检测到 Node.js 版本为 ${node_version_raw}，项目需要 Node.js 22 LTS 或更高版本。请升级后重试。"
  fi

  setup_log "Node.js 已就绪：$(command -v node) (v$node_version_raw)"
}

check_python() {
  # 仅检查 Python 是否存在且版本满足要求。项目需要 Python 3.11 或更高版本。
  local python_cmd=""
  if command -v python3 >/dev/null 2>&1; then
    python_cmd="python3"
  elif command -v python >/dev/null 2>&1; then
    python_cmd="python"
  else
    fail "未找到 Python。请先安装 Python 3.11 或更高版本后重试。推荐方式：通过 Homebrew 安装 \`brew install python@3.11\`，或访问 https://www.python.org/downloads/ 下载安装包。"
  fi

  local python_version_raw
  python_version_raw="$("$python_cmd" --version 2>&1 | awk '{print $2}')"
  local python_major python_minor
  python_major="${python_version_raw%%.*}"
  python_minor="${python_version_raw#*.}"
  python_minor="${python_minor%%.*}"

  if ! [[ "$python_major" =~ ^[0-9]+$ ]] || ! [[ "$python_minor" =~ ^[0-9]+$ ]]; then
    fail "无法解析 Python 版本号：${python_version_raw}。请确认已正确安装 Python 3.11 或更高版本。"
  fi

  if [ "$python_major" -lt 3 ] || { [ "$python_major" -eq 3 ] && [ "$python_minor" -lt 11 ]; }; then
    fail "检测到 Python 版本为 ${python_version_raw}，项目需要 Python 3.11 或更高版本。请升级后重试。"
  fi

  setup_log "Python 已就绪：$(command -v $python_cmd) ($python_version_raw)"
}

check_uv() {
  # 仅检查 uv 是否存在，不做自动安装。uv 用于管理后端 Python 依赖。
  append_path_if_exists "$HOME/.local/bin"
  append_path_if_exists "$HOME/.cargo/bin"

  if command -v uv >/dev/null 2>&1; then
    setup_log "uv 已就绪：$(command -v uv)"
    return
  fi

  fail "未找到 uv。请先安装 uv 后重试。推荐方式：通过 Homebrew 安装 \`brew install uv\`，或执行 \`curl -LsSf https://astral.sh/uv/install.sh | sh\`，或访问 https://docs.astral.sh/uv/getting-started/installation/ 查看更多方式。"
}

check_pnpm() {
  if command -v pnpm >/dev/null 2>&1; then
    setup_log "pnpm 已就绪：$(command -v pnpm) ($(pnpm --version 2>/dev/null))"
    return
  fi

  fail "未找到 pnpm。请先安装 pnpm 10.5.1 后重试。可执行 \`corepack enable && corepack prepare pnpm@10.5.1 --activate\`。"
}

check_rust() {
  append_path_if_exists "$HOME/.cargo/bin"
  if command -v cargo >/dev/null 2>&1; then
    setup_log "Rust 已就绪：$(command -v cargo) ($(cargo --version 2>/dev/null))"
    return
  fi

  fail "未找到 Rust/Cargo。请先通过 https://rustup.rs/ 安装 Rust 1.88 或更高版本。"
}

sync_client_dependencies() {
  if [ ! -d "$CLIENT_DIR/node_modules" ] || [ "${START_FORCE_SETUP:-0}" = "1" ]; then
    setup_log "开始同步桌面客户端依赖..."
    run_logged_command_in_dir "$CLIENT_DIR" pnpm install --frozen-lockfile || fail "桌面客户端依赖同步失败。"
  else
    setup_log "桌面客户端依赖已存在，跳过同步。"
  fi
}

sync_backend_dependencies() {
  local langgraph_bin="$BACKEND_DIR/.venv/bin/langgraph"

  if [ ! -x "$langgraph_bin" ] || [ "${START_FORCE_SETUP:-0}" = "1" ]; then
    setup_log "开始同步后端依赖..."
    run_logged_command uv sync --project "$BACKEND_DIR" --extra dev || fail "后端依赖同步失败。"
  else
    setup_log "后端依赖已存在，跳过同步。"
  fi

  [ -x "$langgraph_bin" ] || fail "未找到 LangGraph 可执行文件：$langgraph_bin"
  LANGGRAPH_BIN="$langgraph_bin"
}

install_playwright_browsers() {
  local marker_file="$APP_STATE_DIR/playwright-chromium-installed"
  local playwright_version
  if [ "${START_INSTALL_PLAYWRIGHT_BROWSERS:-true}" != "true" ]; then
    setup_log "已跳过 Playwright 浏览器安装。"
    return
  fi
  if [ -f "$marker_file" ] && [ "${START_FORCE_SETUP:-0}" != "1" ]; then
    setup_log "Playwright 浏览器安装标记已存在，跳过安装。"
    return
  fi

  setup_log "开始安装 Playwright Chromium 浏览器..."
  playwright_version="$(cd "$PLAYWRIGHT_PROJECT_DIR" && node -p "require('./package.json').devDependencies['@playwright/test']")"
  [ -n "$playwright_version" ] || fail "无法读取内置 demo 的 Playwright 版本。"
  run_logged_command_in_dir "$PLAYWRIGHT_PROJECT_DIR" npx --yes "playwright@$playwright_version" install chromium || fail "Playwright Chromium 浏览器安装失败。"
  touch "$marker_file"
}

resolve_python_bin() {
  if [ -x "$BACKEND_DIR/.venv/bin/python3" ]; then
    PYTHON_BIN="$BACKEND_DIR/.venv/bin/python3"
  elif [ -x "$BACKEND_DIR/.venv/bin/python" ]; then
    PYTHON_BIN="$BACKEND_DIR/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
  else
    fail "未找到可用 Python。"
  fi
}

listener_pids() {
  local port="$1"
  if ! command -v lsof >/dev/null 2>&1; then
    return
  fi

  lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
}

port_is_bindable() {
  "$PYTHON_BIN" - "$1" "$2" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
family = socket.AF_INET6 if ":" in host else socket.AF_INET
with socket.socket(family, socket.SOCK_STREAM) as sock:
    try:
        sock.bind((host, port))
    except OSError:
        raise SystemExit(1)
raise SystemExit(0)
PY
}

port_accepts_connections() {
  "$PYTHON_BIN" - "$1" "$2" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
family = socket.AF_INET6 if ":" in host else socket.AF_INET
with socket.socket(family, socket.SOCK_STREAM) as sock:
    sock.settimeout(0.5)
    if sock.connect_ex((host, port)) == 0:
        raise SystemExit(0)
raise SystemExit(1)
PY
}

wait_for_port() {
  local name="$1"
  local host="$2"
  local port="$3"
  local pid="$4"
  local log_file="${5:-}"
  local deadline=$((SECONDS + STARTUP_WAIT_SECONDS))

  until port_accepts_connections "$host" "$port"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      if [ -n "$log_file" ] && [ -f "$log_file" ]; then
        setup_log "$name 进程已退出。最近日志："
        tail -n 40 "$log_file" 2>/dev/null || true
      else
        setup_log "$name 进程已退出，请查看当前终端输出。"
      fi
      return 1
    fi
    if [ "$SECONDS" -ge "$deadline" ]; then
      if [ -n "$log_file" ] && [ -f "$log_file" ]; then
        setup_log "${name} 未能在 ${STARTUP_WAIT_SECONDS}s 内监听 ${host}:${port}。最近日志："
        tail -n 40 "$log_file" 2>/dev/null || true
      else
        setup_log "${name} 未能在 ${STARTUP_WAIT_SECONDS}s 内监听 ${host}:${port}，请查看当前终端输出。"
      fi
      return 1
    fi
    sleep 0.5
  done
}

collect_descendants() {
  local parent="$1"
  local children child

  children="$(pgrep -P "$parent" 2>/dev/null || true)"
  for child in $children; do
    collect_descendants "$child"
  done

  echo "$parent"
}

kill_tree() {
  local signal="$1"
  local pid="$2"
  local targets

  if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
    return
  fi

  targets="$(collect_descendants "$pid" | awk '!seen[$0]++')"
  if [ -n "$targets" ]; then
    kill "-$signal" $targets 2>/dev/null || true
  fi
}

cleanup() {
  local has_process=0

  if [ "$CLEANED_UP" = "1" ]; then
    return
  fi
  CLEANED_UP=1

  if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    has_process=1
  fi
  if [ "$has_process" = "0" ]; then
    return
  fi

  log "正在停止本地服务..."
  kill_tree TERM "$BACKEND_PID"
  sleep 0.5
  kill_tree KILL "$BACKEND_PID"
}

handle_signal() {
  log
  cleanup
  exit 130
}

start_stop_step() {
  local title="$1"
  STOP_STEP_INDEX=$((STOP_STEP_INDEX + 1))
  log
  log "[$STOP_STEP_INDEX/$STOP_TOTAL_STEPS] $title"
}

finish_stop_step() {
  local title="$1"
  log "[$STOP_STEP_INDEX/$STOP_TOTAL_STEPS] $title 完成"
}

find_script_pids() {
  local script_path="$1"
  local pid

  for pid in $(pgrep -f "$script_path" 2>/dev/null || true); do
    if [ "$pid" = "$$" ] || [ "$pid" = "$PPID" ]; then
      continue
    fi
    echo "$pid"
  done
}

stop_script_sessions() {
  local label="$1"
  local script_path="$2"
  local pid
  local found=0

  for pid in $(find_script_pids "$script_path"); do
    found=1
    log "停止 $label 进程：$pid"
    kill_tree TERM "$pid"
  done

  if [ "$found" = "0" ]; then
    log "未发现运行中的 $label 进程。"
    return
  fi

  sleep 0.5

  for pid in $(find_script_pids "$script_path"); do
    log "强制停止残留 $label 进程：$pid"
    kill_tree KILL "$pid"
  done
}

cancel_backend_runs() {
  local host="$1"
  local port="$2"

  if [ -z "$PYTHON_BIN" ]; then
    log "未找到可用 Python，跳过后端运行取消。"
    return
  fi

  if [ -z "$(listener_pids "$port")" ]; then
    return
  fi

  "$PYTHON_BIN" - "$host" "$port" <<'PY' || true
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

host = sys.argv[1]
port = sys.argv[2]
base_url = f"http://{host}:{port}"


def request(method, path, payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=2) as response:
        raw = response.read()
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def list_runs(thread_id, status):
    query = urllib.parse.urlencode({"limit": 100, "status": status})
    return request("GET", f"/threads/{urllib.parse.quote(thread_id)}/runs?{query}") or []


try:
    threads = request(
        "POST",
        "/threads/search",
        {"status": "busy", "limit": 100},
    ) or []
except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
    raise SystemExit(0)

for thread in threads:
    thread_id = thread.get("thread_id") if isinstance(thread, dict) else None
    if not thread_id:
        continue
    for status in ("running", "pending"):
        try:
            runs = list_runs(thread_id, status)
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
            continue
        for run in runs:
            run_id = run.get("run_id") if isinstance(run, dict) else None
            if not run_id:
                continue
            cancel_path = (
                f"/threads/{urllib.parse.quote(thread_id)}"
                f"/runs/{urllib.parse.quote(run_id)}"
                "/cancel?wait=1&action=interrupt"
            )
            try:
                request("POST", cancel_path)
            except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
                continue
PY
}

stop_listener() {
  local name="$1"
  local host="$2"
  local port="$3"
  local kind="${4:-generic}"
  local pids

  if ! command -v lsof >/dev/null 2>&1; then
    log "未找到 lsof，无法按端口清理 ${name}；仅已尝试停止启动脚本进程。"
    return
  fi

  pids="$(listener_pids "$port")"
  if [ -z "$pids" ]; then
    log "$name 端口 $port 当前没有监听进程。"
    return
  fi

  if [ "$kind" = "backend" ]; then
    cancel_backend_runs "$host" "$port"
  fi

  log "停止 $name 监听进程：$(echo "$pids" | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
  kill $pids 2>/dev/null || true
  sleep 0.5

  pids="$(listener_pids "$port")"
  if [ -n "$pids" ]; then
    log "强制停止残留 $name 监听进程：$(echo "$pids" | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
    kill -9 $pids 2>/dev/null || true
  fi
}

show_logs() {
  if [ ! -f "$BACKEND_LOG_FILE" ]; then
    log "未找到后端日志文件：$BACKEND_LOG_FILE"
    exit 1
  fi

  log "持续查看后端日志：$BACKEND_LOG_FILE"
  tail -n 200 -F "$BACKEND_LOG_FILE"
}

start_backend() {
  local langgraph_args
  setup_log "启动后端：http://$BACKEND_HOST:$BACKEND_PORT"
  (
    cd "$BACKEND_DIR"
    # `--allow-blocking`：MCP stdio 会话建立时底层 anyio 会调 `os.access` 等同步 IO，
    # LangGraph dev 的 BlockingCallDetector 会把这类调用判为非法并中断连接。业务侧
    # 已经把自家同步 IO 包到 asyncio.to_thread，但第三方 MCP 客户端内部仍有同步
    # 预检，这里统一在 dev 入口放行 blocking，避免误杀。
    langgraph_args=(dev --host "$BACKEND_HOST" --port "$BACKEND_PORT" --no-browser --allow-blocking)
    if [ "$NO_RELOAD" = "1" ]; then
      langgraph_args+=(--no-reload)
    fi
    if [ -n "$SERVER_LOG_LEVEL" ]; then
      langgraph_args+=(--server-log-level "$SERVER_LOG_LEVEL")
    fi
    "$LANGGRAPH_BIN" "${langgraph_args[@]}" 2>&1 | tee -a "$BACKEND_LOG_FILE"
  ) &
  BACKEND_PID="$!"
  wait_for_port "后端" "$BACKEND_HOST" "$BACKEND_PORT" "$BACKEND_PID" "$BACKEND_LOG_FILE" || fail "后端启动失败。"
}

start_backend_main() {
  STARTUP_STEP_INDEX=0
  STARTUP_TOTAL_STEPS=9
  trap handle_signal INT TERM
  trap cleanup EXIT
  : > "$BACKEND_LOG_FILE"

  start_step "检查项目配置"
  ensure_backend_env_file
  load_backend_env_file
  finish_step "检查项目配置"

  start_step "检查 Git"
  check_git
  finish_step "检查 Git"

  start_step "检查 Node.js"
  check_node
  finish_step "检查 Node.js"

  start_step "检查 Python"
  check_python
  finish_step "检查 Python"

  start_step "检查 uv"
  check_uv
  finish_step "检查 uv"

  start_step "同步后端依赖"
  sync_backend_dependencies
  finish_step "同步后端依赖"

  start_step "安装 Playwright 浏览器"
  install_playwright_browsers
  finish_step "安装 Playwright 浏览器"

  start_step "解析 Python 与端口"
  resolve_python_bin
  port_is_bindable "$BACKEND_HOST" "$BACKEND_PORT" || fail "后端端口 ${BACKEND_PORT} 已被占用。"
  finish_step "解析 Python 与端口"

  start_step "启动后端"
  start_backend
  finish_step "启动后端"

  log
  log "Web AutoTest Agent 后端已启动。"
  log "后端地址：http://$BACKEND_HOST:$BACKEND_PORT"
  log "后端日志：$BACKEND_LOG_FILE"
  log "请通过 web-agent-client 桌面客户端连接。"
  log "关闭本窗口或按 Ctrl+C 会停止本地服务。"

  while true; do
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
      log "后端进程已退出，请查看 $BACKEND_LOG_FILE"
      exit 1
    fi
    sleep 1
  done
}

start_client_main() {
  STARTUP_STEP_INDEX=0
  STARTUP_TOTAL_STEPS=6

  start_step "检查项目配置"
  ensure_backend_env_file
  load_backend_env_file
  finish_step "检查项目配置"

  start_step "检查 Node.js"
  check_node
  finish_step "检查 Node.js"

  start_step "检查 pnpm"
  check_pnpm
  finish_step "检查 pnpm"

  start_step "检查 Rust"
  check_rust
  finish_step "检查 Rust"

  start_step "同步桌面客户端依赖"
  sync_client_dependencies
  finish_step "同步桌面客户端依赖"

  start_step "启动桌面客户端"
  setup_log "客户端将自动准备并管理 LangGraph 后端。"
  run_logged_command_in_dir "$CLIENT_DIR" pnpm tauri dev || fail "桌面客户端启动失败。"
}

stop_main() {
  start_stop_step "加载项目配置"
  if [ -f "$BACKEND_ENV_FILE" ]; then
    import_project_env_file
  else
    log "未找到项目配置文件：${BACKEND_ENV_FILE}，关闭脚本将使用默认端口。"
  fi
  finish_stop_step "加载项目配置"

  start_stop_step "停止启动脚本进程"
  stop_script_sessions "macos-start.command" "$START_SCRIPT_PATH"
  finish_stop_step "停止启动脚本进程"

  start_stop_step "停止后端服务"
  resolve_python_bin
  stop_listener "后端" "$BACKEND_HOST" "$BACKEND_PORT" "backend"
  finish_stop_step "停止后端服务"

  log
  log "本地服务关闭完成。"
  log "后端端口：$BACKEND_HOST:$BACKEND_PORT"
}

main() {
  case "$MODE" in
    start)
      start_client_main
      ;;
    backend)
      start_backend_main
      ;;
    end)
      stop_main
      ;;
    logs)
      show_logs
      ;;
    *)
      printf "用法：%s [start|end|logs]\n" "$ENTRY_DISPLAY_NAME" >&2
      exit 1
      ;;
  esac
}

main

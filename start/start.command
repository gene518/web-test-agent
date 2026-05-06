#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$PROJECT_ROOT/web-agent"
FRONTEND_DIR="$PROJECT_ROOT/web-poartl"
CONFIG_TEMPLATE="$SCRIPT_DIR/config.env.example"
CONFIG_FILE="$SCRIPT_DIR/config.env"
LOG_DIR="$SCRIPT_DIR/logs"
CACHE_DIR="$SCRIPT_DIR/.cache"
SETUP_LOG_FILE="$LOG_DIR/setup.log"
BACKEND_LOG_FILE="$LOG_DIR/backend.log"
FRONTEND_LOG_FILE="$LOG_DIR/frontend.log"

BACKEND_HOST="127.0.0.1"
FRONTEND_HOST="127.0.0.1"
BACKEND_PORT="${BACKEND_PORT:-2024}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
STARTUP_WAIT_SECONDS="${STARTUP_WAIT_SECONDS:-90}"
NEXT_PUBLIC_ASSISTANT_ID="${NEXT_PUBLIC_ASSISTANT_ID:-web-autotest-agent}"
NEXT_PUBLIC_AUTH_SCHEME="${NEXT_PUBLIC_AUTH_SCHEME:-}"

BACKEND_PID=""
FRONTEND_PID=""
CLEANED_UP=0

mkdir -p "$LOG_DIR" "$CACHE_DIR"
: > "$SETUP_LOG_FILE"
: > "$BACKEND_LOG_FILE"
: > "$FRONTEND_LOG_FILE"

log() {
  printf "%s\n" "$*"
}

setup_log() {
  printf "%s\n" "$*" | tee -a "$SETUP_LOG_FILE"
}

fail() {
  setup_log "启动失败：$*"
  setup_log "请查看日志：$SETUP_LOG_FILE"
  exit 1
}

ensure_config_file() {
  if [ -f "$CONFIG_FILE" ]; then
    return
  fi

  cp "$CONFIG_TEMPLATE" "$CONFIG_FILE"
  setup_log "已生成配置文件：$CONFIG_FILE"
  setup_log "请先填写 config.env 里的模型服务信息，然后重新双击 start.command。"
  exit 1
}

load_config_file() {
  set -a
  # 用户配置文件只用于本地启动，保持 shell .env 语法以支持带引号的值。
  # shellcheck disable=SC1090
  . "$CONFIG_FILE"
  set +a

  if [ -z "${OPENAI_API_KEY:-}" ]; then
    setup_log "提示：OPENAI_API_KEY 为空；如果你的模型服务需要 Key，请先填写 $CONFIG_FILE。"
  fi
}

append_path_if_exists() {
  local path_value="$1"
  if [ -d "$path_value" ]; then
    PATH="$path_value:$PATH"
    export PATH
  fi
}

ensure_uv() {
  append_path_if_exists "$HOME/.local/bin"
  append_path_if_exists "$HOME/.cargo/bin"

  if command -v uv >/dev/null 2>&1; then
    setup_log "uv 已就绪：$(command -v uv)"
    return
  fi

  if ! command -v curl >/dev/null 2>&1; then
    fail "未找到 uv，也未找到 curl，无法自动安装 uv。请先安装 uv 后重试。"
  fi

  setup_log "未找到 uv，开始自动安装 uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh >>"$SETUP_LOG_FILE" 2>&1 || fail "uv 安装失败。"
  append_path_if_exists "$HOME/.local/bin"
  append_path_if_exists "$HOME/.cargo/bin"

  command -v uv >/dev/null 2>&1 || fail "uv 安装后仍不可用，请重开终端或检查 PATH。"
  setup_log "uv 安装完成：$(command -v uv)"
}

ensure_pnpm() {
  if ! command -v node >/dev/null 2>&1; then
    fail "未找到 Node.js。请先安装 Node.js 22 LTS 或更高版本后重试。"
  fi

  if command -v pnpm >/dev/null 2>&1; then
    PNPM_CMD=(pnpm)
    setup_log "pnpm 已就绪：$(command -v pnpm)"
    return
  fi

  if command -v corepack >/dev/null 2>&1; then
    setup_log "未找到 pnpm，使用 corepack 准备 pnpm@10.5.1..."
    corepack enable >>"$SETUP_LOG_FILE" 2>&1 || true
    corepack prepare pnpm@10.5.1 --activate >>"$SETUP_LOG_FILE" 2>&1 || fail "corepack 准备 pnpm 失败。"
  elif command -v npm >/dev/null 2>&1; then
    setup_log "未找到 pnpm/corepack，使用 npm 全局安装 pnpm@10.5.1..."
    npm install -g pnpm@10.5.1 >>"$SETUP_LOG_FILE" 2>&1 || fail "pnpm 安装失败。"
  else
    fail "未找到 pnpm、corepack 或 npm，无法准备前端依赖。"
  fi

  command -v pnpm >/dev/null 2>&1 || fail "pnpm 准备后仍不可用，请检查 Node.js 安装。"
  PNPM_CMD=(pnpm)
  setup_log "pnpm 准备完成：$(command -v pnpm)"
}

sync_backend_dependencies() {
  local langgraph_bin="$BACKEND_DIR/.venv/bin/langgraph"

  if [ ! -x "$langgraph_bin" ] || [ "${START_FORCE_SETUP:-0}" = "1" ]; then
    setup_log "开始同步后端依赖..."
    uv sync --project "$BACKEND_DIR" --extra dev >>"$SETUP_LOG_FILE" 2>&1 || fail "后端依赖同步失败。"
  else
    setup_log "后端依赖已存在，跳过同步。"
  fi

  [ -x "$langgraph_bin" ] || fail "未找到 LangGraph 可执行文件：$langgraph_bin"
  LANGGRAPH_BIN="$langgraph_bin"
}

sync_frontend_dependencies() {
  if [ ! -d "$FRONTEND_DIR/node_modules" ] || [ "${START_FORCE_SETUP:-0}" = "1" ]; then
    setup_log "开始同步前端依赖..."
    (
      cd "$FRONTEND_DIR"
      "${PNPM_CMD[@]}" install
    ) >>"$SETUP_LOG_FILE" 2>&1 || fail "前端依赖同步失败。"
  else
    setup_log "前端依赖已存在，跳过同步。"
  fi
}

install_playwright_browsers() {
  local marker_file="$CACHE_DIR/playwright-chromium-installed"
  if [ "${START_INSTALL_PLAYWRIGHT_BROWSERS:-true}" != "true" ]; then
    setup_log "已跳过 Playwright 浏览器安装。"
    return
  fi
  if [ -f "$marker_file" ] && [ "${START_FORCE_SETUP:-0}" != "1" ]; then
    setup_log "Playwright 浏览器安装标记已存在，跳过安装。"
    return
  fi

  setup_log "开始安装 Playwright Chromium 浏览器..."
  (
    cd "$FRONTEND_DIR"
    npx --yes playwright install chromium
  ) >>"$SETUP_LOG_FILE" 2>&1 || fail "Playwright Chromium 浏览器安装失败。"
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

find_open_port() {
  "$PYTHON_BIN" - "$1" <<'PY'
import socket
import sys

host = sys.argv[1]
family = socket.AF_INET6 if ":" in host else socket.AF_INET
with socket.socket(family, socket.SOCK_STREAM) as sock:
    sock.bind((host, 0))
    print(sock.getsockname()[1])
PY
}

resolve_port() {
  local name="$1"
  local host="$2"
  local preferred_port="$3"
  local port_var="$4"
  local resolved_port="$preferred_port"

  if ! port_is_bindable "$host" "$preferred_port"; then
    resolved_port="$(find_open_port "$host")"
    setup_log "$name 默认端口 $preferred_port 已被占用，改用 $resolved_port。"
  fi

  printf -v "$port_var" "%s" "$resolved_port"
}

wait_for_port() {
  local name="$1"
  local host="$2"
  local port="$3"
  local pid="$4"
  local deadline=$((SECONDS + STARTUP_WAIT_SECONDS))

  until port_accepts_connections "$host" "$port"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      setup_log "$name 进程已退出。最近日志："
      tail -n 40 "$5" 2>/dev/null | tee -a "$SETUP_LOG_FILE" || true
      return 1
    fi
    if [ "$SECONDS" -ge "$deadline" ]; then
      setup_log "$name 未能在 ${STARTUP_WAIT_SECONDS}s 内监听 $host:$port。最近日志："
      tail -n 40 "$5" 2>/dev/null | tee -a "$SETUP_LOG_FILE" || true
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

  if [ -n "$FRONTEND_PID" ] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
    has_process=1
  fi
  if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    has_process=1
  fi
  if [ "$has_process" = "0" ]; then
    return
  fi

  log "正在停止本地服务..."
  kill_tree TERM "$FRONTEND_PID"
  kill_tree TERM "$BACKEND_PID"
  sleep 0.5
  kill_tree KILL "$FRONTEND_PID"
  kill_tree KILL "$BACKEND_PID"
}

handle_signal() {
  log
  cleanup
  exit 130
}

open_frontend_url() {
  if command -v open >/dev/null 2>&1; then
    open "$FRONTEND_OPEN_URL" >/dev/null 2>&1 || true
  else
    setup_log "无法自动打开浏览器，请手动访问：$FRONTEND_OPEN_URL"
  fi
}

start_backend() {
  setup_log "启动后端：http://$BACKEND_HOST:$BACKEND_PORT"
  (
    cd "$BACKEND_DIR"
    exec "$LANGGRAPH_BIN" dev --host "$BACKEND_HOST" --port "$BACKEND_PORT" --no-browser --no-reload
  ) >>"$BACKEND_LOG_FILE" 2>&1 &
  BACKEND_PID="$!"
  wait_for_port "后端" "$BACKEND_HOST" "$BACKEND_PORT" "$BACKEND_PID" "$BACKEND_LOG_FILE" || fail "后端启动失败。"
}

start_frontend() {
  export NEXT_PUBLIC_API_URL="http://$BACKEND_HOST:$BACKEND_PORT"
  export NEXT_PUBLIC_ASSISTANT_ID
  export NEXT_PUBLIC_AUTH_SCHEME

  setup_log "启动前端：http://$FRONTEND_HOST:$FRONTEND_PORT"
  (
    cd "$FRONTEND_DIR"
    exec "${PNPM_CMD[@]}" exec next dev --hostname "$FRONTEND_HOST" --port "$FRONTEND_PORT"
  ) >>"$FRONTEND_LOG_FILE" 2>&1 &
  FRONTEND_PID="$!"
  wait_for_port "前端" "$FRONTEND_HOST" "$FRONTEND_PORT" "$FRONTEND_PID" "$FRONTEND_LOG_FILE" || fail "前端启动失败。"
}

main() {
  trap handle_signal INT TERM
  trap cleanup EXIT

  ensure_config_file
  load_config_file
  ensure_uv
  ensure_pnpm
  sync_backend_dependencies
  sync_frontend_dependencies
  install_playwright_browsers
  resolve_python_bin
  resolve_port "后端" "$BACKEND_HOST" "$BACKEND_PORT" BACKEND_PORT
  resolve_port "前端" "$FRONTEND_HOST" "$FRONTEND_PORT" FRONTEND_PORT

  FRONTEND_OPEN_URL="http://$FRONTEND_HOST:$FRONTEND_PORT/?chatHistoryOpen=true"

  start_backend
  start_frontend
  open_frontend_url

  log
  log "Web AutoTest Agent 已启动。"
  log "前端地址：$FRONTEND_OPEN_URL"
  log "后端地址：http://$BACKEND_HOST:$BACKEND_PORT"
  log "后端日志：$BACKEND_LOG_FILE"
  log "前端日志：$FRONTEND_LOG_FILE"
  log "安装日志：$SETUP_LOG_FILE"
  log "关闭本窗口或按 Ctrl+C 会停止本地服务。"

  while true; do
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
      log "后端进程已退出，请查看 $BACKEND_LOG_FILE"
      exit 1
    fi
    if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
      log "前端进程已退出，请查看 $FRONTEND_LOG_FILE"
      exit 1
    fi
    sleep 1
  done
}

main "$@"

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
START_DIR="$PROJECT_ROOT/start"
BACKEND_DIR="$PROJECT_ROOT/web-agent"
FRONTEND_DIR="$PROJECT_ROOT/web-portal"
BACKEND_ENV_FILE="$BACKEND_DIR/.env"
START_SCRIPT_PATH="$SCRIPT_DIR/macos-start.command"
CACHE_DIR="$SCRIPT_DIR/.cache"
BACKEND_LOG_FILE="$START_DIR/backend.log"

BACKEND_HOST="127.0.0.1"
FRONTEND_HOST="127.0.0.1"
BACKEND_PORT="${BACKEND_PORT:-2024}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
STARTUP_WAIT_SECONDS="${STARTUP_WAIT_SECONDS:-90}"
NO_RELOAD="${NO_RELOAD:-1}"
SERVER_LOG_LEVEL="${SERVER_LOG_LEVEL:-}"
NEXT_PUBLIC_ASSISTANT_ID="${NEXT_PUBLIC_ASSISTANT_ID:-web-autotest-agent}"
NEXT_PUBLIC_AUTH_SCHEME="${NEXT_PUBLIC_AUTH_SCHEME:-}"
FRONTEND_OPEN_URL="${FRONTEND_OPEN_URL:-}"
OPEN_BROWSER="${OPEN_BROWSER:-1}"

MODE="${1:-start}"
ENTRY_DISPLAY_NAME="${WEB_AUTOTEST_ENTRY:-$0}"
BACKEND_PID=""
FRONTEND_PID=""
CLEANED_UP=0
STARTUP_STEP_INDEX=0
STARTUP_TOTAL_STEPS=9
STOP_STEP_INDEX=0
STOP_TOTAL_STEPS=4
PYTHON_BIN=""

mkdir -p "$CACHE_DIR"

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

  fail "未找到项目配置文件：$BACKEND_ENV_FILE。请先参考 $BACKEND_DIR/.env.example 创建并填写它。"
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
  curl -LsSf https://astral.sh/uv/install.sh | sh || fail "uv 安装失败。"
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
    run_logged_command corepack enable || true
    run_logged_command corepack prepare pnpm@10.5.1 --activate || fail "corepack 准备 pnpm 失败。"
  elif command -v npm >/dev/null 2>&1; then
    setup_log "未找到 pnpm/corepack，使用 npm 全局安装 pnpm@10.5.1..."
    run_logged_command npm install -g pnpm@10.5.1 || fail "pnpm 安装失败。"
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
    run_logged_command uv sync --project "$BACKEND_DIR" --extra dev || fail "后端依赖同步失败。"
  else
    setup_log "后端依赖已存在，跳过同步。"
  fi

  [ -x "$langgraph_bin" ] || fail "未找到 LangGraph 可执行文件：$langgraph_bin"
  LANGGRAPH_BIN="$langgraph_bin"
}

sync_frontend_dependencies() {
  if [ ! -d "$FRONTEND_DIR/node_modules" ] || [ "${START_FORCE_SETUP:-0}" = "1" ]; then
    setup_log "开始同步前端依赖..."
    run_logged_command_in_dir "$FRONTEND_DIR" "${PNPM_CMD[@]}" install || fail "前端依赖同步失败。"
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
  run_logged_command_in_dir "$FRONTEND_DIR" npx --yes playwright install chromium || fail "Playwright Chromium 浏览器安装失败。"
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
        setup_log "$name 未能在 ${STARTUP_WAIT_SECONDS}s 内监听 $host:$port。最近日志："
        tail -n 40 "$log_file" 2>/dev/null || true
      else
        setup_log "$name 未能在 ${STARTUP_WAIT_SECONDS}s 内监听 $host:$port，请查看当前终端输出。"
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
    log "未找到 lsof，无法按端口清理 $name；仅已尝试停止启动脚本进程。"
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

open_frontend_url() {
  if [ "$OPEN_BROWSER" != "1" ]; then
    setup_log "已跳过自动打开浏览器，请手动访问：$FRONTEND_OPEN_URL"
    return
  fi

  if command -v open >/dev/null 2>&1; then
    if open "$FRONTEND_OPEN_URL" >/dev/null 2>&1; then
      setup_log "已自动打开前端页面：$FRONTEND_OPEN_URL"
      return
    fi
    setup_log "open 命令打开前端失败，尝试使用 AppleScript。"
  fi

  if command -v osascript >/dev/null 2>&1; then
    if osascript -e "open location \"$FRONTEND_OPEN_URL\"" >/dev/null 2>&1; then
      setup_log "已通过 AppleScript 打开前端页面：$FRONTEND_OPEN_URL"
      return
    fi
    setup_log "AppleScript 打开前端失败，尝试使用 Python 浏览器回退。"
  fi

  if "$PYTHON_BIN" -m webbrowser "$FRONTEND_OPEN_URL" >/dev/null 2>&1; then
    setup_log "已通过 Python 浏览器回退打开前端页面：$FRONTEND_OPEN_URL"
    return
  fi

  setup_log "无法自动打开浏览器，请手动访问：$FRONTEND_OPEN_URL"
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
    langgraph_args=(dev --host "$BACKEND_HOST" --port "$BACKEND_PORT" --no-browser)
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

start_frontend() {
  export NEXT_PUBLIC_API_URL="http://$BACKEND_HOST:$BACKEND_PORT"
  export NEXT_PUBLIC_ASSISTANT_ID
  export NEXT_PUBLIC_AUTH_SCHEME

  setup_log "启动前端：http://$FRONTEND_HOST:$FRONTEND_PORT"
  (
    cd "$FRONTEND_DIR"
    exec "${PNPM_CMD[@]}" exec next dev --hostname "$FRONTEND_HOST" --port "$FRONTEND_PORT"
  ) &
  FRONTEND_PID="$!"
  wait_for_port "前端" "$FRONTEND_HOST" "$FRONTEND_PORT" "$FRONTEND_PID" || fail "前端启动失败。"
}

start_main() {
  trap handle_signal INT TERM
  trap cleanup EXIT
  : > "$BACKEND_LOG_FILE"

  start_step "检查项目配置"
  ensure_backend_env_file
  load_backend_env_file
  finish_step "检查项目配置"

  start_step "检查/准备 uv"
  ensure_uv
  finish_step "检查/准备 uv"

  start_step "检查/准备 pnpm"
  ensure_pnpm
  finish_step "检查/准备 pnpm"

  start_step "同步后端依赖"
  sync_backend_dependencies
  finish_step "同步后端依赖"

  start_step "同步前端依赖"
  sync_frontend_dependencies
  finish_step "同步前端依赖"

  start_step "安装 Playwright 浏览器"
  install_playwright_browsers
  finish_step "安装 Playwright 浏览器"

  start_step "解析 Python 与端口"
  resolve_python_bin
  resolve_port "后端" "$BACKEND_HOST" "$BACKEND_PORT" BACKEND_PORT
  resolve_port "前端" "$FRONTEND_HOST" "$FRONTEND_PORT" FRONTEND_PORT
  finish_step "解析 Python 与端口"

  if [ -z "$FRONTEND_OPEN_URL" ]; then
    FRONTEND_OPEN_URL="http://$FRONTEND_HOST:$FRONTEND_PORT/?chatHistoryOpen=true"
  fi

  start_step "启动后端"
  start_backend
  finish_step "启动后端"

  start_step "启动前端并尝试打开页面"
  start_frontend
  open_frontend_url
  finish_step "启动前端并尝试打开页面"

  log
  log "Web AutoTest Agent 已启动。"
  log "前端地址：$FRONTEND_OPEN_URL"
  log "后端地址：http://$BACKEND_HOST:$BACKEND_PORT"
  log "后端日志：$BACKEND_LOG_FILE"
  log "关闭本窗口或按 Ctrl+C 会停止本地服务。"

  while true; do
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
      log "后端进程已退出，请查看 $BACKEND_LOG_FILE"
      exit 1
    fi
    if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
      log "前端进程已退出，请查看当前终端输出。"
      exit 1
    fi
    sleep 1
  done
}

stop_main() {
  start_stop_step "加载项目配置"
  if [ -f "$BACKEND_ENV_FILE" ]; then
    import_project_env_file
  else
    log "未找到项目配置文件：$BACKEND_ENV_FILE，关闭脚本将使用默认端口。"
  fi
  finish_stop_step "加载项目配置"

  start_stop_step "停止启动脚本进程"
  stop_script_sessions "macos-start.command" "$START_SCRIPT_PATH"
  finish_stop_step "停止启动脚本进程"

  start_stop_step "停止后端服务"
  resolve_python_bin
  stop_listener "后端" "$BACKEND_HOST" "$BACKEND_PORT" "backend"
  finish_stop_step "停止后端服务"

  start_stop_step "停止前端服务"
  stop_listener "前端" "$FRONTEND_HOST" "$FRONTEND_PORT" "frontend"
  finish_stop_step "停止前端服务"

  log
  log "本地服务关闭完成。"
  log "后端端口：$BACKEND_HOST:$BACKEND_PORT"
  log "前端端口：$FRONTEND_HOST:$FRONTEND_PORT"
}

main() {
  case "$MODE" in
    start)
      start_main
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

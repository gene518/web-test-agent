#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SCRIPT_PATH="$SCRIPT_DIR/$(basename "${BASH_SOURCE[0]}")"
BACKEND_DIR="$PROJECT_ROOT/web-agent"
FRONTEND_DIR="$PROJECT_ROOT/web-poartl"
LANGGRAPH_BIN="$BACKEND_DIR/.venv/bin/langgraph"
LOG_DIR="$SCRIPT_DIR"
BACKEND_LOG_FILE="$SCRIPT_DIR/backend.log"
FRONTEND_LOG_FILE="$SCRIPT_DIR/frontend.log"
BACKEND_HOST="127.0.0.1"
BACKEND_PORT="2024"
FRONTEND_HOST="127.0.0.1"
FRONTEND_PORT="3000"
STARTUP_WAIT_SECONDS="${STARTUP_WAIT_SECONDS:-30}"
NO_RELOAD="${NO_RELOAD:-1}"
SERVER_LOG_LEVEL="${SERVER_LOG_LEVEL:-}"
NEXT_PUBLIC_API_URL="http://$BACKEND_HOST:$BACKEND_PORT"
NEXT_PUBLIC_ASSISTANT_ID="${NEXT_PUBLIC_ASSISTANT_ID:-master}"
NEXT_PUBLIC_AUTH_SCHEME="${NEXT_PUBLIC_AUTH_SCHEME:-}"
FRONTEND_OPEN_URL="${FRONTEND_OPEN_URL:-http://127.0.0.1:3000/?chatHistoryOpen=true}"
OPEN_BROWSER="${OPEN_BROWSER:-1}"

BACKEND_PID=""
FRONTEND_PID=""
BACKEND_TAIL_PID=""
FRONTEND_TAIL_PID=""
CLEANED_UP=0

if [ ! -x "$LANGGRAPH_BIN" ]; then
  echo "Cannot find $LANGGRAPH_BIN. Run \`uv sync --project web-agent --extra dev\` first." >&2
  exit 127
fi

if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
  echo "Cannot find $FRONTEND_DIR/node_modules. Run \`cd web-poartl && corepack enable && corepack prepare pnpm@10.5.1 --activate && pnpm install\` first." >&2
  exit 127
fi

if ! command -v lsof >/dev/null 2>&1; then
  echo "Cannot find lsof. Install it or adjust the script to skip port cleanup." >&2
  exit 127
fi

if command -v pnpm >/dev/null 2>&1; then
  FRONTEND_CMD=(pnpm exec next)
elif command -v corepack >/dev/null 2>&1; then
  FRONTEND_CMD=(corepack pnpm exec next)
else
  echo "Cannot find pnpm or corepack. Install one of them to start the frontend." >&2
  exit 127
fi

if [ -x "$BACKEND_DIR/.venv/bin/python3" ]; then
  PYTHON_BIN="$BACKEND_DIR/.venv/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  echo "Cannot find python3. Set PYTHON_BIN to a usable interpreter." >&2
  exit 127
fi

mkdir -p "$LOG_DIR"
: > "$BACKEND_LOG_FILE"
: > "$FRONTEND_LOG_FILE"
rm -f "$SCRIPT_DIR/server.log"

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

listener_pids() {
  lsof -nP -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null || true
}

wait_for_port() {
  local name="$1"
  local host="$2"
  local port="$3"
  local pid="$4"
  local deadline=$((SECONDS + STARTUP_WAIT_SECONDS))

  until port_accepts_connections "$host" "$port"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      return 1
    fi
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "$name did not become reachable on $host:$port within ${STARTUP_WAIT_SECONDS}s." >&2
      return 1
    fi
    sleep 0.5
  done

  return 0
}

open_frontend_url() {
  if [ "$OPEN_BROWSER" != "1" ]; then
    return
  fi

  if command -v open >/dev/null 2>&1; then
    open "$FRONTEND_OPEN_URL" >/dev/null 2>&1 || true
    return
  fi

  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$FRONTEND_OPEN_URL" >/dev/null 2>&1 || true
  fi
}

start_log_tail() {
  local label="$1"
  local file="$2"
  local pid_var="$3"

  (
    tail -n +1 -F "$file" 2>/dev/null | sed -u "s/^/[$label] /"
  ) &
  printf -v "$pid_var" "%s" "$!"
}

collect_descendants() {
  local parent="$1"
  local children
  local child

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

find_other_dev_sessions() {
  local pid

  for pid in $(pgrep -f "$SCRIPT_PATH" 2>/dev/null || true); do
    if [ "$pid" = "$$" ] || [ "$pid" = "$PPID" ]; then
      continue
    fi
    echo "$pid"
  done
}

stop_other_dev_sessions() {
  local pid
  local found=0

  for pid in $(find_other_dev_sessions); do
    found=1
    echo "Stopping existing dev.sh process: $pid"
    kill_tree TERM "$pid"
  done

  if [ "$found" = "0" ]; then
    return
  fi

  sleep 0.5

  for pid in $(find_other_dev_sessions); do
    kill_tree KILL "$pid"
  done
}

ensure_port_available() {
  local name="$1"
  local host="$2"
  local port="$3"
  local pid
  local deadline

  pid="$(listener_pids "$port")"
  if [ -n "$pid" ]; then
    echo "$name port $port is in use by listener PID: $pid, stopping..."
    kill $pid 2>/dev/null || true
    sleep 1
  fi

  pid="$(listener_pids "$port")"
  if [ -n "$pid" ]; then
    echo "$name port $port is still in use by listener PID: $pid, force killing..."
    kill -9 $pid 2>/dev/null || true
  fi

  deadline=$((SECONDS + STARTUP_WAIT_SECONDS))
  until port_is_bindable "$host" "$port"; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      pid="$(listener_pids "$port")"
      if [ -n "$pid" ]; then
        echo "$name port $port is still occupied by listener PID: $pid after waiting ${STARTUP_WAIT_SECONDS}s." >&2
      else
        echo "$name port $port is not owned by a visible listener, but bindability is still unavailable after waiting ${STARTUP_WAIT_SECONDS}s." >&2
      fi
      return 1
    fi
    sleep 0.5
  done
}

cleanup() {
  local exit_code="${1:-0}"

  if [ "$CLEANED_UP" = "1" ]; then
    return
  fi
  CLEANED_UP=1

  kill_tree TERM "$BACKEND_TAIL_PID"
  kill_tree TERM "$FRONTEND_TAIL_PID"
  kill_tree TERM "$FRONTEND_PID"
  kill_tree TERM "$BACKEND_PID"

  sleep 0.5

  kill_tree KILL "$BACKEND_TAIL_PID"
  kill_tree KILL "$FRONTEND_TAIL_PID"
  kill_tree KILL "$FRONTEND_PID"
  kill_tree KILL "$BACKEND_PID"

  exit "$exit_code"
}

handle_signal() {
  echo
  echo "Stopping frontend and backend..."
  cleanup 130
}

trap handle_signal INT TERM

stop_other_dev_sessions

echo "Preparing fixed ports..."
ensure_port_available "Backend" "$BACKEND_HOST" "$BACKEND_PORT"
ensure_port_available "Frontend" "$FRONTEND_HOST" "$FRONTEND_PORT"

echo "Starting backend on http://$BACKEND_HOST:$BACKEND_PORT"
start_log_tail "backend" "$BACKEND_LOG_FILE" BACKEND_TAIL_PID
(
  cd "$BACKEND_DIR"
  LANGGRAPH_ARGS=(dev --host "$BACKEND_HOST" --port "$BACKEND_PORT" --no-browser)
  if [ "$NO_RELOAD" = "1" ]; then
    LANGGRAPH_ARGS+=(--no-reload)
  fi
  if [ -n "$SERVER_LOG_LEVEL" ]; then
    LANGGRAPH_ARGS+=(--server-log-level "$SERVER_LOG_LEVEL")
  fi
  exec "$LANGGRAPH_BIN" "${LANGGRAPH_ARGS[@]}"
) >"$BACKEND_LOG_FILE" 2>&1 &
BACKEND_PID="$!"

if ! wait_for_port "Backend" "$BACKEND_HOST" "$BACKEND_PORT" "$BACKEND_PID"; then
  echo "Backend failed to start. Check $BACKEND_LOG_FILE" >&2
  cleanup 1
fi

echo "Starting frontend on http://$FRONTEND_HOST:$FRONTEND_PORT"
start_log_tail "frontend" "$FRONTEND_LOG_FILE" FRONTEND_TAIL_PID
(
  cd "$FRONTEND_DIR"
  export NEXT_PUBLIC_API_URL
  export NEXT_PUBLIC_ASSISTANT_ID
  export NEXT_PUBLIC_AUTH_SCHEME
  exec "${FRONTEND_CMD[@]}" dev --hostname "$FRONTEND_HOST" --port "$FRONTEND_PORT"
) >"$FRONTEND_LOG_FILE" 2>&1 &
FRONTEND_PID="$!"

if ! wait_for_port "Frontend" "$FRONTEND_HOST" "$FRONTEND_PORT" "$FRONTEND_PID"; then
  echo "Frontend failed to start. Check $FRONTEND_LOG_FILE" >&2
  cleanup 1
fi

open_frontend_url

{
  echo "Backend ready:        http://$BACKEND_HOST:$BACKEND_PORT"
  echo "Frontend ready:       http://$FRONTEND_HOST:$FRONTEND_PORT"
  echo "Frontend page:        $FRONTEND_OPEN_URL"
  echo "Frontend API target:  $NEXT_PUBLIC_API_URL"
  echo "Assistant ID:         $NEXT_PUBLIC_ASSISTANT_ID"
  echo "Backend log:          $BACKEND_LOG_FILE"
  echo "Frontend log:         $FRONTEND_LOG_FILE"
  echo "Backend reload:       $( [ "$NO_RELOAD" = "1" ] && echo "disabled" || echo "enabled" )"
}
echo "Press Ctrl+C to stop both services."

while true; do
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    wait "$BACKEND_PID" || true
    echo "Backend exited. Stopping frontend..." >&2
    cleanup 1
  fi

  if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
    wait "$FRONTEND_PID" || true
    echo "Frontend exited. Stopping backend..." >&2
    cleanup 1
  fi

  sleep 1
done

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PORT="${PORT:-2024}"
HOST="${HOST:-127.0.0.1}"
LOG_FILE="${LOG_FILE:-$SCRIPT_DIR/langgraph-dev.log}"
LANGGRAPH_BIN="${LANGGRAPH_BIN:-$PROJECT_ROOT/.venv/bin/langgraph}"
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_ROOT/.venv/bin/python3}"
PORT_WAIT_SECONDS="${PORT_WAIT_SECONDS:-15}"
PORT_STRICT="${PORT_STRICT:-0}"
NO_RELOAD="${NO_RELOAD:-0}"
SERVER_LOG_LEVEL="${SERVER_LOG_LEVEL:-}"

if [ ! -x "$LANGGRAPH_BIN" ]; then
  echo "Cannot find langgraph. Install dev dependencies or set LANGGRAPH_BIN." >&2
  exit 127
fi

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Cannot find python3. Set PYTHON_BIN to the interpreter for the log filter." >&2
  exit 127
fi

mkdir -p "$(dirname "$LOG_FILE")"

cd "$PROJECT_ROOT"

port_is_bindable() {
  "$PYTHON_BIN" - "$HOST" "$PORT" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    # Align the probe with typical dev servers on macOS/BSD, where a port may be
    # immediately reusable for a new listener even if the kernel is still
    # draining recently closed connections.
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError:
        raise SystemExit(1)

raise SystemExit(0)
PY
}

listener_pids() {
  lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true
}

PID="$(listener_pids)"

if [ -n "$PID" ]; then
  echo "Port $PORT is in use by listener PID: $PID, stopping..."
  kill $PID || true
  sleep 1
fi

PID="$(listener_pids)"
if [ -n "$PID" ]; then
  echo "Port $PORT is still in use by listener PID: $PID, force killing..."
  kill -9 $PID || true
fi

deadline=$((SECONDS + PORT_WAIT_SECONDS))
until port_is_bindable; do
  if [ "$SECONDS" -ge "$deadline" ]; then
    PID="$(listener_pids)"
    if [ -n "$PID" ]; then
      if [ "$PORT_STRICT" = "1" ]; then
        echo "Port $PORT is still occupied by listener PID: $PID after waiting ${PORT_WAIT_SECONDS}s." >&2
        echo "Retry in a moment or run with a different port, for example: PORT=$((PORT + 1)) tests/debug/dev.sh" >&2
        exit 1
      fi
      echo "Port $PORT is still occupied by listener PID: $PID after waiting ${PORT_WAIT_SECONDS}s." >&2
      echo "Falling back to langgraph auto-discovery. Set PORT_STRICT=1 to fail instead." >&2
      break
    else
      if [ "$PORT_STRICT" = "1" ]; then
        echo "Port $PORT is not owned by a visible listener, but the probe still cannot confirm bindability after waiting ${PORT_WAIT_SECONDS}s." >&2
        echo "Set PORT_STRICT=0 or omit it to let langgraph auto-discover an available port." >&2
        exit 1
      fi
      echo "Port $PORT is not owned by a visible listener, but the probe still cannot confirm bindability after waiting ${PORT_WAIT_SECONDS}s." >&2
      echo "Falling back to langgraph auto-discovery. Set PORT_STRICT=1 to fail instead." >&2
      break
    fi
  fi
  sleep 0.5
done

: > "$LOG_FILE"

LANGGRAPH_ARGS=(dev --host "$HOST")
if port_is_bindable; then
  LANGGRAPH_ARGS+=(--port "$PORT")
  echo "Starting langgraph dev on $HOST:$PORT..."
else
  echo "Starting langgraph dev on $HOST with auto-discovered port (preferred $PORT)..."
fi
if [ "$NO_RELOAD" = "1" ]; then
  LANGGRAPH_ARGS+=(--no-reload)
fi
if [ -n "$SERVER_LOG_LEVEL" ]; then
  LANGGRAPH_ARGS+=(--server-log-level "$SERVER_LOG_LEVEL")
fi
echo "Writing log to $LOG_FILE"
echo "Filtering watchfiles logs and localizing UTC timestamps"

"$LANGGRAPH_BIN" "${LANGGRAPH_ARGS[@]}" 2>&1 \
  | "$PYTHON_BIN" "$SCRIPT_DIR/filter_langgraph_log.py" \
  | tee "$LOG_FILE"

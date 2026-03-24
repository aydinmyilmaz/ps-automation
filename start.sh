#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_HOST="127.0.0.1"
API_PORT="8000"
WEB_PORT="5173"
API_PID=""
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "[ERROR] Required command not found: $cmd" >&2
    exit 1
  fi
}

check_port_free() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    if lsof -iTCP:"$port" -sTCP:LISTEN -n -P >/dev/null 2>&1; then
      echo "[ERROR] Port $port is already in use." >&2
      exit 1
    fi
  fi
}

cleanup() {
  if [[ -n "${API_PID}" ]]; then
    kill "${API_PID}" >/dev/null 2>&1 || true
    wait "${API_PID}" 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

require_cmd node
require_cmd npm

check_port_free "$API_PORT"
check_port_free "$WEB_PORT"

mkdir -p "$ROOT_DIR/output/web_single"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[ERROR] .venv not found." >&2
  echo "[INFO] Set up this repo with uv and Python 3.11 first:" >&2
  echo "[INFO]   uv python install 3.11" >&2
  echo "[INFO]   uv venv --python 3.11 .venv" >&2
  echo "[INFO]   source .venv/bin/activate" >&2
  echo "[INFO]   uv pip install -r requirements-desktop.txt" >&2
  exit 1
fi

if [[ ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
  echo "[INFO] Installing frontend dependencies..."
  (cd "$ROOT_DIR/frontend" && npm install)
fi

echo "[INFO] Starting API on http://$API_HOST:$API_PORT ..."
"$PYTHON_BIN" "$ROOT_DIR/scripts/single_render_api.py" &
API_PID="$!"

sleep 1
if ! kill -0 "$API_PID" >/dev/null 2>&1; then
  echo "[ERROR] API failed to start." >&2
  exit 1
fi

echo "[INFO] Starting frontend on http://127.0.0.1:$WEB_PORT ..."
echo "[INFO] Open in browser: http://127.0.0.1:$WEB_PORT"
echo "[INFO] Press Ctrl+C to stop all."

cd "$ROOT_DIR/frontend"
npm run dev -- --host 127.0.0.1 --port "$WEB_PORT"

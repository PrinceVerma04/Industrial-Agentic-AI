#!/usr/bin/env bash
# Full-flow startup: verify ports are free, verify Ollama is up, then
# launch the API (8077) and the Streamlit console (8501) together.
#
# Usage: ./scripts/06_start_all.sh
# Stop:  Ctrl+C (kills both background processes via the trap below)

set -euo pipefail
cd "$(dirname "$0")/.."

VENV=./.venv/bin
API_PORT=8077
APP_PORT=8501
OLLAMA_PORT=11434

port_busy() {
  # Returns 0 (true) if something is already listening on $1
  ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$1\$"
}

fail=0

if port_busy "$OLLAMA_PORT"; then
  echo "[ok] Ollama already listening on $OLLAMA_PORT"
else
  echo "[!] Ollama is not running on $OLLAMA_PORT. Start it first: 'ollama serve'"
  fail=1
fi

if port_busy "$API_PORT"; then
  echo "[x] Port $API_PORT is already in use — refusing to start the API on it."
  echo "    Find the process with: ss -ltnp | grep $API_PORT"
  fail=1
else
  echo "[ok] Port $API_PORT is free for the API"
fi

if port_busy "$APP_PORT"; then
  echo "[x] Port $APP_PORT is already in use — refusing to start Streamlit on it."
  echo "    Find the process with: ss -ltnp | grep $APP_PORT"
  fail=1
else
  echo "[ok] Port $APP_PORT is free for the Streamlit console"
fi

if [ "$fail" -ne 0 ]; then
  echo "Aborting: resolve the port conflicts above before starting the workbench."
  exit 1
fi

echo "All ports clear. Starting API (:$API_PORT) first (it holds the graph write lock)..."

"$VENV/uvicorn" app.main:app --host 127.0.0.1 --port "$API_PORT" --reload &
API_PID=$!

trap 'echo; echo "Stopping..."; kill "$API_PID" "${APP_PID:-}" 2>/dev/null; wait' INT TERM

# Wait for the API to actually open the graph store before starting the
# read-only Streamlit console. Opening both at once races Kuzu's lock file
# and can make the console mis-detect "first run" and try to open as a
# second writer -> "Could not set lock on file".
echo -n "Waiting for API health check"
for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1; then
    echo " - up."
    break
  fi
  echo -n "."
  sleep 1
  if [ "$i" -eq 30 ]; then
    echo
    echo "[x] API did not become healthy in time. Not starting the console."
    kill "$API_PID" 2>/dev/null
    exit 1
  fi
done

"$VENV/streamlit" run streamlit_app.py --server.address 127.0.0.1 --server.port "$APP_PORT" &
APP_PID=$!

echo "API:     http://127.0.0.1:$API_PORT"
echo "Console: http://127.0.0.1:$APP_PORT"
echo "Press Ctrl+C to stop both."

wait

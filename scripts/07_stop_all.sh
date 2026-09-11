#!/usr/bin/env bash
# Stop the workbench: kills whatever is listening on the API (8077) and
# Streamlit console (8501) ports, so the Kuzu graph/Qdrant vector locks are
# released and 'make index' can run cleanly.
#
# Usage: ./scripts/07_stop_all.sh   (or: make stop)

set -uo pipefail

API_PORT=8077
APP_PORT=8501

stop_port() {
  local port="$1" label="$2"
  if ! ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$port\$"; then
    echo "[ok] $label ($port) already stopped"
    return
  fi
  echo "[..] stopping $label on port $port"
  fuser -k -TERM "$port"/tcp 2>/dev/null
  for _ in $(seq 1 10); do
    ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$port\$" || break
    sleep 0.5
  done
  if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$port\$"; then
    echo "[!] $label still up after TERM, sending KILL"
    fuser -k -KILL "$port"/tcp 2>/dev/null
    sleep 0.5
  fi
  if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$port\$"; then
    echo "[x] failed to stop $label on port $port"
  else
    echo "[ok] $label stopped"
  fi
}

stop_port "$API_PORT" "API"
stop_port "$APP_PORT" "Streamlit console"

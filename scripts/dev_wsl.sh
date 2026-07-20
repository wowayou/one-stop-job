#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/data/dev"
SUPERVISOR_PID_FILE="$RUNTIME_DIR/supervisor.pid"
BACKEND_PID_FILE="$RUNTIME_DIR/backend.pid"
FRONTEND_PID_FILE="$RUNTIME_DIR/frontend.pid"
SUPERVISOR_LOG="$RUNTIME_DIR/supervisor.log"
BACKEND_LOG="$RUNTIME_DIR/backend.log"
FRONTEND_LOG="$RUNTIME_DIR/frontend.log"

mkdir -p "$RUNTIME_DIR"
touch "$SUPERVISOR_LOG" "$BACKEND_LOG" "$FRONTEND_LOG"

is_running() {
  local pid_file="$1"
  [[ -f "$pid_file" ]] || return 1
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

require_backend_deps() {
  if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
    echo "Missing .venv/bin/python."
    echo "Run in WSL: python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt"
    exit 1
  fi
}

require_frontend_deps() {
  if [[ ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
    echo "Missing frontend/node_modules."
    echo "Run in WSL: cd frontend && npm install"
    exit 1
  fi
}

start_backend() {
  require_backend_deps
  if is_running "$BACKEND_PID_FILE"; then
    echo "Backend already running, pid $(cat "$BACKEND_PID_FILE")."
    return
  fi
  (
    cd "$ROOT_DIR"
    nohup .venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 >"$BACKEND_LOG" 2>&1 &
    echo "$!" >"$BACKEND_PID_FILE"
  )
  echo "Backend started: http://127.0.0.1:8000"
  echo "Backend log: $BACKEND_LOG"
}

start_frontend() {
  require_frontend_deps
  if is_running "$FRONTEND_PID_FILE"; then
    echo "Frontend already running, pid $(cat "$FRONTEND_PID_FILE")."
    return
  fi
  (
    cd "$ROOT_DIR/frontend"
    nohup node ./node_modules/vite/bin/vite.js --host 127.0.0.1 >"$FRONTEND_LOG" 2>&1 &
    echo "$!" >"$FRONTEND_PID_FILE"
  )
  echo "Frontend started: http://127.0.0.1:5173"
  echo "Frontend log: $FRONTEND_LOG"
}

stop_one() {
  local name="$1"
  local pid_file="$2"
  if ! is_running "$pid_file"; then
    rm -f "$pid_file"
    echo "$name is not running."
    return
  fi
  local pid
  pid="$(cat "$pid_file")"
  stop_children "$pid" TERM
  kill "$pid" 2>/dev/null || true
  for _ in {1..20}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$pid_file"
      echo "$name stopped."
      return
    fi
    sleep 0.2
  done
  stop_children "$pid" KILL
  kill -9 "$pid" 2>/dev/null || true
  rm -f "$pid_file"
  echo "$name force-stopped."
}

stop_children() {
  local parent_pid="$1"
  local signal="$2"
  local child
  while read -r child; do
    [[ -n "$child" ]] || continue
    stop_children "$child" "$signal"
    kill "-$signal" "$child" 2>/dev/null || true
  done < <(pgrep -P "$parent_pid" 2>/dev/null || true)
}

status_one() {
  local name="$1"
  local pid_file="$2"
  local url="$3"
  if is_running "$pid_file"; then
    echo "$name: running, pid $(cat "$pid_file"), $url"
  else
    echo "$name: stopped"
  fi
}

start_all() {
  start_backend
  start_frontend
  echo
  echo "Open http://127.0.0.1:5173/"
}

stop_all() {
  stop_supervisor
  stop_one "Frontend" "$FRONTEND_PID_FILE"
  stop_one "Backend" "$BACKEND_PID_FILE"
  stop_port 5173 "Frontend"
  stop_port 8000 "Backend"
}

status_all() {
  status_one "Supervisor" "$SUPERVISOR_PID_FILE" "hidden WSL runner"
  status_one "Backend" "$BACKEND_PID_FILE" "http://127.0.0.1:8000"
  status_one "Frontend" "$FRONTEND_PID_FILE" "http://127.0.0.1:5173"
  echo "Logs: $RUNTIME_DIR"
}

stop_port() {
  local port="$1"
  local name="$2"
  if ! command -v fuser >/dev/null 2>&1; then
    return
  fi
  if fuser "${port}/tcp" >/dev/null 2>&1; then
    echo "$name port $port is still in use; stopping listener."
    fuser -k "${port}/tcp" >/dev/null 2>&1 || true
  fi
}

stop_supervisor() {
  if ! is_running "$SUPERVISOR_PID_FILE"; then
    rm -f "$SUPERVISOR_PID_FILE"
    return
  fi
  local pid
  pid="$(cat "$SUPERVISOR_PID_FILE")"
  if [[ "$pid" == "$$" ]]; then
    return
  fi
  kill "$pid" 2>/dev/null || true
  for _ in {1..20}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$SUPERVISOR_PID_FILE"
      echo "Supervisor stopped."
      return
    fi
    sleep 0.2
  done
  kill -9 "$pid" 2>/dev/null || true
  rm -f "$SUPERVISOR_PID_FILE"
  echo "Supervisor force-stopped."
}

serve_all() {
  require_backend_deps
  require_frontend_deps
  if is_running "$SUPERVISOR_PID_FILE"; then
    echo "Supervisor already running, pid $(cat "$SUPERVISOR_PID_FILE")."
    exit 0
  fi
  echo "$$" >"$SUPERVISOR_PID_FILE"

  cleanup() {
    stop_one "Frontend" "$FRONTEND_PID_FILE"
    stop_one "Backend" "$BACKEND_PID_FILE"
    rm -f "$SUPERVISOR_PID_FILE"
  }
  trap cleanup EXIT INT TERM

  echo "Supervisor started at $(date -Is), pid $$" >>"$SUPERVISOR_LOG"
  echo "Supervisor started at $(date -Is), pid $$" >>"$BACKEND_LOG"
  echo "Supervisor started at $(date -Is), pid $$" >>"$FRONTEND_LOG"
  start_backend
  start_frontend

  while true; do
    if ! is_running "$BACKEND_PID_FILE"; then
      echo "Backend stopped at $(date -Is); restarting." >>"$BACKEND_LOG"
      rm -f "$BACKEND_PID_FILE"
      start_backend
    fi
    if ! is_running "$FRONTEND_PID_FILE"; then
      echo "Frontend stopped at $(date -Is); restarting." >>"$FRONTEND_LOG"
      rm -f "$FRONTEND_PID_FILE"
      start_frontend
    fi
    sleep 3
  done
}

serve_detached() {
  require_backend_deps
  require_frontend_deps
  if is_running "$SUPERVISOR_PID_FILE"; then
    echo "Supervisor already running, pid $(cat "$SUPERVISOR_PID_FILE")."
    return
  fi
  rm -f "$SUPERVISOR_PID_FILE"
  (
    cd "$ROOT_DIR"
    nohup setsid bash scripts/dev_wsl.sh serve >>"$SUPERVISOR_LOG" 2>&1 &
  )
  for _ in {1..30}; do
    if is_running "$SUPERVISOR_PID_FILE"; then
      echo "Supervisor started, pid $(cat "$SUPERVISOR_PID_FILE")."
      return
    fi
    sleep 0.2
  done
  echo "Supervisor failed to start. Recent log:"
  tail -n 40 "$SUPERVISOR_LOG"
  exit 1
}

case "${1:-status}" in
  start)
    start_all
    ;;
  start-backend)
    start_backend
    ;;
  start-frontend)
    start_frontend
    ;;
  stop)
    stop_all
    ;;
  serve)
    serve_all
    ;;
  serve-detached)
    serve_detached
    ;;
  status)
    status_all
    ;;
  logs)
    tail -n 80 -f "$BACKEND_LOG" "$FRONTEND_LOG"
    ;;
  *)
    echo "Usage: $0 {start|start-backend|start-frontend|serve|serve-detached|stop|status|logs}"
    exit 2
    ;;
esac

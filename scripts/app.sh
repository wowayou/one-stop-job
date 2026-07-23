#!/usr/bin/env bash
set -euo pipefail

# 单进程部署模式:前端构建产物由后端直接挂载(frontend/dist),只跑一个 uvicorn 进程。
# 运行时文件放 data/app/,与 scripts/dev_wsl.sh 的 data/dev/ 互不干扰,可以各自独立启停。

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/data/app"
BACKEND_PID_FILE="$RUNTIME_DIR/backend.pid"
BACKEND_LOG="$RUNTIME_DIR/backend.log"
PORT="${PORT:-8000}"

mkdir -p "$RUNTIME_DIR"
touch "$BACKEND_LOG"

is_running() {
  local pid_file="$1"
  [[ -f "$pid_file" ]] || return 1
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null
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

# config.yaml 是本地个人配置（gitignore），首次运行从 config.example.yaml 生成一份，
# 之后你的改动不再和 git pull 冲突（模板更新只影响 config.example.yaml）。
ensure_config() {
  if [[ ! -f "$ROOT_DIR/config.yaml" && -f "$ROOT_DIR/config.example.yaml" ]]; then
    cp "$ROOT_DIR/config.example.yaml" "$ROOT_DIR/config.yaml"
    echo "已从 config.example.yaml 生成 config.yaml（本地配置，不入 Git）。"
  fi
}

ensure_backend_deps() {
  if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
    echo "缺少 .venv,首次安装依赖(约 2-3 分钟)..."
    (cd "$ROOT_DIR" && python3 -m venv --clear .venv)
    (cd "$ROOT_DIR" && .venv/bin/python -m pip install -r requirements.txt)
  fi
}

ensure_frontend_deps() {
  if [[ ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
    echo "缺少 frontend/node_modules,安装前端依赖..."
    (cd "$ROOT_DIR/frontend" && npm install)
  fi
}

build_frontend() {
  (cd "$ROOT_DIR/frontend" && npm run build)
  local head_commit
  head_commit="$(git -C "$ROOT_DIR" rev-parse HEAD 2>/dev/null || true)"
  if [[ -n "$head_commit" ]]; then
    echo "$head_commit" >"$ROOT_DIR/frontend/dist/.build-commit"
  fi
}

# dist/ 不进 git,git pull 后残留的旧构建会让页面缺新功能;用构建时的 commit 指纹判断新旧。
ensure_frontend_build() {
  local dist_index="$ROOT_DIR/frontend/dist/index.html"
  local stamp_file="$ROOT_DIR/frontend/dist/.build-commit"
  local head_commit
  head_commit="$(git -C "$ROOT_DIR" rev-parse HEAD 2>/dev/null || true)"
  if [[ -f "$dist_index" ]]; then
    if [[ -z "$head_commit" || "$(cat "$stamp_file" 2>/dev/null || true)" == "$head_commit" ]]; then
      return
    fi
    echo "前端构建来自其它代码版本(git pull 后常见),重新构建..."
  else
    echo "缺少 frontend/dist,构建前端..."
  fi
  build_frontend
}

# 端口占用检查:如果端口已被本脚本以外的进程占用,拒绝启动并给出明确提示。
check_port_free_or_owned() {
  local port="$1"
  local listener_pid=""
  if command -v lsof >/dev/null 2>&1; then
    listener_pid="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -n1 || true)"
  elif command -v fuser >/dev/null 2>&1; then
    listener_pid="$(fuser "${port}/tcp" 2>/dev/null | tr -d ' ' || true)"
  fi
  [[ -n "$listener_pid" ]] || return 0

  if is_running "$BACKEND_PID_FILE" && [[ "$listener_pid" == "$(cat "$BACKEND_PID_FILE")" ]]; then
    return 0
  fi

  echo "端口 $port 已被 pid $listener_pid 占用,不是本脚本(scripts/app.sh)管理的进程。" >&2
  echo "可能是本地开发模式后端(scripts/dev_wsl.sh)或 Docker(docker compose)在运行,请先停掉再启动单进程模式。" >&2
  exit 1
}

wait_for_health() {
  local port="$1"
  local tries=30 # 约 15 秒(0.5s * 30)
  local i
  for ((i = 0; i < tries; i++)); do
    if curl -fsS "http://127.0.0.1:${port}/api/health" >/dev/null 2>&1; then
      return 0
    fi
    if ! is_running "$BACKEND_PID_FILE"; then
      return 1
    fi
    sleep 0.5
  done
  return 1
}

do_start() {
  if is_running "$BACKEND_PID_FILE"; then
    echo "已在运行,pid $(cat "$BACKEND_PID_FILE"),http://127.0.0.1:${PORT}/"
    return 0
  fi

  ensure_config
  ensure_backend_deps
  ensure_frontend_deps
  ensure_frontend_build
  check_port_free_or_owned "$PORT"

  # 日志超过 10MB 就滚动一份,避免 backend.log 无限增长。
  if [[ -f "$BACKEND_LOG" ]] && [[ "$(wc -c <"$BACKEND_LOG")" -gt $((10 * 1024 * 1024)) ]]; then
    mv -f "$BACKEND_LOG" "$BACKEND_LOG.1"
    touch "$BACKEND_LOG"
  fi

  (
    cd "$ROOT_DIR"
    nohup .venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port "$PORT" >>"$BACKEND_LOG" 2>&1 &
    echo "$!" >"$BACKEND_PID_FILE"
  )

  echo "启动中,等待健康检查..."
  if wait_for_health "$PORT"; then
    echo "已启动: http://127.0.0.1:${PORT}/"
  else
    echo "启动失败或健康检查超时,最近日志:" >&2
    tail -n 40 "$BACKEND_LOG" >&2
    exit 1
  fi
}

do_stop() {
  if ! is_running "$BACKEND_PID_FILE"; then
    rm -f "$BACKEND_PID_FILE"
    echo "未运行。"
    return 0
  fi
  local pid
  pid="$(cat "$BACKEND_PID_FILE")"
  stop_children "$pid" TERM
  kill "$pid" 2>/dev/null || true
  for _ in {1..20}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$BACKEND_PID_FILE"
      echo "已停止。"
      return 0
    fi
    sleep 0.2
  done
  stop_children "$pid" KILL
  kill -9 "$pid" 2>/dev/null || true
  rm -f "$BACKEND_PID_FILE"
  echo "已强制停止。"
}

do_status() {
  if is_running "$BACKEND_PID_FILE"; then
    echo "进程: running, pid $(cat "$BACKEND_PID_FILE")"
  else
    echo "进程: stopped"
  fi
  if curl -fsS "http://127.0.0.1:${PORT}/api/health" 2>/dev/null; then
    echo
  else
    echo "健康检查: 无响应 (http://127.0.0.1:${PORT}/api/health)"
  fi
  echo "日志: $BACKEND_LOG"
}

do_logs() {
  tail -n 80 -f "$BACKEND_LOG"
}

do_update() {
  echo "更新依赖并重新构建前端..."
  ensure_config
  ensure_backend_deps
  (cd "$ROOT_DIR" && .venv/bin/python -m pip install -r requirements.txt)
  (cd "$ROOT_DIR/frontend" && npm install)
  build_frontend

  if is_running "$BACKEND_PID_FILE"; then
    echo "检测到正在运行,重启..."
    do_stop
    do_start
  else
    echo "当前未运行,更新完成。运行 'scripts/app.sh start' 启动。"
  fi
}

do_backup() {
  local db_path="$ROOT_DIR/data/job_one_stop/job_one_stop.sqlite3"
  local attachments_dir="$ROOT_DIR/data/job_one_stop/chat_attachments"

  if [[ ! -f "$db_path" ]]; then
    echo "尚无数据可备份(未找到 $db_path)。"
    return 0
  fi
  if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
    echo "缺少 .venv,请先运行一次 scripts/app.sh start 建好虚拟环境。" >&2
    exit 1
  fi

  local backup_dir="$ROOT_DIR/data/backups/$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$backup_dir"

  # SQLite Connection.backup() 是并发安全的在线备份,后端运行中也能用。
  "$ROOT_DIR/.venv/bin/python" -c '
import sqlite3
import sys

src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
src.close()
dst.close()
' "$db_path" "$backup_dir/job_one_stop.sqlite3"

  if [[ -d "$attachments_dir" ]]; then
    cp -a "$attachments_dir" "$backup_dir/"
  fi

  echo "备份完成: $backup_dir"
  du -sh "$backup_dir"
  echo "还原方法: 先 scripts/app.sh stop 停止后端,再把备份目录里的 job_one_stop.sqlite3(和 chat_attachments/,如有)复制回 $ROOT_DIR/data/job_one_stop/,然后 scripts/app.sh start。"
}

usage() {
  echo "Usage: $0 {start|stop|status|logs|update|backup}"
  echo
  echo "单进程部署模式:构建后仅需一个 uvicorn 进程(端口 \$PORT,默认 8000),"
  echo "同时提供前端页面与 API。运行时文件在 data/app/。"
}

case "${1:-}" in
  start)
    do_start
    ;;
  stop)
    do_stop
    ;;
  status)
    do_status
    ;;
  logs)
    do_logs
    ;;
  update)
    do_update
    ;;
  backup)
    do_backup
    ;;
  "")
    do_status
    ;;
  *)
    usage
    exit 2
    ;;
esac

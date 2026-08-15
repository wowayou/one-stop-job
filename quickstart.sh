#!/usr/bin/env bash
set -euo pipefail

# job-one-stop 开箱即用启动脚本
# 一条命令完成：依赖安装 → 配置生成 → 前端构建 → 后端启动
# 用法: ./quickstart.sh

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PORT="${PORT:-8000}"

echo ""
echo "  ================================================"
echo "         job-one-stop - 本地求职助手"
echo "  ================================================"
echo ""

# 1. Python virtualenv
if [[ ! -x ".venv/bin/python" ]]; then
  echo "> Creating Python virtualenv..."
  python3 -m venv --clear .venv
  echo "> Installing backend deps (first run ~2-3 min)..."
  .venv/bin/python -m pip install -r requirements.txt -q
fi

# 2. Frontend deps
if [[ ! -d "frontend/node_modules" ]]; then
  echo "> Installing frontend deps..."
  (cd frontend && npm install --silent 2>/dev/null)
fi

# 3. Config files
if [[ ! -f "config.yaml" ]] && [[ -f "config.example.yaml" ]]; then
  echo "> Generating config.yaml..."
  cp config.example.yaml config.yaml
fi
if [[ ! -f ".env" ]] && [[ -f ".env.template" ]]; then
  echo "> Generating .env (fill in API keys as needed)..."
  cp .env.template .env
fi

# 4. Build frontend
echo "> Building frontend..."
(cd frontend && npm run build 2>/dev/null)

# 5. Start backend
echo "> Starting server..."
echo ""
echo "  -----------------------------------------------"
echo "  URL: http://127.0.0.1:${PORT}"
echo "  Press Ctrl+C to stop"
echo "  -----------------------------------------------"
echo ""

exec .venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port "$PORT"
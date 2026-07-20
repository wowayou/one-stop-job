#!/bin/bash
# One-Stop-Job 快速启动脚本

set -e

echo "
╔════════════════════════════════════════════════════════════════╗
║              One-Stop-Job 快速启动                              ║
╚════════════════════════════════════════════════════════════════╝
"

echo "请选择启动方式："
echo ""
echo "  1) 本地开发模式（推荐，5-10秒启动）"
echo "  2) Docker 模式（适合部署，首次3-5分钟）"
echo ""
read -p "请输入选项 [1/2]: " choice

case $choice in
  1)
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "🚀 启动本地开发模式"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    # 检查虚拟环境
    if [ ! -x ".venv/bin/python" ]; then
      echo "📦 创建或修复虚拟环境..."
      python3 -m venv --clear .venv
      echo "📥 安装 Python 依赖..."
      .venv/bin/python -m pip install -r requirements.txt
    fi

    # 检查前端依赖
    if [ ! -d "frontend/node_modules" ]; then
      echo "📥 安装前端依赖..."
      cd frontend && npm install && cd ..
    fi

    echo ""
    echo "✅ 依赖已就绪"
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "启动服务（需要两个终端）"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "终端 1（后端）："
    echo "  .venv/bin/python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000"
    echo ""
    echo "终端 2（前端）："
    echo "  cd frontend && npm run dev"
    echo ""
    echo "访问: http://127.0.0.1:5173"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    read -p "按回车键在当前终端启动后端... "
    .venv/bin/python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
    ;;

  2)
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "🐳 启动 Docker 模式"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    # 检查 .env
    if [ ! -f ".env" ]; then
      echo "📝 创建 .env 配置文件..."
      cp .env.template .env
    fi

    echo "📦 构建并启动容器..."
    echo "⏱️  首次构建约需 3-5 分钟，请耐心等待..."
    echo ""

    docker compose up -d --build

    if [ $? -eq 0 ]; then
      echo ""
      echo "✅ 启动成功！"
      echo ""
      echo "访问: http://127.0.0.1:8000"
      echo ""
      echo "查看日志: docker compose logs -f"
      echo "停止服务: docker compose down"
    else
      echo ""
      echo "❌ 启动失败"
      echo ""
      echo "常见问题："
      echo "  1. Docker 构建超时 → 查看 docs/docker-optimization.md"
      echo "  2. 端口被占用 → docker compose down 后重试"
      echo "  3. 网络问题 → 建议改用本地开发模式"
      echo ""
      echo "详细诊断: ./scripts/deploy_check.sh"
    fi
    ;;

  *)
    echo ""
    echo "❌ 无效选项"
    exit 1
    ;;
esac

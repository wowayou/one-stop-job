#!/bin/bash
# 项目文档和功能完整性检查

echo "
╔════════════════════════════════════════════════════════════════╗
║              One-Stop-Job 项目完整性检查                        ║
╚════════════════════════════════════════════════════════════════╝
"

# 检查核心文件
check_file() {
    if [ -f "$1" ]; then
        echo "  ✅ $1"
        return 0
    else
        echo "  ❌ $1 (缺失)"
        return 1
    fi
}

check_dir() {
    if [ -d "$1" ]; then
        echo "  ✅ $1/"
        return 0
    else
        echo "  ❌ $1/ (缺失)"
        return 1
    fi
}

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📁 核心文件"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
check_file "README.md"
check_file "QUICKSTART.md"
check_file "CLAUDE.md"
check_file "start.sh"
check_file "Dockerfile"
check_file "docker-compose.yml"
check_file "config.yaml"
check_file ".env.template"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📦 依赖文件"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
check_file "requirements.txt"
check_file "requirements-runtime.txt"
check_file "requirements-small.txt"
check_file "requirements-large.txt"
check_file "frontend/package.json"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔧 脚本"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
check_file "scripts/quality_gate.sh"
check_file "scripts/deploy_check.sh"
check_file "scripts/system_smoke.sh"
check_file "scripts/test_dependencies.sh"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📚 文档"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
check_dir "docs"
check_dir "docs/images"
check_file "docs/maintenance-guide.md"
check_file "docs/operations.md"
check_file "docs/docker-optimization.md"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🏗️ 后端"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
check_dir "backend/app"
check_file "backend/app/main.py"
check_file "backend/app/models.py"
check_file "backend/app/config.py"
check_dir "backend/app/services"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🎨 前端"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
check_dir "frontend/src"
check_file "frontend/src/App.tsx"
check_file "frontend/src/api.ts"
check_file "frontend/index.html"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🧪 测试"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
check_dir "tests"
check_dir "tests/fixtures"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ 检查完成"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

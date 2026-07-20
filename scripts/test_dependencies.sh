#!/bin/bash
# 测试依赖是否能成功解析

set -e

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📦 测试依赖解析（不实际安装）"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# 创建临时虚拟环境
TEMP_VENV=$(mktemp -d)/test_venv
python3 -m venv "$TEMP_VENV"
source "$TEMP_VENV/bin/activate"

echo "✅ 临时虚拟环境已创建"
echo ""

echo "📥 解析依赖..."
pip install --dry-run -r requirements-runtime.txt 2>&1 | tee /tmp/pip_test.log

if grep -q "ERROR" /tmp/pip_test.log; then
    echo ""
    echo "❌ 依赖解析失败"
    echo ""
    grep -A 10 "ERROR" /tmp/pip_test.log
    deactivate
    rm -rf "$TEMP_VENV"
    exit 1
fi

echo ""
echo "✅ 依赖解析成功！"
echo ""
echo "关键包版本："
pip install -r requirements-runtime.txt 2>&1 | grep -E "(sqlmodel|alembic|SQLAlchemy)" | head -10

# 清理
deactivate
rm -rf "$TEMP_VENV"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ 测试完成"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

#!/bin/bash
# Phase 3 - Agent 编排启动脚本 (macOS/Linux)
# 启动命令: bash scripts/start-agent.sh

set -e

echo "========================================"
echo "  PrivateAI Agent - Phase 3 启动"
echo "========================================"
echo ""

# 设置环境变量
export AGENT_HOST=${AGENT_HOST:-127.0.0.1}
export AGENT_PORT=${AGENT_PORT:-8765}

# 生成或获取 Token
if [ -z "$AGENT_TOKEN" ]; then
    echo "[agent] 生成随机 Token..."
    export AGENT_TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(32))")
fi

echo "[agent] Token: ${AGENT_TOKEN:0:8}..."
echo "[agent] 监听地址: $AGENT_HOST:$AGENT_PORT"
echo "[agent] 启动中..."
echo ""

# 切换到 agent 目录并启动
cd "$(dirname "$0")/../agent"
python3 -m app.main --host "$AGENT_HOST" --port "$AGENT_PORT"

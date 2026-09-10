#!/usr/bin/env bash
# ============================================================
# 一键推送到 GitHub
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "[*] 检查 remote..."
if ! git remote -v | grep -q 'github.com/16069141-glitch/agentdesktop'; then
  echo "[*] 添加 remote..."
  git remote add origin https://github.com/16069141-glitch/agentdesktop.git
fi

echo "[*] 检查分支..."
current_branch=$(git rev-parse --abbrev-ref HEAD)
echo "  当前分支: $current_branch"

echo "[*] 准备推送..."
echo "  如果首次推送，会提示输入 GitHub 用户名和个人访问令牌（Personal Access Token）"
echo "  推荐在 GitHub 设置中生成 Token: https://github.com/settings/tokens"
echo ""

git push -u origin "$current_branch" 2>&1

echo ""
echo "[OK] 推送完成！"
echo "  仓库地址: https://github.com/16069141-glitch/agentdesktop"

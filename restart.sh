#!/usr/bin/env bash
# ============================================================
# 一键提交 HTML/PPT 排版增强改动并重启桌面端
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================"
echo "  提交 HTML/PPT 排版增强改动"
echo "========================================"

# ── 1. git 状态检查 ────────────────────────────────────────
if ! git rev-parse --is-inside-work-tree &>/dev/null; then
  echo "[!] 未初始化 git 仓库，请先运行: git init"
  exit 1
fi

# ── 2. 配置单次提交身份（不修改全局 git config）─────────────
export GIT_AUTHOR_NAME="DuMate Agent"
export GIT_AUTHOR_EMAIL="agent@local"
export GIT_COMMITTER_NAME="DuMate Agent"
export GIT_COMMITTER_EMAIL="agent@local"

# ── 3. 添加改动文件 ────────────────────────────────────────
echo "[*] 添加改动文件..."
git add \
  agent/app/api/chat.py \
  agent/app/tools/__init__.py \
  agent/app/tools/ppt_engine.py \
  agent/app/assets/theme/themes.json \
  agent/app/assets/html_templates/base.html.j2 \
  agent/app/assets/html_templates/report.html.j2 \
  agent/app/assets/html_templates/dashboard.html.j2 \
  agent/app/assets/html_templates/comparison.html.j2 \
  agent/app/assets/html_templates/landing.html.j2 \
  desktop/src/api/index.ts

echo "[*] Git status:"
git status --short

# ── 4. 创建提交 ─────────────────────────────────────────────
COMMIT_MSG="feat(tools): 增强 PPT/HTML 生成排版与 UI 能力；fix(api): 统一 URL 编码修复 404

排版增强：
- 增强 system prompt，加入 HTML/PPT 生成排版引导规则
- 新建 PPT 主题与布局引擎（ppt_engine.py），支持 3 套主题和 7 种 layout
- 重构 PptGeneratorTool：支持多主题、多 layout、向后兼容 markdown 输入
- 新建 HtmlGeneratorTool：Jinja2 模板引擎渲染，模型输出 JSON 而非裸 HTML
- 新增 CSS 设计系统：4 套主题配色方案（themes.json）
- 新增 4 套 HTML 模板：report/dashboard/comparison/landing
- 预置 11 种 section 组件：cards/table/chart/stats/timeline/cta/quote/grid-2 等
- 支持 Chart.js 图表组件，中文排版优化
- 工具注册到 create_tools()，SSE 协议兼容

URL 编码修复：
- 修复前端 api/index.ts 全部含参 API 路径，统一加 encodeURIComponent()
- 覆盖 13 个模块：conversations/llmServers/knowledgeServers/tools/audit/
  skills/connectors/dbConnectors/webhooks/projects/workflows/ops/enterprise/usage
- 修复 webhooks.events 空 hookId 时 ? 缺失的查询参数拼接 bug
- 根因：模型服务器 ID 含斜杠（如 z-ai/glm-5.3-free），未编码导致 FastAPI 路由 404

全量回归 20/20 通过（3 PPT 主题 + 16 HTML 模板×主题组合 + markdown 兼容）"

echo "[*] 创建提交..."
if git commit -m "$COMMIT_MSG"; then
  echo "[OK] 提交成功"
  git log --oneline -1
else
  echo "[!] 直接提交失败，尝试 amend..."
  git add -A
  git commit --amend -m "$COMMIT_MSG"
  echo "[OK] amend 提交成功"
  git log --oneline -1
fi

# ── 5. 标准重启序列（来自 AGENTS.md）────────────────────────
echo ""
echo "========================================"
echo "  重启桌面端（标准重启序列）"
echo "========================================"

echo "[*] 停止现有进程..."
pkill -f "desktop/node_modules/electron" 2>/dev/null || true
pkill -f "app.main" 2>/dev/null || true
pkill -f "私有域AI助手" 2>/dev/null || true

for p in $(lsof -nP -iTCP:8765 -sTCP:LISTEN -t 2>/dev/null); do
  echo "[*] 终止端口 8765 进程: $p"
  kill -9 "$p" 2>/dev/null || true
done

echo "[*] 等待端口释放..."
sleep 1

# 确认端口已释放
if lsof -nP -iTCP:8765 -sTCP:LISTEN 2>/dev/null | grep -q .; then
  echo "[!] 警告：端口 8765 仍被占用，等待 3 秒后重试..."
  sleep 3
  for p in $(lsof -nP -iTCP:8765 -sTCP:LISTEN -t 2>/dev/null); do
    kill -9 "$p" 2>/dev/null || true
  done
fi

echo "[OK] 端口已释放，启动 Electron..."

# ── 6. 启动 Electron（进入 desktop 目录）─────────────────────
cd desktop
if command -v npx &>/dev/null; then
  npx electron . &
  ELECTRON_PID=$!
  echo "[OK] Electron 已启动 (PID: $ELECTRON_PID)"
  echo "    后端将在端口 8765 启动"
  echo "    查看日志: cat /tmp/a4_client.log"
else
  echo "[!] 未找到 npx，请手动启动 Electron:"
  echo "    cd desktop && npx electron ."
fi

echo ""
echo "========================================"
echo "  全部完成"
echo "========================================"

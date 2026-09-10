#!/bin/bash
# 同步后端 Python 代码与配置到已安装的 App
# 用途：当修改了 agent/ 下的 Python 或 config/settings.json 后，同步到 /Applications/颤翎子AI助手.app
# 说明：后端在 Resources/agent/（不在 asar 内），可直接复制；前端需另跑 update_app.sh 重打包 asar

set -u

SRC="/Users/caojian/Desktop/agent/workdesktop/v2"

# 自动找最新安装的 App（兼容旧名「私有域AI助手.app」与新名「颤翎子AI助手.app」）
APP_DIR="$(find /Applications -maxdepth 2 \( -name '颤翎子AI助手*.app' -o -name '私有域AI助手*.app' \) -print -quit 2>/dev/null | head -n1)"
if [ -z "$APP_DIR" ]; then
    echo "错误：/Applications 下未找到颤翎子AI助手.app / 私有域AI助手.app"
    exit 1
fi
APP="$APP_DIR/Contents/Resources"
AGENT_DST="$APP/agent"

echo "========================================"
echo "  同步后端到 $(basename "$APP_DIR")"
echo "========================================"

# 0) 关闭运行中的 App（兼容两种产品名）
APP_RUNNING=0
for nm in "颤翎子AI助手" "私有域AI助手"; do
    if pgrep -f "$nm" > /dev/null 2>&1; then
        echo "[0/4] 关闭运行中的 $nm ..."
        killall "$nm" 2>/dev/null || true
        APP_RUNNING=1
    fi
done
if [ $APP_RUNNING -eq 0 ]; then
    echo "[0/4] App 未运行，跳过关闭"
fi
sleep 1

# 1) web_tools.py（域名白名单）
echo "[1/6] 同步 web_tools.py..."
cp -f "$SRC/agent/app/tools/web_tools.py" "$AGENT_DST/app/tools/web_tools.py" && echo "      ✓ 已更新"

# 2) settings.py（allowed_domains 默认值 + SettingsUpdate 模型）
echo "[2/6] 同步 settings.py..."
cp -f "$SRC/agent/app/api/settings.py" "$AGENT_DST/app/api/settings.py" && echo "      ✓ 已更新"

# 3) shell_security.py（白名单控制器）
echo "[3/6] 同步 shell_security.py..."
cp -f "$SRC/agent/app/security/shell_security.py" "$AGENT_DST/app/security/shell_security.py" && echo "      ✓ 已更新"

# 4) chat.py（shell 审批回调：本地 admin 自动放行 + 服务故障降级）
echo "[4/6] 同步 chat.py..."
cp -f "$SRC/agent/app/api/chat.py" "$AGENT_DST/app/api/chat.py" && echo "      ✓ 已更新"

# 5) orchestrator.py（Agent 循环：轮次上限 / 同工具连续熔断阈值）
echo "[5/6] 同步 orchestrator.py..."
cp -f "$SRC/agent/app/agents/orchestrator.py" "$AGENT_DST/app/agents/orchestrator.py" && echo "      ✓ 已更新"

# 6) config/settings.json
echo "[6/6] 同步 config/settings.json..."
mkdir -p "$APP/config"
cp -f "$SRC/config/settings.json" "$APP/config/settings.json" && echo "      ✓ 已更新"

echo ""
echo "========================================"
echo "  同步完成"
echo "========================================"
echo ""
echo "配置内容："
echo "  · shell 白名单含 curl/wget/git 等 30 个命令"
echo "  · allowed_root_dirs: \${HOME}, \${HOME}/Downloads（自动展开）"
echo "  · allowed_domains: github.com 等 10 个域名"
echo ""
echo "现在可启动「$(basename "$APP_DIR" .app)」App"
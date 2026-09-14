#!/bin/bash
# 同步后端 Python 代码与配置到已安装的 App
# 用途：当修改了 agent/app/ 下的 Python 或 config/settings.json 后，同步到 /Applications/颤翎子AI助手.app
# 说明：后端在 Resources/agent/app/（不在 asar 内），可直接 rsync；前端需另跑 v2/update_app.sh 重打包 asar

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
        echo "[0/2] 关闭运行中的 $nm ..."
        killall "$nm" 2>/dev/null || true
        APP_RUNNING=1
    fi
done
if [ $APP_RUNNING -eq 0 ]; then
    echo "[0/2] App 未运行，跳过关闭"
fi
sleep 1

# 1) 同步整个 agent/app/ 代码树（镜像，排除缓存/测试/数据）
#    改同步策略原因：之前按文件逐个拷贝，每次新增模块（如 dsml、rag）都会漏同步，导致
#    安装版 App 启动报 "No module named 'app.xxx'"。改为 rsync 后新增目录自动进 App。
echo "[1/2] 同步 agent/app/ 后端代码..."
mkdir -p "$AGENT_DST/app"
rsync -a --delete \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.pytest_cache' \
    "$SRC/agent/app/" "$AGENT_DST/app/"
echo "      ✓ 已更新（含 dsml、rag 等新增模块）"

# 2) config/settings.json
echo "[2/2] 同步 config/settings.json..."
mkdir -p "$APP/config"
cp -f "$SRC/config/settings.json" "$APP/config/settings.json" && echo "      ✓ 已更新"

echo ""
echo "========================================"
echo "  同步完成"
echo "========================================"
echo ""
echo "现在可启动「$(basename "$APP_DIR" .app)」App"

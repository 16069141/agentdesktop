#!/bin/bash
# 同步前端构建到已安装的 App
# 用途：改了 desktop/src 后，先 `npx vite build`，再跑本脚本重打包 app.asar
# 说明：前端在 app.asar 内，必须重新打包；后端走 scripts/sync_agent_to_app.sh
#
# 注意：写入 /Applications 需关闭沙箱（WorkBuddy 中执行时勾选「绕过沙箱」）
set -u

SRC="/Users/caojian/Desktop/agent/workdesktop/v2/desktop"
# 优先使用项目本地 node_modules 中的 asar；不存在时尝试 npx 兜底
if [ -x "$SRC/node_modules/.bin/asar" ]; then
  ASAR="$SRC/node_modules/.bin/asar"
else
  ASAR="npx asar"
fi
EXTRACT="$HOME/Desktop/app_extract"

# 自动找最新安装的 App（兼容旧名「私有域AI助手.app」与新名「颤翎子AI助手.app」）
APP_DIR="$(find /Applications -maxdepth 2 \( -name '颤翎子AI助手*.app' -o -name '私有域AI助手*.app' \) -print -quit 2>/dev/null | head -n1)"
if [ -z "$APP_DIR" ]; then
    echo "错误：/Applications 下未找到颤翎子AI助手.app / 私有域AI助手.app"
    exit 1
fi
APP="$APP_DIR/Contents/Resources"

echo "========================================"
echo "  同步前端到 $(basename "$APP_DIR")"
echo "========================================"

# 0) 关闭运行中的 App（兼容两种产品名）
APP_RUNNING=0
for nm in "颤翎子AI助手" "私有域AI助手"; do
    if pgrep -f "$nm" > /dev/null 2>&1; then
        echo "[0/5] 关闭运行中的 $nm ..."
        killall "$nm" 2>/dev/null || true
        APP_RUNNING=1
    fi
done
if [ $APP_RUNNING -eq 0 ]; then
    echo "[0/5] App 未运行，跳过关闭"
fi
sleep 1

# 1) 动态取新构建的 JS / CSS 文件名（内容 hash 每次构建都变，不能硬编码）
NEW_JS=$(ls "$SRC"/dist/assets/index-*.js 2>/dev/null | head -1 | xargs -n1 basename)
NEW_CSS=$(ls "$SRC"/dist/assets/index-*.css 2>/dev/null | head -1 | xargs -n1 basename)
if [ -z "$NEW_JS" ]; then
    echo "错误：未找到 dist/assets/index-*.js，请先运行 npx vite build"
    exit 1
fi
echo "[1/5] 新构建: $NEW_JS ${NEW_CSS:+| $NEW_CSS}"

# 2) 提取当前 app.asar
echo "[2/5] 提取 app.asar..."
rm -rf "$EXTRACT"
"$ASAR" extract "$APP/app.asar" "$EXTRACT" 2>&1 | tail -2
[ -d "$EXTRACT/dist" ] || { echo "错误：提取失败"; exit 1; }

# 3) 复制新构建（先清掉历史 index-* 产物，否则 asar 每次叠加、体积持续膨胀）
echo "[3/5] 复制最新构建..."
mkdir -p "$EXTRACT/dist/assets"
rm -f "$EXTRACT"/dist/assets/index-*.js "$EXTRACT"/dist/assets/index-*.css
cp -f "$SRC"/dist/assets/*.js "$EXTRACT/dist/assets/"
cp -f "$SRC"/dist/assets/*.css "$EXTRACT/dist/assets/"
[ -f "$SRC/dist/index.html" ] && cp -f "$SRC/dist/index.html" "$EXTRACT/dist/index.html"
# 主进程（agentProcess.ts / main.ts / preload.ts 编译产物在 dist-electron/）
# 历史 bug：asar 入口是 package.json 的 "main": "dist-electron/main.js"，
# 而旧脚本只更新 dist/assets、把主进程复制到 main/ 从未被加载——
# 主进程改动（如端口 8765→8766）从不生效。必须覆盖 dist-electron/。
if ls "$SRC"/dist-electron/*.js >/dev/null 2>&1; then
    mkdir -p "$EXTRACT/dist-electron"
    cp -f "$SRC"/dist-electron/*.js "$EXTRACT/dist-electron/"
    echo "  ✓ 主进程 dist-electron 已同步到 asar/dist-electron/ ($(ls "$SRC"/dist-electron/*.js | wc -l | tr -d ' ') 个文件)"
fi

# 4) 同步 index.html 中的资源引用（统一指向新 hash）
echo "[4/5] 更新 index.html 引用..."
python3 - "$EXTRACT/dist/index.html" "$NEW_JS" "$NEW_CSS" << 'PYEOF'
import re, sys
path, new_js, new_css = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    html = open(path, encoding='utf-8').read()
except FileNotFoundError:
    print('  index.html 不存在，跳过'); sys.exit(0)
html = re.sub(r'index-[A-Za-z0-9_-]+\.js', new_js, html)
if new_css:
    html = re.sub(r'index-[A-Za-z0-9_-]+\.css', new_css, html)
open(path, 'w', encoding='utf-8').write(html)
print(f'  → {new_js} {new_css}')
PYEOF

# 5) 重新打包
echo "[5/5] 重新打包 app.asar..."
rm -f "$APP/app.asar"
"$ASAR" pack "$EXTRACT" "$APP/app.asar" --unpack-dir "{node_modules}" 2>&1 | tail -2
if [ -f "$APP/app.asar" ]; then
    echo ""
    echo "========================================"
    echo "  ✓ 更新完成"
    echo "========================================"
    echo ""
    ls -lh "$APP/app.asar" | awk '{print "app.asar:", $5}'
    echo ""
    echo "现在可启动「$(basename "$APP_DIR" .app)」App"
else
    echo "错误：打包失败"
    exit 1
fi

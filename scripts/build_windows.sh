#!/bin/bash
# 构建 Windows x64 安装包（NSIS）
# 用法：
#   bash v2/scripts/build_windows.sh          # 全流程：staging → electron-builder
#
# 前置：
#   - 已下载 win-x64 Python 运行时：v2/runtime/python/win-x64/
#       （缺失时先跑 v2/scripts/fetch-python-runtime.sh win-x64）
#   - whisper 模型：v2/agent/data/models/whisper-base/（离线 ASR，缺失则安装包不带语音输入）
#   - 前端产物：cd desktop && npx tsc --noEmit && npx vite build（本脚本会检查）
#
# 已知约束（macOS 交叉构建 Windows）：
#   1) win.signAndEditExecutable=false（macOS 无 wine，rcedit 不可用）→ 已用
#      afterPack 钩子 desktop/scripts/after-pack-win.js（resedit 纯 JS 改 PE 资源）
#      在 pack 后替换 exe 图标为 build/icon.ico，一键构建自带正确图标。
#   2) NSIS 工具链从 GitHub Releases 下载，国内网络常超时：
#      预置缓存 ~/Library/Caches/electron-builder/nsis/nsis-3.0.4.1.7z
#      镜像源：https://registry.npmmirror.com/-/binary/electron-builder-binaries/nsis-3.0.4.1/nsis-3.0.4.1.7z
#      nsis-resources-3.4.1.7z 同源下载，放 ~/Library/Caches/electron-builder/nsis/
#   3) 产物无法在本机运行验证，须在真实 Windows 机器冒烟（见 README 或交付说明）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"        # v2/
DESKTOP="$ROOT/desktop"

echo "========================================"
echo "  构建 Windows x64 安装包"
echo "========================================"

# 0) 前置检查
[ -d "$ROOT/runtime/python/win-x64" ] || { echo "✗ 缺 win-x64 运行时（先跑 scripts/fetch-python-runtime.sh win-x64）"; exit 1; }
[ -f "$DESKTOP/dist/index.html" ] || { echo "✗ 前端未构建（cd desktop && npx tsc --noEmit && npx vite build）"; exit 1; }

# 1) staging（后端 + win 运行时 + 出厂配置 + whisper 模型）
echo "[1/2] stage-release win-x64 ..."
bash "$DESKTOP/scripts/stage-release.sh" win-x64

# 2) electron-builder NSIS
echo "[2/2] electron-builder --win nsis --x64 ..."
cd "$DESKTOP"
npx electron-builder --win nsis --x64

echo "完成："
ls -lh "$ROOT"/release/*-x64.exe 2>/dev/null | tail -3

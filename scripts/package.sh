#!/bin/bash
# Phase 4 - 打包分发脚本
# 用于将 Electron + Python Agent 打包成可分发安装包
#
# 用法:
#   bash scripts/package.sh [mac|win|linux|all]

set -e

echo "========================================"
echo "  PrivateAI - Phase 4 打包"
echo "========================================"
echo ""

# 配置
PLATFORM=${1:-"all"}  # mac, win, linux, all
BUILD_DIR=$(pwd)/desktop/dist
RELEASE_DIR=$(pwd)/release
AGENT_DIR=$(pwd)/agent
VENV_DIR="$AGENT_DIR/.venv"

# 检查依赖
echo "[检查] 验证环境..."
if ! command -v node &> /dev/null; then
    echo "错误：Node.js 未安装"
    exit 1
fi

if ! command -v python3 &> /dev/null; then
    echo "错误：Python 3 未安装"
    exit 1
fi

echo "  ✓ Node.js: $(node --version)"
echo "  ✓ Python: $(python3 --version)"
echo ""

# 构建前端
echo "[构建] 前端..."
cd desktop
npm install
npm run build
echo "  ✓ 前端构建完成"
echo ""

# 构建 Agent Python 虚拟环境
echo "[构建] Python 虚拟环境..."
if [ ! -d "$VENV_DIR" ]; then
    echo "  → 创建 venv..."
    python3 -m venv "$VENV_DIR"
fi

echo "  → 安装依赖..."
source "$VENV_DIR/bin/activate"
pip install --upgrade pip
pip install -r "$AGENT_DIR/requirements.txt"
deactivate
echo "  ✓ Python 环境构建完成"
echo ""

# 检查 agent 目录结构
echo "[检查] Agent 目录结构..."
if [ ! -f "$AGENT_DIR/app/main.py" ]; then
    echo "错误：找不到 agent/app/main.py"
    exit 1
fi
echo "  ✓ Agent 入口文件存在"
echo ""

# 打包配置
echo "[配置] electron-builder 配置..."
cat > electron-builder.yml << 'EOF'
appId: com.private-ai.client
productName: 私有域AI助手
directories:
  build: build
  output: ../release
files:
  - dist/**/*
  - dist-electron/**/*
  - ../../agent/**/*
extraMetadata:
  main: dist-electron/main.js

# macOS 配置
mac:
  category: public.app-category.productivity
  target:
    - target: dmg
      arch: [arm64, x64]
  icon: build/icon.icns
  hardenedRuntime: true
  gatekeeperAssess: false
  entitlements: build/entitlements.mac.plist
  entitlementsInherit: build/entitlements.mac.plist

# Windows 配置
win:
  target:
    - target: nsis
      arch: [x64]
  icon: build/icon.ico

# Linux 配置
linux:
  target:
    - target: AppImage
      arch: [x64]
  category: Utility
  icon: build/icon.png

# NSIS 配置
nsis:
  oneClick: false
  perMachine: false
  allowToChangeInstallationDirectory: true
  createDesktopShortcut: true
  createStartMenuShortcut: true

# 自动更新（可选）
publish:
  provider: generic
  url: https://example.com/releases
EOF

echo "  ✓ 打包配置完成"
echo ""

# 运行打包
echo "[打包] 开始构建..."
if [ "$PLATFORM" = "mac" ] || [ "$PLATFORM" = "all" ]; then
    echo "  → macOS DMG..."
    npx electron-builder --mac
fi

if [ "$PLATFORM" = "win" ] || [ "$PLATFORM" = "all" ]; then
    echo "  → Windows NSIS..."
    npx electron-builder --win
fi

if [ "$PLATFORM" = "linux" ] || [ "$PLATFORM" = "all" ]; then
    echo "  → Linux AppImage..."
    npx electron-builder --linux
fi

echo ""
echo "========================================"
echo "  打包完成！"
echo "========================================"
echo ""
echo "输出目录: $RELEASE_DIR"
ls -lh "$RELEASE_DIR"

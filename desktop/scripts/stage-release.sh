#!/usr/bin/env bash
# 打包前置：把后端代码、Python 运行时、配置暂存到项目内。
# electron-builder 不允许 extraResources 引用项目目录外文件，故需要中转。
#
# 用法：
#   bash scripts/stage-release.sh                      # 全部运行时（默认）
#   bash scripts/stage-release.sh macos-arm64          # 只中转 arm64（减小打包体积）
#   bash scripts/stage-release.sh win-x64              # 只中转 win-x64
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"            # desktop/
PROJECT="$(cd "$ROOT/.." && pwd)"                    # v2/
AGENT_SRC="$PROJECT/agent"                           # v2/agent/
CONFIG_SRC="$PROJECT/config"                         # v2/config/
STAGE_AGENT="$ROOT/build-staging/agent"
STAGE_RUNTIME="$ROOT/build-staging/runtime"
STAGE_CONFIG="$ROOT/build-staging/config"

RUNTIME_KEY="${1:-}"
case "$(uname -s)" in
  Darwin) HOST_OS="macos";;
  Linux) HOST_OS="linux";;
  MINGW*|MSYS*|CYGWIN*|Windows) HOST_OS="win";;
  *) HOST_OS="unknown";;
esac
HOST_ARCH="$(uname -m)"
case "$HOST_ARCH" in
  arm64|aarch64) HOST_ARCH="arm64";;
  x86_64) HOST_ARCH="x64";;
esac
HOST_KEY="${HOST_OS}-${HOST_ARCH}"

echo "[stage] 源: $AGENT_SRC"
echo "[stage] 目标: $ROOT/build-staging/"
echo "[stage] 主机: $HOST_KEY  运行时 key: ${RUNTIME_KEY:-(全部)}"

rm -rf "$ROOT/build-staging"
mkdir -p "$STAGE_AGENT"

# 1) 后端代码（排除运行时数据与虚拟环境）
rsync -a --exclude '.venv' --exclude 'data' --exclude '__pycache__' \
  --exclude '*.pyc' --exclude '.pytest_cache' \
  "$AGENT_SRC/" "$STAGE_AGENT/"

# 2) Python 运行时（python-build-standalone，跨机器可移植）
#    runtime/python/<key>/  由 scripts/fetch-python-runtime.sh 生成。
#    按 key 筛选，否则把三平台全打进去会让安装包从 90M 涨到 ~250M。
if [ -d "$PROJECT/runtime/python" ]; then
  mkdir -p "$STAGE_RUNTIME/python"
  if [ -n "$RUNTIME_KEY" ]; then
    src="$PROJECT/runtime/python/$RUNTIME_KEY"
    if [ ! -d "$src" ]; then
      echo "[stage] ✗ 缺少运行时: $src（请先执行 scripts/fetch-python-runtime.sh $RUNTIME_KEY）" >&2
      exit 1
    fi
    rsync -a "$src/" "$STAGE_RUNTIME/python/$RUNTIME_KEY/"
    echo "[stage] ✓ 内置运行时: $RUNTIME_KEY"
  else
    rsync -a "$PROJECT/runtime/python/" "$STAGE_RUNTIME/python/"
    echo "[stage] ✓ 内置运行时（全部已下载的）"
  fi
else
  echo "[stage] ⚠ 未发现 runtime/python/，回落到 .venv（仅同机可用）"
  rsync -a --exclude '__pycache__' --exclude '*.pyc' \
    "$AGENT_SRC/.venv/" "$STAGE_AGENT/.venv/"
fi

# 3) 出厂默认配置（无机器相关的绝对路径；运行时复制到 userData 后可写）
if [ -d "$CONFIG_SRC" ]; then
  mkdir -p "$STAGE_CONFIG"
  rsync -a --exclude '*.local.json' "$CONFIG_SRC/" "$STAGE_CONFIG/"
  echo "[stage] ✓ 出厂配置"
fi

echo "[stage] 完成:"
du -sh "$ROOT/build-staging"/* 2>/dev/null || true

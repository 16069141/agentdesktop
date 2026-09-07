#!/usr/bin/env bash
# 打包前置：把后端代码与 Python 虚拟环境暂存到项目内（electron-builder 无法引用项目目录外文件）。
# 用法：bash scripts/stage-release.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"            # desktop/
AGENT_SRC="$(cd "$ROOT/.." && pwd)/agent"            # v2/agent/
STAGE="$ROOT/build-staging/agent"

echo "[stage] 源: $AGENT_SRC"
echo "[stage] 目标: $STAGE"

rm -rf "$ROOT/build-staging"
mkdir -p "$STAGE"

# 1) 后端代码（排除运行时数据与虚拟环境）
rsync -a --exclude '.venv' --exclude 'data' --exclude '__pycache__' \
  --exclude '*.pyc' --exclude '.pytest_cache' \
  "$AGENT_SRC/" "$STAGE/"

# 2) Python 虚拟环境（含全部依赖；同机发布可用，跨机需重建 venv）
rsync -a --exclude '__pycache__' --exclude '*.pyc' \
  "$AGENT_SRC/.venv/" "$STAGE/.venv/"

echo "[stage] 完成:"
du -sh "$STAGE" "$STAGE/.venv"

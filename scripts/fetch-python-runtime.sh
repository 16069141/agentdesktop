#!/usr/bin/env bash
# 为「他人电脑安装」准备可移植 Python 运行时。
#
# 为什么需要它：
#   开发机的 agent/.venv 里 pyvenv.cfg 和 bin/python3 软链都写死了
#   /Users/caojian/.workbuddy/binaries/python/... —— 拷到别人电脑上必定启动失败。
#   本脚本改用 astral-sh/python-build-standalone 的 install_only 构建：
#   自带解释器 + 自带 site-packages，纯相对路径，整目录可搬。
#
# 用法（macOS 自带 bash 3.2，故不使用关联数组等 bash4 语法）：
#   bash scripts/fetch-python-runtime.sh                # 全部平台（耗时较长）
#   bash scripts/fetch-python-runtime.sh macos-arm64    # 只做本机
#   bash scripts/fetch-python-runtime.sh macos-x64 win-x64
#
# 产物：
#   runtime/python/macos-arm64/bin/python3
#   runtime/python/macos-x64/bin/python3   （经 Rosetta 装依赖）
#   runtime/python/win-x64/python.exe      （交叉装依赖，产物须在 Windows 上实测）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENT="$ROOT/agent"
OUT="$ROOT/runtime/python"
TMP="$ROOT/runtime/.tmp"

PBS_TAG="${PBS_TAG:-20260901}"
PY_VER="${PY_VER:-3.13.15}"

# GitHub release 直连域名在部分网络环境被代理拦截，故一律走 API 资产端点下载。
ASSET_ID_ARM64="${ASSET_ID_ARM64:-539916466}"
ASSET_ID_X64_MAC="${ASSET_ID_X64_MAC:-539916854}"
ASSET_ID_X64_WIN="${ASSET_ID_X64_WIN:-539916876}"

asset_name() {
  case "$1" in
    macos-arm64) echo "cpython-${PY_VER}+${PBS_TAG}-aarch64-apple-darwin-install_only.tar.gz" ;;
    macos-x64)   echo "cpython-${PY_VER}+${PBS_TAG}-x86_64-apple-darwin-install_only.tar.gz" ;;
    win-x64)     echo "cpython-${PY_VER}+${PBS_TAG}-x86_64-pc-windows-msvc-install_only.tar.gz" ;;
    *) return 1 ;;
  esac
}

asset_id() {
  case "$1" in
    macos-arm64) echo "$ASSET_ID_ARM64" ;;
    macos-x64)   echo "$ASSET_ID_X64_MAC" ;;
    win-x64)     echo "$ASSET_ID_X64_WIN" ;;
    *) return 1 ;;
  esac
}

mkdir -p "$OUT" "$TMP"

download() {
  local key="$1" dest="$2"
  local name id url
  name="$(asset_name "$key")"
  id="$(asset_id "$key")"
  url="https://api.github.com/repos/astral-sh/python-build-standalone/releases/assets/$id"

  # 已下载且 gzip 完整 → 直接复用
  if [ -f "$dest" ] && gzip -t "$dest" 2>/dev/null; then
    echo "  ✓ 已存在且完整，跳过下载"
    return 0
  fi

  echo "  ↓ $name"
  # 大文件在代理环境容易中途断流，做 5 轮「断点续传 + 完整性校验」
  local i
  for i in 1 2 3 4 5; do
    curl -fsSL -C - --retry 2 --retry-delay 2 -m 900 \
      -H "Accept: application/octet-stream" \
      -o "$dest" "$url" || true
    if [ -f "$dest" ] && gzip -t "$dest" 2>/dev/null; then
      echo "  ✓ 下载完成（$(du -h "$dest" | cut -f1)）"
      return 0
    fi
    echo "  ↻ 第 $i 次不完整，续传重试..."
  done

  # 全部失败：删掉残包，避免下次被误判为已下载
  rm -f "$dest"
  echo "  ✗ 下载失败（可尝试更新 PBS_TAG / ASSET_ID_*，或手动下载后放到 $(basename "$dest")）"
  return 1
}

# 解压后把顶层 python/ 内容提升为 runtime/python/<key>/{bin,lib,...}
extract() {
  local key="$1" tarball="$2"
  local stage="$TMP/$key"
  rm -rf "$stage"; mkdir -p "$stage"
  tar -xzf "$tarball" -C "$stage"
  if [ ! -d "$stage/python" ]; then
    echo "  ✗ 包结构与预期不符（缺少顶层 python/）"
    return 1
  fi
  rm -rf "$OUT/$key"; mkdir -p "$OUT/$key"
  # 用 tar 管道复制而不是 mv：跨设备移动可能失败，且能保留符号链接
  (cd "$stage/python" && tar cf - .) | (cd "$OUT/$key" && tar xf -)
}

# 砍掉测试套件 / IDLE / 头文件等运行时不需要的内容
prune() {
  local key="$1"
  echo "  ✂ 裁剪"
  find "$OUT/$key" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
  rm -rf "$OUT/$key/lib/python3.13/test" \
         "$OUT/$key/lib/python3.13/idlelib" \
         "$OUT/$key/lib/python3.13/lib2to3" \
         "$OUT/$key/lib/python3.13/tkinter" \
         "$OUT/$key/lib/python3.13/ensurepip" \
         "$OUT/$key/share" 2>/dev/null || true
  # Windows 调试符号
  find "$OUT/$key" -name '*.pdb' -delete 2>/dev/null || true
}

# 移除 app/ 未直接引用的重包，缩减安装包体积。
# 这些都是开发期实验遗留在 venv 里、未被产品代码使用的：
#   chromadb + bindings：本地 RAG 早就下线
#   onnxruntime / tokenizers / huggingface_hub：chromadb 的传递依赖
#   kubernetes：eval 工具遗留
#   babel：仅 chromadb 间接依赖
prune_unused_heavy() {
  local key="$1"
  local sp="$OUT/$key/lib/python3.13/site-packages"
  # Windows 包布局
  [ -d "$OUT/$key/Lib/site-packages" ] && sp="$OUT/$key/Lib/site-packages"
  echo "  ✂ 移除未用重包"
  rm -rf "$sp/chromadb" "$sp/chromadb_rust_bindings" \
         "$sp/onnxruntime" "$sp/tokenizers" "$sp/huggingface_hub" \
         "$sp/kubernetes" "$sp/babel" 2>/dev/null || true
}

req_file() {
  if [ -f "$AGENT/requirements-dist.txt" ]; then
    echo "$AGENT/requirements-dist.txt"
  else
    echo "$AGENT/requirements.txt"
  fi
}

install_deps_native() {
  local py="$1"
  local req
  req="$(req_file)"
  echo "  📦 安装依赖（原生）: $(basename "$req")"
  "$py" -m pip install --upgrade pip -q
  "$py" -m pip install -r "$req"
}

install_deps_rosetta() {
  local py="$1"
  local req
  req="$(req_file)"
  echo "  📦 安装依赖（Rosetta x86_64）: $(basename "$req")"
  arch -x86_64 "$py" -m pip install --upgrade pip -q
  arch -x86_64 "$py" -m pip install -r "$req"
}

# Windows：uvicorn[standard] 会拉 uvloop，而 uvloop 无 win 轮子，
# 交叉安装时 pip 的新解析器会直接 ResolutionImpossible。
# 拆成「uvicorn + 有 win 轮子的 standard 组件」，跳过 uvloop。
make_win_requirements() {
  local req="$AGENT/requirements.txt"
  local out="$TMP/requirements-win.txt"
  sed 's/^uvicorn\[standard\].*/uvicorn/' "$req" > "$out"
  printf 'httptools\nwatchfiles\nwebsockets\n' >> "$out"
  echo "$out"
}

# 宿主就是 Windows（Git Bash / MSYS）→ 直接用内置 python 原生装，最可靠
install_deps_win_native() {
  local py="$OUT/win-x64/python.exe"
  local req_win; req_win="$(make_win_requirements)"
  echo "  📦 安装依赖（Windows 原生）: $(basename "$req_win")"
  "$py" -m pip install --upgrade pip -q
  "$py" -m pip install -r "$req_win"
}

# 宿主是 mac/Linux → 交叉装 win_amd64 轮子
install_deps_cross_win() {
  local hostpy="$1"
  local req_win; req_win="$(make_win_requirements)"
  local target="$TMP/win-site"
  rm -rf "$target"; mkdir -p "$target"

  echo "  📦 交叉安装依赖（win_amd64）: $(basename "$req_win")"
  "$hostpy" -m pip install --upgrade pip -q
  "$hostpy" -m pip install \
    --platform win_amd64 --python-version 3.13 --implementation cp \
    --only-binary=:all: --target "$target" -r "$req_win"

  local sp="$OUT/win-x64/Lib/site-packages"
  mkdir -p "$sp"
  (cd "$target" && tar cf - .) | (cd "$sp" && tar xf -)
  echo "  ⚠ Windows 依赖为交叉安装，务必在真实 Windows 机器上冒烟一次"
}

build_one() {
  local key="$1"
  echo "=== $key ==="
  local tarball="$TMP/${key}.tar.gz"
  download "$key" "$tarball"
  extract "$key" "$tarball"

  case "$key" in
    macos-arm64)
      install_deps_native "$OUT/$key/bin/python3"
      ;;
    macos-x64)
      if arch -x86_64 /bin/echo >/dev/null 2>&1; then
        install_deps_rosetta "$OUT/$key/bin/python3"
      else
        echo "  ⚠ 未安装 Rosetta 2，跳过依赖安装（请在 Intel Mac 上执行本脚本）"
      fi
      ;;
    win-x64)
      # 宿主就是 Windows（Git Bash / MSYS / CI windows-latest）→ 原生装，最可靠
      case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*|Windows_NT)
          install_deps_win_native
          ;;
        *)
          install_deps_cross_win "$(command -v python3)"
          ;;
      esac
      ;;
  esac

  prune "$key"
  prune_unused_heavy "$key"
  echo "  ✓ 完成: $OUT/$key ($(du -sh "$OUT/$key" | cut -f1))"
}

if [ "$#" -eq 0 ]; then
  set -- macos-arm64 macos-x64 win-x64
fi

for t in "$@"; do
  if ! asset_name "$t" >/dev/null 2>&1; then
    echo "未知平台: $t（可选 macos-arm64 / macos-x64 / win-x64）"
    exit 1
  fi
  build_one "$t"
done

echo
echo "全部完成。产物在 $OUT"
du -sh "$OUT"/* 2>/dev/null || true

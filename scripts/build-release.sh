#!/usr/bin/env bash
# 多平台打包：mac arm64 / mac x64 / win x64，逐个 stage → build。
#
# 用法：
#   bash scripts/build-release.sh mac-arm64
#   bash scripts/build-release.sh mac-x64
#   bash scripts/build-release.sh win-x64
#   bash scripts/build-release.sh all                # 当前主机所有可构建目标
#
# 注意：
#   1) mac + win 各自独立打包，不要混在一个 build-staging 里（运行时隔离）。
#   2) Windows 目标在 mac 上需 Wine + Mono，且 NSIS 打包质量不稳定；
#      强烈建议在真实 Windows 机器或 CI 上运行 win-x64。
set -eo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"            # v2/
DESK="$(cd "$ROOT/desktop" && pwd)"
RELEASE="$ROOT/release"

# 平台表：runtime key / electron-builder flag / 输出后缀
declare -a TARGETS=(
  "mac-arm64|macos-arm64|--mac --arm64|arm64"
  "mac-x64|macos-x64|--mac --x64|x64"
  "win-x64|win-x64|--win --x64|x64"
)

run_one() {
  local name="$1" rtkey="$2" flags="$3" suffix="$4"
  echo
  echo "============================================="
  echo "  构建 $name （runtime=$rtkey, flags=$flags）"
  echo "============================================="

  if [ ! -d "$ROOT/runtime/python/$rtkey" ]; then
    echo "[build] ✗ 缺少运行时: runtime/python/$rtkey"
    echo "      请先执行: bash scripts/fetch-python-runtime.sh $rtkey"
    return 1
  fi

  (cd "$DESK" && bash scripts/stage-release.sh "$rtkey")
  mkdir -p "$RELEASE"

  (cd "$DESK" && npx electron-builder $flags \
    --config.compression=maximum \
    --projectDir="$DESK")

  echo "[build] ✓ $name 完成 → $RELEASE"
}

target_spec() {
  case "$1" in
    mac-arm64) echo "mac-arm64|macos-arm64|--mac --arm64|arm64" ;;
    mac-x64)   echo "mac-x64|macos-x64|--mac --x64|x64" ;;
    win-x64)   echo "win-x64|win-x64|--win --x64|x64" ;;
  esac
}

if [ "$#" -eq 0 ] || [ "$1" = "all" ]; then
  # 只挑当前主机能跑的（避免在 mac 上误跑 win）
  case "$(uname -s)" in
    Darwin)
      for t in mac-arm64 mac-x64; do
        IFS='|' read -r _ rtkey flags suffix <<<"$(target_spec "$t")" || true
        : "${flags:=}"
        : "${suffix:=}"
        run_one "$t" "$rtkey" "$flags" "$suffix" || true
      done
      echo "[build] 提示：win-x64 需在 Windows 机器或 CI 上执行。"
      ;;
    Linux)
      echo "[build] 当前是 Linux，仅能跑 win-x64（需 Wine）。"
      IFS='|' read -r _ rtkey flags suffix <<<"$(target_spec win-x64)" || true
      : "${flags:=}"
      : "${suffix:=}"
      run_one win-x64 "$rtkey" "$flags" "$suffix" || true
      ;;
    MINGW*|MSYS*|CYGWIN*|Windows)
      IFS='|' read -r _ rtkey flags suffix <<<"$(target_spec win-x64)" || true
      run_one win-x64 "$rtkey" "$flags" "$suffix" || true
      ;;
  esac
else
  for t in "$@"; do
    if [ -z "$(target_spec "$t")" ]; then
      echo "未知目标: $t（可选 mac-arm64 / mac-x64 / win-x64 / all）"; exit 1
    fi
    IFS='|' read -r _ rtkey flags suffix <<<"$(target_spec "$t")" || true
    : "${flags:=}"
    : "${suffix:=}"
    run_one "$t" "$rtkey" "$flags" "$suffix" || true
  done
fi

echo
echo "=== 产物清单 ==="
ls -lah "$RELEASE"/*.{dmg,zip,exe,msi,yml,yaml} 2>/dev/null || true
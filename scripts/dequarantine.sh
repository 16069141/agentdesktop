#!/usr/bin/env bash
# 解除 macOS Gatekeeper 隔离标记 + 把 .app 移到 /Applications + 打开。
# 仅在「未签名 / 未公证」安装包上需要执行（开发版 / 内测版）。
#
# 用法：
#   bash scripts/dequarantine.sh /path/to/颤翎子AI助手.app
#   bash scripts/dequarantine.sh /Applications
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "用法: $0 <颤翎子AI助手.app 或 /Applications>"
  exit 1
fi

target="$1"
if [ ! -e "$target" ]; then
  echo "✗ 不存在: $target"; exit 1
fi

# 自动取 /Applications 内匹配 .app（兼容旧版 "私有域AI助手.app" 与新版 "颤翎子AI助手.app"）
case "$target" in
  /Applications|/Applications/|"")
    target="$(find /Applications -maxdepth 2 \( -name '颤翎子AI助手*.app' -o -name '私有域AI助手*.app' \) | head -n1 || true)"
    [ -z "$target" ] && { echo "✗ /Applications 下未找到「颤翎子AI助手.app」"; exit 1; }
    ;;
esac

echo "→ 处理: $target"

# 1) 清除所有隔离标记（递归）
xattr -cr "$target" && echo "  ✓ 已清除 quarantine / com.apple.metadata / 等扩展属性"

# 2) 重置签名（即便未签名，也避免残留伪造的孤立签名元数据）
codesign --remove-signature "$target" 2>/dev/null || true
codesign --force --deep --sign - "$target" 2>/dev/null \
  && echo "  ✓ 已用临时本地签名（ad-hoc）" \
  || echo "  ⚠ 临时签名失败（非阻塞；如仍被拦截，请按下方说明手动放行）"

# 3) 启动
echo "→ 打开..."
open "$target" && echo "  ✓ 已启动"
#!/usr/bin/env python3
"""应用图标生成：直接把颤翎子鹦鹉 logo 加 macOS 标准圆角后导出
icon.png / icon.icns (macOS) / icon.ico (Windows)。

设计：
- 源图：build_temp/app_new_build/assets/parrot-icon-Czzeu4Mi.png（1024×1024，米色背景鹦鹉）
- 圆角：macOS Big Sur 起的「Squircle」实际是 22.37% 半径（≈229/1024）
- 保留原图背景色与鹦鹉整体外观，「和颤翎子 logo 一样」
"""
import os
import shutil
import subprocess
import sys

from PIL import Image

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BUILD = os.path.join(ROOT, "desktop", "build")

SIZE = 1024
# macOS Big Sur+ 标准应用图标圆角比 ≈ 0.2237
CORNER_RADIUS_RATIO = 0.2237


def rounded_mask(size: int, radius_ratio: float = CORNER_RADIUS_RATIO) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw(mask)
    d.rounded_rectangle(
        [0, 0, size - 1, size - 1],
        radius=int(size * radius_ratio),
        fill=255,
    )
    return mask


def find_source() -> str:
    """按优先级找颤翎子鹦鹉原图。

    正式素材在 assets/brand/（入库），build_temp 只是历史构建残留（不入库），
    因此 CI / 新克隆的仓库必须能只靠 assets/brand 生成图标。
    """
    candidates = [
        os.path.join(ROOT, "assets", "brand", "parrot-icon.png"),
        os.path.join(ROOT, "build_temp", "app_new_build", "assets", "parrot-icon-Czzeu4Mi.png"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    print("✗ 找不到鹦鹉源图，已尝试：", file=sys.stderr)
    for c in candidates:
        print(f"  - {c}", file=sys.stderr)
    sys.exit(1)


def build_icon(size: int = SIZE) -> Image.Image:
    src = Image.open(find_source()).convert("RGBA")
    if src.size != (size, size):
        src = src.resize((size, size), Image.LANCZOS)
    # 圆角蒙版：让鹦鹉 logo 在 macOS / Windows 上有相同的圆角外观
    src.putalpha(rounded_mask(size))
    return src


class _Stub:
    """轻量替代，避免在没 PIL.ImageDraw 时崩。"""
    def rounded_rectangle(self, *a, **kw): pass


def ImageDraw(img):
    from PIL import ImageDraw as _ID
    return _ID.Draw(img)


def main():
    os.makedirs(BUILD, exist_ok=True)
    icon = build_icon()

    png = os.path.join(BUILD, "icon.png")
    icon.save(png, "PNG")
    print(f"✓ {png}")

    # Windows ICO：多尺寸
    ico = os.path.join(BUILD, "icon.ico")
    icon.save(
        ico,
        "ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )
    print(f"✓ {ico}")

    # macOS ICNS：sips 系统工具优先（兼容性最好），失败回落 Pillow
    icns = os.path.join(BUILD, "icon.icns")
    ok = False
    if shutil.which("sips"):
        try:
            subprocess.run(
                ["sips", "-s", "format", "icns", png, "--out", icns],
                check=True, capture_output=True,
            )
            ok = os.path.exists(icns)
            if ok:
                print(f"✓ {icns} (sips)")
        except Exception as e:
            print(f"  sips 失败: {e}")
    if not ok:
        try:
            icon.save(icns, "ICNS")
            print(f"✓ {icns} (Pillow)")
        except Exception as e:
            print(f"✗ ICNS 生成失败: {e}", file=sys.stderr)
            return 1

    # DMG 拖拽背景（浅蓝白渐变，与品牌一致）
    bg = Image.new("RGB", (660, 400), (245, 248, 255))
    bg.save(os.path.join(BUILD, "dmg-background.png"), "PNG")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""PPT 主题与布局引擎。

将 PPT 的视觉设计固化到代码模板中，让模型只负责内容组织。
支持 3 套主题：corporate_blue / tech_dark / minimal_white
支持 7 种 layout：cover / section / content / two_column / data / image_text / closing
"""

from __future__ import annotations

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.oxml.ns import qn
from typing import Any, Dict, List, Optional
import copy
import json

# ── 主题定义 ──────────────────────────────────────────────────
THEMES: Dict[str, Dict[str, Any]] = {
    "corporate_blue": {
        "name": "商务蓝",
        "primary": RGBColor(0x1A, 0x73, 0xE8),
        "primary_dark": RGBColor(0x0D, 0x47, 0xA1),
        "accent": RGBColor(0xFF, 0x6D, 0x00),
        "bg": RGBColor(0xFF, 0xFF, 0xFF),
        "bg_alt": RGBColor(0xF5, 0xF7, 0xFA),
        "text": RGBColor(0x1A, 0x1A, 0x2E),
        "text_light": RGBColor(0xFF, 0xFF, 0xFF),
        "text_muted": RGBColor(0x64, 0x74, 0x8B),
        "border": RGBColor(0xE2, 0xE8, 0xF0),
        "success": RGBColor(0x16, 0xA3, 0x4A),
        "warning": RGBColor(0xF5, 0x9E, 0x0B),
        "danger": RGBColor(0xDC, 0x26, 0x26),
        "font_heading": "Microsoft YaHei",
        "font_body": "Microsoft YaHei",
    },
    "tech_dark": {
        "name": "科技暗色",
        "primary": RGBColor(0x00, 0xD9, 0xFF),
        "primary_dark": RGBColor(0x00, 0x99, 0xCC),
        "accent": RGBColor(0x7C, 0x3A, 0xED),
        "bg": RGBColor(0x0F, 0x17, 0x2A),
        "bg_alt": RGBColor(0x1E, 0x29, 0x3B),
        "text": RGBColor(0xE2, 0xE8, 0xF0),
        "text_light": RGBColor(0xF8, 0xFA, 0xFC),
        "text_muted": RGBColor(0x94, 0xA3, 0xB8),
        "border": RGBColor(0x33, 0x41, 0x55),
        "success": RGBColor(0x22, 0xC5, 0x5E),
        "warning": RGBColor(0xEA, 0xB3, 0x08),
        "danger": RGBColor(0xEF, 0x44, 0x44),
        "font_heading": "Microsoft YaHei",
        "font_body": "Microsoft YaHei",
    },
    "minimal_white": {
        "name": "极简白",
        "primary": RGBColor(0x00, 0x00, 0x00),
        "primary_dark": RGBColor(0x00, 0x00, 0x00),
        "accent": RGBColor(0x3B, 0x82, 0xF6),
        "bg": RGBColor(0xFF, 0xFF, 0xFF),
        "bg_alt": RGBColor(0xFA, 0xFA, 0xFA),
        "text": RGBColor(0x1F, 0x29, 0x37),
        "text_light": RGBColor(0xFF, 0xFF, 0xFF),
        "text_muted": RGBColor(0x9C, 0xA3, 0xAF),
        "border": RGBColor(0xE5, 0xE7, 0xEB),
        "success": RGBColor(0x10, 0xB9, 0x81),
        "warning": RGBColor(0xF5, 0x9E, 0x0B),
        "danger": RGBColor(0xEF, 0x44, 0x44),
        "font_heading": "Microsoft YaHei",
        "font_body": "Microsoft YaHei",
    },
}

# 幻灯片尺寸（16:9 宽屏）
SLIDE_WIDTH = Inches(13.333)
SLIDE_HEIGHT = Inches(7.5)

# 布局类型列表
LAYOUT_TYPES = [
    "cover",
    "section",
    "content",
    "two_column",
    "data",
    "image_text",
    "closing",
]


def get_theme(name: str) -> Dict[str, Any]:
    """获取主题配置，不存在则回退到 corporate_blue。"""
    return THEMES.get(name, THEMES["corporate_blue"])


def create_presentation(theme_name: str = "corporate_blue") -> tuple:
    """创建空白演示文稿，返回 (presentation, theme)。

    不使用内置 slide_layouts，而是全部用空白布局自定义渲染。
    """
    prs = Presentation()
    prs.slide_width = SLIDE_WIDTH
    prs.slide_height = SLIDE_HEIGHT
    # 使用空白布局
    blank_layout = prs.slide_layouts[6]
    theme = get_theme(theme_name)
    # 在 prs 上标记主题信息
    prs._theme_name = theme_name
    return prs, theme, blank_layout


def _add_bg(slide, theme, color_key: str = "bg"):
    """添加纯色背景。"""
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = theme[color_key]


def _add_rect(slide, theme, left, top, width, height, color_key="primary"):
    """添加矩形色块。"""
    shape = slide.shapes.add_shape(
        1,  # MSO_SHAPE.RECTANGLE
        left,
        top,
        width,
        height,
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = theme[color_key]
    shape.line.fill.background()  # 无边框
    return shape


def _add_textbox(
    slide,
    theme,
    left,
    top,
    width,
    height,
    text,
    *,
    font_size=24,
    font_color_key="text",
    bold=False,
    alignment=PP_ALIGN.LEFT,
    font_key="font_body",
    anchor=MSO_ANCHOR.TOP,
    line_spacing=1.5,
):
    """添加文本框。"""
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor

    lines = text.split("\n") if isinstance(text, str) else [str(text)]
    for i, line in enumerate(lines):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.text = line
        para.alignment = alignment
        para.space_after = Pt(6)
        try:
            para.line_spacing = line_spacing
        except Exception:
            pass
        for run in para.runs:
            run.font.size = Pt(font_size)
            run.font.bold = bold
            run.font.color.rgb = theme[font_color_key]
            run.font.name = theme[font_key]
    return txBox


def _add_bullets(
    slide,
    theme,
    left,
    top,
    width,
    height,
    bullets: list,
    font_size=22,
    font_color_key="text",
):
    """添加要点列表。"""
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    for i, bullet in enumerate(bullets):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.text = bullet
        para.alignment = PP_ALIGN.LEFT
        para.space_after = Pt(12)
        try:
            para.line_spacing = 1.5
        except Exception:
            pass
        # 字号自适应
        size = font_size
        if len(bullet) > 80:
            size = font_size - 4
        elif len(bullet) > 120:
            size = font_size - 6
        for run in para.runs:
            run.font.size = Pt(size)
            run.font.color.rgb = theme[font_color_key]
            run.font.name = theme["font_body"]
    return txBox


def render_cover(prs, blank_layout, theme, slide_data: dict):
    """封面页：大标题 + 副标题 + 底部色条。"""
    slide = prs.slides.add_slide(blank_layout)
    _add_bg(slide, theme, "bg")

    # 顶部色条
    _add_rect(slide, theme, Inches(0), Inches(0), SLIDE_WIDTH, Inches(0.08), "primary")

    # 标题
    title = slide_data.get("title", "")
    _add_textbox(
        slide,
        theme,
        Inches(1.5),
        Inches(2.2),
        Inches(10),
        Inches(1.8),
        title,
        font_size=44,
        font_color_key="primary",
        bold=True,
        font_key="font_heading",
        line_spacing=1.2,
    )

    # 副标题
    subtitle = slide_data.get("subtitle", "")
    if subtitle:
        _add_textbox(
            slide,
            theme,
            Inches(1.5),
            Inches(4.2),
            Inches(10),
            Inches(1),
            subtitle,
            font_size=22,
            font_color_key="text_muted",
            line_spacing=1.4,
        )

    # 底部色条
    _add_rect(slide, theme, Inches(0), Inches(7.0), SLIDE_WIDTH, Inches(0.5), "primary")

    # 底部文字
    footer = slide_data.get("footer", "")
    if footer:
        _add_textbox(
            slide,
            theme,
            Inches(1.5),
            Inches(7.05),
            Inches(10),
            Inches(0.4),
            footer,
            font_size=14,
            font_color_key="text_light",
        )
    return slide


def render_section(prs, blank_layout, theme, slide_data: dict):
    """章节页：大号章节标题居中，背景色为 primary_dark。"""
    slide = prs.slides.add_slide(blank_layout)
    _add_bg(slide, theme, "primary_dark")

    title = slide_data.get("title", "")
    _add_textbox(
        slide,
        theme,
        Inches(1.5),
        Inches(2.8),
        Inches(10),
        Inches(2),
        title,
        font_size=40,
        font_color_key="text_light",
        bold=True,
        alignment=PP_ALIGN.CENTER,
        font_key="font_heading",
        line_spacing=1.2,
    )

    subtitle = slide_data.get("subtitle", "")
    if subtitle:
        _add_textbox(
            slide,
            theme,
            Inches(2),
            Inches(4.8),
            Inches(9),
            Inches(1),
            subtitle,
            font_size=20,
            font_color_key="text_light",
            alignment=PP_ALIGN.CENTER,
            line_spacing=1.4,
        )

    # 装饰线
    _add_rect(
        slide, theme, Inches(5.5), Inches(4.4), Inches(2.3), Inches(0.04), "primary"
    )
    return slide


def render_content(prs, blank_layout, theme, slide_data: dict):
    """内容页：标题 + 要点列表。"""
    slide = prs.slides.add_slide(blank_layout)
    _add_bg(slide, theme, "bg")

    # 顶部色条
    _add_rect(slide, theme, Inches(0), Inches(0), SLIDE_WIDTH, Inches(0.06), "primary")

    title = slide_data.get("title", "")
    _add_textbox(
        slide,
        theme,
        Inches(0.8),
        Inches(0.5),
        Inches(11.7),
        Inches(1),
        title,
        font_size=32,
        font_color_key="primary",
        bold=True,
        font_key="font_heading",
        line_spacing=1.2,
    )

    # 标题下分割线
    _add_rect(slide, theme, Inches(0.8), Inches(1.5), Inches(2), Inches(0.04), "accent")

    bullets = slide_data.get("bullets", [])
    if bullets:
        _add_bullets(
            slide,
            theme,
            Inches(0.8),
            Inches(1.8),
            Inches(11.5),
            Inches(5),
            bullets,
            font_size=22,
            font_color_key="text",
        )

    note = slide_data.get("note", "")
    if note:
        _add_textbox(
            slide,
            theme,
            Inches(0.8),
            Inches(6.8),
            Inches(11),
            Inches(0.5),
            note,
            font_size=14,
            font_color_key="text_muted",
        )
    return slide


def render_two_column(prs, blank_layout, theme, slide_data: dict):
    """双栏布局：标题 + 左右两列内容。"""
    slide = prs.slides.add_slide(blank_layout)
    _add_bg(slide, theme, "bg")
    _add_rect(slide, theme, Inches(0), Inches(0), SLIDE_WIDTH, Inches(0.06), "primary")

    title = slide_data.get("title", "")
    _add_textbox(
        slide,
        theme,
        Inches(0.8),
        Inches(0.5),
        Inches(11.7),
        Inches(1),
        title,
        font_size=32,
        font_color_key="primary",
        bold=True,
        font_key="font_heading",
    )
    _add_rect(slide, theme, Inches(0.8), Inches(1.5), Inches(2), Inches(0.04), "accent")

    # 左栏
    left_title = slide_data.get("left_title", "")
    left_items = slide_data.get("left_content", [])
    if left_title:
        _add_textbox(
            slide,
            theme,
            Inches(0.8),
            Inches(1.8),
            Inches(5.5),
            Inches(0.8),
            left_title,
            font_size=24,
            font_color_key="primary_dark",
            bold=True,
            font_key="font_heading",
        )
    if left_items:
        _add_bullets(
            slide,
            theme,
            Inches(0.8),
            Inches(2.8),
            Inches(5.5),
            Inches(4),
            left_items if isinstance(left_items, list) else [left_items],
            font_size=20,
            font_color_key="text",
        )

    # 中间分割线
    _add_rect(slide, theme, Inches(6.6), Inches(1.8), Inches(0.03), Inches(5), "border")

    # 右栏
    right_title = slide_data.get("right_title", "")
    right_items = slide_data.get("right_content", [])
    if right_title:
        _add_textbox(
            slide,
            theme,
            Inches(7),
            Inches(1.8),
            Inches(5.5),
            Inches(0.8),
            right_title,
            font_size=24,
            font_color_key="primary_dark",
            bold=True,
            font_key="font_heading",
        )
    if right_items:
        _add_bullets(
            slide,
            theme,
            Inches(7),
            Inches(2.8),
            Inches(5.5),
            Inches(4),
            right_items if isinstance(right_items, list) else [right_items],
            font_size=20,
            font_color_key="text",
        )
    return slide


def render_data(prs, blank_layout, theme, slide_data: dict):
    """数据页：标题 + 数据指标卡 + 要点说明。"""
    slide = prs.slides.add_slide(blank_layout)
    _add_bg(slide, theme, "bg")
    _add_rect(slide, theme, Inches(0), Inches(0), SLIDE_WIDTH, Inches(0.06), "primary")

    title = slide_data.get("title", "")
    _add_textbox(
        slide,
        theme,
        Inches(0.8),
        Inches(0.5),
        Inches(11.7),
        Inches(1),
        title,
        font_size=32,
        font_color_key="primary",
        bold=True,
        font_key="font_heading",
    )
    _add_rect(slide, theme, Inches(0.8), Inches(1.5), Inches(2), Inches(0.04), "accent")

    # 数据指标卡
    metrics = slide_data.get("metrics", [])
    if metrics:
        col_width = Inches(2.6)
        gap = Inches(0.3)
        start_left = Inches(0.8)
        for i, m in enumerate(metrics[:4]):
            left = start_left + i * (col_width + gap)
            # 卡片背景
            card = _add_rect(
                slide, theme, left, Inches(1.9), col_width, Inches(2.2), "bg_alt"
            )
            # 数值
            _add_textbox(
                slide,
                theme,
                left + Inches(0.2),
                Inches(2.1),
                col_width - Inches(0.4),
                Inches(1),
                str(m.get("value", "")),
                font_size=36,
                font_color_key="primary",
                bold=True,
                alignment=PP_ALIGN.CENTER,
                font_key="font_heading",
            )
            # 标签
            _add_textbox(
                slide,
                theme,
                left + Inches(0.2),
                Inches(3.2),
                col_width - Inches(0.4),
                Inches(0.6),
                str(m.get("label", "")),
                font_size=16,
                font_color_key="text_muted",
                alignment=PP_ALIGN.CENTER,
            )

    # 要点说明
    bullets = slide_data.get("bullets", [])
    if bullets:
        _add_bullets(
            slide,
            theme,
            Inches(0.8),
            Inches(4.5),
            Inches(11.5),
            Inches(2.5),
            bullets,
            font_size=20,
            font_color_key="text",
        )
    return slide


def render_image_text(prs, blank_layout, theme, slide_data: dict):
    """图文页：标题 + 左文右图（或左图右文）。"""
    slide = prs.slides.add_slide(blank_layout)
    _add_bg(slide, theme, "bg")
    _add_rect(slide, theme, Inches(0), Inches(0), SLIDE_WIDTH, Inches(0.06), "primary")

    title = slide_data.get("title", "")
    _add_textbox(
        slide,
        theme,
        Inches(0.8),
        Inches(0.5),
        Inches(11.7),
        Inches(1),
        title,
        font_size=32,
        font_color_key="primary",
        bold=True,
        font_key="font_heading",
    )
    _add_rect(slide, theme, Inches(0.8), Inches(1.5), Inches(2), Inches(0.04), "accent")

    image_left = slide_data.get("image_left", False)
    image_path = slide_data.get("image_path", "")
    bullets = slide_data.get("bullets", [])

    if image_left and image_path:
        # 左图右文
        try:
            slide.shapes.add_picture(
                image_path, Inches(0.8), Inches(2.0), Inches(5.5), Inches(4.5)
            )
        except Exception:
            _add_rect(
                slide,
                theme,
                Inches(0.8),
                Inches(2.0),
                Inches(5.5),
                Inches(4.5),
                "bg_alt",
            )
        if bullets:
            _add_bullets(
                slide,
                theme,
                Inches(7),
                Inches(2.0),
                Inches(5.5),
                Inches(4.5),
                bullets,
                font_size=22,
                font_color_key="text",
            )
    else:
        # 左文右图
        if bullets:
            _add_bullets(
                slide,
                theme,
                Inches(0.8),
                Inches(2.0),
                Inches(5.5),
                Inches(4.5),
                bullets,
                font_size=22,
                font_color_key="text",
            )
        if image_path:
            try:
                slide.shapes.add_picture(
                    image_path, Inches(7), Inches(2.0), Inches(5.5), Inches(4.5)
                )
            except Exception:
                _add_rect(
                    slide,
                    theme,
                    Inches(7),
                    Inches(2.0),
                    Inches(5.5),
                    Inches(4.5),
                    "bg_alt",
                )
    return slide


def render_closing(prs, blank_layout, theme, slide_data: dict):
    """结束页：感谢标题居中 + 联系信息。"""
    slide = prs.slides.add_slide(blank_layout)
    _add_bg(slide, theme, "primary_dark")

    title = slide_data.get("title", "Thank You")
    _add_textbox(
        slide,
        theme,
        Inches(2),
        Inches(2.5),
        Inches(9.3),
        Inches(2),
        title,
        font_size=44,
        font_color_key="text_light",
        bold=True,
        alignment=PP_ALIGN.CENTER,
        font_key="font_heading",
    )

    subtitle = slide_data.get("subtitle", "")
    if subtitle:
        _add_textbox(
            slide,
            theme,
            Inches(2.5),
            Inches(4.5),
            Inches(8.3),
            Inches(1),
            subtitle,
            font_size=20,
            font_color_key="text_light",
            alignment=PP_ALIGN.CENTER,
        )

    # 装饰线
    _add_rect(
        slide, theme, Inches(5.5), Inches(4.1), Inches(2.3), Inches(0.04), "primary"
    )

    contact = slide_data.get("contact", "")
    if contact:
        _add_textbox(
            slide,
            theme,
            Inches(2.5),
            Inches(5.5),
            Inches(8.3),
            Inches(1),
            contact,
            font_size=16,
            font_color_key="text_light",
            alignment=PP_ALIGN.CENTER,
        )
    return slide


# 布局渲染函数映射
LAYOUT_RENDERERS = {
    "cover": render_cover,
    "section": render_section,
    "content": render_content,
    "two_column": render_two_column,
    "data": render_data,
    "image_text": render_image_text,
    "closing": render_closing,
}


def render_slides(prs, theme, blank_layout, slides_data: list):
    """按 slides_data 逐页渲染。"""
    for slide_data in slides_data:
        layout = slide_data.get("layout", "content")
        renderer = LAYOUT_RENDERERS.get(layout, render_content)
        renderer(prs, blank_layout, theme, slide_data)
    return prs


def generate_pptx(
    title: str, theme_name: str, slides_data: list, output_path: str
) -> dict:
    """完整生成 PPT 文件。

    Args:
        title: 演示文稿标题（用于文件名）
        theme_name: 主题名称
        slides_data: 幻灯片数据列表，每项含 layout + 内容字段
        output_path: 输出文件路径

    Returns:
        {"success": bool, "path": str, "slides": int}
    """
    prs, theme, blank_layout = create_presentation(theme_name)
    render_slides(prs, theme, blank_layout, slides_data)

    slide_count = len(prs.slides)
    if slide_count == 0:
        return {"success": False, "error": "未解析出有效页面"}

    prs.save(output_path)
    return {
        "success": True,
        "path": output_path,
        "slides": slide_count,
    }

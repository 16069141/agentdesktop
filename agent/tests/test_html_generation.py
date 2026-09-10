"""HTML 生成器注入消毒回归测试。

锁定 HtmlGeneratorTool 三条防线：
- chart_config 经模板 tojson 输出，</script> 等被 unicode 转义，无法逃逸 <script>
- 非法 JSON 配置整体降级丢弃
- chart id 清洗为 [\\w-]（防引号逃逸），custom_css 剔除尖括号（防 </style> 逃逸）
"""
import asyncio
from pathlib import Path

import pytest

from app.tools import HtmlGeneratorTool

arun = asyncio.run


def _generate(sections, custom_css="", title="注入回归测试"):
    gen = HtmlGeneratorTool()
    r = arun(gen.execute({
        "title": title,
        "sections": sections,
        "custom_css": custom_css,
    }))
    assert r["success"] is True, f"HTML 生成失败: {r.get('error')}"
    path = Path(r["path"])
    assert path.exists()
    try:
        return path.read_text(encoding="utf-8")
    finally:
        path.unlink(missing_ok=True)


def test_chart_script_tag_escaped():
    # 合法 chart JSON，但 label 内藏 </script><script> 逃逸串
    cfg = {
        "type": "bar",
        "data": {
            "labels": ["正常", '</script><script>alert("xss")</script>'],
            "datasets": [{"label": "销量", "data": [10, 20]}],
        },
    }
    html = _generate([{"type": "chart", "id": "c1", "chart_config": cfg}])
    # 原始闭合标签不得出现；tojson 把 < 转义为 \u003c
    assert '</script><script>alert("xss")' not in html
    assert "\\u003c/script" in html


def test_invalid_chart_config_discarded():
    # JSON 后面夹带闭合标签：json.loads 失败 → 配置整体丢弃
    sections = [{
        "type": "chart", "id": "bad",
        "chart_config": '{"type":"bar"} </script><script>alert(2)</script>',
    }]
    html = _generate(sections, title="非法配置测试")
    assert "alert(2)" not in html


def test_custom_css_angle_brackets_stripped():
    html = _generate(
        [{"type": "paragraph", "content": "x"}],
        custom_css="body{color:red} </style><script>alert('css-xss')</script>",
        title="CSS注入测试",
    )
    assert "</style><script>alert('css-xss')" not in html


def test_chart_id_quoted_sanitized():
    sections = [{
        "type": "chart",
        "id": "c1', evil = 1",
        "chart_config": {"type": "bar", "data": {"labels": ["a"],
                        "datasets": [{"data": [1]}]}},
    }]
    html = _generate(sections, title="ID注入测试")
    # 引号/空格被清洗，JS 字符串里不应残留可逃逸片段
    assert "evil = 1" not in html
    assert "c1'," not in html


def test_paragraph_section_rendered():
    html = _generate([{"type": "paragraph", "content": "正文内容保留"}],
                     title="正文渲染测试")
    assert "正文内容保留" in html

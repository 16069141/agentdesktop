"""文档/HTML 生成工具：docx→HTML 转换、Jinja2 模板渲染 HTML 页面。

从 tools/__init__.py 拆出，两个工具均为「读文件/套模板/落盘」的内聚类，
共享 _foundation 的 BaseTool 与路径沙箱。
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict

from ._foundation import BaseTool, _data_uploads_dir, _resolve_in_roots

logger = logging.getLogger(__name__)


class DocToHtmlTool(BaseTool):
    """Word 文档 → HTML 转换工具（mammoth 引擎）。

    面向「上传 docx → 提炼总结 → 生成 HTML」场景：模型给出原始 docx
    文件路径，工具返回正文 HTML（图片内嵌为 base64，样式由调用方决定），
    模型据此提炼并输出成品 HTML 页面。
    """

    name = "doc_to_html"
    description = (
        "把 Word 文档（.docx）转换为 HTML 正文。"
        "输入 docx 文件的绝对路径，返回 HTML 内容（正文结构 + 文本，图片以 base64 内嵌）。"
        "适合把用户上传的 Word 文档提炼为 HTML 页面的场景。\n"
        "与 generate_html 的区别：本工具是「docx → 正文 HTML」的格式转换，"
        "不做视觉美化；要生成带设计系统/图表/多区块的精美页面请用 generate_html。"
    )
    requires_approval = False

    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "要转换的 .docx 文件绝对路径（如 /Users/xxx/.../文档.docx）",
            },
        },
        "required": ["path"],
    }

    def __init__(self, allowed_roots: list[str] | None = None):
        # 与 filesystem 同一沙箱：只转换允许根目录内的 docx
        # （旧实现可读取任意路径文件）
        self.allowed_roots = [
            Path(r).resolve() for r in (allowed_roots or [Path.home()])
        ]

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        path = arguments.get("path", "")
        if not path:
            return {"success": False, "error": "缺少 path 参数"}
        try:
            p = _resolve_in_roots(path, self.allowed_roots)
        except PermissionError as e:
            return {"success": False, "error": str(e)}
        if not p.exists():
            return {"success": False, "error": f"文件不存在: {path}"}
        if p.suffix.lower() != ".docx":
            return {
                "success": False,
                "error": f"仅支持 .docx 文件，收到: {p.suffix or '(无扩展名)'}",
            }

        try:
            import mammoth

            with p.open("rb") as fh:
                result = mammoth.convert_to_html(fh, style_map=[])

            html = result.value
            messages = result.messages or []
            if len(html) > 60_000:
                html = html[:60_000] + "\n<!-- [截断] HTML 过长，剩余部分省略 -->"
            return {
                "success": True,
                "html": html,
                "char_count": len(html),
                "warnings": [str(m) for m in messages][:5],
            }
        except ImportError:
            return {"success": False, "error": "mammoth 库未安装，无法转换 docx"}
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[tools] doc_to_html 转换失败: {exc}")
            return {"success": False, "error": f"docx 转换失败: {exc}"}


class HtmlGeneratorTool(BaseTool):
    """HTML 生成工具：通过 Jinja2 模板引擎渲染精美 HTML 页面。

    面向「生成 HTML 报告 / 数据看板 / 对比分析 / 落地页」场景。
    模型提供结构化 JSON 内容（title, subtitle, sections, theme），
    工具自动套用 CSS 设计系统渲染成品 HTML 文件。

    核心优势：排版由模板固化，模型只负责内容组织，不写 CSS。
    支持 4 套模板 + 4 套主题 + Chart.js 图表。
    """

    name = "generate_html"
    description = (
        "根据结构化 JSON 内容生成精美 HTML 页面文件。"
        "模板引擎自动套用 CSS 设计系统（配色、字体、间距、响应式），"
        "支持 Chart.js 图表、内联 SVG 图标库、中文排版优化。\n"
        "与 doc_to_html 的区别：本工具从零生成带设计的完整页面（JSON 内容 → 成品页面）；"
        "doc_to_html 只做 Word 文档 → 正文 HTML 的格式转换。\n"
        "参数说明：\n"
        "  - title: 页面标题（必填）\n"
        "  - template: 模板类型，可选 report(分析报告) / dashboard(数据看板) / comparison(对比分析) / landing(落地页)\n"
        "  - theme: 主题，可选 corporate_blue(商务蓝) / tech_dark(科技暗色) / minimal_white(极简白) / "
        "warm_earth(暖土色) / aurora_purple(极光紫) / forest_green(森林绿) / sunset_orange(落日橙)，默认 corporate_blue\n"
        "  - custom_css: 自定义 CSS 覆盖代码（可选），追加到页面 <style> 末尾，可覆盖任意样式变量或类\n"
        "  - subtitle: 副标题（可选）\n"
        "  - sections: 内容区块数组，每项含 type 和对应内容字段\n"
        "    支持的 section type：\n"
        "      heading(标题段) / paragraph(段落) / cards(卡片组) / table(表格) / chart(图表) / "
        "stats(统计指标) / quote(引用) / list(列表) / timeline(时间线) / grid-2(两栏) / cta(行动号召)\n"
        "    cards 的 items 项可带 icon 字段，内置图标名：rocket / chart / shield / doc / database / "
        "globe / code / star / check / arrow / search / download / link\n"
        "    chart section 需提供 id 和 chart_config（Chart.js 配置 JSON）\n"
        '示例 sections: [{"type":"cards","title":"核心优势","cols":3,"items":[{"title":"高性能","content":"...","icon":"rocket"}]}]'
    )
    requires_approval = False

    parameters = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "页面标题（显示在 hero 区域和浏览器标签）",
            },
            "template": {
                "type": "string",
                "enum": ["report", "dashboard", "comparison", "landing"],
                "description": "模板类型（默认 report）",
            },
            "theme": {
                "type": "string",
                "enum": [
                    "corporate_blue",
                    "tech_dark",
                    "minimal_white",
                    "warm_earth",
                    "aurora_purple",
                    "forest_green",
                    "sunset_orange",
                ],
                "description": "视觉主题（默认 corporate_blue）",
            },
            "custom_css": {
                "type": "string",
                "description": "自定义 CSS 覆盖代码（可选）。追加到页面 <style> 末尾，"
                "可覆盖 --color-* / --font-* 变量或任意类样式，实现逐样式定制",
            },
            "subtitle": {
                "type": "string",
                "description": "副标题（显示在 hero 区域标题下方）",
            },
            "sections": {
                "type": "array",
                "description": "内容区块数组。每项含 type 字段和对应内容",
                "items": {"type": "object"},
            },
            "badge": {"type": "string", "description": "hero 区域标签文字（可选）"},
            "footer_text": {"type": "string", "description": "页脚文字（可选）"},
        },
        "required": ["title", "sections"],
    }

    _TEMPLATE_MAP = {
        "report": "report.html.j2",
        "dashboard": "dashboard.html.j2",
        "comparison": "comparison.html.j2",
        "landing": "landing.html.j2",
    }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        title = (arguments.get("title") or "").strip()
        if not title:
            return {"success": False, "error": "缺少 title 参数"}

        sections = arguments.get("sections")
        if not sections or not isinstance(sections, list):
            return {
                "success": False,
                "error": "缺少 sections 内容区块（至少需要 1 个 section）",
            }

        template_name = arguments.get("template", "report")
        theme_name = arguments.get("theme", "corporate_blue")
        subtitle = arguments.get("subtitle", "")
        badge = arguments.get("badge", "")
        footer_text = arguments.get("footer_text", "")
        custom_css = arguments.get("custom_css", "") or ""
        # CSS 上下文消毒：custom_css 被原样放入 <style>，合法 CSS 不含尖括号；
        # 剔除 < > 可阻断 `</style><script>...` 这类逃逸出样式块的注入。
        if custom_css:
            custom_css = custom_css.replace("<", "").replace(">", "")

        try:
            import json as _json
            from jinja2 import Environment, FileSystemLoader, select_autoescape

            template_dir = str(
                Path(__file__).resolve().parent.parent / "assets" / "html_templates"
            )
            env = Environment(
                loader=FileSystemLoader(template_dir),
                autoescape=select_autoescape(["html", "xml"]),
                trim_blocks=True,
                lstrip_blocks=True,
            )

            theme_path = (
                Path(__file__).resolve().parent.parent
                / "assets"
                / "theme"
                / "themes.json"
            )
            with open(theme_path, "r", encoding="utf-8") as f:
                all_themes = _json.load(f)
            theme = all_themes.get(theme_name, all_themes["corporate_blue"])

            charts = []
            for sec in sections:
                if isinstance(sec, dict) and sec.get("type") == "chart":
                    # id 会进入模板 getElementById('...') 与 <canvas id="..."> 的
                    # JS/HTML 上下文，只保留字母数字下划线连字符，防引号/脚本注入。
                    # 回写 section.id：模板 canvas 与脚本 charts 共用同一干净 id，
                    # 否则 id 不一致会导致图表找不到画布。
                    raw_id = str(sec.get("id") or f"chart_{len(charts)}")
                    chart_id = re.sub(r"[^\w-]", "_", raw_id) or f"chart_{len(charts)}"
                    sec["id"] = chart_id
                    chart_config = sec.get("chart_config") or sec.get("config") or {}
                    # 统一解析为 dict 交给模板：模板用 tojson 输出（自动转义
                    # < > &，防 </script> 注入）。非法 JSON 降级为空配置。
                    if isinstance(chart_config, str):
                        try:
                            chart_config = _json.loads(chart_config)
                        except (ValueError, TypeError):
                            chart_config = {}
                    if not isinstance(chart_config, dict):
                        chart_config = {}
                    charts.append({"id": chart_id, "config": chart_config})

            template_file = self._TEMPLATE_MAP.get(template_name, "report.html.j2")
            template = env.get_template(template_file)

            html = template.render(
                title=title,
                subtitle=subtitle,
                badge=badge,
                sections=sections,
                theme=theme,
                charts=charts,
                footer_text=footer_text,
                custom_css=custom_css,
                meta=arguments.get("meta", ""),
            )

            html_dir = _data_uploads_dir("html")
            safe_name = "".join(c for c in title if c.isalnum() or c in "._-") or "page"
            out_path = html_dir / f"{safe_name}.html"
            out_path.write_text(html, encoding="utf-8")

            return {
                "success": True,
                "path": str(out_path),
                "filename": out_path.name,
                "template": template_name,
                "theme": theme_name,
                "sections_count": len(sections),
            }

        except ImportError as exc:
            return {"success": False, "error": f"依赖库未安装: {exc}"}
        except Exception as exc:
            logger.error(f"[tools] generate_html 失败: {exc}", exc_info=True)
            return {"success": False, "error": f"HTML 生成失败: {exc}"}

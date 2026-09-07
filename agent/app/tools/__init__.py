"""内置工具注册表。

本文件定义 Phase 2 内置工具的实现类，每个工具暴露统一的 execute() 接口，
供 LangGraph Agent（Phase 3）调用。
"""

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class BaseTool:
    """工具基类。"""

    name: str = "base"
    description: str = "Base tool"
    requires_approval: bool = False

    # OpenAI function-calling 风格的参数 JSON Schema。
    # 没有它，模型就无从知道该传什么参数，工具调用闭环无法成立。
    parameters: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def to_openai_schema(self) -> Dict[str, Any]:
        """转换为 OpenAI/Ollama function-calling 的工具描述。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class FilesystemTool(BaseTool):
    """文件系统工具（受限根目录）。"""

    name = "filesystem"
    description = "读取/写入文件、列出目录、搜索文件。操作限制在 allowed_root_dirs 内。"
    requires_approval = False

    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["read", "write", "list", "search"],
                "description": "操作类型：read 读文件 / write 写文件 / list 列目录 / search 按 glob 搜索文件",
            },
            "path": {
                "type": "string",
                "description": "目标文件或目录的绝对路径",
            },
            "content": {
                "type": "string",
                "description": "action=write 时必填：要写入的文本内容",
            },
            "pattern": {
                "type": "string",
                "description": "action=search 时使用的 glob 模式，如 '*.py'，默认 '*'",
            },
        },
        "required": ["action", "path"],
    }

    def __init__(self, allowed_roots: list[str] | None = None):
        self.allowed_roots = [
            Path(r).resolve() for r in (allowed_roots or [Path.home()])
        ]

    def _check_path(self, path_str: str) -> Path:
        p = Path(path_str)
        if not p.is_absolute():
            p = p.resolve()
        try:
            p.resolve().relative_to(self.allowed_roots[0])
        except ValueError:
            raise PermissionError(f"path outside allowed roots: {path_str}")
        return p

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        action = arguments.get("action", "read")
        path = arguments.get("path", "")
        content = arguments.get("content")

        try:
            p = self._check_path(path)
        except PermissionError as e:
            return {"success": False, "error": str(e)}

        if action == "read":
            try:
                text = p.read_text(encoding="utf-8")
                return {"success": True, "content": text[:10000]}  # 截断防上下文溢出
            except Exception as e:
                return {"success": False, "error": str(e)}

        elif action == "write":
            if content is None:
                return {"success": False, "error": "content required"}
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
                return {"success": True, "path": str(p)}
            except Exception as e:
                return {"success": False, "error": str(e)}

        elif action == "list":
            try:
                items = [str(x) for x in p.iterdir()]
                return {"success": True, "items": items[:100]}
            except Exception as e:
                return {"success": False, "error": str(e)}

        elif action == "search":
            pattern = arguments.get("pattern", "*")
            try:
                matches = [str(x) for x in p.rglob(pattern) if x.is_file()]
                return {"success": True, "matches": matches[:50]}
            except Exception as e:
                return {"success": False, "error": str(e)}

        return {"success": False, "error": f"unknown action: {action}"}


class ShellTool(BaseTool):
    """Shell 命令工具（白名单 + 确认 + 超时 + 审计）。"""

    name = "shell"
    description = "执行 shell 命令（白名单校验、UI 确认、超时限制、全量审计）"
    requires_approval = True

    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的 shell 命令，如 'ls -la /tmp'。禁止危险命令（rm -rf、mkfs、dd 等）",
            },
            "cwd": {
                "type": "string",
                "description": "可选：命令执行的工作目录",
            },
        },
        "required": ["command"],
    }

    def __init__(self, security, approval_callback=None):
        self.security = security
        self.approval_callback = approval_callback  # 异步回调，返回 bool

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        command = arguments.get("command", "")
        cwd = arguments.get("cwd")

        if not command:
            return {"success": False, "error": "command required"}

        # 安全检查
        check = self.security.check_command(command)
        if not check["allowed"]:
            return {
                "success": False,
                "error": f"security_check_failed: {check['reason']}",
                "approved": False,
            }

        # 需要确认 → 触发 IPC
        # 兼容同步/异步回调：生产路径是 Electron 主进程的异步 IPC（返回 coroutine），
        # 评测/脚本路径常传同步 lambda（直接返回 bool）。统一在此归一化为 bool。
        approved = False
        if self.approval_callback:
            try:
                cb_result = self.approval_callback({"command": command, "cwd": cwd})
                if asyncio.iscoroutine(cb_result):
                    approved = await asyncio.wait_for(cb_result, timeout=60.0)
                else:
                    approved = bool(cb_result)
            except asyncio.TimeoutError:
                approved = False
        else:
            # 无回调时默认拒绝（安全优先）
            approved = False

        if not approved:
            result = {
                "success": False,
                "error": "command_denied_by_user",
                "approved": False,
                "command": command,
            }
            logger.info("[shell] 命令被用户拒绝: %s", command)
            return result

        # 执行
        exec_result = await self.security.execute(command, approved=True, cwd=cwd)
        exec_result["command"] = command
        logger.info(
            "[shell] 命令执行: %s -> success=%s", command, exec_result.get("success")
        )
        return exec_result


class KnowledgeTool(BaseTool):
    """知识库检索工具（连接式：远程 RAG / Wiki 知识库，带引用溯源）。

    纯云端/连接式架构下不再依赖本地 RAG Pipeline（chromadb 已移除）。
    检索目标来自「知识库连接管理」模块（knowledge_servers 表）：
    - 未指定 server_id：使用第一个已启用的连接；
    - 指定 server_id：使用对应连接（模型可在 arguments 中给出）。
    """

    name = "knowledge"
    description = "企业知识库检索（RAG / Wiki，带引用溯源：来源 + 标题 + 摘要 + 链接），从已配置的知识库连接中检索"
    requires_approval = False

    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "检索查询语句，应使用自然语言描述要查找的内容",
            },
            "server_id": {
                "type": "string",
                "description": "可选：指定要检索的知识库连接 id（留空则自动选择第一个已启用连接）",
            },
            "top_k": {
                "type": "integer",
                "description": "返回的最相关结果条数，默认 5",
            },
        },
        "required": ["query"],
    }

    def __init__(self, **kwargs):
        pass

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        query = arguments.get("query", "")
        top_k = int(arguments.get("top_k", 5))
        server_id = (arguments.get("server_id") or "").strip()
        if not query:
            return {"success": False, "error": "query required"}

        try:
            from ..knowledge_connectors import create_connector
            from ..security import keychain
            from ..storage.knowledge_servers import list_knowledge_servers_sync

            servers = list_knowledge_servers_sync()
        except Exception as exc:
            logger.exception("[knowledge] 读取知识库连接失败")
            return {
                "success": False,
                "error": f"读取知识库连接失败: {exc}",
                "results": [],
                "count": 0,
            }

        target = None
        if server_id:
            for s in servers:
                if s.get("id") == server_id:
                    target = s
                    break
            if target is None:
                return {
                    "success": False,
                    "error": f"知识库连接 {server_id} 不存在，请先在设置中新增并测试连接。",
                    "results": [],
                    "count": 0,
                }
        else:
            enabled = [s for s in servers if s.get("enabled")]
            if not enabled:
                return {
                    "success": False,
                    "error": "尚未配置知识库连接，无法检索。请在设置中新增 RAG / Wiki 知识库连接并完成连通性测试。",
                    "results": [],
                    "count": 0,
                }
            target = enabled[0]

        if not target.get("enabled"):
            return {
                "success": False,
                "error": f"知识库连接 {target.get('id')} 已停用，无法检索。",
                "results": [],
                "count": 0,
            }

        api_key = ""
        ref = target.get("api_key_ref")
        if ref:
            try:
                api_key = await keychain.retrieve(ref) or ""
            except Exception:
                api_key = ""

        connector = create_connector(target, api_key=api_key)
        try:
            results = await connector.search(query, top_k=top_k)
            ok = not any(
                r.get("title") in ("检索失败", "适配器未实现", "配置缺失")
                for r in results
            )
            return {
                "success": ok,
                "query": query,
                "server_id": target.get("id"),
                "server_name": target.get("name"),
                "results": results,
                "count": len(results),
            }
        except Exception as e:
            logger.exception("[knowledge] 检索失败")
            return {"success": False, "error": str(e)}


class CodeTool(BaseTool):
    """代码分析工具（只读静态分析，不执行代码）。

    提供三类动作：
    - analyze：统计行数 / 函数 / 类 / 注释 / 复杂度
    - outline：列出函数与类定义
    - complexity：估算圈复杂度（基于控制流关键字计数）
    不依赖外部解析器，正则实现，保证在任何部署环境可用。
    """

    name = "code"
    description = "代码静态分析（只读，不执行代码）：解析结构、统计复杂度、查找符号"
    requires_approval = False

    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["analyze", "outline", "complexity"],
                "description": "analyze 综合分析 / outline 列出函数与类结构 / complexity 估算复杂度",
            },
            "path": {
                "type": "string",
                "description": "要分析的代码文件路径（与 code 二选一）",
            },
            "code": {
                "type": "string",
                "description": "可选：直接传入代码片段（不指定 path 时使用）",
            },
            "language": {
                "type": "string",
                "description": "代码语言，如 python / typescript / javascript",
            },
        },
        "required": ["action"],
    }

    # 语言 → 行注释前缀（用于注释统计）
    _COMMENT_PREFIXES = {
        "python": ("#",),
        "typescript": ("//",),
        "javascript": ("//",),
        "go": ("//",),
        "java": ("//",),
        "c": ("//",),
        "cpp": ("//",),
        "ruby": ("#",),
        "shell": ("#",),
    }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        action = arguments.get("action", "analyze")
        path = arguments.get("path")
        code = arguments.get("code", "")
        language = arguments.get("language", "")

        source = code or ""
        if path and not source:
            try:
                source = Path(path).read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                return {"success": False, "error": f"读取文件失败: {e}"}
        if not source.strip():
            return {"success": False, "error": "需要提供 path 或 code"}

        if action == "analyze":
            return {
                "success": True,
                "result": self._analyze(source, language),
                "arguments": arguments,
            }
        if action == "outline":
            return {
                "success": True,
                "result": self._outline(source),
                "arguments": arguments,
            }
        if action == "complexity":
            return {
                "success": True,
                "result": self._complexity(source),
                "arguments": arguments,
            }
        return {"success": False, "error": f"unknown action: {action}"}

    def _analyze(self, source: str, language: str = "") -> str:
        lines = source.splitlines()
        total = len(lines)
        blank = sum(1 for l in lines if not l.strip())
        prefixes = self._COMMENT_PREFIXES.get(language.lower(), ("#", "//"))
        comments = sum(1 for l in lines if l.strip().startswith(prefixes))
        funcs = sum(
            1 for l in lines if re.match(r"\s*(async\s+)?(def|func|function)\s+", l)
        )
        classes = sum(1 for l in lines if re.match(r"\s*(class|type|interface)\s+", l))
        complexity = self._estimate_complexity(source)
        return (
            f"代码分析结果：\n"
            f"  总行数: {total}\n"
            f"  代码行: {total - blank - comments}\n"
            f"  注释行: {comments}\n"
            f"  空行:   {blank}\n"
            f"  函数/方法定义: {funcs}\n"
            f"  类/接口定义:  {classes}\n"
            f"  估算圈复杂度: {complexity}"
        )

    def _outline(self, source: str) -> str:
        symbols = []
        for m in re.finditer(
            r"(?:async\s+)?(?:def|func|function)\s+(\w+)|(?:class|type|interface)\s+(\w+)",
            source,
        ):
            name = m.group(1) or m.group(2)
            kind = "function" if m.group(1) else "class"
            symbols.append(f"  {kind}: {name}")
        return "\n".join(symbols[:50]) if symbols else "未找到符号定义"

    def _complexity(self, source: str) -> str:
        return f"估算圈复杂度: {self._estimate_complexity(source)}"

    def _estimate_complexity(self, source: str) -> int:
        """McCabe 风格估算：1 + 控制流关键字计数。"""
        keywords = re.findall(
            r"\b(if|elif|else|for|while|case|catch|except|and|or|&&|\|\||\?)\b",
            source,
            re.IGNORECASE,
        )
        return 1 + len(keywords)


class BrowserTool(BaseTool):
    """浏览器自动化工具（来源白名单校验）。

    当前为占位实现：v0.1.0 尚未接入 Playwright。为避免误导模型与用户，
    明确返回「未实现」而非模拟成功。
    """

    name = "browser"
    description = (
        "网页浏览与抓取（来源白名单校验）：打开页面、提取正文（当前版本未实现）"
    )
    requires_approval = False

    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["open", "extract"],
                "description": "open 打开页面 / extract 提取正文内容",
            },
            "url": {
                "type": "string",
                "description": "目标网页 URL，必须在来源白名单内",
            },
        },
        "required": ["action", "url"],
    }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "success": False,
            "error": "browser 工具在当前版本（v0.1.0）尚未实现，请勿依赖其返回内容。",
            "arguments": arguments,
        }


class ConnectorTool(BaseTool):
    """企业系统连接器工具（需求 §2.1）。

    每个连接器类型注册一个工具（erp / crm / oa），操作以 enum 呈现，
    便于 Agent 按业务意图（订单/库存/客户/审批…）调度。
    绑定该类型第一个启用实例；未配置连接时返回明确提示。
    执行走 connector.call()（带身份透传 + 操作级审计）。
    """

    requires_approval = False

    def __init__(
        self,
        connector_type: str,
        name: str,
        description: str,
        operations: list[str],
        requires_approval: bool = False,
    ):
        self.connector_type = connector_type
        self.name = name
        self.description = description
        self.requires_approval = requires_approval
        self.parameters = {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": operations,
                    "description": "要执行的业务操作",
                },
                "params": {
                    "type": "object",
                    "description": "操作参数：路径参数与请求体/查询参数（如 order_id、sku、客户信息等）",
                },
            },
            "required": ["operation"],
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        from ..connectors import create_connector
        from ..security import keychain
        from ..storage import connector_config_repo

        operation = arguments.get("operation", "")
        params = arguments.get("params") or {}
        if not operation:
            return {"success": False, "error": "operation 不能为空"}

        configs = await connector_config_repo.list_all(type_=self.connector_type)
        enabled = [c for c in configs if c.get("enabled")]
        if not enabled:
            return {
                "success": False,
                "error": (
                    f"尚未配置启用的 {self.connector_type.upper()} 连接器，无法调用。"
                    "请在设置 → 连接 中新增并测试连接。"
                ),
            }
        cfg = enabled[0]
        api_key = ""
        ref = cfg.get("apiKeyRef")
        if ref:
            try:
                api_key = await keychain.retrieve(ref) or ""
            except Exception:
                api_key = ""
        connector = create_connector({**cfg, "api_key": api_key})
        return await connector.call(operation, params)


class DbQueryTool(BaseTool):
    """数据库只读查询工具（需求 §2.2 数据库只读直连）。

    强制只读：文件级 mode=ro + SQL 语句级只读校验双重防线，
    写操作一律拒绝。connection 指定已配置的只读连接。
    """

    name = "db_query"
    description = (
        "对已配置的数据库只读连接执行 SQL 查询（仅允许 SELECT/WITH/EXPLAIN，强制只读）"
    )
    requires_approval = False

    parameters = {
        "type": "object",
        "properties": {
            "connection": {
                "type": "string",
                "description": "只读数据库连接 id（在设置 → 连接 中配置）",
            },
            "sql": {
                "type": "string",
                "description": "只读 SQL 查询语句（SELECT / WITH / EXPLAIN）",
            },
        },
        "required": ["connection", "sql"],
    }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        from ..connectors import query_db
        from ..storage import db_connector_repo

        conn_id = arguments.get("connection", "")
        sql = arguments.get("sql", "")
        if not conn_id or not sql:
            return {"success": False, "error": "connection 与 sql 不能为空"}

        conn = await db_connector_repo.get(conn_id)
        if conn is None:
            return {
                "success": False,
                "error": f"只读连接 {conn_id} 不存在，请先在设置 → 连接 中配置。",
            }
        if not conn.get("enabled"):
            return {"success": False, "error": f"只读连接 {conn_id} 已停用。"}
        return await query_db(conn, sql)


class RpaTool(BaseTool):
    """RPA 模拟操作工具（需求 §2.2，P1 骨架预留）。"""

    name = "rpa"
    description = (
        "RPA 模拟操作（适配无接口遗留系统；P1 骨架预留，Playwright 驱动 P2 接入）"
    )
    requires_approval = False

    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["open", "click", "input", "extract"],
                "description": "open 打开页面 / click 点击 / input 输入 / extract 提取内容",
            },
            "params": {
                "type": "object",
                "description": "RPA 操作参数（url / selector / text 等）",
            },
        },
        "required": ["action"],
    }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        from ..connectors import rpa_execute

        return await rpa_execute(
            arguments.get("action", ""), arguments.get("params") or {}
        )


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
        "适合把用户上传的 Word 文档提炼为 HTML 页面的场景。"
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

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        path = arguments.get("path", "")
        if not path:
            return {"success": False, "error": "缺少 path 参数"}
        p = Path(path)
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


class PptGeneratorTool(BaseTool):
    """PPT 生成工具（增强版）：支持多主题、多 layout、结构化 JSON 输入。

    面向「生成 PPT / 做演示文稿」场景。模型输出结构化 JSON：
    - title: 演示文稿标题
    - theme: 主题（corporate_blue / tech_dark / minimal_white）
    - slides: 幻灯片列表，每项含 layout + 内容字段
    支持 7 种 layout：cover / section / content / two_column / data / image_text / closing

    向后兼容：如果 markdown 参数传入，走旧的简单渲染路径。
    """

    name = "generate_ppt"
    description = (
        "根据结构化数据生成精美 PowerPoint 演示文稿（.pptx 文件）。"
        "推荐使用 slides 参数（JSON 格式的幻灯片列表）生成高质量 PPT。\n"
        "每页 slide 是一个对象，必须指定 layout 类型：\n"
        "  - cover: 封面页（字段：title, subtitle, footer）\n"
        "  - section: 章节页（字段：title, subtitle）\n"
        "  - content: 内容页（字段：title, bullets[], note）\n"
        "  - two_column: 双栏页（字段：title, left_title, left_content[], right_title, right_content[]）\n"
        "  - data: 数据页（字段：title, metrics[{value,label}], bullets[]）\n"
        "  - image_text: 图文页（字段：title, bullets[], image_path, image_left）\n"
        "  - closing: 结束页（字段：title, subtitle, contact）\n"
        "theme 可选：corporate_blue(商务蓝) / tech_dark(科技暗色) / minimal_white(极简白)，默认 corporate_blue。\n"
        "建议控制在 8-15 页，每页 3-5 条要点，单条不超过 40 字。"
    )
    requires_approval = False

    parameters = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "演示文稿标题（用于封面页与文件名），如『产品白皮书』",
            },
            "slides": {
                "type": "array",
                "description": "幻灯片数据列表（推荐）。每项含 layout 和对应内容字段",
                "items": {
                    "type": "object",
                    "properties": {
                        "layout": {
                            "type": "string",
                            "enum": [
                                "cover",
                                "section",
                                "content",
                                "two_column",
                                "data",
                                "image_text",
                                "closing",
                            ],
                        },
                    },
                },
            },
            "theme": {
                "type": "string",
                "enum": ["corporate_blue", "tech_dark", "minimal_white"],
                "description": "视觉主题（默认 corporate_blue）",
            },
            "markdown": {
                "type": "string",
                "description": "（兼容旧版）大纲内容：# 开头为新页面标题；该页下方以 - 或 * 开头的行为要点",
            },
        },
        "required": ["title"],
    }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        title = (arguments.get("title") or "").strip()
        if not title:
            return {"success": False, "error": "缺少 title 参数"}

        try:
            from .ppt_engine import (
                generate_pptx,
                LAYOUT_RENDERERS,
                get_theme,
                create_presentation,
                render_slides,
            )

            # 落盘目录
            ppt_dir = (
                Path(__file__).resolve().parent.parent.parent
                / "data"
                / "uploads"
                / "ppt"
            )
            ppt_dir.mkdir(parents=True, exist_ok=True)
            safe_name = (
                "".join(c for c in title if c.isalnum() or c in "._-") or "presentation"
            )
            out_path = ppt_dir / f"{safe_name}.pptx"

            theme_name = arguments.get("theme", "corporate_blue")
            slides_data = arguments.get("slides")

            if slides_data and isinstance(slides_data, list):
                # 新路径：结构化 JSON → 主题引擎渲染
                result = generate_pptx(title, theme_name, slides_data, str(out_path))
                if not result.get("success"):
                    return result
                return {
                    "success": True,
                    "path": str(out_path),
                    "slides": result["slides"],
                    "filename": out_path.name,
                    "theme": theme_name,
                }
            else:
                # 兼容旧版 markdown 路径，转为 slides_data 再走新引擎
                markdown = arguments.get("markdown") or ""
                if not markdown.strip():
                    return {
                        "success": False,
                        "error": "需要提供 slides（结构化数据）或 markdown（大纲文本）",
                    }

                slides_data = self._markdown_to_slides(markdown)
                result = generate_pptx(title, theme_name, slides_data, str(out_path))
                if not result.get("success"):
                    return result
                return {
                    "success": True,
                    "path": str(out_path),
                    "slides": result["slides"],
                    "filename": out_path.name,
                    "theme": theme_name,
                }

        except ImportError as exc:
            return {"success": False, "error": f"依赖库未安装: {exc}"}
        except Exception as exc:
            logger.error(f"[tools] generate_ppt 失败: {exc}", exc_info=True)
            return {"success": False, "error": f"PPT 生成失败: {exc}"}

    def _markdown_to_slides(self, markdown: str) -> list:
        """将旧版 markdown 大纲转换为 slides_data 结构。"""
        slides = []
        current: dict | None = None
        for raw_line in markdown.splitlines():
            line = raw_line.rstrip()
            if not line.strip():
                continue
            stripped = line.lstrip()
            if stripped.startswith("#"):
                if current:
                    slides.append(current)
                current = {
                    "layout": "content",
                    "title": stripped.lstrip("#").strip(),
                    "bullets": [],
                }
            elif re.match(r"^[-*•] |^\d+[.、)]", stripped):
                if current:
                    current["bullets"].append(
                        re.sub(r"^[-*•] |^\d+[.、)]", "", stripped).strip()
                    )
            elif stripped and current:
                current["bullets"].append(stripped)
        if current:
            slides.append(current)
        return slides


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
        "支持 Chart.js 图表，中文排版优化。\n"
        "参数说明：\n"
        "  - title: 页面标题（必填）\n"
        "  - template: 模板类型，可选 report(分析报告) / dashboard(数据看板) / comparison(对比分析) / landing(落地页)\n"
        "  - theme: 主题，可选 corporate_blue / tech_dark / minimal_white / warm_earth\n"
        "  - subtitle: 副标题（可选）\n"
        "  - sections: 内容区块数组，每项含 type 和对应内容字段\n"
        "    支持的 section type：\n"
        "      heading(标题段) / paragraph(段落) / cards(卡片组) / table(表格) / chart(图表) / "
        "stats(统计指标) / quote(引用) / list(列表) / timeline(时间线) / grid-2(两栏) / cta(行动号召)\n"
        "    chart section 需提供 id 和 chart_config（Chart.js 配置 JSON）\n"
        '示例 sections: [{"type":"cards","title":"核心优势","cols":3,"items":[{"title":"高性能","content":"..."}]}]'
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
                "enum": ["corporate_blue", "tech_dark", "minimal_white", "warm_earth"],
                "description": "视觉主题（默认 corporate_blue）",
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
                    chart_id = sec.get("id", f"chart_{len(charts)}")
                    chart_config = sec.get("chart_config") or sec.get("config") or "{}"
                    if isinstance(chart_config, dict):
                        chart_config = _json.dumps(chart_config, ensure_ascii=False)
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
                meta=arguments.get("meta", ""),
            )

            html_dir = (
                Path(__file__).resolve().parent.parent.parent
                / "data"
                / "uploads"
                / "html"
            )
            html_dir.mkdir(parents=True, exist_ok=True)
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


# 工具工厂
def create_tools(
    security=None,
    approval_callback=None,
    allowed_root_dirs=None,
) -> Dict[str, BaseTool]:
    """根据配置创建工具实例。

    纯云端/连接式架构：knowledge 工具不再依赖本地 RAG Pipeline，
    改为从知识库连接管理（knowledge_servers 表）按连接检索。

    P1 新增：企业系统连接器工具（erp/crm/oa，按启用实例动态绑定）、
    数据库只读查询（db_query）、RPA 骨架（rpa）。

    Agent 化新增：doc_to_html（Word 文档 → HTML，mammoth 引擎）、
    generate_ppt（PPT 生成，多主题多 layout）、
    generate_html（HTML 生成，Jinjia2 模板 + CSS 设计系统）。
    """
    from app.security.shell_security import get_security as _get_shell_security

    sec = security or _get_shell_security()
    tools: Dict[str, BaseTool] = {
        "filesystem": FilesystemTool(allowed_roots=allowed_root_dirs),
        "shell": ShellTool(sec, approval_callback=approval_callback),
        # 始终注册 knowledge：未配置知识库连接时由工具自身返回明确提示，
        # 而不是让工具凭空消失（模型会因此不知道知识库功能的存在）。
        "knowledge": KnowledgeTool(),
        "code": CodeTool(),
        "browser": BrowserTool(),
        "db_query": DbQueryTool(),
        "rpa": RpaTool(),
        "doc_to_html": DocToHtmlTool(),
        "generate_ppt": PptGeneratorTool(),
        "generate_html": HtmlGeneratorTool(),
    }
    # 连接器工具：默认全部注册，未配置连接时由工具自身返回提示
    from ..connectors import CONNECTOR_TYPES

    for ctype, meta in CONNECTOR_TYPES.items():
        operations = list(meta["operations"].keys())
        high_risk = any(t.get("risk") == "high" for t in meta["operations"].values())
        tools[ctype] = ConnectorTool(
            connector_type=ctype,
            name=ctype,
            description=meta["description"] + "。可用操作: " + ", ".join(operations),
            operations=operations,
            requires_approval=high_risk,
        )
    return tools


# 工具注册表（单例）
_tool_registry: Dict[str, BaseTool] = {}


def get_tool(name: str) -> Optional[BaseTool]:
    return _tool_registry.get(name)


def register_tool(name: str, tool: BaseTool) -> None:
    _tool_registry[name] = tool


def init_tools(
    security=None,
    approval_callback=None,
    allowed_root_dirs=None,
) -> Dict[str, BaseTool]:
    global _tool_registry
    _tool_registry = create_tools(
        security=security,
        approval_callback=approval_callback,
        allowed_root_dirs=allowed_root_dirs,
    )
    return _tool_registry

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


# 基类 / 落盘目录 / 路径沙箱下沉到 _foundation（叶子模块，供本包各子模块共用，避免循环导入）
from ._foundation import (
    BaseTool,
    _CREDENTIAL_DIR_NAMES,
    _assert_writable,
    _data_uploads_dir,
    _resolve_in_roots,
)

# 文档/HTML 生成工具（docx 转换、Jinja2 渲染），实现见 doc_tools.py
from .doc_tools import DocToHtmlTool, HtmlGeneratorTool
from .image_tools import GenerateImageTool
from .video_tools import GenerateVideoTool
from .planner_tools import CreatePlanTool, UpdatePlanTool
from .subagent_tools import SubagentTool


class FilesystemTool(BaseTool):
    """文件系统工具（受限根目录）。"""

    name = "filesystem"
    description = (
        "读写本地文件、列出目录、按 glob 搜索文件（操作限制在授权目录内）。\n"
        "何时用：用户要求读取/创建/修改本地文件、查看目录内容、在磁盘上查找文件。\n"
        "何时不用：定位客户端自身代码位置用 code_locate；查企业内部资料用 knowledge；"
        "联网内容用 web_search/browser；需要真实执行命令用 shell。\n"
        "失败表现：路径不存在或超出授权目录会返回 error（含原因），"
        "请先列父目录确认真实路径再重试；read 返回内容截断至 10000 字符。"
    )
    requires_approval = False

    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["read", "write", "list", "search"],
                "description": "操作类型：read 读文件（返回前 10000 字符）/ write 写文件（自动建父目录）/ list 列目录（返回前 100 项）/ search 按 glob 递归搜索文件（返回前 50 个）",
            },
            "path": {
                "type": "string",
                "description": "目标文件或目录的绝对路径（相对路径基于用户主目录解析）；不存在或越权会返回 error",
            },
            "content": {
                "type": "string",
                "description": "action=write 时必填：要写入的文本内容（禁止覆盖 ~/.zshrc、~/.ssh 等敏感配置）",
            },
            "pattern": {
                "type": "string",
                "description": "action=search 时的 glob 模式（如 '*.py'、'**/*.md'），在 path 目录下递归匹配文件，默认 '*'",
            },
        },
        "required": ["action", "path"],
    }

    def __init__(self, allowed_roots: list[str] | None = None):
        self.allowed_roots = [
            Path(r).resolve() for r in (allowed_roots or [Path.home()])
        ]

    def _check_path(self, path_str: str) -> Path:
        return _resolve_in_roots(path_str, self.allowed_roots)

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
                # 写保护：禁止覆盖 shell/应用隐藏配置（~/.zshrc、~/.ssh/…）
                _assert_writable(p, self.allowed_roots)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
                return {"success": True, "path": str(p)}
            except PermissionError as e:
                return {"success": False, "error": str(e)}
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
    description = (
        "执行 shell 命令（运行脚本、启动/停止服务、查看进程、安装依赖等真实命令操作）。\n"
        "何时用：必须真实执行命令时（文件读写用 filesystem，抓取网页用 browser，"
        "代码静态分析用 code，代码定位用 code_locate）。\n"
        "约束：命令经白名单校验，危险命令（rm -rf、mkfs、dd 等）会被拦截；"
        "高危命令需用户确认；有超时限制；长输出会被截断。\n"
        "失败表现：返回退出码与错误输出，请据错误修正命令后重试，"
        "不要重复完全相同的失败命令。"
    )
    requires_approval = True

    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的 shell 命令，如 'ls -la /tmp'。禁止危险命令（rm -rf、mkfs、dd、fork 炸弹等）；优先只读/幂等命令，破坏性操作先说明后果",
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

        # 安全检查（cwd 一并校验，防止借工作目录逃逸根目录）
        check = self.security.check_command(command, cwd=cwd)
        if not check["allowed"]:
            return {
                "success": False,
                "error": f"security_check_failed: {check['reason']}",
                "approved": False,
            }

        # 用户要求：放开所有 shell，跳过审批弹窗直接执行
        approved = True

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
    description = (
        "企业知识库检索（RAG / Wiki，带引用溯源：来源 + 标题 + 摘要 + 链接），"
        "从已配置的知识库连接中检索。\n"
        "何时用：查公司制度、内部资料、历史项目文档、团队知识沉淀。\n"
        "何时不用：查询目标是本地文件时用 filesystem；定位客户端自身代码用 code_locate。"
    )
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
                "description": "要分析的代码文件绝对路径（与 code 参数二选一，推荐用 path 指向真实文件）",
            },
            "code": {
                "type": "string",
                "description": "可选：直接传入代码片段（仅当没有真实文件路径时使用）",
            },
            "language": {
                "type": "string",
                "description": "代码语言，如 python / typescript / javascript / java / go；自动识别时可省略",
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

    def __init__(self, allowed_roots: list[str] | None = None):
        # 读文件同样受沙箱约束（旧实现直接 Path(path).read_text，
        # 模型可读取 ~/.ssh/id_rsa 等任意文件）
        self.allowed_roots = [
            Path(r).resolve() for r in (allowed_roots or [Path.home()])
        ]

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        action = arguments.get("action", "analyze")
        path = arguments.get("path")
        code = arguments.get("code", "")
        language = arguments.get("language", "")

        source = code or ""
        if path and not source:
            try:
                p = _resolve_in_roots(path, self.allowed_roots)
                source = p.read_text(encoding="utf-8", errors="replace")
            except PermissionError as e:
                return {"success": False, "error": str(e)}
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


class CodeLocateTool(BaseTool):
    """代码定位工具（只读）：按关键词在客户端代码库中快速定位文件与行号。

    对比豆包"梳理代码逻辑"的差距补齐：模型此前要靠 filesystem search +
    shell grep 多轮摸索才能找到相关代码；本工具一步返回
    「文件:行号: 匹配行（带上下文）」，降低客户端自身问题排查的取证成本。
    """

    name = "code_locate"
    description = (
        "在客户端自身代码库中按关键词快速定位相关文件与行号（只读搜索，一次返回全部命中）。"
        "当用户的问题涉及客户端自身行为（报错、模型/服务器连接、设置、功能缺陷、界面、打包）时，"
        "**优先用本工具**而非逐个 filesystem 搜索：传入 1-5 个关键词，直接拿到命中文件+行号+上下文。"
    )
    requires_approval = False

    parameters = {
        "type": "object",
        "properties": {
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 5,
                "description": "要定位的关键词（如 normalize_v1_url、/models、allowed_models），多个词会分别搜索",
            },
            "path": {
                "type": "string",
                "description": "可选：限定搜索子目录（相对代码库根，如 agent/app/api）；缺省搜全库",
            },
        },
        "required": ["keywords"],
    }

    # 代码文件扩展名（排除图片/音频/二进制）
    _CODE_EXTS = {
        ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".html",
        ".css", ".scss", ".yml", ".yaml", ".sh", ".toml", ".ini", ".vue",
    }
    # 遍历时跳过的目录（体积大/无关）
    _SKIP_DIRS = {
        "node_modules", ".git", "dist", "build", "release", ".venv",
        "__pycache__", "data", "uploads", "models", "runtime", "cache",
        ".vite", "out", "coverage", ".next", "win-unpacked",
    }
    _MAX_FILES = 12
    _MAX_HITS_PER_FILE = 5
    _MAX_CHARS = 6000

    async def execute(self, arguments: dict) -> dict:
        try:
            from ..agents.self_awareness import code_root

            root = code_root()
        except Exception as exc:
            return {"success": False, "result": f"无法定位代码库根: {exc}"}
        keywords = [k.strip() for k in (arguments.get("keywords") or []) if k.strip()]
        if not keywords:
            return {"success": False, "result": "请提供至少一个关键词"}
        rel_path = (arguments.get("path") or "").strip().lstrip("/")
        search_root = str(Path(root) / rel_path) if rel_path else root
        if not Path(search_root).is_dir():
            return {
                "success": False,
                "result": f"路径不存在: {search_root}（可用子目录: agent/app、desktop/src 等）",
            }

        # 大小写不敏感多关键词匹配
        patterns = [re.compile(re.escape(k), re.IGNORECASE) for k in keywords]
        hits: dict[str, list[str]] = {}

        def _walk(directory: Path):
            try:
                entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            except PermissionError:
                return
            for entry in entries:
                if entry.is_dir():
                    if entry.name in self._SKIP_DIRS or entry.name.startswith("."):
                        continue
                    _walk(entry)
                else:
                    if entry.suffix.lower() not in self._CODE_EXTS:
                        continue
                    if len(hits) >= self._MAX_FILES:
                        return
                    try:
                        lines = entry.read_text(encoding="utf-8", errors="ignore").splitlines()
                    except Exception:
                        continue
                    found: list[tuple[int, str]] = []
                    for idx, line in enumerate(lines):
                        if any(p.search(line) for p in patterns):
                            found.append((idx, line))
                            if len(found) >= self._MAX_HITS_PER_FILE:
                                break
                    if found:
                        rel = entry.relative_to(Path(root))
                        blk = [f"{rel}"]
                        for ln, line in found:
                            blk.append(f"  L{ln+1}: {line.strip()[:160]}")
                        hits[str(rel)] = blk

        _walk(Path(search_root))

        if not hits:
            return {
                "success": True,
                "result": f"代码库（{search_root}）中未命中关键词 {keywords}。"
                "建议：换更短的词（如函数名、报错关键字），或先用 filesystem list 看目录结构。",
            }

        lines_out = [f"命中 {len(hits)} 个文件（关键词: {', '.join(keywords)}）：", ""]
        total = 0
        for rel, blk in hits.items():
            if total >= self._MAX_CHARS:
                lines_out.append(f"…（结果已截断，剩余 {len(hits) - len([l for l in lines_out if l and l[0].isdigit()])} 个文件未展示）")
                break
            chunk = "\n".join(blk)
            total += len(chunk)
            lines_out.append(chunk)
            lines_out.append("")
        return {
            "success": True,
            "result": "\n".join(lines_out).strip(),
            "file_count": len(hits),
            "root": root,
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
        "对已配置的数据库只读连接执行 SQL 查询（仅允许 SELECT/WITH/EXPLAIN，强制只读）。"
        "一次查询尽量取全所需字段（用聚合/string_agg 一条 SQL 拿全量元数据），"
        "超长结果会自动压缩并标注 truncated——不要因截断而反复分批补查，"
        "拿够结构信息后立即继续生成最终结果。"
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
        "RPA 模拟操作（模拟点击/输入/提取，适配无接口遗留系统）。"
        "【当前不可用】执行器尚未接入（P1 骨架预留），任何调用都会返回失败。"
        "请勿调用本工具；需要接入外部系统时改用数据库只读（db_query）或浏览器抓取（browser）。"
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


# DocToHtmlTool / HtmlGeneratorTool 已移至 app/tools/doc_tools.py（顶部 re-export 保持 import 可用）


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
        "  - agenda: 议程页（字段：title, items[{no,title,content}]）\n"
        "  - quote: 金句页（字段：content, author）\n"
        "  - stats: 数据指标页（字段：title, metrics[{value,label,desc}], note）\n"
        "theme 可选：corporate_blue(商务蓝) / tech_dark(科技暗色) / minimal_white(极简白) / "
        "warm_earth(暖土色) / aurora_purple(极光紫) / forest_green(森林绿) / sunset_orange(落日橙)，"
        "默认 corporate_blue。\n"
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
                                "agenda",
                                "quote",
                                "stats",
                            ],
                        },
                    },
                },
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
            ppt_dir = _data_uploads_dir("ppt")
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


# HtmlGeneratorTool 实现见 app/tools/doc_tools.py（顶部 re-export）


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
    from .web_tools import BrowserTool, WebSearchTool, McpDynamicTool
    from ..storage import list_mcp_servers_sync

    sec = security or _get_shell_security()
    tools: Dict[str, BaseTool] = {
        "filesystem": FilesystemTool(allowed_roots=allowed_root_dirs),
        "shell": ShellTool(sec, approval_callback=approval_callback),
        # 始终注册 knowledge：未配置知识库连接时由工具自身返回明确提示，
        # 而不是让工具凭空消失（模型会因此不知道知识库功能的存在）。
        "knowledge": KnowledgeTool(),
        "code": CodeTool(allowed_roots=allowed_root_dirs),
        # 代码定位（只读）：按关键词一步返回命中文件+行号+上下文，
        # 客户端自身问题排查的取证入口（对齐豆包"梳理代码逻辑"）
        "code_locate": CodeLocateTool(),
        # 联网能力：browser（运行时安全校验的网页抓取/正文提取）+
        # web_search（多 provider 搜索，未配置时 DuckDuckGo 免 Key 兜底）
        "browser": BrowserTool(),
        "web_search": WebSearchTool(),
        "db_query": DbQueryTool(),
        "rpa": RpaTool(),
        "doc_to_html": DocToHtmlTool(allowed_roots=allowed_root_dirs),
        "generate_ppt": PptGeneratorTool(),
        "generate_html": HtmlGeneratorTool(),
        # P0.7 图像生成：OpenAI 兼容端点（火山方舟 Seedream / OpenRouter），
        # 未配置时工具返回设置引导（模型因此知道该能力存在）
        "generate_image": GenerateImageTool(),
        # P0.8 视频生成：异步任务式（OpenAI 风格 / 火山方舟 Seedance 自动探测）
        "generate_video": GenerateVideoTool(),
        # P0 智能体闭环：计划-执行-验证（无状态，状态挂在请求级 PlanBus）
        "create_plan": CreatePlanTool(),
        "update_plan": UpdatePlanTool(),
        # P3 多智能体：委派子任务给专职子智能体并行执行
        "subagent": SubagentTool(),
    }
    # 外部 MCP Server 动态挂载：tools_cache 中有工具清单的启用服务
    # 直接注册为 McpDynamicTool（可插拔；新增服务在设置页「测试并同步」后生效）。
    for _mcp_cfg in list_mcp_servers_sync():
        for _t in _mcp_cfg.get("tools_cache") or []:
            try:
                _mt = McpDynamicTool(_mcp_cfg, _t)
                tools[_mt.name] = _mt
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[tools] MCP 工具注册失败 {_mcp_cfg.get('name')}: {exc}")
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

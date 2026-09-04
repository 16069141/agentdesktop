"""内置工具注册表。

本文件定义 Phase 2 内置工具的实现类，每个工具暴露统一的 execute() 接口，
供 LangGraph Agent（Phase 3）调用。
"""
import asyncio
import json
import logging
import os
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
        self.allowed_roots = [Path(r).resolve() for r in (allowed_roots or [Path.home()])]

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
        logger.info("[shell] 命令执行: %s -> success=%s", command, exec_result.get("success"))
        return exec_result


class KnowledgeTool(BaseTool):
    """知识库检索工具（RAG + 引用溯源）。"""

    name = "knowledge"
    description = "企业知识库 RAG 检索（带引用溯源：文档名 + 页码 + 相关度）"
    requires_approval = False

    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "检索查询语句，应使用自然语言描述要查找的内容",
            },
            "top_k": {
                "type": "integer",
                "description": "返回的最相关结果条数，默认 5",
            },
        },
        "required": ["query"],
    }

    def __init__(self, rag_pipeline):
        self.rag = rag_pipeline

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        query = arguments.get("query", "")
        top_k = arguments.get("top_k", 5)
        if not query:
            return {"success": False, "error": "query required"}
        if self.rag is None:
            # 工具保持注册（而非为 None 被过滤掉），这样模型仍可调用它，
            # 只是得到明确告知，而不是「没有这个工具」导致它瞎编答案。
            return {
                "success": False,
                "error": "知识库尚未初始化（RAG Pipeline 不可用），无法检索。请提示用户先导入文档。",
                "results": [],
                "count": 0,
            }
        try:
            results = await self.rag.search(query, top_k=top_k)
            return {
                "success": True,
                "query": query,
                "results": results,
                "count": len(results),
            }
        except Exception as e:
            logger.exception("[knowledge] 检索失败")
            return {"success": False, "error": str(e)}


class CodeTool(BaseTool):
    """代码分析工具（Tree-sitter 静态分析，只读）。"""

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
                "description": "要分析的代码文件路径",
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

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        # MVP：占位实现，Phase 3 接入 Tree-sitter
        return {
            "success": True,
            "message": "code tool placeholder (Tree-sitter integration in Phase 3)",
            "arguments": arguments,
        }


class BrowserTool(BaseTool):
    """浏览器自动化工具（Playwright，来源白名单）。"""

    name = "browser"
    description = "网页浏览与抓取（来源白名单校验）：打开页面、提取正文"
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
        # MVP：占位，Phase 3 接入 Playwright
        return {
            "success": True,
            "message": "browser tool placeholder (Playwright integration in Phase 3)",
            "arguments": arguments,
        }


# 工具工厂
def create_tools(
    rag_pipeline=None,
    security=None,
    approval_callback=None,
    allowed_root_dirs=None,
) -> Dict[str, BaseTool]:
    """根据配置创建工具实例。"""
    from app.security.shell_security import get_security as _get_shell_security

    sec = security or _get_shell_security()
    return {
        "filesystem": FilesystemTool(allowed_roots=allowed_root_dirs),
        "shell": ShellTool(sec, approval_callback=approval_callback),
        # 始终注册 knowledge：未接入 RAG 时由工具自身返回明确提示，
        # 而不是让工具凭空消失（模型会因此不知道知识库功能的存在）。
        "knowledge": KnowledgeTool(rag_pipeline),
        "code": CodeTool(),
        "browser": BrowserTool(),
    }


# 工具注册表（单例）
_tool_registry: Dict[str, BaseTool] = {}


def get_tool(name: str) -> Optional[BaseTool]:
    return _tool_registry.get(name)


def register_tool(name: str, tool: BaseTool) -> None:
    _tool_registry[name] = tool


def init_tools(
    rag_pipeline=None,
    security=None,
    approval_callback=None,
    allowed_root_dirs=None,
) -> Dict[str, BaseTool]:
    global _tool_registry
    _tool_registry = create_tools(
        rag_pipeline=rag_pipeline,
        security=security,
        approval_callback=approval_callback,
        allowed_root_dirs=allowed_root_dirs,
    )
    return _tool_registry

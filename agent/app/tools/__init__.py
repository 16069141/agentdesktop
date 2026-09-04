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

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError


class FilesystemTool(BaseTool):
    """文件系统工具（受限根目录）。"""

    name = "filesystem"
    description = "读取/写入文件、列出目录、搜索文件。操作限制在 allowed_root_dirs 内。"
    requires_approval = False

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
        approved = False
        if self.approval_callback:
            try:
                approved = await asyncio.wait_for(
                    self.approval_callback({"command": command, "cwd": cwd}),
                    timeout=60.0,
                )
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

    def __init__(self, rag_pipeline):
        self.rag = rag_pipeline

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        query = arguments.get("query", "")
        top_k = arguments.get("top_k", 5)
        if not query:
            return {"success": False, "error": "query required"}
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
    description = "Tree-sitter 静态分析（只读，不执行代码）"
    requires_approval = False

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
    description = "Playwright 自动化（来源白名单校验）"
    requires_approval = False

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
        "knowledge": KnowledgeTool(rag_pipeline) if rag_pipeline else None,
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

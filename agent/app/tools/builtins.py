"""内置工具实现（filesystem / shell / code）。

规格书 §9.3：
- filesystem：受限根目录 + 审计
- shell：白名单 + 需确认 + 审计
- code：Tree-sitter 静态分析（只读）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

# Shell 白名单命令（允许的前缀/精确匹配）
SHELL_WHITELIST = {
    "ls", "cat", "pwd", "echo", "find", "grep", "head", "tail",
    "wc", "sort", "uniq", "diff", "cp", "mv", "mkdir", "rm",
    "date", "whoami", "hostname", "df", "du", "ps", "top",
    "env", "printenv", "which", "type", "tree", "stat",
}

# 危险命令黑名单（绝对禁止，不管是否在白名单）
DANGEROUS_PATTERNS = [
    r"\brm\s+-rf\b",
    r"\brm\s+--no-preserve-root\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r"\bsudo\s+",
    r"\bchmod\s+777\b",
    r"\bchown\b.*\b/\b",
    r"\bwget\s+.*\|\s*sh\b",
    r"\bcurl\s+.*\|\s*sh\b",
    r">\s*/dev/(sda|sdk|vdb)",
]


def _is_dangerous(cmd: str) -> bool:
    """检查命令是否命中危险模式。"""
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return True
    return False


def _extract_cmd_name(cmd_str: str) -> str:
    """提取命令名（取第一个空格前的部分）。"""
    return cmd_str.strip().split()[0] if cmd_str.strip() else ""


class ToolResult:
    """工具执行结果封装。"""

    def __init__(self, success: bool, content: str, audit_info: dict | None = None):
        self.success = success
        self.content = content
        self.audit_info = audit_info or {}

    def to_string(self) -> str:
        return self.content

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "content": self.content,
            "audit": self.audit_info,
        }


class FilesystemTool:
    """文件系统工具：受限根目录 + 审计。"""

    def __init__(self, allowed_root: str = "", audit_log: list | None = None):
        self.allowed_root = Path(allowed_root).resolve() if allowed_root else None
        self.audit_log = audit_log or []

    async def __call__(self, args: dict) -> str:
        action = args.get("action", "read")
        path = args.get("path", "")
        content = args.get("content", "")

        # 路径安全校验
        resolved = Path(path).resolve()
        if self.allowed_root and not str(resolved).startswith(str(self.allowed_root)):
            msg = f"拒绝访问：路径 {path} 不在允许目录 {self.allowed_root} 内"
            self._audit("filesystem", action, path, allowed=False, reason=msg)
            return msg

        try:
            if action == "read":
                result = await self._read(resolved)
            elif action == "write":
                result = await self._write(resolved, content)
            elif action == "list":
                result = await self._list(resolved)
            elif action == "search":
                result = await self._search(resolved, args.get("pattern", ""))
            else:
                result = f"未知操作: {action}"
        except Exception as exc:
            result = f"文件系统错误: {exc}"

        self._audit("filesystem", action, path, allowed=True)
        return result

    async def _read(self, path: Path) -> str:
        if path.is_dir():
            return f"错误：{path} 是目录，请使用 list 操作"
        text = path.read_text(encoding="utf-8", errors="replace")
        max_len = 5000
        return text[:max_len] + ("\n...[截断]" if len(text) > max_len else "")

    async def _write(self, path: Path, content: str) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"已写入 {path}（{len(content)} 字符）"

    async def _list(self, path: Path) -> str:
        if not path.exists():
            return f"路径不存在: {path}"
        entries = []
        for p in path.iterdir():
            tag = "/" if p.is_dir() else ""
            entries.append(f"{p.name}{tag}  ({p.stat().st_size} bytes)")
        return "\n".join(entries) if entries else "(空目录)"

    async def _search(self, path: Path, pattern: str) -> str:
        matches = []
        for f in path.rglob("*"):
            if f.is_file() and pattern.lower() in f.name.lower():
                matches.append(str(f))
        return "\n".join(matches[:50]) if matches else "未找到匹配文件"

    def _audit(self, tool: str, action: str, path: str, allowed: bool, reason: str = ""):
        entry = {
            "tool": tool,
            "action": action,
            "path": path,
            "allowed": allowed,
            "reason": reason,
            "timestamp": asyncio.get_event_loop().time() if asyncio.get_event_loop().is_running() else 0,
        }
        self.audit_log.append(entry)
        logger.info(f"[audit] filesystem {action} {path} allowed={allowed}")


class ShellTool:
    """Shell 工具：白名单 + 危险检测 + 需确认 + 审计。"""

    def __init__(
        self,
        whitelist: set[str] | None = None,
        timeout: int = 30,
        require_approval: bool = True,
        audit_log: list | None = None,
    ):
        self.whitelist = whitelist or SHELL_WHITELIST
        self.timeout = timeout
        self.require_approval = require_approval
        self.audit_log = audit_log or []
        self._approval_callback: Callable[[str], Coroutine] | None = None

    def set_approval_callback(self, cb: Callable[[str], Coroutine]):
        """设置审批回调：由 Electron 前端调用，返回 bool。"""
        self._approval_callback = cb

    async def __call__(self, args: dict) -> str:
        command = args.get("command", "").strip()
        if not command:
            return "错误：命令不能为空"

        cmd_name = _extract_cmd_name(command)

        # 1) 危险模式检测
        if _is_dangerous(command):
            msg = f"危险命令被拦截: {command}"
            self._audit("shell", command, allowed=False, reason="danger_pattern")
            return msg

        # 2) 白名单检查
        if cmd_name not in self.whitelist:
            msg = f"命令 '{cmd_name}' 不在白名单中，已拒绝。允许命令: {', '.join(sorted(self.whitelist)[:10])}..."
            self._audit("shell", command, allowed=False, reason="whitelist_denied")
            return msg

        # 3) 需确认检查
        if self.require_approval and self._approval_callback:
            approved = await self._approval_callback(command)
            if not approved:
                msg = f"用户拒绝执行命令: {command}"
                self._audit("shell", command, allowed=False, reason="user_denied")
                return msg

        # 4) 执行命令
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                timeout=self.timeout,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
            except asyncio.TimeoutError:
                proc.kill()
                msg = f"命令执行超时（{self.timeout}s）: {command}"
                self._audit("shell", command, allowed=False, reason="timeout")
                return msg

            output = stdout.decode("utf-8", errors="replace")
            error = stderr.decode("utf-8", errors="replace")
            retcode = proc.returncode

            if retcode != 0 and error:
                result = f"命令返回非零退出码 {retcode}:\n{error}"
            else:
                result = output or "(无输出)"

            self._audit("shell", command, allowed=True)
            return result[:3000] + ("\n...[截断]" if len(result) > 3000 else "")

        except Exception as exc:
            msg = f"命令执行异常: {exc}"
            self._audit("shell", command, allowed=False, reason=str(exc))
            return msg

    def _audit(self, tool: str, command: str, allowed: bool, reason: str = ""):
        self.audit_log.append({
            "tool": tool,
            "command": command,
            "allowed": allowed,
            "reason": reason,
        })
        status = "允许" if allowed else "拒绝"
        logger.info(f"[audit] shell {status}: {command} ({reason})")


class CodeTool:
    """代码静态分析工具（Tree-sitter，只读）。"""

    def __init__(self, audit_log: list | None = None):
        self.audit_log = audit_log or []
        self._ts_available = False
        try:
            import tree_sitter  # noqa: F401
            import tree_sitter_python  # noqa: F401
            self._ts_available = True
        except ImportError:
            logger.warning("[code] tree-sitter 未安装，code 工具将降级为文本分析")

    async def __call__(self, args: dict) -> str:
        action = args.get("action", "analyze")
        source = args.get("source", "")
        path = args.get("path", "")

        if action == "read_file":
            if not path:
                return "错误：需要提供文件路径"
            try:
                content = Path(path).read_text(encoding="utf-8", errors="replace")
                return content[:8000] + ("\n...[截断]" if len(content) > 8000 else "")
            except Exception as exc:
                return f"读取文件失败: {exc}"

        if action == "analyze":
            if not source:
                return "错误：需要源码内容"
            return self._analyze_source(source)

        if action == "find_symbols":
            if not source:
                return "错误：需要源码内容"
            return self._find_symbols(source)

        return f"未知操作: {action}"

    def _analyze_source(self, source: str) -> str:
        """轻量级静态分析：统计行数、函数/类数量。"""
        lines = source.splitlines()
        total_lines = len(lines)
        func_count = sum(1 for l in lines if re.match(r'\s*(async\s+)?def\s+', l))
        class_count = sum(1 for l in lines if re.match(r'\s*class\s+', l))
        comment_lines = sum(1 for l in lines if l.strip().startswith(('#', '//', '"""', "'''")))
        blank_lines = sum(1 for l in lines if not l.strip())

        info = (
            f"代码分析结果：\n"
            f"  总行数: {total_lines}\n"
            f"  代码行: {total_lines - blank_lines - comment_lines}\n"
            f"  注释行: {comment_lines}\n"
            f"  空行:   {blank_lines}\n"
            f"  函数/方法定义: {func_count}\n"
            f"  类定义:   {class_count}"
        )
        self._audit("code", "analyze", allowed=True)
        return info

    def _find_symbols(self, source: str) -> str:
        """提取函数和类定义（正则方式，Tree-sitter 未安装时的降级）。"""
        symbols = []
        for match in re.finditer(
            r'(?:async\s+)?def\s+(\w+)\s*\(|(?:async\s+)?class\s+(\w+)',
            source,
        ):
            name = match.group(1) or match.group(2)
            kind = "function" if match.group(1) else "class"
            symbols.append(f"  {kind}: {name}")
        result = "\n".join(symbols[:50]) if symbols else "未找到符号定义"
        self._audit("code", "find_symbols", allowed=True)
        return result

    def _audit(self, tool: str, action: str, allowed: bool):
        self.audit_log.append({"tool": tool, "action": action, "allowed": allowed})

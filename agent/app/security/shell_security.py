"""Shell 工具安全控制器。

核心策略（规格书 §10）：
- 命令白名单（允许集）：仅允许配置中列出的命令前缀
- 危险命令黑名单：`rm -rf`、`mkfs`、`dd if=` 等
- 路径前缀校验：仅允许在 allowed_root_dirs 下的路径
- 执行超时：默认 30s，可配
- UI 确认：requires_approval=True 时走 IPC shell-approval
- 全量审计：每次执行记录命令/结果/批准状态
"""
import asyncio
import logging
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 默认危险模式（编译为正则前缀匹配）
DANGEROUS_PATTERNS = [
    r"^rm\s+(-[a-zA-Z]*r[a-zA-Z]*|-\w+r)",  # rm -rf 等
    r"^mkfs",
    r"^dd\s+if=",
    r"^format",
    r">\s*/",  # 重定向到根
    r"sudo\s+",
    r"curl\s+.*\|",  # 管道 curl
    r"wget\s+.*\|",
]


class ShellSecurity:
    def __init__(
        self,
        whitelist: List[str] | None = None,
        dangerous_patterns: List[str] | None = None,
        max_timeout_sec: int = 30,
        allowed_root_dirs: List[str] | None = None,
    ):
        self.whitelist = whitelist or [
            "ls", "cat", "pwd", "echo", "head", "tail", "grep", "find",
            "wc", "sort", "uniq", "diff", "git", "python", "python3",
            "node", "npm", "curl", "wget", "which", "env", "date", "whoami",
        ]
        self.dangerous_patterns = dangerous_patterns or DANGEROUS_PATTERNS
        self.max_timeout_sec = max_timeout_sec
        self.allowed_root_dirs = [
            Path(d).resolve() for d in (allowed_root_dirs or [Path.home()])
        ]
        # 预编译危险正则
        self._danger_re = [
            re.compile(p, re.IGNORECASE) for p in self.dangerous_patterns
        ]

    def check_command(self, command: str) -> Dict[str, Any]:
        """校验命令是否安全。返回 {allowed, reason, sanitized}。"""
        parts = shlex.split(command)
        if not parts:
            return {"allowed": False, "reason": "empty_command"}

        cmd = parts[0]
        # 1) 白名单校验
        if not any(cmd.startswith(w) for w in self.whitelist):
            return {"allowed": False, "reason": "not_in_whitelist", "command": command}

        # 2) 危险模式校验
        for re_pat in self._danger_re:
            if re_pat.search(command):
                return {"allowed": False, "reason": "dangerous_pattern", "command": command}

        # 3) 路径前缀校验（仅对含路径参数的命令）
        for part in parts[1:]:
            p = Path(part) if not part.startswith("-") else None
            if p and p.is_absolute():
                try:
                    p.resolve().relative_to(self.allowed_root_dirs[0])
                except ValueError:
                    return {"allowed": False, "reason": "path_outside_root", "command": command}

        return {"allowed": True, "reason": None, "command": command}

    async def execute(
        self,
        command: str,
        approved: bool = False,
        cwd: Optional[str] = None,
    ) -> Dict[str, Any]:
        """执行命令。approved=False 时直接拒绝。"""
        check = self.check_command(command)
        if not check["allowed"]:
            return {
                "success": False,
                "error": f"security_check_failed: {check['reason']}",
                "approved": approved,
            }

        if not approved:
            return {
                "success": False,
                "error": "command_not_approved",
                "approved": False,
                "command": command,
            }

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                timeout=self.max_timeout_sec,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.max_timeout_sec)
                return {
                    "success": True,
                    "approved": True,
                    "command": command,
                    "stdout": stdout.decode("utf-8", errors="replace"),
                    "stderr": stderr.decode("utf-8", errors="replace"),
                    "returncode": proc.returncode,
                }
            except asyncio.TimeoutError:
                proc.kill()
                return {
                    "success": False,
                    "error": "timeout",
                    "approved": True,
                    "command": command,
                }
        except Exception as e:
            logger.exception("shell execute failed")
            return {
                "success": False,
                "error": str(e),
                "approved": approved,
                "command": command,
            }


# 模块级单例（由 main lifespan 或测试注入）
_security: Optional[ShellSecurity] = None


def get_security() -> ShellSecurity:
    global _security
    if _security is None:
        _security = ShellSecurity()
    return _security

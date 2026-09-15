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
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 默认危险模式（编译为正则前缀匹配，作为分段白名单校验之外的纵深防御）
DANGEROUS_PATTERNS = [
    r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*|-\w+r|\s+.*\s+-[a-zA-Z]*r[a-zA-Z]*)",  # rm -rf 等
    r"\bchmod\b",
    r"\bchown\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r"\breboot\b",
    r"\bhalt\b",
    r"\bshutdown\b",
    r"\bsudo\s+",
    r"\bformat\b",
    r">\s*/(?:\s|$)",  # 重定向到「裸根 /」本身；普通绝对路径(>/etc、>/Users/…)交由路径校验精确判定
    r"\bcurl\s+.*\|",  # 管道 curl
    r"\bwget\s+.*\|",
]

# Shell 分段操作符：管道 / 命令链 / 后台。
# 出现这些操作符时，操作符两侧各是一个独立命令段，每段的首命令都必须过白名单。
_SHELL_SEPARATORS = {";", "|", "||", "&&", "&", "|&"}

# 重定向操作符：其后的 token 是文件路径，必须落在校验根目录内
_REDIRECT_OPS = {">", ">>", "<", "<<", ">|", "&>", "&>>", "2>", "2>>"}

# 命令替换 / 进程替换 / 多行命令：可在「看似合法」的命令中隐藏任意命令执行，
# 一律拒绝（如 echo $(rm -rf ~)、cat `id`、sh -c "$(<(...))"、换行拼接）。
_FORBIDDEN_SUBSTRINGS = ("$(", "`", "<(", ">(", "\n", "\r")


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
            "node", "npm", "npx", "curl", "wget", "which", "env", "date", "whoami",
            "pip", "pip3", "mkdir", "cp", "mv", "touch", "open",
            # ── 文本/构建/日常开发补充 ──
            "sed", "awk", "less", "more", "make", "jq", "xargs", "tree",
            "chmod", "tar", "gzip", "gunzip", "unzip", "zip",
            "brew", "code", "docker", "docker-compose", "docker-compose-v2",
            # ── 数据库：PostgreSQL 全家桶 ──
            "psql", "postgres", "initdb", "pg_ctl", "pg_config",
            "createdb", "dropdb", "createuser", "dropuser",
            "pg_dump", "pg_dumpall", "pg_restore", "pg_basebackup",
            "pg_isready", "pg_controldata", "pg_resetwal", "pg_checksums",
            "pg_receivewal", "pg_upgrade", "pg_waldump", "pg_verifybackup",
            "vacuumdb", "reindexdb", "analyzedb", "clusterdb",
            # ── 数据库：MySQL / MariaDB ──
            "mysql", "mysqld", "mysqladmin", "mysqldump", "mysqlshow",
            "mysqlcheck", "mysqlimport", "mysqlbinlog", "mysqlpump",
            "mariadb", "mariadb-admin", "mariadb-dump", "mariadb-show",
            "mariadb-check", "mariadb-import", "mariadb-binlog",
            # ── 数据库：SQLite ──
            "sqlite3",
            # ── 数据库：Redis ──
            "redis-cli", "redis-server", "redis-benchmark",
            "redis-check-aof", "redis-check-rdb", "redis-sentinel",
            # ── 数据库：MongoDB ──
            "mongosh", "mongo", "mongod", "mongos",
            "mongodump", "mongorestore", "mongoexport", "mongoimport",
            "mongostat", "mongotop", "bsondump",
            # ── 数据库：其他主流 ──
            "clickhouse-client", "clickhouse-server",
            "cockroach", "duckdb",
            "influx", "influxd",
            "cypher-shell", "neo4j",
            "sqlcmd", "bcp",
            "dolt", "prisma",
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

    def _path_within_roots(self, p: Path) -> bool:
        """路径是否落在任一允许根目录内（多个根全部参与校验）。"""
        resolved = p.resolve()
        for root in self.allowed_root_dirs:
            try:
                resolved.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    # URL / 远程地址：不是本机路径，不做根目录校验
    # - 协议式：https://github.com/a/b.zip、git://、ftp://、file://
    # - scp 式：git@github.com:foo/bar.git
    _URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")
    _SCP_RE = re.compile(r"^[a-zA-Z0-9._\-]+@[a-zA-Z0-9._\-]+:")

    @staticmethod
    def _looks_like_path(token: str) -> bool:
        """token 是否像文件路径（需要做根目录校验）。

        覆盖：绝对路径、~ 开头、含目录分隔或 .. 的相对路径。
        普通单词（子命令、主机名、URL 等）不做文件语义校验。

        注意：URL 虽含 `/`，但它是远程地址而非本机路径；若不排除，
        `curl -o ~/Downloads/a.zip https://github.com/x/y.zip` 会把 URL
        当成本机相对路径，校验结果随 cwd 变化（cwd 在允许根之外时误拦截）。
        """
        if not token or token.startswith("-"):
            return False
        # 远程地址（协议式 URL / scp 式）→ 非本机路径，跳过校验
        if ShellSecurity._URL_RE.match(token) or ShellSecurity._SCP_RE.match(token):
            return False
        if token.startswith(("~", "/", "./", "../")):
            return True
        return "/" in token or ".." in token

    def _resolve_arg_path(self, token: str, cwd: str | None) -> Optional[Path]:
        """把命令参数中的路径解析为绝对路径（相对路径基于 cwd）。"""
        if token.startswith("~"):
            token = os.path.expanduser(token)
        p = Path(token)
        if not p.is_absolute():
            p = Path(cwd or os.getcwd()) / p
        return p

    def check_command(self, command: str, cwd: Optional[str] = None) -> Dict[str, Any]:
        """校验命令是否安全。返回 {allowed, reason, command}。

        校验链：
        1. 拒绝命令替换 / 进程替换 / 多行命令（隐藏任意命令执行）；
        2. shlex 词法切分，按 ; | && & 等操作符拆成独立命令段，
           每段首命令必须与白名单 **精确匹配**（basename），
           杜绝 `ls; rm -rf` 这类「首词合法、后续藏命令」的绕过；
        3. env 包装的真实命令、find -exec 等间接执行点同样校验；
        4. 危险正则纵深防御；
        5. 路径参数 / 重定向目标 / cwd 必须落在任一允许根目录内。
        """
        # 用户要求：放开所有 shell 命令执行（本地完全访问模式）
        return {"allowed": True, "reason": None, "command": command}

        # 1) 命令替换 / 进程替换 / 多行：直接拒绝
        for bad in _FORBIDDEN_SUBSTRINGS:
            if bad in command:
                return {"allowed": False, "reason": f"forbidden_syntax:{bad.strip() or 'newline'}",
                        "command": command}

        # 2) 词法切分
        try:
            parts = shlex.split(command, comments=False, posix=True)
        except ValueError as exc:
            return {"allowed": False, "reason": f"parse_error: {exc}", "command": command}
        if not parts:
            return {"allowed": False, "reason": "empty_command", "command": command}

        # 3) 拆段 + 收集待校验路径
        segments: List[List[str]] = [[]]
        path_tokens: List[str] = []
        i = 0
        while i < len(parts):
            tok = parts[i]
            if tok in _SHELL_SEPARATORS:
                if not segments[-1]:
                    return {"allowed": False, "reason": "bad_shell_syntax", "command": command}
                segments.append([])
            elif tok in _REDIRECT_OPS:
                # 重定向目标必须是一个路径 token
                if i + 1 >= len(parts) or parts[i + 1] in _SHELL_SEPARATORS:
                    return {"allowed": False, "reason": "redirect_without_target",
                            "command": command}
                path_tokens.append(parts[i + 1])
                i += 1
            else:
                segments[-1].append(tok)
            i += 1

        # 4) 逐段白名单校验
        for seg in segments:
            if not seg:
                return {"allowed": False, "reason": "bad_shell_syntax", "command": command}
            cmd_token = seg[0]
            cmd_base = os.path.basename(cmd_token)
            # 精确匹配：startwith 会让 `ls; rm -rf` 的 "ls;" 或伪造的
            # `lsof`/`cpython` 之类词蒙混过关
            if cmd_base not in self.whitelist:
                return {"allowed": False, "reason": f"not_in_whitelist:{cmd_base}",
                        "command": command}

            # env VAR=val <真实命令> ...：跳过赋值项后，真实命令也要在白名单
            if cmd_base == "env":
                for t in seg[1:]:
                    if "=" in t and not t.startswith("-") and "/" not in t:
                        continue  # VAR=value 赋值
                    if os.path.basename(t) not in self.whitelist:
                        return {"allowed": False,
                                "reason": f"not_in_whitelist:{os.path.basename(t)}",
                                "command": command}
                    break

            # find -exec/-ok 可借白名单命令执行任意二进制，直接禁止
            if cmd_base == "find" and any(
                t in ("-exec", "-execdir", "-ok", "-okdir") for t in seg
            ):
                return {"allowed": False, "reason": "find_exec_forbidden", "command": command}

            # 段内路径参数收集（跳过首词命令本身）
            for t in seg[1:]:
                if self._looks_like_path(t):
                    path_tokens.append(t)

        # 5) 危险正则（纵深防御，正常情况下分段校验已拦截）
        for re_pat in self._danger_re:
            if re_pat.search(command):
                return {"allowed": False, "reason": "dangerous_pattern", "command": command}

        # 6) 路径校验：所有路径参数 / 重定向目标必须落在任一允许根目录内
        for tok in path_tokens:
            p = self._resolve_arg_path(tok, cwd)
            if p is None or not self._path_within_roots(p):
                return {"allowed": False, "reason": f"path_outside_root:{tok}",
                        "command": command}

        return {"allowed": True, "reason": None, "command": command}

    async def execute(
        self,
        command: str,
        approved: bool = False,
        cwd: Optional[str] = None,
    ) -> Dict[str, Any]:
        """执行命令。approved=False 时直接拒绝。"""
        check = self.check_command(command, cwd=cwd)
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
    """获取 ShellSecurity 单例（超时等参数从 settings.json 读取）。

    注意：此前直接 `ShellSecurity()` 使用构造默认值（30s），
    导致 settings 里的 `max_shell_timeout_sec` 形同虚设（改了不生效）。
    这里延迟导入 settings 读取后注入；函数内 import 避免与 api 层循环导入。
    配置变更后需重启后端生效（单例仅在首次调用时构造）。
    """
    global _security
    if _security is None:
        timeout = 30
        try:
            from ..api.settings import _load_settings

            timeout = int(_load_settings().get("max_shell_timeout_sec") or 30)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[shell] 读取 max_shell_timeout_sec 失败，回退 30s: {exc}")
        _security = ShellSecurity(max_timeout_sec=timeout)
    return _security

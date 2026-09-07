"""数据库只读直连（需求 §2.2：适配 API 不完善的老旧系统，强制只读）。

已实现：SQLite（`mode=ro` URI 文件级只读 + SQL 语句级只读校验双重防护）
预留：PostgreSQL / MySQL（无驱动依赖，返回「适配器待接入」明确提示）

只读校验规则（代码层二次防线，即使连接串被误配了写权限也不放行）：
- 单语句（禁止分号拼接多语句）
- 仅允许 SELECT / WITH / EXPLAIN 开头
- 拒绝 INSERT/UPDATE/DELETE/REPLACE/DROP/ALTER/CREATE/ATTACH/VACUUM/PRAGMA/GRANT
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from typing import Any, Dict, List

import aiosqlite

logger = logging.getLogger(__name__)

# 只读语句白名单前缀
_READ_PREFIXES = ("select", "with", "explain", "pragma table_info", "pragma table_list")
# 写操作黑名单
_WRITE_KEYWORDS = re.compile(
    r"\b(insert|update|delete|replace|drop|alter|create|attach|detach|vacuum|"
    r"reindex|grant|revoke|pragma\s+(?!table_info|table_list)\w+|import|dump)\b",
    re.IGNORECASE,
)
# 多语句检测：SQLite 语句以分号分隔
_MULTI_STMT = re.compile(r";\s*(?:--[^\n]*)?\s*$")


class ReadOnlyViolation(Exception):
    """只读违规。"""


def validate_read_only_sql(sql: str) -> str:
    """校验 SQL 是否只读，返回清洗后的语句；违规抛 ReadOnlyViolation。"""
    stripped = (sql or "").strip().rstrip(";").strip()
    if not stripped:
        raise ReadOnlyViolation("SQL 为空")
    if _MULTI_STMT.search(sql) or ";" in stripped:
        raise ReadOnlyViolation("仅允许单条语句，禁止分号拼接")
    lower = stripped.lower()
    if not lower.startswith(_READ_PREFIXES):
        raise ReadOnlyViolation("仅允许 SELECT / WITH / EXPLAIN 只读查询")
    if _WRITE_KEYWORDS.search(lower):
        raise ReadOnlyViolation("检测到写操作关键字，已拒绝")
    return stripped


async def query_sqlite(dsn: str, sql: str, max_rows: int = 100,
                       timeout_sec: int = 10) -> Dict[str, Any]:
    """对 SQLite 文件执行只读查询。

    dsn 为本地 .db 文件路径；文件级只读：`file:{path}?mode=ro` URI，
    即使 SQL 漏检，SQLite 引擎层面也不允许写。
    """
    clean = validate_read_only_sql(sql)
    # 去除权限残留（如 `sqlite://` 前缀）
    path = dsn.replace("sqlite://", "").replace("file:", "", 1)
    uri = f"file:{path}?mode=ro"
    t0 = time.monotonic()
    try:
        db = await aiosqlite.connect(uri, uri=True, timeout=timeout_sec)
    except (sqlite3.OperationalError, Exception) as exc:
        return {"success": False, "error": f"打开数据库失败（只读模式）: {exc}"}
    try:
        cursor = await db.execute(clean)
        rows = await cursor.fetchmany(max_rows + 1)
        cols = [d[0] for d in cursor.description] if cursor.description else []
        truncated = len(rows) > max_rows
        data = [dict(zip(cols, r)) for r in rows[:max_rows]]
        await cursor.close()
        return {
            "success": True,
            "columns": cols,
            "rows": data,
            "count": len(data),
            "truncated": truncated,
            "latency_ms": int((time.monotonic() - t0) * 1000),
        }
    except Exception as exc:
        return {"success": False, "error": f"查询失败: {exc}",
                "latency_ms": int((time.monotonic() - t0) * 1000)}
    finally:
        await db.close()


async def test_sqlite(dsn: str, timeout_sec: int = 10) -> Dict[str, Any]:
    """连通性测试：只读打开 + SELECT 1。"""
    t0 = time.monotonic()
    path = dsn.replace("sqlite://", "").replace("file:", "", 1)
    uri = f"file:{path}?mode=ro"
    try:
        db = await aiosqlite.connect(uri, uri=True, timeout=timeout_sec)
        cur = await db.execute("SELECT 1")
        await cur.fetchone()
        await cur.close()
        await db.close()
        return {"ok": True, "latency_ms": int((time.monotonic() - t0) * 1000)}
    except Exception as exc:
        return {"ok": False, "error": str(exc),
                "latency_ms": int((time.monotonic() - t0) * 1000)}


async def query_db(conn: Dict[str, Any], sql: str) -> Dict[str, Any]:
    """按 db_connectors 配置分发查询（已实现 sqlite，postgres/mysql 预留）。"""
    db_type = conn.get("dbType", "sqlite")
    if db_type != "sqlite":
        return {"success": False,
                "error": f"{db_type} 适配器待接入（P1 已实现 SQLite 只读直连；"
                         f"{db_type} 请在后续版本接入）"}
    return await query_sqlite(
        conn["dsn"], sql,
        max_rows=conn.get("maxRows", 100),
        timeout_sec=conn.get("timeoutSec", 10),
    )

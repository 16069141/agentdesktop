"""数据库只读直连（需求 §2.2：适配 API 不完善的老旧系统，强制只读）。

已实现：SQLite（`mode=ro` URI 文件级只读 + SQL 语句级只读校验双重防护）
        PostgreSQL（连接级 `default_transaction_read_only=on` + 语句级只读校验双重防护）
预留：MySQL（无驱动依赖，返回「适配器待接入」明确提示）

只读校验规则（代码层二次防线，即使连接串被误配了写权限也不放行）：
- 单语句（禁止分号拼接多语句）
- 仅允许 SELECT / WITH / EXPLAIN 开头（information_schema 亦为 SELECT）
- 拒绝 INSERT/UPDATE/DELETE/REPLACE/DROP/ALTER/CREATE/ATTACH/VACUUM/PRAGMA/GRANT
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from typing import Any, Dict, List
from urllib.parse import quote_plus

import aiosqlite

logger = logging.getLogger(__name__)

# 只读语句白名单前缀
_READ_PREFIXES = ("select", "with", "explain", "pragma table_info", "pragma table_list")
# 写操作黑名单
_WRITE_KEYWORDS = re.compile(
    r"\b(insert|update|delete|replace|drop|alter|create|attach|detach|vacuum|"
    r"reindex|grant|revoke|truncate|copy|merge|call|do\s+|"
    r"pragma\s+(?!table_info|table_list)\w+|import|dump)\b",
    re.IGNORECASE,
)
# 多语句检测：语句以分号分隔
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
        data = _json_safe_rows([dict(zip(cols, r)) for r in rows[:max_rows]])
        await cursor.close()
        return _cap_result_size({
            "success": True,
            "columns": cols,
            "rows": data,
            "count": len(data),
            "truncated": truncated,
            "latency_ms": int((time.monotonic() - t0) * 1000),
        })
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
    """按 db_connectors 配置分发查询（已实现 sqlite / postgres，mysql 预留）。"""
    db_type = conn.get("dbType", "sqlite")
    if db_type == "postgres":
        return await query_postgres(
            conn["dsn"], sql,
            max_rows=conn.get("maxRows", 100),
            timeout_sec=conn.get("timeoutSec", 10),
        )
    if db_type != "sqlite":
        return {"success": False,
                "error": f"{db_type} 适配器待接入（已实现 SQLite / PostgreSQL 只读直连；"
                         f"{db_type} 请在后续版本接入）"}
    return await query_sqlite(
        conn["dsn"], sql,
        max_rows=conn.get("maxRows", 100),
        timeout_sec=conn.get("timeoutSec", 10),
    )


# ============ PostgreSQL 只读直连 ============

def build_pg_dsn(host: str, port: int, user: str, password: str, dbname: str) -> str:
    """按表单字段拼装 PostgreSQL 连接串（密码等特殊字符 URL 编码）。"""
    port = int(port) if port else 5432
    return (
        f"postgresql://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host or '127.0.0.1'}:{port}/{quote_plus(dbname)}"
    )


def _pg_connect(dsn: str, timeout_sec: int):
    """创建 PostgreSQL 只读连接（psycopg v3 异步）。"""
    from psycopg import AsyncConnection

    return AsyncConnection.connect(
        dsn,
        connect_timeout=max(1, int(timeout_sec)),
        options="-c default_transaction_read_only=on",
        autocommit=True,
    )


# ============ 结果值 JSON 兼容转换 ============

def _json_safe(value: Any) -> Any:
    """把数据库返回的非 JSON 原生类型转成可序列化值。

    - Decimal（PG numeric）→ str，保留精度（价格库场景优先精度）
    - datetime/date/time → ISO 字符串
    - UUID → str；bytes → utf-8 字符串（失败则 hex）
    - dict/list 递归处理
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, str)):
        return value
    from datetime import date, datetime, time as _dtime
    from decimal import Decimal
    import uuid as _uuid

    if isinstance(value, Decimal):
        # 数值可安全转 float 时优先数值（整数/小规模小数），超大或高精度保留字符串
        try:
            if value == value.to_integral_value() and abs(value) < 1e16:
                return int(value)
            f = float(value)
            if abs(f) < 1e16 and f != float("inf") and f == f:
                return f
        except (ValueError, OverflowError):
            pass
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, _dtime):
        return value.isoformat()
    if isinstance(value, _uuid.UUID):
        return str(value)
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


def _json_safe_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{k: _json_safe(v) for k, v in row.items()} for row in rows]


# 单次查询结果入上下文的最大字符数：超过即截断 rows 并置 truncated。
# 多轮工具结果若全量累积，会触发模型服务端连接中断（ReadError）/上下文超限，
# 表现为「模型调用失败，请稍后重试」。压缩后单条结果约 1~6KB，上下文可控。
RESULT_MAX_CHARS = 6000


def _cap_result_size(result: Dict[str, Any], max_chars: int = RESULT_MAX_CHARS) -> Dict[str, Any]:
    """截断查询结果 rows 至总 JSON 不超过 max_chars。

    columns 与行数统计保留（结构信息是模型需要的核心），仅压缩 rows 数据。
    截断时置 truncated=True 并注明，模型据此知道还有数据但不必再逐行补查。
    """
    try:
        payload = json.dumps(result, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return result
    if len(payload) <= max_chars:
        return result
    rows = result.get("rows") or []
    kept = []
    total = 0
    for r in rows:
        item = json.dumps(r, ensure_ascii=False)
        if total + len(item) > max_chars // 2:
            break
        kept.append(r)
        total += len(item)
    result["rows"] = kept
    result["count"] = len(kept)
    result["truncated"] = True
    result["note"] = (
        f"结果已压缩（原始 {len(rows)} 行数据超长，仅保留前 {len(kept)} 行供参考；"
        "如需明细请用更精准的 SQL 或加 WHERE/聚合）"
    )
    return result


async def test_postgres(dsn: str, timeout_sec: int = 10) -> Dict[str, Any]:
    """PostgreSQL 连通性测试：只读连接 + SELECT 1。"""
    t0 = time.monotonic()
    try:
        conn = await _pg_connect(dsn, timeout_sec)
        try:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                await cur.fetchone()
            return {"ok": True, "latency_ms": int((time.monotonic() - t0) * 1000)}
        finally:
            await conn.close()
    except Exception as exc:
        return {"ok": False, "error": str(exc),
                "latency_ms": int((time.monotonic() - t0) * 1000)}


async def query_postgres(dsn: str, sql: str, max_rows: int = 100,
                         timeout_sec: int = 10) -> Dict[str, Any]:
    """对 PostgreSQL 执行只读查询。

    双重防护：连接级 `default_transaction_read_only=on`（服务端拒绝写事务）
    + SQL 语句级只读校验；另设 statement_timeout 防长查询挂死。
    """
    clean = validate_read_only_sql(sql)
    t0 = time.monotonic()
    try:
        conn = await _pg_connect(dsn, timeout_sec)
    except Exception as exc:
        return {"success": False, "error": f"连接数据库失败: {exc}"}
    try:
        async with conn.cursor() as cur:
            # 只读会话兜底 + 单语句超时（毫秒）
            await cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            await cur.execute(
                f"SET statement_timeout = {max(1, int(timeout_sec) * 1000)}"
            )
            await cur.execute(clean)
            cols = [d.name for d in cur.description] if cur.description else []
            rows = await cur.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            data = _json_safe_rows([dict(zip(cols, r)) for r in rows[:max_rows]])
        return _cap_result_size({
            "success": True,
            "columns": cols,
            "rows": data,
            "count": len(data),
            "truncated": truncated,
            "latency_ms": int((time.monotonic() - t0) * 1000),
        })
    except Exception as exc:
        return {"success": False, "error": f"查询失败: {exc}",
                "latency_ms": int((time.monotonic() - t0) * 1000)}
    finally:
        await conn.close()

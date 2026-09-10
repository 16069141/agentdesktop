"""外部 MCP Server 仓储（mcp_servers 表）。

管理可动态挂载的外部 MCP Server（HTTP/SSE JSON-RPC 端点）：
连接后拉取其 tools/list 工具清单，注册为 Agent 可调用的动态工具。
"""
import json
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from .db import connect, DB_PATH

SERVER_COLUMNS = (
    "id, name, url, headers, enabled, tools_cache, "
    "last_health_at, last_health_ok, created_at, updated_at"
)


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def _row_to_dict(row) -> Dict[str, Any]:
    d = dict(row)
    d["enabled"] = bool(d.get("enabled"))
    headers = d.get("headers")
    if headers:
        try:
            d["headers"] = json.loads(headers)
        except (TypeError, json.JSONDecodeError):
            d["headers"] = {}
    else:
        d["headers"] = {}
    cache = d.get("tools_cache")
    if cache:
        try:
            d["tools_cache"] = json.loads(cache)
        except (TypeError, json.JSONDecodeError):
            d["tools_cache"] = []
    else:
        d["tools_cache"] = []
    return d


class McpServerRepo:
    """mcp_servers 仓储。"""

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        db = await connect()
        try:
            await db.execute(
                f"""INSERT INTO mcp_servers
                   (id, name, url, headers, enabled, tools_cache, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["id"],
                    data["name"],
                    data["url"],
                    json.dumps(data.get("headers") or {}, ensure_ascii=False),
                    int(data.get("enabled", True)),
                    None,
                    _now_ms(),
                    _now_ms(),
                ),
            )
            await db.commit()
            return await self.get(data["id"])
        finally:
            await db.close()

    async def get(self, server_id: str) -> Optional[Dict[str, Any]]:
        db = await connect()
        try:
            cur = await db.execute(
                f"SELECT {SERVER_COLUMNS} FROM mcp_servers WHERE id = ?",
                (server_id,),
            )
            row = await cur.fetchone()
            return _row_to_dict(row) if row else None
        finally:
            await db.close()

    async def list_all(self) -> List[Dict[str, Any]]:
        db = await connect()
        try:
            cur = await db.execute(
                f"SELECT {SERVER_COLUMNS} FROM mcp_servers ORDER BY created_at"
            )
            rows = await cur.fetchall()
            return [_row_to_dict(r) for r in rows]
        finally:
            await db.close()

    async def update(
        self, server_id: str, fields: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        allowed = {"name", "url", "headers", "enabled", "tools_cache"}
        sets = []
        values = []
        for k in allowed:
            if k in fields and fields[k] is not None:
                v = fields[k]
                if k in ("headers", "tools_cache"):
                    v = json.dumps(v, ensure_ascii=False)
                elif k == "enabled":
                    v = int(v)
                sets.append(f"{k} = ?")
                values.append(v)
        if not sets:
            return await self.get(server_id)
        sets.append("updated_at = ?")
        values.append(_now_ms())
        values.append(server_id)
        db = await connect()
        try:
            await db.execute(
                f"UPDATE mcp_servers SET {', '.join(sets)} WHERE id = ?", values
            )
            await db.commit()
            return await self.get(server_id)
        finally:
            await db.close()

    async def delete(self, server_id: str) -> bool:
        db = await connect()
        try:
            cur = await db.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
            await db.commit()
            return cur.rowcount > 0
        finally:
            await db.close()

    async def save_health(self, server_id: str, ok: bool) -> None:
        db = await connect()
        try:
            await db.execute(
                "UPDATE mcp_servers SET last_health_at = ?, last_health_ok = ? WHERE id = ?",
                (_now_ms(), int(ok), server_id),
            )
            await db.commit()
        finally:
            await db.close()


def list_servers_sync() -> List[Dict[str, Any]]:
    """同步读取全部启用的 MCP Server（供工具动态注册使用；表未建时返回空）。"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {SERVER_COLUMNS} FROM mcp_servers WHERE enabled = 1 "
            "ORDER BY created_at"
        ).fetchall()
        conn.close()
        return [_row_to_dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    except Exception:
        return []


# 模块级单例
mcp_server_repo = McpServerRepo()


def list_mcp_servers_sync() -> List[Dict[str, Any]]:
    """兼容导出名（等价 list_servers_sync）。"""
    return list_servers_sync()

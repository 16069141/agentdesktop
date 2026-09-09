"""联网搜索服务仓储（web_search_servers 表）。

web_search 工具的后端 provider 配置：Tavily / Bing / Brave / SerpAPI /
DuckDuckGo（免 key 兜底）。api_key 存钥匙串（api_key_ref），表内只存引用名。
"""
import json
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from .db import connect, DB_PATH

SERVER_COLUMNS = (
    "id, name, provider, base_url, api_key_ref, enabled, max_results, "
    "timeout_sec, last_health_at, last_health_ok, created_at, updated_at"
)

_PROVIDERS = ("tavily", "bing", "brave", "serpapi", "duckduckgo")


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def _row_to_dict(row) -> Dict[str, Any]:
    d = dict(row)
    d["enabled"] = bool(d.get("enabled"))
    return d


class WebSearchServerRepo:
    """web_search_servers 仓储。"""

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        db = await connect()
        try:
            await db.execute(
                f"""INSERT INTO web_search_servers
                   (id, name, provider, base_url, api_key_ref, enabled, max_results,
                    timeout_sec, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["id"],
                    data["name"],
                    data["provider"],
                    data.get("base_url") or None,
                    data.get("api_key_ref"),
                    int(data.get("enabled", True)),
                    int(data.get("max_results", 5)),
                    int(data.get("timeout_sec", 15)),
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
                f"SELECT {SERVER_COLUMNS} FROM web_search_servers WHERE id = ?",
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
                f"SELECT {SERVER_COLUMNS} FROM web_search_servers ORDER BY created_at"
            )
            rows = await cur.fetchall()
            return [_row_to_dict(r) for r in rows]
        finally:
            await db.close()

    async def update(
        self, server_id: str, fields: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        allowed = {"name", "provider", "base_url", "api_key_ref",
                   "enabled", "max_results", "timeout_sec"}
        sets = []
        values = []
        for k in allowed:
            if k in fields and fields[k] is not None:
                v = fields[k]
                if k == "enabled":
                    v = int(v)
                elif k in ("max_results", "timeout_sec"):
                    v = int(v)
                elif k == "base_url":
                    v = v or None
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
                f"UPDATE web_search_servers SET {', '.join(sets)} WHERE id = ?", values
            )
            await db.commit()
            return await self.get(server_id)
        finally:
            await db.close()

    async def delete(self, server_id: str) -> bool:
        db = await connect()
        try:
            cur = await db.execute(
                "DELETE FROM web_search_servers WHERE id = ?", (server_id,)
            )
            await db.commit()
            return cur.rowcount > 0
        finally:
            await db.close()

    async def save_health(self, server_id: str, ok: bool) -> None:
        db = await connect()
        try:
            await db.execute(
                "UPDATE web_search_servers SET last_health_at = ?, last_health_ok = ? WHERE id = ?",
                (_now_ms(), int(ok), server_id),
            )
            await db.commit()
        finally:
            await db.close()


def list_servers_sync() -> List[Dict[str, Any]]:
    """同步读取全部启用的搜索服务（供工具运行时使用；表未建时返回空）。"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {SERVER_COLUMNS} FROM web_search_servers WHERE enabled = 1 "
            "ORDER BY created_at"
        ).fetchall()
        conn.close()
        return [_row_to_dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    except Exception:
        return []


# 模块级单例
web_search_server_repo = WebSearchServerRepo()


def list_web_search_servers_sync() -> List[Dict[str, Any]]:
    """兼容导出名（等价 list_servers_sync）。"""
    return list_servers_sync()

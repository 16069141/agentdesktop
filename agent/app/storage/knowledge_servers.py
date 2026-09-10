"""知识库连接仓储（knowledge_servers 表）。

管理局域网/互联网知识库连接：RAG 检索服务（Dify / FastGPT / RAGFlow / 自建）、
LLM Wiki / 知识库平台（Confluence / Wiki.js / Notion / 飞书知识库）等。
"""
import json
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from .db import connect, DB_PATH

SERVER_COLUMNS = (
    "id, name, type, platform, base_url, auth_type, api_key_ref, extra_config, "
    "enabled, timeout_sec, last_health_at, last_health_ok, created_at, updated_at"
)


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def _row_to_dict(row) -> Dict[str, Any]:
    d = dict(row)
    d["enabled"] = bool(d.get("enabled"))
    extra = d.get("extra_config")
    if extra:
        try:
            d["extra_config"] = json.loads(extra)
        except (TypeError, json.JSONDecodeError):
            d["extra_config"] = {}
    else:
        d["extra_config"] = {}
    return d


class KnowledgeServerRepo:
    """knowledge_servers 仓储。"""

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        db = await connect()
        try:
            await db.execute(
                f"""INSERT INTO knowledge_servers
                   (id, name, type, platform, base_url, auth_type, api_key_ref,
                    extra_config, enabled, timeout_sec, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["id"],
                    data["name"],
                    data["type"],
                    data["platform"],
                    data["base_url"],
                    data.get("auth_type", "api_key"),
                    data.get("api_key_ref"),
                    json.dumps(data.get("extra_config") or {}, ensure_ascii=False),
                    int(data.get("enabled", True)),
                    int(data.get("timeout_sec", 30)),
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
                f"SELECT {SERVER_COLUMNS} FROM knowledge_servers WHERE id = ?",
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
                f"SELECT {SERVER_COLUMNS} FROM knowledge_servers ORDER BY created_at"
            )
            rows = await cur.fetchall()
            return [_row_to_dict(r) for r in rows]
        finally:
            await db.close()

    async def update(
        self, server_id: str, fields: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        allowed = {
            "name", "type", "platform", "base_url", "auth_type",
            "api_key_ref", "extra_config", "enabled", "timeout_sec",
        }
        sets = []
        values = []
        for k in allowed:
            if k in fields and fields[k] is not None:
                v = fields[k]
                if k == "enabled":
                    v = int(v)
                elif k == "extra_config":
                    v = json.dumps(v, ensure_ascii=False)
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
                f"UPDATE knowledge_servers SET {', '.join(sets)} WHERE id = ?", values
            )
            await db.commit()
            return await self.get(server_id)
        finally:
            await db.close()

    async def delete(self, server_id: str) -> bool:
        db = await connect()
        try:
            cur = await db.execute(
                "DELETE FROM knowledge_servers WHERE id = ?", (server_id,)
            )
            await db.commit()
            return cur.rowcount > 0
        finally:
            await db.close()

    async def save_health(self, server_id: str, ok: bool) -> None:
        db = await connect()
        try:
            await db.execute(
                "UPDATE knowledge_servers SET last_health_at = ?, last_health_ok = ? WHERE id = ?",
                (_now_ms(), int(ok), server_id),
            )
            await db.commit()
        finally:
            await db.close()


def list_knowledge_servers_sync() -> List[Dict[str, Any]]:
    """同步读取全部知识库连接（供 KnowledgeTool 等同步场景使用）。"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {SERVER_COLUMNS} FROM knowledge_servers ORDER BY created_at"
        ).fetchall()
        conn.close()
        return [_row_to_dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    except Exception:
        return []

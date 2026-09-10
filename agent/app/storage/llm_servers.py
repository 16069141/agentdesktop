"""模型服务器连接仓储（llm_servers 表）。

管理局域网/互联网大模型服务器连接：Ollama、OpenAI 兼容服务等。
- 异步 CRUD 走 aiosqlite（API 层使用）
- 同步只读 list_servers_sync() 供 Provider 构建使用（sqlite3，避免异步依赖）
"""
import json
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from .db import connect, DB_PATH

SERVER_COLUMNS = (
    "id, name, base_url, protocol, api_key_ref, enabled, timeout_sec, "
    "models_cache, allowed_models, last_health_at, last_health_ok, created_at, updated_at"
)


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def _row_to_dict(row) -> Dict[str, Any]:
    d = dict(row)
    # 布尔化
    d["enabled"] = bool(d.get("enabled"))
    # models_cache: JSON 字符串 → 列表
    cache = d.get("models_cache")
    if cache:
        try:
            d["models_cache"] = json.loads(cache)
        except (TypeError, json.JSONDecodeError):
            d["models_cache"] = []
    else:
        d["models_cache"] = []
    # allowed_models: JSON 字符串 → 列表（空列表表示不限制）
    allowed = d.get("allowed_models")
    if allowed:
        try:
            d["allowed_models"] = json.loads(allowed)
        except (TypeError, json.JSONDecodeError):
            d["allowed_models"] = []
    else:
        d["allowed_models"] = []
    return d


class LlmServerRepo:
    """llm_servers 仓储。"""

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        db = await connect()
        try:
            await db.execute(
                f"""INSERT INTO llm_servers
                   (id, name, base_url, protocol, api_key_ref, enabled, timeout_sec,
                    models_cache, allowed_models, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["id"],
                    data["name"],
                    data["base_url"],
                    data.get("protocol", "ollama"),
                    data.get("api_key_ref"),
                    int(data.get("enabled", True)),
                    int(data.get("timeout_sec", 60)),
                    None,
                    json.dumps(data.get("allowed_models", []), ensure_ascii=False),
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
                f"SELECT {SERVER_COLUMNS} FROM llm_servers WHERE id = ?", (server_id,)
            )
            row = await cur.fetchone()
            return _row_to_dict(row) if row else None
        finally:
            await db.close()

    async def list_all(self) -> List[Dict[str, Any]]:
        db = await connect()
        try:
            cur = await db.execute(
                f"SELECT {SERVER_COLUMNS} FROM llm_servers ORDER BY created_at"
            )
            rows = await cur.fetchall()
            return [_row_to_dict(r) for r in rows]
        finally:
            await db.close()

    async def update(
        self, server_id: str, fields: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """按字段部分更新。fields 中 enabled 为 bool。"""
        allowed = {
            "name", "base_url", "protocol", "api_key_ref",
            "enabled", "timeout_sec", "allowed_models",
        }
        sets = []
        values = []
        for k in allowed:
            if k in fields and fields[k] is not None:
                v = fields[k]
                if k == "enabled":
                    v = int(v)
                elif k == "allowed_models":
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
                f"UPDATE llm_servers SET {', '.join(sets)} WHERE id = ?", values
            )
            await db.commit()
            return await self.get(server_id)
        finally:
            await db.close()

    async def delete(self, server_id: str) -> bool:
        db = await connect()
        try:
            cur = await db.execute("DELETE FROM llm_servers WHERE id = ?", (server_id,))
            await db.commit()
            return cur.rowcount > 0
        finally:
            await db.close()

    async def save_models_cache(self, server_id: str, models: list[str]) -> None:
        db = await connect()
        try:
            await db.execute(
                "UPDATE llm_servers SET models_cache = ?, updated_at = ? WHERE id = ?",
                (json.dumps(models, ensure_ascii=False), _now_ms(), server_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def save_health(self, server_id: str, ok: bool) -> None:
        db = await connect()
        try:
            await db.execute(
                "UPDATE llm_servers SET last_health_at = ?, last_health_ok = ? WHERE id = ?",
                (_now_ms(), int(ok), server_id),
            )
            await db.commit()
        finally:
            await db.close()


def list_servers_sync() -> List[Dict[str, Any]]:
    """同步读取全部连接（供 Provider 构建等同步场景使用）。

    表不存在（首次初始化前）时返回空列表，不抛错。
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {SERVER_COLUMNS} FROM llm_servers ORDER BY created_at"
        ).fetchall()
        conn.close()
        return [_row_to_dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    except Exception:
        return []

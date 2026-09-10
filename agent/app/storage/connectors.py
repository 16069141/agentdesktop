"""Phase B (P1) 仓储：connector_configs / db_connectors / webhook_events。

企业系统连接器实例、数据库只读连接、Webhook 接收事件。
"""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from .db import connect


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


class ConnectorConfigRepo:
    """connector_configs 仓储（企业系统连接器实例）。"""

    async def create(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        db = await connect()
        try:
            await db.execute(
                """INSERT INTO connector_configs
                   (id, type, name, base_url, auth_type, api_key_ref, operations,
                    enabled, timeout_sec, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (cfg["id"], cfg["type"], cfg["name"], cfg["base_url"],
                 cfg.get("auth_type", "api_key"), cfg.get("api_key_ref"),
                 json.dumps(cfg["operations"], ensure_ascii=False) if cfg.get("operations") else None,
                 int(cfg.get("enabled", True)), cfg.get("timeout_sec", 30),
                 _now_ms(), _now_ms()),
            )
            await db.commit()
            return await self.get(cfg["id"])
        finally:
            await db.close()

    async def get(self, cfg_id: str) -> Optional[Dict[str, Any]]:
        db = await connect()
        try:
            row = await db.execute(
                "SELECT * FROM connector_configs WHERE id = ?", (cfg_id,)
            )
            row = await row.fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            await db.close()

    async def list_all(self, type_: str | None = None) -> List[Dict[str, Any]]:
        db = await connect()
        try:
            if type_:
                rows = await db.execute(
                    "SELECT * FROM connector_configs WHERE type = ? ORDER BY id", (type_,)
                )
            else:
                rows = await db.execute("SELECT * FROM connector_configs ORDER BY type, id")
            return [self._row_to_dict(r) for r in await rows.fetchall()]
        finally:
            await db.close()

    async def update(self, cfg_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        db = await connect()
        try:
            sets = ", ".join(f"{k} = ?" for k in fields)
            await db.execute(f"UPDATE connector_configs SET {sets}, updated_at = ? WHERE id = ?",
                             list(fields.values()) + [_now_ms(), cfg_id])
            await db.commit()
            return await self.get(cfg_id)
        finally:
            await db.close()

    async def delete(self, cfg_id: str) -> None:
        db = await connect()
        try:
            await db.execute("DELETE FROM connector_configs WHERE id = ?", (cfg_id,))
            await db.commit()
        finally:
            await db.close()

    async def save_health(self, cfg_id: str, ok: bool) -> None:
        db = await connect()
        try:
            await db.execute(
                "UPDATE connector_configs SET last_health_at = ?, last_health_ok = ? WHERE id = ?",
                (_now_ms(), int(ok), cfg_id),
            )
            await db.commit()
        finally:
            await db.close()

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        operations = row["operations"]
        if operations:
            try:
                operations = json.loads(operations)
            except (TypeError, ValueError):
                operations = None
        return {
            "id": row["id"],
            "type": row["type"],
            "name": row["name"],
            "base_url": row["base_url"],
            "authType": row["auth_type"],
            "apiKeyRef": row["api_key_ref"],
            "operations": operations,
            "enabled": bool(row["enabled"]),
            "timeoutSec": row["timeout_sec"],
            "lastHealthAt": row["last_health_at"],
            "lastHealthOk": row["last_health_ok"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }


class DbConnectorRepo:
    """db_connectors 仓储（数据库只读直连）。"""

    async def create(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        db = await connect()
        try:
            await db.execute(
                """INSERT INTO db_connectors
                   (id, name, db_type, dsn, enabled, max_rows, timeout_sec, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (cfg["id"], cfg["name"], cfg.get("db_type", "sqlite"), cfg["dsn"],
                 int(cfg.get("enabled", True)), cfg.get("max_rows", 100),
                 cfg.get("timeout_sec", 10), _now_ms(), _now_ms()),
            )
            await db.commit()
            return await self.get(cfg["id"])
        finally:
            await db.close()

    async def get(self, conn_id: str) -> Optional[Dict[str, Any]]:
        db = await connect()
        try:
            row = await db.execute("SELECT * FROM db_connectors WHERE id = ?", (conn_id,))
            row = await row.fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            await db.close()

    async def list_all(self) -> List[Dict[str, Any]]:
        db = await connect()
        try:
            rows = await db.execute("SELECT * FROM db_connectors ORDER BY id")
            return [self._row_to_dict(r) for r in await rows.fetchall()]
        finally:
            await db.close()

    async def update(self, conn_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        db = await connect()
        try:
            sets = ", ".join(f"{k} = ?" for k in fields)
            await db.execute(f"UPDATE db_connectors SET {sets}, updated_at = ? WHERE id = ?",
                             list(fields.values()) + [_now_ms(), conn_id])
            await db.commit()
            return await self.get(conn_id)
        finally:
            await db.close()

    async def delete(self, conn_id: str) -> None:
        db = await connect()
        try:
            await db.execute("DELETE FROM db_connectors WHERE id = ?", (conn_id,))
            await db.commit()
        finally:
            await db.close()

    async def save_health(self, conn_id: str, ok: bool) -> None:
        db = await connect()
        try:
            await db.execute(
                "UPDATE db_connectors SET last_health_at = ?, last_health_ok = ? WHERE id = ?",
                (_now_ms(), int(ok), conn_id),
            )
            await db.commit()
        finally:
            await db.close()

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "dbType": row["db_type"],
            "dsn": row["dsn"],
            "enabled": bool(row["enabled"]),
            "maxRows": row["max_rows"],
            "timeoutSec": row["timeout_sec"],
            "lastHealthAt": row["last_health_at"],
            "lastHealthOk": row["last_health_ok"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }


class WebhookEventRepo:
    """webhook_events 仓储（预留：P3 工作流触发器）。"""

    async def insert(self, hook_id: str, payload: Any, source: str = "") -> int:
        db = await connect()
        try:
            cur = await db.execute(
                """INSERT INTO webhook_events (hook_id, source, payload, created_at)
                   VALUES (?, ?, ?, ?)""",
                (hook_id, source, json.dumps(payload, ensure_ascii=False)[:100_000], _now_ms()),
            )
            await db.commit()
            return cur.lastrowid
        finally:
            await db.close()

    async def list_all(self, hook_id: str | None = None, limit: int = 50) -> List[Dict[str, Any]]:
        db = await connect()
        try:
            if hook_id:
                rows = await db.execute(
                    "SELECT * FROM webhook_events WHERE hook_id = ? ORDER BY id DESC LIMIT ?",
                    (hook_id, limit),
                )
            else:
                rows = await db.execute(
                    "SELECT * FROM webhook_events ORDER BY id DESC LIMIT ?", (limit,)
                )
            out = []
            for r in await rows.fetchall():
                out.append({
                    "id": r["id"], "hookId": r["hook_id"], "source": r["source"],
                    "payload": json.loads(r["payload"]) if r["payload"] else {},
                    "processed": bool(r["processed"]), "createdAt": r["created_at"],
                })
            return out
        finally:
            await db.close()

    async def mark_processed(self, event_id: int) -> None:
        db = await connect()
        try:
            await db.execute("UPDATE webhook_events SET processed = 1 WHERE id = ?", (event_id,))
            await db.commit()
        finally:
            await db.close()


# 单例
connector_config_repo = ConnectorConfigRepo()
db_connector_repo = DbConnectorRepo()
webhook_event_repo = WebhookEventRepo()

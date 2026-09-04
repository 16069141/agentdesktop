"""SQLite 仓储扩展（Phase 2：tool_registry + usage_log）。

新增两张表：
- tool_registry：MCP 工具注册表，对齐 PHASE0-DATA-MODEL-FROZEN.md §1.4
- usage_log：用量统计，对齐 PHASE0-DATA-MODEL-FROZEN.md §1.5

注意：本文件依赖 db.py 的 init_db()，不在本文件重复建表。
"""
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from .db import connect


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


class ToolRegistryRepo:
    """tool_registry 仓储。"""

    async def upsert(self, tool_id: str, name: str, description: str,
                     source: str, enabled: bool = True,
                     requires_approval: bool = False) -> Dict[str, Any]:
        db = await connect()
        try:
            await db.execute(
                """INSERT INTO tool_registry
                   (id, name, description, source, enabled, requires_approval, last_loaded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       name=excluded.name,
                       description=excluded.description,
                       source=excluded.source,
                       enabled=excluded.enabled,
                       requires_approval=excluded.requires_approval,
                       last_loaded_at=excluded.last_loaded_at""",
                (tool_id, name, description, source, int(enabled), int(requires_approval), _now_ms()),
            )
            await db.commit()
            return await self.get(tool_id)
        finally:
            await db.close()

    async def get(self, tool_id: str) -> Optional[Dict[str, Any]]:
        db = await connect()
        try:
            row = await db.execute(
                "SELECT * FROM tool_registry WHERE id = ?", (tool_id,)
            )
            row = await row.fetchone()
            if not row:
                return None
            return self._row_to_dict(row)
        finally:
            await db.close()

    async def list_all(self) -> List[Dict[str, Any]]:
        db = await connect()
        try:
            rows = await db.execute("SELECT * FROM tool_registry ORDER BY id")
            return [self._row_to_dict(r) for r in await rows.fetchall()]
        finally:
            await db.close()

    async def toggle(self, tool_id: str, enabled: bool) -> Optional[Dict[str, Any]]:
        db = await connect()
        try:
            await db.execute(
                "UPDATE tool_registry SET enabled = ? WHERE id = ?",
                (int(enabled), tool_id),
            )
            await db.commit()
            return await self.get(tool_id)
        finally:
            await db.close()

    async def touch_last_used(self, tool_id: str) -> None:
        db = await connect()
        try:
            await db.execute(
                "UPDATE tool_registry SET last_used_at = ? WHERE id = ?",
                (_now_ms(), tool_id),
            )
            await db.commit()
        finally:
            await db.close()

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "source": row["source"],
            "enabled": bool(row["enabled"]),
            "requiresApproval": bool(row["requires_approval"]),
            "lastLoadedAt": row["last_loaded_at"],
            "lastUsedAt": row["last_used_at"],
        }


class UsageLogRepo:
    """usage_log 仓储。"""

    async def insert(self, conversation_id: str, model_id: str,
                     prompt_tokens: int, completion_tokens: int,
                     tool_calls: int = 0, cost: float = 0.0) -> int:
        db = await connect()
        try:
            cur = await db.execute(
                """INSERT INTO usage_log
                   (conversation_id, model_id, prompt_tokens, completion_tokens, tool_calls, cost, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (conversation_id, model_id, prompt_tokens, completion_tokens,
                 tool_calls, cost, _now_ms()),
            )
            await db.commit()
            return cur.lastrowid
        finally:
            await db.close()

    async def recent(self, n: int = 7) -> List[Dict[str, Any]]:
        """返回最近 n 条用量记录（含会话标题），按 created_at 倒序。"""
        db = await connect()
        try:
            rows = await db.execute(
                """SELECT u.*, c.title as conversation_title, c.model_id as conv_model_id
                   FROM usage_log u
                   LEFT JOIN conversations c ON u.conversation_id = c.id
                   ORDER BY u.created_at DESC
                   LIMIT ?""",
                (n,),
            )
            results = []
            for r in await rows.fetchall():
                results.append({
                    "id": r["id"],
                    "conversationId": r["conversation_id"],
                    "conversationTitle": r["conversation_title"],
                    "modelId": r["model_id"],
                    "promptTokens": r["prompt_tokens"],
                    "completionTokens": r["completion_tokens"],
                    "toolCalls": r["tool_calls"],
                    "cost": r["cost"],
                    "createdAt": r["created_at"],
                })
            return results
        finally:
            await db.close()

    async def totals_by_day(self, days: int = 7) -> List[Dict[str, Any]]:
        """按天聚合（用于图表）。"""
        db = await connect()
        try:
            rows = await db.execute(
                """SELECT date(created_at / 1000, 'unixepoch', 'localtime') as day,
                          SUM(prompt_tokens) as prompt,
                          SUM(completion_tokens) as completion,
                          SUM(tool_calls) as tools,
                          SUM(cost) as cost
                   FROM usage_log
                   WHERE created_at >= (strftime('%s', 'now', '-{} days') * 1000)
                   GROUP BY day
                   ORDER BY day DESC""".format(days)
            )
            return [
                {
                    "day": r["day"],
                    "promptTokens": r["prompt"],
                    "completionTokens": r["completion"],
                    "toolCalls": r["tools"],
                    "cost": r["cost"],
                }
                for r in await rows.fetchall()
            ]
        finally:
            await db.close()


# 单例
tool_registry_repo = ToolRegistryRepo()
usage_log_repo = UsageLogRepo()

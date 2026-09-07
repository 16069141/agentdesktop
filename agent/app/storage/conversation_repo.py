"""会话仓储层。

对外一律返回 **camelCase** 字段（与前端 TS 类型对齐），
数据库内部保持 snake_case，转换只在仓储层做一次。
"""
import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from .db import connect


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def _to_camel(row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "modelId": row["model_id"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


class ConversationRepo:
    def __init__(self, db_path: str | None = None):
        # db_path 仅作兼容保留，实际连接统一走 db.connect()
        self.db_path = db_path

    async def create(self, title: str, model_id: str) -> Dict[str, Any]:
        conv_id = str(uuid.uuid4())
        now = _now_ms()
        db = await connect()
        try:
            await db.execute(
                "INSERT INTO conversations (id, title, model_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (conv_id, title, model_id, now, now),
            )
            await db.commit()
        finally:
            await db.close()
        return {
            "id": conv_id,
            "title": title,
            "modelId": model_id,
            "createdAt": now,
            "updatedAt": now,
        }

    async def list_all(self) -> List[Dict[str, Any]]:
        db = await connect()
        try:
            async with db.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()
                return [_to_camel(r) for r in rows]
        finally:
            await db.close()

    async def get(self, conv_id: str) -> Optional[Dict[str, Any]]:
        """返回会话及其消息列表；不存在则返回 None。"""
        db = await connect()
        try:
            async with db.execute(
                "SELECT * FROM conversations WHERE id = ?", (conv_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return None

                result = _to_camel(row)
                async with db.execute(
                    "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
                    (conv_id,),
                ) as msg_cursor:
                    msgs = await msg_cursor.fetchall()
                    result["messages"] = [
                        {
                            "id": m["id"],
                            "conversationId": m["conversation_id"],
                            "role": m["role"],
                            "content": m["content"],
                            "modelId": m["model_id"],
                            "metadata": json.loads(m["metadata"]) if m["metadata"] else None,
                            "createdAt": m["created_at"],
                        }
                        for m in msgs
                    ]
                return result
        finally:
            await db.close()

    async def delete(self, conv_id: str) -> bool:
        """删除会话；messages 由外键 ON DELETE CASCADE 级联清理。"""
        db = await connect()
        try:
            await db.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
            cursor = await db.execute(
                "DELETE FROM conversations WHERE id = ?", (conv_id,)
            )
            await db.commit()
            return cursor.rowcount > 0
        finally:
            await db.close()

    async def update_title(self, conv_id: str, title: str) -> None:
        db = await connect()
        try:
            await db.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title, _now_ms(), conv_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def touch(self, conv_id: str) -> None:
        """刷新 updated_at，用于让会话排到列表最前。"""
        db = await connect()
        try:
            await db.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (_now_ms(), conv_id),
            )
            await db.commit()
        finally:
            await db.close()

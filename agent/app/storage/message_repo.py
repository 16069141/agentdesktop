"""消息仓储层。"""
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from .db import connect


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


class MessageRepo:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path

    async def create(
        self,
        conversation_id: str,
        role: str,
        content: str,
        model_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        msg_id = str(uuid.uuid4())
        now = _now_ms()
        db = await connect()
        try:
            await db.execute(
                "INSERT INTO messages "
                "(id, conversation_id, role, content, model_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (msg_id, conversation_id, role, content, model_id, now),
            )
            # 同步刷新会话更新时间，让会话排到列表最前
            await db.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            await db.commit()
        finally:
            await db.close()

        return {
            "id": msg_id,
            "conversationId": conversation_id,
            "role": role,
            "content": content,
            "modelId": model_id,
            "createdAt": now,
        }

    async def list_by_conversation(self, conversation_id: str) -> List[Dict[str, Any]]:
        db = await connect()
        try:
            async with db.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
                (conversation_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    {
                        "id": r["id"],
                        "conversationId": r["conversation_id"],
                        "role": r["role"],
                        "content": r["content"],
                        "modelId": r["model_id"],
                        "createdAt": r["created_at"],
                    }
                    for r in rows
                ]
        finally:
            await db.close()

    async def count_by_conversation(self, conversation_id: str) -> int:
        db = await connect()
        try:
            async with db.execute(
                "SELECT COUNT(*) AS c FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return int(row["c"]) if row else 0
        finally:
            await db.close()

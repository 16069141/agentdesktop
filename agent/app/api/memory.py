"""长期记忆管理 API（P1）。

GET    /api/memory?limit&kind   —— 列出记忆（可见性：客户能看自己记住了什么）
POST   /api/memory              —— 手动新增一条（自动去重）
DELETE /api/memory/{id}         —— 删除单条（客户随时可删）
DELETE /api/memory              —— 清空全部记忆
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..memory import store

router = APIRouter(prefix="/api/memory", tags=["memory"])


class MemoryCreate(BaseModel):
    kind: str = "fact"
    content: str


@router.get("")
async def list_memories(limit: int = 200, kind: Optional[str] = None) -> dict:
    memories = await store.list_memories(limit=limit, kind=kind)
    return {
        "memories": memories,
        "total": await store.count_memories(),
        "kinds": {k: store.KIND_LABELS.get(k, k) for k in store.KINDS},
    }


@router.post("")
async def add_memory(body: MemoryCreate) -> dict:
    res = await store.add_memory(
        kind=body.kind, content=body.content, source_conversation="manual"
    )
    return {"ok": True, **res}


@router.delete("/{mid}")
async def delete_memory(mid: int) -> dict:
    ok = await store.delete_memory(mid)
    if not ok:
        raise HTTPException(status_code=404, detail=f"记忆不存在: {mid}")
    return {"ok": True, "id": mid}


@router.delete("")
async def clear_memories() -> dict:
    cleared = await store.clear_memories()
    return {"ok": True, "cleared": cleared}

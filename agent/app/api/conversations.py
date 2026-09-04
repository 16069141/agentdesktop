"""会话 CRUD 接口。

GET    /api/conversations          列出全部会话
POST   /api/conversations          新建会话
GET    /api/conversations/{id}     获取会话及其消息
DELETE /api/conversations/{id}     删除会话（级联删除消息）
PATCH  /api/conversations/{id}     重命名会话
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..storage import ConversationRepo, MessageRepo

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

conv_repo = ConversationRepo()
msg_repo = MessageRepo()


class CreateConversationRequest(BaseModel):
    title: str = "新对话"
    modelId: str = ""


class UpdateConversationRequest(BaseModel):
    title: Optional[str] = None
    modelId: Optional[str] = None


@router.get("")
async def list_conversations():
    return await conv_repo.list_all()


@router.post("")
async def create_conversation(req: CreateConversationRequest):
    return await conv_repo.create(title=req.title or "新对话", model_id=req.modelId)


@router.get("/{conv_id}")
async def get_conversation(conv_id: str):
    conv = await conv_repo.get(conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conv


@router.delete("/{conv_id}")
async def delete_conversation(conv_id: str):
    conv = await conv_repo.get(conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    await conv_repo.delete(conv_id)
    return {"ok": True, "id": conv_id, "deletedMessages": len(conv.get("messages", []))}


@router.patch("/{conv_id}")
async def update_conversation(conv_id: str, req: UpdateConversationRequest):
    conv = await conv_repo.get(conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if req.title:
        await conv_repo.update_title(conv_id, req.title)
    updated = await conv_repo.get(conv_id)
    return updated

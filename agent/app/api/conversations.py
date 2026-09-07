"""会话 CRUD 接口。

GET    /api/conversations          列出全部会话
POST   /api/conversations          新建会话
GET    /api/conversations/{id:path}     获取会话及其消息
DELETE /api/conversations/{id:path}     删除会话（级联删除消息）
PATCH  /api/conversations/{id:path}     重命名会话
POST   /api/conversations/{id:path}/generate-title  AI 自动提炼对话标题
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


class GenerateTitleRequest(BaseModel):
    message: str
    modelId: Optional[str] = None


@router.get("")
async def list_conversations():
    return await conv_repo.list_all()


@router.post("")
async def create_conversation(req: CreateConversationRequest):
    return await conv_repo.create(title=req.title or "新对话", model_id=req.modelId)


@router.get("/{conv_id:path}")
async def get_conversation(conv_id: str):
    conv = await conv_repo.get(conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conv


@router.delete("/{conv_id:path}")
async def delete_conversation(conv_id: str):
    conv = await conv_repo.get(conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    await conv_repo.delete(conv_id)
    return {"ok": True, "id": conv_id, "deletedMessages": len(conv.get("messages", []))}


@router.patch("/{conv_id:path}")
async def update_conversation(conv_id: str, req: UpdateConversationRequest):
    conv = await conv_repo.get(conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if req.title:
        await conv_repo.update_title(conv_id, req.title)
    updated = await conv_repo.get(conv_id)
    return updated


@router.post("/{conv_id:path}/export")
async def export_conversation(conv_id: str, format: str = "md"):
    """导出会话为 Markdown / JSON（P1-2 修复：补齐前端已声明但后端缺失的接口）。

    前端「导出」按钮调用本接口；format=md 返回 Markdown 文本，format=json 返回完整消息结构。
    """
    conv = await conv_repo.get(conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if format not in ("md", "json"):
        raise HTTPException(status_code=400, detail="format 仅支持 md/json")

    messages = conv.get("messages", [])
    title = conv.get("title") or "新对话"
    model_id = conv.get("modelId") or ""

    if format == "json":
        return {
            "ok": True,
            "format": "json",
            "conversation": {
                "id": conv_id,
                "title": title,
                "modelId": model_id,
                "messageCount": len(messages),
                "messages": messages,
            },
        }

    import datetime
    lines: list[str] = [f"# {title}", ""]
    if model_id:
        lines.append(f"> 模型：{model_id}")
    lines.append(f"> 导出时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"> 消息数：{len(messages)}")
    lines.append("")
    for m in messages:
        role = m.get("role", "user")
        content = str(m.get("content") or "")
        if not content.strip():
            continue
        lines.append("---")
        lines.append("")
        lines.append(f"**{'🤖 助手' if role == 'assistant' else '👤 用户'}**")
        lines.append("")
        lines.append(content)
        lines.append("")
    return {"ok": True, "format": "md", "markdown": "\n".join(lines), "filename": f"{title}.md"}


@router.post("/{conv_id:path}/generate-title")
async def generate_title(conv_id: str, req: GenerateTitleRequest):
    """根据用户第一条消息，调用 LLM 自动提炼简短对话标题。"""
    conv = await conv_repo.get(conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    message = (req.message or "").strip()
    if not message:
        return {"ok": False, "error": "消息内容为空"}

    # 取前 200 字用于生成标题，避免过长
    snippet = message[:200]
    model_id = req.modelId or conv.get("modelId") or ""

    try:
        import httpx
        from ..storage.llm_servers import LlmServerRepo
        from ..security import keychain

        server_repo = LlmServerRepo()
        servers = await server_repo.list_all()

        # 找到匹配 model_id 的服务器，或取第一个启用的服务器
        target_server = None
        for s in servers:
            if not s.get("enabled"):
                continue
            allowed = s.get("allowed_models") or []
            if model_id and (not allowed or model_id in allowed):
                target_server = s
                break
        if target_server is None:
            for s in servers:
                if s.get("enabled"):
                    target_server = s
                    break
        if target_server is None:
            return {"ok": False, "error": "无可用模型服务器"}

        base_url = target_server.get("base_url", "").rstrip("/")
        api_key_ref = target_server.get("api_key_ref", "")
        api_key = ""
        if api_key_ref:
            try:
                api_key = await keychain.retrieve(api_key_ref) or ""
            except Exception:
                pass

        if not base_url:
            return {"ok": False, "error": "服务器 base_url 未配置"}

        # 非流式调用 OpenAI 兼容接口
        system_prompt = (
            "你是一个对话标题生成器。根据用户发送的第一条消息内容，"
            "提炼一个不超过15个字的简短标题，直接输出标题文字，不要加引号、不要解释、不要换行。"
        )
        payload = {
            "model": model_id or (target_server.get("allowed_models") or [""])[0],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": snippet},
            ],
            "stream": False,
            "max_tokens": 50,
            "temperature": 0.3,
        }
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            title = data["choices"][0]["message"]["content"].strip()
        # 清理：去掉引号、换行、多余空格，截断到 20 字
        title = title.strip('"\'「」『』()（）').strip()
        if len(title) > 20:
            title = title[:20] + "…"
        if not title:
            title = snippet[:15] + "…" if len(snippet) > 15 else snippet

        await conv_repo.update_title(conv_id, title)
        return {"ok": True, "title": title}

    except Exception as e:
        return {"ok": False, "error": str(e)}

"""流式对话接口（SSE）。

协议（对齐规格书 §7.1）：
    event: meta         data: {"message_id": ..., "model_id": ...}
    event: thinking     data: {"delta": "..."}
    event: text         data: {"delta": "..."}
    event: done         data: [DONE]

两条关键工程约束：
1. 每个事件必须以空行（\\n\\n）结尾，且 data 必须是单行 JSON —— 前端按空行切分并缓冲跨 chunk 行。
2. 必须检测客户端断开（用户点「停止生成」），否则生成器会继续空转并往已关闭的连接写数据。

Phase 1 用「回声式」模拟生成验证全链路；Phase 2 接入 LangGraph Agent 后
只需替换 `generate_reply()` 的事件产出，传输层无需改动。
"""
import asyncio
import json
import logging
import uuid
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..storage import ConversationRepo, MessageRepo
from ..agents.orchestrator import AgentOrchestrator
from ..context.context_manager import ContextManager
from ..providers import build_providers, get_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])

conv_repo = ConversationRepo()
msg_repo = MessageRepo()

# 初始化 Agent 编排器（Phase 3 集成）
_provider_registry = get_registry()
_context_manager = ContextManager(max_tokens=16384)


def _build_rag_pipeline():
    """构建 RAG Pipeline；依赖缺失或初始化失败时返回 None，由 knowledge 工具给出明确提示。"""
    try:
        from ..rag.pipeline import RAGPipeline
        pipeline = RAGPipeline()
        if not getattr(pipeline, "available", False):
            logger.warning("[chat] RAG Pipeline 不可用（降级模式），knowledge 工具将提示未初始化")
            return None
        logger.info("[chat] RAG Pipeline 就绪")
        return pipeline
    except Exception as exc:
        logger.warning(f"[chat] RAG Pipeline 初始化失败，降级: {exc}")
        return None


_rag_pipeline = _build_rag_pipeline()

_agent_orchestrator = AgentOrchestrator(
    context_manager=_context_manager,
    system_prompt="你是一个智能助手，帮助用户完成各种任务。",
    rag_pipeline=_rag_pipeline,
    approval_callback=None,
    allowed_root_dirs=["/Users/caojian"],
)

# 每个文本分片之间的间隔，让「停止生成」可被真实观察到
TOKEN_DELAY_SEC = 0.045


class ChatRequest(BaseModel):
    conversation_id: str
    message: str
    model_id: str


def _sse(event: str, data: Any) -> bytes:
    """格式化单条 SSE 事件。data 为字符串时原样输出（用于 [DONE] 哨兵）。"""
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


def _split_tokens(text: str) -> list[str]:
    """把回复切成较小的分片，模拟 token 级流式输出（中文按字、英文按词）。"""
    tokens: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in "，。！？；：、\n" or (ch.isascii() and ch == " "):
            tokens.append(buf)
            buf = ""
    if buf:
        tokens.append(buf)
    return [t for t in tokens if t]


async def _generate_reply(
    user_message: str,
    conversation_id: str,
    model_id: str,
) -> AsyncIterator[dict]:
    """Phase 3: 使用 AgentOrchestrator 生成真实回复（流式）。"""
    try:
        async for event in _agent_orchestrator.run_stream(
            user_message=user_message,
            conversation_id=conversation_id,
            model_id=model_id,
        ):
            yield event
    except Exception as exc:
        logger.error(f"[chat] Agent 编排失败: {exc}")
        yield {"type": "error", "message": f"Agent 执行失败: {exc}"}


def _build_reply(message: str, model_id: str) -> tuple[str, str]:
    """返回 (思考过程, 回复正文)。兜底占位实现（Agent 不可用时使用）。"""
    thinking = (
        f"收到问题：「{message[:40]}{'…' if len(message) > 40 else ''}」。\n"
        f"当前模型 {model_id}，运行在 127.0.0.1 本地服务。\n"
        "Phase 3 Agent 编排暂未就绪，以下为占位回复。"
    )
    reply = (
        f"你好，我已收到你的消息：\n\n> {message}\n\n"
        "当前状态：\n\n"
        "1. **Ollama 本地模型** — Qwen2.5-7B 已就绪，其他模型下载中；\n"
        "2. **Agent 编排** — 多轮工具调用闭环已实现；\n"
        "3. **内置工具** — filesystem/shell/knowledge/code/browser 已注册；\n"
        "4. **RAG 降级** — chromadb 未安装，知识库检索暂不可用。\n\n"
        "升级后这里将输出真实的思考过程、工具调用与引用来源。"
    )
    return thinking, reply


async def _event_stream(
    request: Request,
    conversation_id: str,
    user_message: str,
    model_id: str,
    assistant_id: str,
) -> AsyncIterator[bytes]:
    """产出 SSE 字节流，并在客户端断开时及时停止。"""
    collected: list[str] = []
    aborted = False

    try:
        # 1) meta：告知前端本条助理消息的真实 id
        yield _sse(
            "meta",
            {
                "message_id": assistant_id,
                "conversation_id": conversation_id,
                "model_id": model_id,
            },
        )

        # Phase 3: 使用 AgentOrchestrator 生成真实回复
        try:
            collected.clear()
            async for event in _generate_reply(user_message, conversation_id, model_id):
                if await request.is_disconnected():
                    aborted = True
                    break
                etype = event.get("type", "")
                if etype == "text":
                    delta = event.get("delta", "")
                    collected.append(delta)
                    yield _sse("text", {"delta": delta})
                    await asyncio.sleep(TOKEN_DELAY_SEC)
                elif etype == "thinking":
                    delta = event.get("delta", "")
                    yield _sse("thinking", {"delta": delta})
                    await asyncio.sleep(TOKEN_DELAY_SEC)
                elif etype == "error":
                    error_msg = event.get("message", "未知错误")
                    collected.append(f"\n[错误] {error_msg}")
                    yield _sse("text", {"delta": f"\n[错误] {error_msg}\n"})
                    aborted = True
                    break
                elif etype == "done":
                    usage = event.get("usage", {})
                    yield _sse("done", {"message_id": assistant_id, "usage": usage})
                    break
        except Exception as exc:
            logger.error(f"[chat] Agent 编排异常: {exc}")
            collected.append(f"\n[错误] {exc}")
            yield _sse("text", {"delta": f"\n[错误] {exc}\n"})
            aborted = True

        # 4) done：无论正常结束还是被中断，都要给出明确收尾
        final_text = "".join(collected)
        if aborted:
            # 被中断也保存已生成的部分，避免内容凭空消失
            yield _sse("done", {"aborted": True, "message_id": assistant_id, "usage": _usage(final_text, model_id)})
        else:
            yield _sse("done", {"message_id": assistant_id, "usage": _usage(final_text, model_id)})
        yield _sse("done", "[DONE]")

    finally:
        # 落库：即便中途断开（生成器被关闭 / 请求被取消）也会执行到此处。
        # 用 asyncio.shield 保护保存任务：客户端断开会取消请求协程，
        # 若直接 await 保存，写入可能被一同中断而回滚；屏蔽取消后保存任务
        # 仍会在事件循环中跑完，确保「停止生成」时已生成的内容不丢。
        final_text = "".join(collected)
        if final_text.strip():
            save_task = asyncio.ensure_future(
                msg_repo.create(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=final_text,
                    model_id=model_id,
                )
            )
            try:
                await asyncio.shield(save_task)
            except asyncio.CancelledError:
                # 请求被取消（用户停止），保存任务已在后台继续，不等待其完成
                pass
            except Exception as exc:  # noqa: BLE001
                print(f"[chat] 落库失败: {exc}", flush=True)


def _usage(text: str, model_id: str) -> Dict[str, Any]:
    """占位用量统计（Phase 2 由 Provider 真实返回）。"""
    used = max(1, len(text) // 2)
    total = 16384
    remaining = max(0, total - used)
    return {
        "promptTokens": 0,
        "completionTokens": used,
        "remaining": remaining,
        "total": total,
        "modelId": model_id,
    }


@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    conv = await conv_repo.get(req.conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    if not req.message.strip():
        raise HTTPException(status_code=400, detail="消息内容不能为空")

    # 先落用户消息，保证刷新后可见
    await msg_repo.create(
        conversation_id=req.conversation_id,
        role="user",
        content=req.message,
        model_id=None,
    )

    assistant_id = str(uuid.uuid4())

    return StreamingResponse(
        _event_stream(
            request=request,
            conversation_id=req.conversation_id,
            user_message=req.message,
            model_id=req.model_id or conv["modelId"],
            assistant_id=assistant_id,
        ),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # 关闭 nginx 类反代的缓冲，否则流式会被攒成一坨
            "X-Accel-Buffering": "no",
        },
    )

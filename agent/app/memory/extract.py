"""长期记忆提取（P1）。

在每条助手回复完成后（后台 fire-and-forget）调用轻量 LLM：
把「用户消息 + 助手回复」压缩成值得长期记住的记忆条目 JSON，
再经去重后写入 memories 表。提取失败/解析失败静默跳过，不影响主流程。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, List, Optional

from . import store

logger = logging.getLogger(__name__)

_EXTRACT_PROMPT = """你是「长期记忆提取器」。根据下面的对话，提取值得跨会话长期记住的稳定信息。

规则：
1. 只输出一个 JSON 数组，不要任何其他文字。格式：
   [{"kind": "preference|fact|conclusion", "content": "一句话，主语明确"}]
2. kind 含义：
   - preference：用户的偏好/习惯/工作要求/技术栈/常用术语（如"用户习惯用简体中文回复"、"用户是前端工程师"）
   - fact：关于用户或项目的稳定事实（如"项目使用 FastAPI + Electron"、"用户使用 macOS"）
   - conclusion：本次对话达成的决定或重要结论（如"决定用 SQLite FTS5 做记忆检索"）
3. content 用简体中文，不超过 40 字，一条只讲一件事。
4. 只提取明确、稳定、对今后回答有用的信息；不提取一次性临时指令、寒暄、重复已有内容。
5. 没有值得记住的内容时，输出 []。

对话：
用户：{user}
助手：{assistant}"""

_EXTRACT_TIMEOUT = 45
_MIN_CHARS = 40  # 助手回复低于此长度不触发提取（省模型调用）


def _build_extract_messages(user_text: str, assistant_text: str) -> List[dict]:
    return [
        {"role": "system", "content": _EXTRACT_PROMPT},
        {
            "role": "user",
            "content": (
                f"用户：{user_text[:800]}\n\n助手：{assistant_text[:2000]}"
            ),
        },
    ]


def _parse_memories(raw: str) -> List[dict]:
    """从模型输出中解析 JSON 数组；容忍代码块包裹与前后噪声。"""
    text = (raw or "").strip()
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return []
    out: List[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        kind = str(it.get("kind") or "fact").strip().lower()
        content = str(it.get("content") or "").strip()
        if kind not in store.KINDS:
            kind = "fact"
        if content and len(content) <= 120:
            out.append({"kind": kind, "content": content})
    return out


async def extract_from_pair(
    user_text: str,
    assistant_text: str,
    source_conversation: str = "",
    model_id: str = "",
) -> int:
    """从一轮对话提取记忆并入库，返回新增条数（失败返回 0）。"""
    if not user_text.strip() or len((assistant_text or "").strip()) < _MIN_CHARS:
        return 0
    try:
        from ..providers import get_registry, resolve_model

        registry = get_registry()
        # 后台任务内做网络解析，不阻塞事件循环
        resolved = await asyncio.to_thread(_resolve_model, registry, model_id)
        if resolved is None:
            return 0
        provider_id, resolved_model = resolved
        provider = registry.get(provider_id)
        if provider is None:
            return 0

        stream = provider.chat(
            _build_extract_messages(user_text, assistant_text),
            model=resolved_model,
            stream=True,
        )
        raw = ""
        while True:
            try:
                ev = await asyncio.wait_for(anext(stream), timeout=_EXTRACT_TIMEOUT)
            except StopAsyncIteration:
                break
            except Exception:  # noqa: BLE001
                logger.info("[memory] 提取调用中断")
                return 0
            if ev.get("type") == "text":
                raw += ev.get("delta", "")

        memories = _parse_memories(raw)
        added = 0
        for m in memories:
            res = await store.add_memory(
                kind=m["kind"],
                content=m["content"],
                source_conversation=source_conversation,
            )
            if res.get("dup") is False:
                added += 1
        if memories:
            logger.info(f"[memory] 提取 {len(memories)} 条，新增 {added} 条")
        return added
    except Exception as exc:  # noqa: BLE001
        logger.info(f"[memory] 提取失败（静默跳过）: {exc}")
        return 0


def _resolve_model(registry, preferred: str):
    """选一个用于提取的模型：优先会话模型（registry 中有即返回，不探测），
    否则回退 resolve_model 常规路由。返回 (provider_id, model_id)。"""
    if preferred:
        for p in registry.all():
            models = getattr(p, "_models", None) or []
            if any(m.id == preferred for m in models):
                return p.provider_id, preferred
    try:
        model_id, provider_id = resolve_model(
            registry, "general", user_preferred=preferred or None, require_tools=False
        )
        return provider_id, model_id
    except Exception:  # noqa: BLE001
        return None

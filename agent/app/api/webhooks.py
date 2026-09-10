"""Webhook 接收端点（需求 §2.2：异步事件驱动，P3 工作流触发器使用）。

接口：
  POST /api/webhooks/{hook_id:path}   — 接收外部系统事件（落库 webhook_events；匹配工作流则异步触发）
  GET  /api/webhooks/events      — 最近事件（调试/工作流查看）
"""
import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..storage import webhook_event_repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

# 预留的 hook 标识白名单（P3 工作流创建时注册；当前开放任意非空 hook_id）
_KNOWN_HOOKS: set[str] = {"erp-events", "crm-events", "wms-events"}


async def _fire_workflows(hook_id: str, payload: Any) -> int:
    """匹配 webhook 触发器工作流并异步触发，返回触发条数。"""
    try:
        from ..workflows import engine
        from ..storage import workflow_repo  # noqa: F401

        matched = await engine.match_webhook_workflows(hook_id, payload)
        for wf in matched:
            asyncio.create_task(engine.trigger_workflows(wf, "webhook", payload))
        return len(matched)
    except Exception:  # noqa: BLE001
        logger.exception("[webhook] 工作流触发失败 hook=%s", hook_id)
        return 0


@router.post("/{hook_id:path}")
async def receive(hook_id: str, request: Request):
    """接收外部系统事件：JSON body 落库，匹配工作流异步触发。返回 202 已接收。"""
    try:
        payload: Any = await request.json()
    except Exception:
        payload = {"raw": (await request.body()).decode("utf-8", errors="replace")[:10_000]}
    source = ""
    if isinstance(payload, dict):
        source = str(payload.get("source") or payload.get("event") or "")
    event_id = await webhook_event_repo.insert(hook_id, payload, source=source)
    fired = await _fire_workflows(hook_id, payload)
    logger.info("[webhook] 接收事件 hook=%s event_id=%s workflows_fired=%s",
                hook_id, event_id, fired)
    return {"ok": True, "event_id": event_id, "hook_id": hook_id,
            "workflows_fired": fired}


class EventListQuery(BaseModel):
    hook_id: str = ""
    limit: int = 50


@router.get("/events")
async def events(hook_id: str = "", limit: int = 50):
    """最近接收事件（按 hook 筛选可选）。"""
    return await webhook_event_repo.list_all(hook_id=hook_id or None, limit=min(limit, 200))

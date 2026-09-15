"""后台任务 API（P0：长任务跑后台 + 进度查询 + 取消）。

能力：
- POST /api/tasks/launch  —— 把一次 Agent 任务放到后台执行（复用对话编排器），
  立即返回 task_id；任务跑完前调用方无需保持 SSE 连接。
- GET  /api/tasks/{id}    —— 任务状态 + 事件快照（进度查询，前端轮询/刷新用）。
- POST /api/tasks/{id}/cancel —— 请求取消（编排器在下一轮循环前检查，立即生效）。
- GET  /api/tasks        —— 最近任务列表（按更新时间倒序）。

状态机：queued → running → done | failed | cancelled

事件快照：保存 text/tool_call/tool_result/plan/thinking/error/done/cancelled，
单条长度截断、总量上限，防止行无限膨胀；正文 delta 在 GET 时按序拼接。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..storage import connect

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

# ── 常量 ──
MAX_EVENTS = 500          # 单任务事件快照上限
EVENT_STR_LIMIT = 3000    # 单条事件内长文本截断
PERSIST_EVERY = 20        # 每收集 20 条事件落库一次（进度可查询）

# ── 运行中的任务取消信号（task_id -> asyncio.Event）──
_running: Dict[str, asyncio.Event] = {}


# ── 存储 ──
async def _init_table() -> None:
    db = await connect()
    try:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS background_tasks (
                id              TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                user_message    TEXT NOT NULL,
                model_id        TEXT NOT NULL DEFAULT '',
                mode            TEXT NOT NULL DEFAULT 'work',
                status          TEXT NOT NULL DEFAULT 'queued',
                events          TEXT NOT NULL DEFAULT '[]',
                error           TEXT,
                created_at      INTEGER NOT NULL,
                updated_at      INTEGER NOT NULL
            );
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_bg_tasks_updated ON background_tasks(updated_at DESC);"
        )
        await db.commit()
    finally:
        await db.close()


async def _write(task_id: str, status: str, events: Optional[List[dict]] = None,
                 error: Optional[str] = None) -> None:
    db = await connect()
    try:
        if events is not None:
            await db.execute(
                "UPDATE background_tasks SET status=?, events=?, error=?, updated_at=? WHERE id=?",
                (status, json.dumps(events, ensure_ascii=False), error, int(time.time()), task_id),
            )
        else:
            await db.execute(
                "UPDATE background_tasks SET status=?, error=?, updated_at=? WHERE id=?",
                (status, error, int(time.time()), task_id),
            )
        await db.commit()
    finally:
        await db.close()


async def _read(task_id: str) -> Optional[dict]:
    db = await connect()
    try:
        async with db.execute(
            "SELECT * FROM background_tasks WHERE id=?", (task_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        try:
            d["events"] = json.loads(d.get("events") or "[]")
        except Exception:  # noqa: BLE001
            d["events"] = []
        return d
    finally:
        await db.close()


# ── 事件快照裁剪 ──
def _pick_event(ev: dict) -> dict:
    """挑选并截断需要持久化的事件字段（不存完整工具结果，控制体积）。"""
    t = ev.get("type", "")
    base: dict[str, Any] = {"type": t}
    if t == "text":
        base["delta"] = str(ev.get("delta", ""))[:EVENT_STR_LIMIT]
    elif t == "thinking":
        base["delta"] = str(ev.get("delta", ""))[:EVENT_STR_LIMIT]
    elif t == "tool_call":
        base["name"] = str(ev.get("name", ""))
        base["arguments"] = str(ev.get("arguments", ""))[:EVENT_STR_LIMIT]
    elif t == "tool_result":
        base["name"] = str(ev.get("name", ""))
        base["result"] = str(ev.get("result", ""))[:EVENT_STR_LIMIT]
        base["is_error"] = bool(ev.get("is_error", False))
    elif t == "plan":
        base["goal"] = str(ev.get("goal", ""))
        base["steps"] = ev.get("steps", [])
    elif t == "error":
        base["message"] = str(ev.get("message", ""))[:EVENT_STR_LIMIT]
    elif t == "cancelled":
        base["message"] = str(ev.get("message", ""))
    return base


# ── 请求模型 ──
class LaunchRequest(BaseModel):
    conversation_id: str
    message: str = Field(..., min_length=1)
    model_id: Optional[str] = None
    mode: str = "work"
    workspace_dir: str = ""
    persist_user: bool = True


class LaunchResponse(BaseModel):
    ok: bool
    task_id: str


# ── 后台执行 ──
async def _run_background(task_id: str, cancel_evt: asyncio.Event, body: LaunchRequest) -> None:
    events: List[dict] = []
    try:
        await _write(task_id, "running")
        from .chat import _get_orchestrator, msg_repo

        # 多轮上下文：同会话历史（排除末尾即将写入的当前消息——由 run_stream 追加）
        history = await msg_repo.list_by_conversation(body.conversation_id)
        prev_history = [
            {"role": m["role"], "content": m["content"]}
            for m in history
            if m["role"] in ("user", "assistant") and m.get("content")
        ]

        orch = await _get_orchestrator(body.mode or "work")
        last_done = False
        cancelled = False
        async for ev in orch.run_stream(
            user_message=body.message,
            conversation_id=body.conversation_id,
            model_id=body.model_id,
            history=prev_history,
            workspace_dir=body.workspace_dir,
            cancel_event=cancel_evt,
        ):
            t = ev.get("type", "")
            if t in ("text", "thinking", "tool_call", "tool_result", "plan", "error", "cancelled", "done"):
                events.append(_pick_event(ev))
                if len(events) > MAX_EVENTS:
                    events.pop(0)
                if t == "done":
                    last_done = True
                if t == "cancelled":
                    cancelled = True
                if len(events) % PERSIST_EVERY == 0:
                    await _write(task_id, "running", events)

        if cancelled or cancel_evt.is_set():
            await _write(task_id, "cancelled", events)
        elif last_done:
            await _write(task_id, "done", events)
        else:
            await _write(task_id, "failed", events,
                         error="任务异常结束（未收到 done 事件）")

        # P0 续跑基础：把已生成正文落库到会话历史（与普通聊天一致），
        # 之后同一会话继续提问（重试/续跑）可获得完整上下文。
        final_text = "".join(
            e.get("delta", "") for e in events if e.get("type") == "text"
        )
        if final_text.strip():
            try:
                await msg_repo.create(
                    conversation_id=body.conversation_id,
                    role="assistant",
                    content=final_text,
                    model_id=body.model_id or "",
                )
            except Exception:  # noqa: BLE001
                logger.exception("[tasks] 后台任务正文落库失败")
    except asyncio.CancelledError:  # 服务关闭等场景
        await _write(task_id, "cancelled", events, error="任务被系统取消")
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[tasks] 后台任务 {task_id} 失败: {exc}")
        try:
            await _write(task_id, "failed", events, error=str(exc)[:1000])
        except Exception:  # noqa: BLE001
            logger.exception("[tasks] 写失败状态时再次出错")
    finally:
        _running.pop(task_id, None)


# ── 路由 ──
@router.post("/launch", response_model=LaunchResponse)
async def launch(body: LaunchRequest) -> LaunchResponse:
    await _init_table()
    task_id = uuid.uuid4().hex[:12]
    cancel_evt = asyncio.Event()
    _running[task_id] = cancel_evt
    db = await connect()
    try:
        await db.execute(
            "INSERT INTO background_tasks"
            "(id, conversation_id, user_message, model_id, mode, status, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (task_id, body.conversation_id, body.message, body.model_id or "",
             body.mode or "work", "queued", int(time.time()), int(time.time())),
        )
        await db.commit()
    finally:
        await db.close()
    # 首次启动把用户消息落库（普通聊天一致），保证后续轮次/续跑有完整上下文；
    # 续跑（retry）显式传 persist_user=False，避免同一消息重复入史。
    if body.persist_user:
        try:
            from .chat import msg_repo
            await msg_repo.create(
                conversation_id=body.conversation_id,
                role="user",
                content=body.message,
                model_id=body.model_id or "",
            )
        except Exception:  # noqa: BLE001
            logger.exception("[tasks] 用户消息落库失败")
    asyncio.create_task(_run_background(task_id, cancel_evt, body))
    logger.info(f"[tasks] 已启动后台任务 {task_id}（会话 {body.conversation_id}）")
    return LaunchResponse(ok=True, task_id=task_id)


@router.get("/{task_id}")
async def get_task(task_id: str) -> dict:
    await _init_table()
    row = await _read(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    return {
        "task_id": task_id,
        "status": row["status"],
        "conversation_id": row["conversation_id"],
        "model_id": row["model_id"],
        "mode": row["mode"],
        "error": row.get("error"),
        "events": row.get("events", []),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str) -> dict:
    await _init_table()
    row = await _read(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    evt = _running.get(task_id)
    if evt is not None:
        evt.set()
        status = "cancelled"
        await _write(task_id, status, row.get("events", []))
        return {"ok": True, "task_id": task_id, "status": status, "note": "取消信号已发送，任务将在当前步骤结束后停止"}
    if row["status"] in ("queued", "running"):
        # 无运行句柄（如服务重启后遗留）：仅标记
        await _write(task_id, "cancelled", row.get("events", []))
        return {"ok": True, "task_id": task_id, "status": "cancelled", "note": "任务状态已标记为取消"}
    return {"ok": False, "task_id": task_id, "status": row["status"], "note": "任务已结束，无需取消"}


@router.get("")
async def list_tasks(limit: int = 20) -> dict:
    await _init_table()
    db = await connect()
    try:
        async with db.execute(
            "SELECT id, conversation_id, status, created_at, updated_at"
            " FROM background_tasks ORDER BY updated_at DESC LIMIT ?",
            (min(max(limit, 1), 100),),
        ) as cur:
            rows = await cur.fetchall()
    finally:
        await db.close()
    return {"tasks": [dict(r) for r in rows]}

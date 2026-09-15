"""主动触发调度器（P2）：Agent 按计划定时自主行动，结果送达用户。

能力：
- scheduled_jobs 表：title / message(Agent 指令) / schedule(JSON) / 状态 / 最近运行
- 五种调度规则（无第三方依赖，自包含解析）：
    at       —— 一次性："now + 1 minute"、"now + 2 hours"、"tomorrow 09:15"、"HH:MM"、"2026-09-16 09:15:00"
    interval —— 周期："每 N 分钟/小时/天"（分钟下限 5）
    daily    —— 每天 HH:MM
    weekly   —— 每周 X HH:MM（X=mon..sun 或 0-6）
    hourly   —— 每小时第 MM 分
- 调度循环：15s 轮询到期任务 → 复用 P0 后台任务管线（tasks._run_background）
  跑一次完整 Agent 任务（多轮工具调用）→ 结果写回会话历史 → 生成通知
- notifications 表：未读通知（前端徽标轮询）

API：
  GET    /api/schedule               —— 任务列表
  POST   /api/schedule               —— 创建任务
  PUT    /api/schedule/{id}          —— 修改（标题/指令/规则/启用）
  DELETE /api/schedule/{id}          —— 删除任务
  POST   /api/schedule/{id}/run      —— 立即运行一次（不改变下次计划）
  GET    /api/notifications          —— 通知列表 + 未读数
  POST   /api/notifications/read     —— 全部标记已读
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..storage import connect
from ..storage.conversation_repo import ConversationRepo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["schedule"])

LOOP_INTERVAL = 15          # 调度循环轮询间隔（秒）
MIN_INTERVAL_MIN = 5        # 分钟级周期下限，防止刷爆模型
_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
             "周一": 0, "周二": 1, "周三": 2, "周四": 3, "周五": 4, "周六": 5, "周日": 6}

_firing: set = set()        # 正在运行的任务 id（防止重复触发）


# ── 存储 ──
async def _init_table() -> None:
    db = await connect()
    try:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id              TEXT PRIMARY KEY,
                title           TEXT NOT NULL,
                message         TEXT NOT NULL,
                model_id        TEXT NOT NULL DEFAULT '',
                schedule        TEXT NOT NULL,
                enabled         INTEGER NOT NULL DEFAULT 1,
                next_run_at     INTEGER,
                last_run_at     INTEGER,
                last_status     TEXT NOT NULL DEFAULT 'pending',
                last_task_id    TEXT,
                conversation_id TEXT,
                created_at      INTEGER NOT NULL,
                updated_at      INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_next ON scheduled_jobs(next_run_at);
            CREATE TABLE IF NOT EXISTS notifications (
                id         TEXT PRIMARY KEY,
                job_id     TEXT,
                title      TEXT NOT NULL,
                message    TEXT NOT NULL,
                status     TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                read       INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_notif_read ON notifications(read, created_at DESC);
            """
        )
        await db.commit()
    finally:
        await db.close()


async def _row_to_job(row) -> Dict[str, Any]:
    d = dict(row)
    try:
        d["schedule"] = json.loads(d.get("schedule") or "{}")
    except Exception:  # noqa: BLE001
        d["schedule"] = {}
    d["enabled"] = bool(d.get("enabled"))
    return d


# ── 调度规则（纯函数，可单测） ──
_AT_RE = re.compile(r"^now\s*\+\s*(\d+)\s*(minute|minutes|min|hour|hours|day|days)$", re.I)
_TOMORROW_RE = re.compile(r"^tomorrow\s+(\d{1,2}):(\d{2})$", re.I)
_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?$")
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _parse_hhmm(s: str):
    """解析 HH:MM 并校验范围；非法返回 None。"""
    m = _TIME_RE.match(s)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return h, mi


def compute_next_run(schedule: Dict[str, Any], now_ts: Optional[int] = None) -> Optional[int]:
    """计算下次运行时间戳；一次性任务已过期返回 None。schedule 非法返回 None。"""
    now = datetime.fromtimestamp(now_ts or time.time())
    typ = (schedule or {}).get("type")
    value = (schedule or {}).get("value")

    if typ == "at":
        s = str(value or "").strip()
        m = _AT_RE.match(s)
        if m:
            n = int(m.group(1)); unit = m.group(2).lower()
            mult = {"minute": 60, "min": 60, "minutes": 60, "hour": 3600, "hours": 3600,
                    "day": 86400, "days": 86400}[unit]
            return int(now.timestamp()) + n * mult
        m = _TOMORROW_RE.match(s)
        if m:
            hm = _parse_hhmm(f"{m.group(1)}:{m.group(2)}")
            if hm is None:
                return None
            dt = (now + timedelta(days=1)).replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
            return int(dt.timestamp())
        m = _ISO_RE.match(s)
        if m:
            try:
                dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                              int(m.group(4)), int(m.group(5)), int(m.group(6) or 0))
            except ValueError:
                return None
            return int(dt.timestamp())
        hm = _parse_hhmm(s)
        if hm is not None:
            dt = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
            if dt <= now:
                dt += timedelta(days=1)
            return int(dt.timestamp())
        return None

    if typ == "interval":
        unit = (schedule or {}).get("unit", "minutes")
        try:
            n = int(value)
        except (TypeError, ValueError):
            return None
        mult = {"minutes": 60, "hours": 3600, "days": 86400}.get(unit)
        if mult is None or (unit == "minutes" and n < MIN_INTERVAL_MIN):
            return None
        return int(now.timestamp()) + n * mult

    if typ == "daily":
        hm = _parse_hhmm(str(value or ""))
        if hm is None:
            return None
        dt = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        if dt <= now:
            dt += timedelta(days=1)
        return int(dt.timestamp())

    if typ == "weekly":
        raw = value if isinstance(value, list) else None
        if not raw or len(raw) != 2:
            return None
        wd = _WEEKDAYS.get(str(raw[0]).lower()) or _WEEKDAYS.get(str(raw[0]))
        hm = _parse_hhmm(str(raw[1]))
        if wd is None or hm is None:
            return None
        target = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        for _i in range(8):
            if target.weekday() == wd and target > now:
                return int(target.timestamp())
            target += timedelta(days=1)
        return None

    if typ == "hourly":
        try:
            mm = int(value)
        except (TypeError, ValueError):
            return None
        if not 0 <= mm <= 59:
            return None
        target = now.replace(minute=mm, second=0, microsecond=0)
        if target <= now:
            target += timedelta(hours=1)
        return int(target.timestamp())

    return None


def humanize_schedule(schedule: Dict[str, Any]) -> str:
    typ = (schedule or {}).get("type")
    value = (schedule or {}).get("value")
    if typ == "at":
        return f"一次性：{value}"
    if typ == "interval":
        return f"每 {value} {('分钟' if (schedule or {}).get('unit', 'minutes') == 'minutes' else '小时' if (schedule or {}).get('unit') == 'hours' else '天')}"
    if typ == "daily":
        return f"每天 {value}"
    if typ == "weekly":
        raw = value if isinstance(value, list) else []
        wd = {v: k for k, v in _WEEKDAYS.items()}.get(_WEEKDAYS.get(str(raw[0]).lower(), -1), str(raw[0]))
        return f"每周{wd} {raw[1] if len(raw) > 1 else ''}".strip()
    if typ == "hourly":
        return f"每小时第 {value} 分"
    return f"未知规则（{typ}）"


# ── 通知 ──
async def _notify(job_id: str, title: str, message: str, status: str) -> None:
    db = await connect()
    try:
        await db.execute(
            "INSERT INTO notifications (id, job_id, title, message, status, created_at, read)"
            " VALUES (?,?,?,?,?,?,0)",
            (uuid.uuid4().hex[:12], job_id, title, (message or "")[:500], status, int(time.time())),
        )
        await db.commit()
    finally:
        await db.close()


# ── 任务运行 ──
async def _fire_job(job: Dict[str, Any]) -> None:
    """执行一次定时任务：确保会话 → 落用户消息 → 复用后台任务管线跑 Agent → 通知。"""
    job_id = job["id"]
    try:
        from . import tasks as task_api
        from .chat import msg_repo

        await task_api._init_table()

        # 1. 确保每个任务有稳定会话
        conv_id = job.get("conversation_id") or ""
        if not conv_id:
            conv = await ConversationRepo().create(
                f"定时任务：{job['title']}", job.get("model_id") or "", mode="work"
            )
            conv_id = conv["id"]
            await _update_job(job_id, conversation_id=conv_id)

        # 1.5 默认模型：任务未指定时，沿用客户端最近一次会话实际使用的模型
        model_id = job.get("model_id") or ""
        if not model_id:
            db = await connect()
            try:
                async with db.execute(
                    "SELECT model_id FROM conversations WHERE model_id != ''"
                    " ORDER BY updated_at DESC LIMIT 1"
                ) as cur:
                    row = await cur.fetchone()
                model_id = (row["model_id"] if row else "") or ""
            finally:
                await db.close()

        # 2. 用户消息落库（与普通对话一致，提供上下文）
        try:
            await msg_repo.create(
                conversation_id=conv_id, role="user", content=job["message"],
                model_id=model_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("[schedule] 用户消息落库失败")

        # 3. 跑后台 Agent 任务（先 INSERT 任务行——_run_background 依赖行存在
        #    才能持久化运行状态/错误/事件）
        task_id = uuid.uuid4().hex[:12]
        cancel_evt = asyncio.Event()
        now0 = int(time.time())
        db = await connect()
        try:
            await db.execute(
                "INSERT INTO background_tasks"
                "(id, conversation_id, user_message, model_id, mode, status, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (task_id, conv_id, job["message"], model_id, "work", "queued", now0, now0),
            )
            await db.commit()
        finally:
            await db.close()
        body = task_api.LaunchRequest(
            conversation_id=conv_id,
            message=job["message"],
            model_id=model_id or None,
            mode="work",
            persist_user=False,  # 已手动落库
        )
        await task_api._run_background(task_id, cancel_evt, body)
        row = await task_api._read(task_id)
        status = (row or {}).get("status", "failed")
        events = (row or {}).get("events", []) or []
        final_text = "".join(e.get("delta", "") for e in events if e.get("type") == "text")
        error = (row or {}).get("error")

        # 4. 更新任务状态 + 计算下次运行
        now = int(time.time())
        sched = job.get("schedule") or {}
        next_run = compute_next_run(sched, now) if sched.get("type") != "at" else None
        await _update_job(
            job_id,
            last_run_at=now,
            last_status=status,
            last_task_id=task_id,
            next_run_at=next_run,
        )

        # 5. 通知用户（一次性任务执行完自动停用，避免 UI 上继续排队）
        if sched.get("type") == "at":
            await _update_job(job_id, enabled=False)
        if status == "done":
            await _notify(job_id, job["title"], final_text.strip() or "任务完成（无正文输出）", "done")
        else:
            await _notify(job_id, job["title"], error or f"任务未完成（{status}）", status)
        logger.info(f"[schedule] 任务 {job_id} 完成：{status}（下次运行 {next_run}）")
    except Exception:  # noqa: BLE001
        logger.exception(f"[schedule] 任务 {job_id} 执行异常")
        await _update_job(job_id, last_status="failed")
        await _notify(job_id, job.get("title", "定时任务"), "任务执行异常，详见运行记录", "failed")
    finally:
        _firing.discard(job_id)


async def _update_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    keys = ", ".join(f"{k}=?" for k in fields)
    db = await connect()
    try:
        await db.execute(
            f"UPDATE scheduled_jobs SET {keys}, updated_at=? WHERE id=?",
            (*fields.values(), int(time.time()), job_id),
        )
        await db.commit()
    finally:
        await db.close()


# ── 调度循环 ──
async def scheduler_loop(stop: asyncio.Event) -> None:
    """后台轮询到期任务；App 运行期间持续生效。"""
    logger.info("[schedule] 调度循环启动（interval=%ss）", LOOP_INTERVAL)
    while not stop.is_set():
        try:
            await _init_table()
            db = await connect()
            try:
                async with db.execute(
                    "SELECT * FROM scheduled_jobs WHERE enabled=1 AND next_run_at IS NOT NULL"
                    " AND next_run_at <= ? ORDER BY next_run_at ASC",
                    (int(time.time()),),
                ) as cur:
                    rows = await cur.fetchall()
            finally:
                await db.close()
            for r in rows:
                job = await _row_to_job(r)
                if job["id"] in _firing:
                    continue
                _firing.add(job["id"])
                asyncio.create_task(_fire_job(job))
        except Exception:  # noqa: BLE001
            logger.exception("[schedule] 调度循环异常（本轮跳过）")
        try:
            await asyncio.wait_for(stop.wait(), timeout=LOOP_INTERVAL)
        except asyncio.TimeoutError:
            continue


# ── 请求模型 ──
class ScheduleCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=60)
    message: str = Field(..., min_length=1, max_length=2000)
    model_id: Optional[str] = None
    schedule: Dict[str, Any]
    enabled: bool = True


class ScheduleUpdate(BaseModel):
    title: Optional[str] = None
    message: Optional[str] = None
    model_id: Optional[str] = None
    schedule: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None


# ── 路由 ──
@router.get("/schedule")
async def list_jobs() -> dict:
    await _init_table()
    db = await connect()
    try:
        async with db.execute(
            "SELECT * FROM scheduled_jobs ORDER BY created_at DESC"
        ) as cur:
            rows = await cur.fetchall()
    finally:
        await db.close()
    jobs = [await _row_to_job(r) for r in rows]
    for j in jobs:
        j["schedule_text"] = humanize_schedule(j.get("schedule") or {})
    return {"jobs": jobs}


@router.post("/schedule")
async def create_job(body: ScheduleCreate) -> dict:
    await _init_table()
    next_run = compute_next_run(body.schedule)
    if next_run is None:
        raise HTTPException(status_code=422, detail=f"无法解析调度规则: {body.schedule}")
    job_id = uuid.uuid4().hex[:12]
    now = int(time.time())
    db = await connect()
    try:
        await db.execute(
            "INSERT INTO scheduled_jobs"
            " (id, title, message, model_id, schedule, enabled, next_run_at, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (job_id, body.title, body.message, body.model_id or "",
             json.dumps(body.schedule, ensure_ascii=False), 1 if body.enabled else 0,
             next_run, now, now),
        )
        await db.commit()
    finally:
        await db.close()
    logger.info(f"[schedule] 新建任务 {job_id}「{body.title}」下次运行 {next_run}")
    return {"ok": True, "id": job_id, "next_run_at": next_run,
            "schedule_text": humanize_schedule(body.schedule)}


@router.put("/schedule/{job_id}")
async def update_job(job_id: str, body: ScheduleUpdate) -> dict:
    await _init_table()
    db = await connect()
    try:
        async with db.execute("SELECT * FROM scheduled_jobs WHERE id=?", (job_id,)) as cur:
            row = await cur.fetchone()
    finally:
        await db.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
    job = await _row_to_job(row)

    fields: Dict[str, Any] = {}
    if body.title is not None:
        fields["title"] = body.title
    if body.message is not None:
        fields["message"] = body.message
    if body.model_id is not None:
        fields["model_id"] = body.model_id
    if body.schedule is not None:
        fields["schedule"] = json.dumps(body.schedule, ensure_ascii=False)
        nr = compute_next_run(body.schedule)
        if nr is None:
            raise HTTPException(status_code=422, detail=f"无法解析调度规则: {body.schedule}")
        fields["next_run_at"] = nr
    if body.enabled is not None:
        fields["enabled"] = 1 if body.enabled else 0
    if fields:
        await _update_job(job_id, **fields)
    return {"ok": True, "id": job_id, "schedule_text": humanize_schedule(
        body.schedule if body.schedule is not None else job.get("schedule") or {})}


@router.delete("/schedule/{job_id}")
async def delete_job(job_id: str) -> dict:
    await _init_table()
    db = await connect()
    try:
        async with db.execute("DELETE FROM scheduled_jobs WHERE id=?", (job_id,)) as cur:
            deleted = cur.rowcount
        await db.commit()
    finally:
        await db.close()
    if not deleted:
        raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
    return {"ok": True, "id": job_id}


@router.post("/schedule/{job_id}/run")
async def run_now(job_id: str) -> dict:
    """立即运行一次（不改变下次计划）；已在运行则拒绝。"""
    await _init_table()
    db = await connect()
    try:
        async with db.execute("SELECT * FROM scheduled_jobs WHERE id=?", (job_id,)) as cur:
            row = await cur.fetchone()
    finally:
        await db.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
    job = await _row_to_job(row)
    if job["id"] in _firing:
        raise HTTPException(status_code=409, detail="任务正在运行中")
    _firing.add(job["id"])
    asyncio.create_task(_fire_job(job))
    return {"ok": True, "id": job_id, "note": "已触发运行"}


# ── 通知 ──
@router.get("/notifications")
async def list_notifications(limit: int = 20) -> dict:
    await _init_table()
    db = await connect()
    try:
        async with db.execute("SELECT COUNT(*) AS c FROM notifications WHERE read=0") as cur:
            row = await cur.fetchone()
        unread = row["c"] if row else 0
        async with db.execute(
            "SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?",
            (min(max(limit, 1), 100),),
        ) as cur:
            rows = await cur.fetchall()
    finally:
        await db.close()
    return {"unread": unread, "items": [dict(r) for r in rows]}


@router.post("/notifications/read")
async def mark_read() -> dict:
    await _init_table()
    db = await connect()
    try:
        await db.execute("UPDATE notifications SET read=1 WHERE read=0")
        await db.commit()
    finally:
        await db.close()
    return {"ok": True}

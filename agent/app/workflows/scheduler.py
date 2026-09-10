"""Phase B (P3) 后台触发器调度器。

- schedule 类型：极简 cron（'m h * * *'）匹配 → 触发
- db 类型：执行只读 SQL + 阈值比较 → 满足触发（防抖：同工作流同一分钟不重复）
- webhook / event / manual 由各自入口触发，不在此轮询

间隔由环境变量 AGENT_SCHEDULER_INTERVAL 控制（秒，默认 30）。
"""
import asyncio
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

from ..storage import workflow_repo
from . import engine

logger = logging.getLogger(__name__)

_INTERVAL = max(2, int(os.environ.get("AGENT_SCHEDULER_INTERVAL", "30")))
_FIRED: Dict[str, str] = {}  # workflow_id -> "YYYY-MM-DD HH:MM"（防抖）


async def _run_background(wf, trigger: str, payload: Optional[Dict] = None) -> None:
    try:
        await engine.trigger_workflows(wf, trigger, payload)
    except Exception:  # noqa: BLE001
        logger.exception("[workflow-scheduler] 后台触发失败 workflow=%s", wf.get("id"))


async def workflow_scheduler_loop(stop: asyncio.Event) -> None:
    """后台轮询：schedule / db 触发器。"""
    logger.info("[workflow-scheduler] 启动（interval=%ss）", _INTERVAL)
    while not stop.is_set():
        try:
            workflows = await workflow_repo.list_all(enabled_only=True)
            now = datetime.now()
            minute_key = now.strftime("%Y-%m-%d %H:%M")
            for wf in workflows:
                wtype = wf.get("trigger_type")
                if wtype == "schedule":
                    cron = (wf.get("triggerConfig") or {}).get("cron", "")
                    if cron and engine.cron_matches(cron, now) and _FIRED.get(wf["id"]) != minute_key:
                        _FIRED[wf["id"]] = minute_key
                        logger.info("[workflow-scheduler] schedule 触发 %s", wf["id"])
                        asyncio.create_task(_run_background(wf, "schedule"))
                elif wtype == "db":
                    try:
                        hit = await engine.check_db_trigger(wf)
                    except Exception:  # noqa: BLE001
                        hit = False
                    if hit and _FIRED.get(wf["id"]) != minute_key:
                        _FIRED[wf["id"]] = minute_key
                        logger.info("[workflow-scheduler] db 触发 %s", wf["id"])
                        asyncio.create_task(_run_background(wf, "db"))
        except Exception:  # noqa: BLE001
            logger.exception("[workflow-scheduler] 轮询异常")
        try:
            await asyncio.wait_for(stop.wait(), timeout=_INTERVAL)
        except asyncio.TimeoutError:
            pass
    logger.info("[workflow-scheduler] 已停止")

"""用量统计路由。

接口：
  GET /api/usage/recent?n=7       — 最近 n 次对话
  GET /api/usage/daily?days=7     — 近 N 天聚合
"""
from typing import List, Dict, Any

from fastapi import APIRouter

from ..storage import usage_log_repo

router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("/recent")
async def recent(n: int = 7) -> List[Dict[str, Any]]:
    return await usage_log_repo.recent(n)


@router.get("/daily")
async def daily(days: int = 7) -> List[Dict[str, Any]]:
    return await usage_log_repo.totals_by_day(days)

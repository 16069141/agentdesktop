"""审计与合规路由（需求 §2.4）。

接口：
  GET  /api/audit/logs?conversation_id=&tool_name=&actor=&limit=&offset=
           — 审计日志查询（多条件筛选）
  GET  /api/audit/replay?conversation_id=
           — 操作回放（按时间正序完整调用链）
  GET  /api/audit/breakers
           — 熔断器状态（各工具窗口调用数 / 是否熔断）
  POST /api/audit/breakers/reset
           — 手动重置熔断（管理员排障用）
"""
from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ..audit.breaker import get_breaker
from ..storage import audit_log_repo

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/logs")
async def list_logs(conversation_id: Optional[str] = None,
                    tool_name: Optional[str] = None,
                    actor: Optional[str] = None,
                    limit: int = Query(default=200, ge=1, le=1000),
                    offset: int = Query(default=0, ge=0)):
    return await audit_log_repo.query(
        conversation_id=conversation_id or None,
        tool_name=tool_name or None,
        actor=actor or None,
        limit=limit,
        offset=offset,
    )


@router.get("/replay")
async def replay(conversation_id: str, summary: bool = False):
    """操作回放：某会话完整工具调用链（时间正序）。

    P4 优化：summary=true 时返回结构化摘要
    （成功/失败/拒绝/耗时/工具分布）；默认保持步骤列表（向后兼容）。
    """
    if summary:
        return await audit_log_repo.replay_with_summary(conversation_id)
    return await audit_log_repo.replay(conversation_id)


@router.get("/replay/stats")
async def replay_stats(conversation_id: str):
    """操作回放可视化聚合（P5）：

    - tools：工具名（按调用次数降序，最多 8 个）
    - buckets：时间桶标签（每 5 步一桶）
    - heat：热力图数据 [toolIdx, bucketIdx, count]（工具 × 时间桶）
    - trend：按步序的成功 / 失败 / 拒绝累计数（多系列折线）
    - latency：按步序的耗时（ms）折线
    """
    data = await audit_log_repo.replay_with_summary(conversation_id)
    steps = data.get("steps") or []
    tool_order: list[str] = []
    tool_count: dict[str, int] = {}
    for s in steps:
        t = (s.get("toolName") or "unknown").split(":")[0]
        tool_count[t] = tool_count.get(t, 0) + 1
        if t not in tool_order:
            tool_order.append(t)
    tool_order.sort(key=lambda t: -tool_count[t])
    tools = tool_order[:8]

    bucket_size = 5
    nb = max(1, (len(steps) + bucket_size - 1) // bucket_size)
    buckets = [f"步 {i * bucket_size + 1}-{min((i + 1) * bucket_size, len(steps))}"
               for i in range(nb)]
    heat: list[list] = []
    for ti, t in enumerate(tools):
        for bi in range(nb):
            seg = steps[bi * bucket_size:(bi + 1) * bucket_size]
            c = sum(1 for s in seg if (s.get("toolName") or "unknown").split(":")[0] == t)
            if c:
                heat.append([ti, bi, c])

    trend = {"ok": [], "failed": [], "rejected": []}
    latency = {"x": [], "ms": []}
    ok_c = fail_c = rej_c = 0
    for i, s in enumerate(steps, start=1):
        if s.get("action") == "rejected":
            rej_c += 1
        elif s.get("error"):
            fail_c += 1
        else:
            ok_c += 1
        trend["ok"].append(ok_c)
        trend["failed"].append(fail_c)
        trend["rejected"].append(rej_c)
        latency["x"].append(i)
        latency["ms"].append(int(s.get("latencyMs") or 0))

    return {
        "conversationId": conversation_id,
        "tools": tools,
        "buckets": buckets,
        "heat": heat,
        "trend": trend,
        "latency": latency,
        "summary": data.get("summary"),
    }


@router.get("/breakers")
async def breaker_status():
    return get_breaker().status()


class BreakerReset(BaseModel):
    tool_name: Optional[str] = None


@router.post("/breakers/reset")
async def breaker_reset(body: BreakerReset):
    get_breaker().reset(body.tool_name)
    return {"ok": True}

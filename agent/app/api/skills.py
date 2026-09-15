"""Skill 集成路由（需求 §2.7，P2 运营形态）。

接口：
  GET  /api/skills                 — Skill 列表
  GET  /api/skills/market          — 本地市场技能包清单
  POST /api/skills/market/search   — 市场检索（对话式安装匹配）
  GET  /api/skills/{id}            — 详情（含 manifest）
  POST /api/skills/install         — 安装（local_dir / zip / git / market / dialog）
  POST /api/skills/{id}/enable|disable — 启用/停用
  DELETE /api/skills/{id}          — 卸载
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..skills import loader
from ..storage import skills_repo

router = APIRouter(prefix="/api/skills", tags=["skills"])


class InstallRequest(BaseModel):
    source: str = "local_dir"
    path: str = ""
    git_url: str = ""
    market_id: str = ""
    query: str = ""


class MarketSearch(BaseModel):
    query: str


@router.get("/market/stats")
async def market_stats():
    """技能市场运营统计（P5）：

    - total_local / total_online：目录包数（本地 + SkillHub/ClawHub 在线）
    - installed_count：已安装技能数
    - source_distribution：已安装技能来源分布
    - recent_installs：最近安装 5 条（含时间与来源）
    """
    local_items = loader.list_local_market()
    online_items = loader.fetch_online_market_catalog()
    installed = await loader.list_skills()
    dist: dict[str, int] = {}
    for s in installed:
        src = s.get("source") or "unknown"
        dist[src] = dist.get(src, 0) + 1
    recent = sorted(
        [s for s in installed if s.get("installedAt")],
        key=lambda s: s["installedAt"], reverse=True,
    )[:5]
    return {
        "total_local": len(local_items),
        "total_online": len(online_items),
        "installed_count": len(installed),
        "source_distribution": dist,
        "recent_installs": [{
            "id": s["id"], "name": s.get("name") or s["id"],
            "version": s.get("version") or "", "source": s.get("source") or "",
            "installedAt": s.get("installedAt"),
        } for s in recent],
    }


@router.get("")
async def list_skills():
    return await loader.list_skills()


@router.get("/market")
async def market():
    """技能市场清单：items=本地市场包，online=在线市场目录（两路不重叠）。"""
    local_items = loader.list_local_market()
    online_items = loader.fetch_online_market_catalog()
    return {
        "source": "local_market",
        "items": local_items,
        "online": online_items,
    }


@router.post("/market/search")
async def market_search(body: MarketSearch):
    """市场检索：返回按相关性排序的候选（对话式安装取第一个）。"""
    query = (body.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query 不能为空")
    return {"query": query, "items": await loader.search_market(query)}


@router.post("/install")
async def install(req: InstallRequest):
    payload = {
        "source": req.source,
        "path": req.path,
        "git_url": req.git_url,
        "market_id": req.market_id,
        "query": req.query,
    }
    result = await loader.install_skill(payload)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result


@router.get("/{skill_id:path}")
async def get_skill(skill_id: str):
    rec = await skills_repo.get(skill_id)
    if not rec:
        raise HTTPException(status_code=404, detail="skill_not_found")
    return rec


@router.post("/{skill_id:path}/enable")
async def enable_skill(skill_id: str):
    rec = await loader.set_enabled(skill_id, True)
    if not rec:
        raise HTTPException(status_code=404, detail="skill_not_found")
    return rec


@router.post("/{skill_id:path}/disable")
async def disable_skill(skill_id: str):
    rec = await loader.set_enabled(skill_id, False)
    if not rec:
        raise HTTPException(status_code=404, detail="skill_not_found")
    return rec


@router.delete("/{skill_id:path}")
async def uninstall(skill_id: str):
    await loader.uninstall_skill(skill_id)
    return {"ok": True}

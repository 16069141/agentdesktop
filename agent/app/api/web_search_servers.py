"""联网搜索服务 API（web_search 工具的 provider 配置）。

接口：
  GET  /api/web-search-servers          — 列表（key 打码）
  POST /api/web-search-servers          — 新增（api_key 存钥匙串）
  PUT  /api/web-search-servers/{id}     — 更新
  DELETE /api/web-search-servers/{id}   — 删除
  POST /api/web-search-servers/{id}/test — 连通性测试（真实搜索一次）
"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from ..storage import web_search_server_repo
from ..security import keychain
from ..tools.web_tools import _search_bing_web, _search_duckduckgo, _search_api

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/web-search-servers", tags=["web-search"])

_PLACEHOLDER = "********"
_PROVIDERS = ("tavily", "bing", "brave", "serpapi", "duckduckgo", "bing_web")


def _mask(server: dict) -> dict:
    s = dict(server)
    s["has_api_key"] = bool(s.get("api_key_ref"))
    s.pop("api_key_ref", None)
    return s


class ServerCreate(BaseModel):
    id: str
    name: str
    provider: str = "duckduckgo"
    base_url: Optional[str] = None
    api_key: str = ""
    enabled: bool = True
    max_results: int = 5
    timeout_sec: int = 15


class ServerUpdate(BaseModel):
    name: Optional[str] = None
    provider: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    enabled: Optional[bool] = None
    max_results: Optional[int] = None
    timeout_sec: Optional[int] = None


@router.get("")
async def list_servers():
    servers = await web_search_server_repo.list_all()
    return [_mask(s) for s in servers]


@router.post("")
async def create_server(body: ServerCreate):
    if not body.id.strip() or not body.name.strip():
        raise HTTPException(status_code=400, detail="id/name 不能为空")
    if body.provider not in _PROVIDERS:
        raise HTTPException(status_code=400, detail=f"provider 仅支持 {_PROVIDERS}")
    if await web_search_server_repo.get(body.id):
        raise HTTPException(status_code=409, detail=f"搜索服务 {body.id} 已存在")

    api_key_ref = None
    if body.api_key and body.api_key != _PLACEHOLDER:
        ref = f"web-search:{body.id}"
        try:
            await keychain.store(ref, body.api_key)
            api_key_ref = ref
        except Exception as exc:
            logger.warning(f"[web-search] keychain 不可用: {exc}")
            api_key_ref = None

    server = await web_search_server_repo.create(
        {
            "id": body.id,
            "name": body.name,
            "provider": body.provider,
            "base_url": body.base_url,
            "api_key_ref": api_key_ref,
            "enabled": body.enabled,
            "max_results": body.max_results,
            "timeout_sec": body.timeout_sec,
        }
    )
    return _mask(server)


@router.put("/{server_id:path}")
async def update_server(server_id: str, body: ServerUpdate):
    existing = await web_search_server_repo.get(server_id)
    if not existing:
        raise HTTPException(status_code=404, detail="not_found")
    fields = body.model_dump(exclude_unset=True)
    if "api_key" in fields:
        api_key = fields.pop("api_key")
        if api_key and api_key != _PLACEHOLDER:
            ref = f"web-search:{server_id}"
            try:
                await keychain.store(ref, api_key)
                fields["api_key_ref"] = ref
            except Exception as exc:
                logger.warning(f"[web-search] keychain 不可用: {exc}")
    if "provider" in fields and fields["provider"] not in _PROVIDERS:
        raise HTTPException(status_code=400, detail=f"provider 仅支持 {_PROVIDERS}")
    server = await web_search_server_repo.update(server_id, fields)
    return _mask(server)


@router.delete("/{server_id:path}")
async def delete_server(server_id: str):
    ok = await web_search_server_repo.delete(server_id)
    if not ok:
        raise HTTPException(status_code=404, detail="not_found")
    try:
        await keychain.delete(f"web-search:{server_id}")
    except Exception:
        pass
    return {"ok": True}


@router.post("/{server_id:path}/test")
async def test_server(server_id: str):
    """真实搜索一次验证配置可用（duckduckgo 免 key；带 key provider 用钥匙串 key）。"""
    server = await web_search_server_repo.get(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="not_found")
    provider = server.get("provider", "bing_web")
    timeout_sec = int(server.get("timeout_sec", 15))
    query = "测试 搜索 连接"
    try:
        if provider in ("duckduckgo", "bing_web"):
            if provider == "duckduckgo":
                res = await _search_duckduckgo(query, 3, timeout_sec)
                if not res.get("success") or not res.get("count"):
                    res = await _search_bing_web(query, 3, timeout_sec)
            else:
                res = await _search_bing_web(query, 3, timeout_sec)
        else:
            api_key = ""
            ref = server.get("api_key_ref")
            if ref:
                try:
                    api_key = await keychain.retrieve(ref) or ""
                except Exception:
                    api_key = ""
            if not api_key:
                raise HTTPException(status_code=400, detail=f"{provider} 未配置 API Key")
            res = await _search_api(provider, server, query, api_key, timeout_sec)
        await web_search_server_repo.save_health(server_id, bool(res.get("success")))
        if not res.get("success"):
            raise HTTPException(status_code=502, detail=res.get("error", "搜索失败"))
        return {"ok": True, "provider": provider, "count": res.get("count", 0)}
    except HTTPException:
        raise
    except Exception as exc:
        await web_search_server_repo.save_health(server_id, False)
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/{server_id:path}/api-key")
async def get_api_key(server_id: str):
    """返回真实 API Key（编辑时回填显示用；仅本地回环 + Token 鉴权可访问）。"""
    server = await web_search_server_repo.get(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="连接不存在")
    ref = server.get("api_key_ref")
    api_key = ""
    if ref:
        api_key = await keychain.retrieve(ref) or ""
    return {"server_id": server_id, "api_key": api_key, "has_api_key": bool(api_key)}

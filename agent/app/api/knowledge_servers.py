"""知识库连接管理路由（局域网/互联网 RAG、LLM Wiki 等）。

接口：
  GET    /api/knowledge-servers                 — 连接列表（不含密钥）
  GET    /api/knowledge-servers/platforms       — 平台清单（下拉用）
  POST   /api/knowledge-servers                 — 新增连接
  PUT    /api/knowledge-servers/{id}            — 编辑连接
  DELETE /api/knowledge-servers/{id}            — 删除连接
  POST   /api/knowledge-servers/{id}/test       — 连通性测试
  POST   /api/knowledge-servers/{id}/search     — 检索（返回带引用结果）
  GET    /api/knowledge-servers/{id}/spaces     — 列出可用知识库/空间（平台支持时）
"""
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..knowledge_connectors import create_connector, list_platforms
from ..security import keychain
from ..storage.knowledge_servers import KnowledgeServerRepo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge-servers", tags=["knowledge-servers"])
repo = KnowledgeServerRepo()

_PLACEHOLDER = "**stored**"

# type 白名单
TYPES = {"rag", "wiki", "generic"}


def _mask(server: dict) -> dict:
    out = {k: v for k, v in server.items() if k != "api_key_ref"}
    out["has_api_key"] = bool(server.get("api_key_ref"))
    return out


async def _resolve_api_key(server: dict) -> str:
    ref = server.get("api_key_ref")
    if not ref:
        return ""
    return await keychain.retrieve(ref) or ""


class ServerCreate(BaseModel):
    id: str
    name: str
    type: str = "rag"
    platform: str = "dify"
    base_url: str
    auth_type: str = "api_key"
    api_key: str = ""
    extra_config: dict = {}
    timeout_sec: int = 30
    enabled: bool = True


class ServerUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    platform: Optional[str] = None
    base_url: Optional[str] = None
    auth_type: Optional[str] = None
    api_key: Optional[str] = None
    extra_config: Optional[dict] = None
    timeout_sec: Optional[int] = None
    enabled: Optional[bool] = None


@router.get("/platforms")
async def platforms():
    return list_platforms()


@router.get("")
async def list_servers():
    servers = await repo.list_all()
    return [_mask(s) for s in servers]


@router.post("")
async def create_server(body: ServerCreate):
    if not body.id.strip() or not body.name.strip() or not body.base_url.strip():
        raise HTTPException(status_code=400, detail="id/name/base_url 不能为空")
    if body.type not in TYPES:
        raise HTTPException(status_code=400, detail="type 仅支持 rag / wiki / generic")
    existing = await repo.get(body.id)
    if existing:
        raise HTTPException(status_code=409, detail=f"连接 {body.id} 已存在")

    api_key_ref = None
    if body.api_key and body.api_key != _PLACEHOLDER:
        ref = f"kb-server:{body.id}"
        try:
            await keychain.store(ref, body.api_key)
            api_key_ref = ref
        except Exception as exc:
            logger.warning(f"[knowledge-servers] keychain 不可用，明文存储（不推荐）: {exc}")

    server = await repo.create({
        "id": body.id,
        "name": body.name,
        "type": body.type,
        "platform": body.platform,
        "base_url": body.base_url.rstrip("/"),
        "auth_type": body.auth_type,
        "api_key_ref": api_key_ref,
        "extra_config": body.extra_config,
        "timeout_sec": body.timeout_sec,
        "enabled": body.enabled,
    })
    return _mask(server)


@router.put("/{server_id:path}")
async def update_server(server_id: str, body: ServerUpdate):
    server = await repo.get(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="连接不存在")

    fields: dict[str, Any] = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.type is not None:
        if body.type not in TYPES:
            raise HTTPException(status_code=400, detail="type 仅支持 rag / wiki / generic")
        fields["type"] = body.type
    if body.platform is not None:
        fields["platform"] = body.platform
    if body.base_url is not None:
        fields["base_url"] = body.base_url.rstrip("/")
    if body.auth_type is not None:
        fields["auth_type"] = body.auth_type
    if body.timeout_sec is not None:
        fields["timeout_sec"] = body.timeout_sec
    if body.enabled is not None:
        fields["enabled"] = body.enabled
    if body.extra_config is not None:
        fields["extra_config"] = body.extra_config
    if body.api_key is not None and body.api_key and body.api_key != _PLACEHOLDER:
        ref = f"kb-server:{server_id}"
        try:
            await keychain.store(ref, body.api_key)
            fields["api_key_ref"] = ref
        except Exception as exc:
            logger.warning(f"[knowledge-servers] keychain 不可用: {exc}")

    updated = await repo.update(server_id, fields)
    return _mask(updated) if updated else None


@router.delete("/{server_id:path}")
async def delete_server(server_id: str):
    server = await repo.get(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="连接不存在")
    ref = server.get("api_key_ref")
    if ref:
        await keychain.delete(ref)
    await repo.delete(server_id)
    return {"ok": True}


@router.post("/{server_id:path}/test")
async def test_server(server_id: str):
    server = await repo.get(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="连接不存在")
    api_key = await _resolve_api_key(server)
    connector = create_connector(server, api_key=api_key)
    ok = await connector.test()
    await repo.save_health(server_id, ok)
    if not ok and isinstance(connector, _Unsupported):
        return {"ok": False, "error": connector._msg}
    return {"ok": ok}


@router.post("/{server_id:path}/search")
async def search_server(server_id: str, body: dict):
    query = (body.get("query") or "").strip()
    top_k = int(body.get("top_k") or 5)
    if not query:
        raise HTTPException(status_code=400, detail="query 不能为空")
    server = await repo.get(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="连接不存在")
    if not server.get("enabled"):
        raise HTTPException(status_code=403, detail="连接已停用")
    api_key = await _resolve_api_key(server)
    connector = create_connector(server, api_key=api_key)
    results = await connector.search(query, top_k=top_k)
    return {"server_id": server_id, "query": query, "results": results}


@router.get("/{server_id:path}/spaces")
async def list_spaces(server_id: str):
    """列出可用知识库/空间（Dify 数据集、Confluence 空间等）。"""
    server = await repo.get(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="连接不存在")
    api_key = await _resolve_api_key(server)
    platform = server.get("platform")
    items: list[dict] = []
    if platform == "dify":
        items = await _dify_datasets(server, api_key)
    elif platform == "confluence":
        items = await _confluence_spaces(server, api_key)
    return {"server_id": server_id, "platform": platform, "items": items}


async def _dify_datasets(server: dict, api_key: str) -> list[dict]:
    import httpx
    base = server["base_url"].rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
            resp = await client.get(f"{base}/datasets", headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return [
            {"id": d.get("id", ""), "name": d.get("name", "")}
            for d in data.get("data", [])
        ]
    except Exception as exc:
        return [{"id": "", "name": f"获取失败: {exc}"}]


async def _confluence_spaces(server: dict, api_key: str) -> list[dict]:
    import base64
    import httpx
    headers = {}
    if api_key:
        token = api_key
        if ":" in api_key and not api_key.startswith("Basic "):
            token = "Basic " + base64.b64encode(api_key.encode()).decode()
        headers["Authorization"] = token
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
            resp = await client.get(
                f"{server['base_url']}/rest/api/space?limit=100", headers=headers
            )
            resp.raise_for_status()
            data = resp.json()
        return [
            {"id": s.get("key", ""), "name": s.get("name", "")}
            for s in data.get("results", [])
        ]
    except Exception as exc:
        return [{"id": "", "name": f"获取失败: {exc}"}]


from ..knowledge_connectors.platforms import UnsupportedConnector as _Unsupported  # noqa: E402

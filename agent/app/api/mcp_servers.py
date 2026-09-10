"""外部 MCP Server API（动态挂载为 Agent 工具）。

接口：
  GET  /api/mcp-servers               — 列表
  POST /api/mcp-servers               — 新增（url/headers/name）
  PUT  /api/mcp-servers/{id}          — 更新
  DELETE /api/mcp-servers/{id}        — 删除
  POST /api/mcp-servers/{id}/sync     — 连接并拉取工具清单（动态挂载核心）
  POST /api/mcp-servers/{id}/test     — 连通性测试（initialize）
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..storage import mcp_server_repo
from ..tools.web_tools import McpClient, sync_mcp_tools

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp-servers", tags=["mcp-servers"])


def _mask(server: dict) -> dict:
    s = dict(server)
    return s


class ServerCreate(BaseModel):
    id: str
    name: str
    url: str
    headers: dict = {}
    enabled: bool = True


class ServerUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    headers: Optional[dict] = None
    enabled: Optional[bool] = None


@router.get("")
async def list_servers():
    servers = await mcp_server_repo.list_all()
    return [_mask(s) for s in servers]


@router.post("")
async def create_server(body: ServerCreate):
    if not body.id.strip() or not body.name.strip() or not body.url.strip():
        raise HTTPException(status_code=400, detail="id/name/url 不能为空")
    if not body.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="MCP URL 仅支持 http/https 端点")
    if await mcp_server_repo.get(body.id):
        raise HTTPException(status_code=409, detail=f"MCP Server {body.id} 已存在")
    server = await mcp_server_repo.create(
        {
            "id": body.id,
            "name": body.name,
            "url": body.url,
            "headers": body.headers or {},
            "enabled": body.enabled,
        }
    )
    return _mask(server)


@router.put("/{server_id:path}")
async def update_server(server_id: str, body: ServerUpdate):
    existing = await mcp_server_repo.get(server_id)
    if not existing:
        raise HTTPException(status_code=404, detail="not_found")
    fields = body.model_dump(exclude_unset=True)
    url = fields.get("url")
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="MCP URL 仅支持 http/https 端点")
    server = await mcp_server_repo.update(server_id, fields)
    return _mask(server)


@router.delete("/{server_id:path}")
async def delete_server(server_id: str):
    ok = await mcp_server_repo.delete(server_id)
    if not ok:
        raise HTTPException(status_code=404, detail="not_found")
    return {"ok": True}


@router.post("/{server_id:path}/test")
async def test_server(server_id: str):
    """连通性测试：initialize 握手。"""
    server = await mcp_server_repo.get(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="not_found")
    client = McpClient(
        server.get("url", ""),
        headers=server.get("headers") or {},
        timeout_sec=20,
    )
    try:
        info = await client.initialize()
        result = info.get("result", {})
        await mcp_server_repo.save_health(server_id, True)
        return {
            "ok": True,
            "protocolVersion": result.get("protocolVersion", ""),
            "serverInfo": result.get("serverInfo", {}),
        }
    except Exception as exc:
        await mcp_server_repo.save_health(server_id, False)
        raise HTTPException(status_code=502, detail=f"MCP 连接失败: {exc}")
    finally:
        await client.close()


@router.post("/{server_id:path}/sync")
async def sync_tools(server_id: str):
    """连接并拉取工具清单，写入 tools_cache（重启后动态注册为 Agent 工具）。"""
    res = await sync_mcp_tools(server_id)
    if not res.get("success"):
        raise HTTPException(status_code=502, detail=res.get("error", "同步失败"))
    return res

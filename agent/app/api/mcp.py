"""MCP 工具集与统一元数据中心路由（需求 §2.1 / §2.7 / MCP 集成模块修改版）。

接口：
  GET  /api/mcp/tools                    — 统一工具列表（tool / skill / mcp / connector 四类）
       ?kind=&source=&enabled=           — 筛选
  GET  /api/mcp/tools/{tool_id}          — 单工具详情（含元数据）
  POST /api/mcp/{toolset}/enable|disable — 启用 / 禁用
  GET  /api/mcp/connectors               — 连接器注册表列表
  POST /api/mcp/connectors/{id}/invoke   — 调用连接器（带审计 + 身份透传）
  GET  /api/mcp/stats                    — 注册表统计（按 kind 聚合）
  POST /api/mcp/audit                    — 审计事件写入
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..connectors import get_connector_registry
from ..storage import tool_registry_repo

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


@router.get("/tools")
async def list_tools(kind: Optional[str] = None,
                     source: Optional[str] = None,
                     enabled: Optional[bool] = None):
    """统一元数据中心：按 kind/source/enabled 筛选工具、Skill、MCP、连接器条目。"""
    return await tool_registry_repo.list_all(
        kind=kind or None, source=source or None, enabled=enabled
    )


@router.get("/tools/{tool_id}")
async def get_tool(tool_id: str):
    tool = await tool_registry_repo.get(tool_id)
    if not tool:
        raise HTTPException(status_code=404, detail="tool_not_found")
    return tool


@router.get("/stats")
async def stats():
    """按 kind / source 聚合的注册表统计。"""
    tools = await tool_registry_repo.list_all()
    by_kind: dict = {}
    by_source: dict = {}
    enabled = 0
    for t in tools:
        by_kind[t["kind"]] = by_kind.get(t["kind"], 0) + 1
        by_source[t["source"]] = by_source.get(t["source"], 0) + 1
        if t["enabled"]:
            enabled += 1
    return {"total": len(tools), "enabled": enabled,
            "byKind": by_kind, "bySource": by_source}


@router.post("/{toolset}/enable")
async def enable_tool(toolset: str):
    """来源白名单校验后启用工具。

    白名单 = builtin（内置）/ trusted（可信）/ connector（企业连接器）。
    connector 工具集的真实调用仍要求连接器已配置且密钥有效（调用层校验），
    允许其启用只是让模型在对话中可见，不会绕过任何鉴权。
    """
    tool = await tool_registry_repo.get(toolset)
    if not tool:
        raise HTTPException(status_code=404, detail="tool_not_found")
    if tool["source"] not in ("builtin", "trusted", "connector"):
        raise HTTPException(status_code=403, detail="source_not_whitelisted")
    updated = await tool_registry_repo.toggle(toolset, True)
    return updated or {}


@router.post("/{toolset}/disable")
async def disable_tool(toolset: str):
    tool = await tool_registry_repo.get(toolset)
    if not tool:
        raise HTTPException(status_code=404, detail="tool_not_found")
    updated = await tool_registry_repo.toggle(toolset, False)
    return updated or {}


# ---------- 连接器（P0 框架：注册表 + 带审计调用） ----------

@router.get("/connectors")
async def list_connectors():
    return get_connector_registry().list()


class ConnectorInvokeRequest(BaseModel):
    operation: str = "read"
    params: dict = {}


@router.post("/connectors/{connector_id}/invoke")
async def invoke_connector(connector_id: str, req: ConnectorInvokeRequest, request: Request):
    from ..auth import parse_identity_headers
    connector = get_connector_registry().get(connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="connector_not_found")
    identity = parse_identity_headers(request.headers)
    return await connector.call(req.operation, req.params or {}, identity=identity)


@router.post("/audit")
async def log_audit(event: dict):
    """审计事件写入（Phase B：结构化审计走 audit_logs 表）。"""
    from ..audit.logger import record_tool_call
    await record_tool_call(
        tool_name=event.get("tool_name") or event.get("toolName") or "unknown",
        arguments=event.get("params") or {},
        result=event.get("result"),
        conversation_id=event.get("conversation_id", ""),
        actor=event.get("actor", "local"),
    )
    return {"ok": True}

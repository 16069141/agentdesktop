"""MCP 工具集启停路由。

接口：
  GET    /api/mcp/tools              — 工具列表（含状态）
  POST   /api/mcp/{toolset}/enable   — 启用
  POST   /api/mcp/{toolset}/disable  — 禁用
  GET    /api/mcp/tools/{tool_id}    — 单工具详情
  POST   /api/mcp/tools/{tool_id}/audit — 审计日志（占位，Phase 3 扩展）
"""
from fastapi import APIRouter, HTTPException

from ..storage import tool_registry_repo

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


@router.get("/tools")
async def list_tools():
    """返回 tool_registry 全部工具。"""
    return await tool_registry_repo.list_all()


@router.get("/tools/{tool_id}")
async def get_tool(tool_id: str):
    tool = await tool_registry_repo.get(tool_id)
    if not tool:
        raise HTTPException(status_code=404, detail="tool_not_found")
    return tool


@router.post("/{toolset}/enable")
async def enable_tool(toolset: str):
    """来源白名单校验后启用工具（白名单 = source in ('builtin', 'trusted')）。"""
    tool = await tool_registry_repo.get(toolset)
    if not tool:
        raise HTTPException(status_code=404, detail="tool_not_found")
    if tool["source"] not in ("builtin", "trusted"):
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


@router.post("/audit")
async def log_audit(event: dict):
    """审计事件写入（占位，Phase 3 接结构化日志落盘）。"""
    # MVP：仅打印，Phase 3 落 SQLite 审计表
    import logging
    logging.getLogger("private_ai.audit").info("mcp_audit: %s", event)
    return {"ok": True}

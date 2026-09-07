"""企业系统连接器管理路由（需求 §2.1）。

接口：
  GET    /api/connectors/types                 — 连接器类型清单（含操作模板）
  GET    /api/connectors                       — 实例列表（不含密钥）
  POST   /api/connectors                       — 新增实例
  PUT    /api/connectors/{id}                  — 编辑实例
  DELETE /api/connectors/{id}                  — 删除实例（同时删 keychain 密钥）
  POST   /api/connectors/{id}/test             — 连通性测试
  POST   /api/connectors/{id}/invoke           — 调用操作（带审计 + 身份透传）
"""
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..connectors import (
    CONNECTOR_TYPES,
    create_connector,
    list_connector_types,
    sync_connector_registry,
)
from ..security import keychain
from ..storage import connector_config_repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connectors", tags=["connectors"])
repo = connector_config_repo

_PLACEHOLDER = "**stored**"
# P4：连接器类型随声明式框架扩展（erp/crm/oa + wms/eam/hrm/mes/srm/bi）
VALID_TYPES = set(CONNECTOR_TYPES.keys())


def _mask(cfg: dict) -> dict:
    out = {k: v for k, v in cfg.items() if k != "apiKeyRef"}
    out["has_api_key"] = bool(cfg.get("apiKeyRef"))
    return out


async def _resolve_api_key(cfg: dict) -> str:
    ref = cfg.get("apiKeyRef")
    if not ref:
        return ""
    return await keychain.retrieve(ref) or ""


class ConnectorCreate(BaseModel):
    id: str
    type: str
    name: str
    base_url: str
    auth_type: str = "api_key"
    api_key: str = ""
    operations: dict = {}
    timeout_sec: int = 30
    enabled: bool = True


class ConnectorUpdate(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    auth_type: Optional[str] = None
    api_key: Optional[str] = None
    operations: Optional[dict] = None
    timeout_sec: Optional[int] = None
    enabled: Optional[bool] = None


@router.get("/types")
async def types():
    return list_connector_types()


@router.get("")
async def list_connectors():
    configs = await repo.list_all()
    return [_mask(c) for c in configs]


@router.post("")
async def create_connector_cfg(body: ConnectorCreate):
    if not body.id.strip() or not body.name.strip() or not body.base_url.strip():
        raise HTTPException(status_code=400, detail="id/name/base_url 不能为空")
    if body.type not in VALID_TYPES:
        raise HTTPException(status_code=400, detail="type 仅支持 erp / crm / oa")
    if await repo.get(body.id):
        raise HTTPException(status_code=409, detail=f"连接器 {body.id} 已存在")

    api_key_ref = None
    if body.api_key and body.api_key != _PLACEHOLDER:
        ref = f"connector:{body.id}"
        try:
            await keychain.store(ref, body.api_key)
            api_key_ref = ref
        except Exception as exc:
            logger.warning(f"[connectors] keychain 不可用: {exc}")

    cfg = await repo.create({
        "id": body.id.strip(),
        "type": body.type,
        "name": body.name.strip(),
        "base_url": body.base_url.rstrip("/"),
        "auth_type": body.auth_type,
        "api_key_ref": api_key_ref,
        "operations": body.operations or {},
        "enabled": body.enabled,
        "timeout_sec": body.timeout_sec,
    })
    await _resync()
    return _mask(cfg)


@router.put("/{cfg_id:path}")
async def update_connector_cfg(cfg_id: str, body: ConnectorUpdate):
    cfg = await repo.get(cfg_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="连接器不存在")

    fields: dict[str, Any] = {}
    for key, attr in (("name", "name"), ("base_url", "base_url"),
                      ("auth_type", "auth_type"), ("timeout_sec", "timeout_sec"),
                      ("enabled", "enabled")):
        value = getattr(body, key)
        if value is not None:
            fields[attr] = value.rstrip("/") if key == "base_url" else value
    if body.operations is not None:
        fields["operations"] = body.operations
    if body.api_key and body.api_key != _PLACEHOLDER:
        ref = f"connector:{cfg_id}"
        try:
            await keychain.store(ref, body.api_key)
            fields["api_key_ref"] = ref
        except Exception as exc:
            logger.warning(f"[connectors] keychain 不可用: {exc}")

    updated = await repo.update(cfg_id, fields)
    await _resync()
    return _mask(updated) if updated else None


@router.delete("/{cfg_id:path}")
async def delete_connector_cfg(cfg_id: str):
    cfg = await repo.get(cfg_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="连接器不存在")
    ref = cfg.get("apiKeyRef")
    if ref:
        await keychain.delete(ref)
    await repo.delete(cfg_id)
    await _resync()
    return {"ok": True}


@router.post("/{cfg_id:path}/test")
async def test_connector(cfg_id: str):
    cfg = await repo.get(cfg_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="连接器不存在")
    api_key = await _resolve_api_key(cfg)
    connector = create_connector({**cfg, "api_key": api_key})
    result = await connector.test()
    await repo.save_health(cfg_id, bool(result.get("ok")))
    return result


class InvokeRequest(BaseModel):
    operation: str
    params: dict = {}


@router.post("/{cfg_id:path}/invoke")
async def invoke_connector_cfg(cfg_id: str, body: InvokeRequest, request: Request):
    """按实例调用连接器操作：审计 + 身份透传（X-User-*）。"""
    from ..auth import parse_identity_headers
    cfg = await repo.get(cfg_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="连接器不存在")
    if not cfg.get("enabled"):
        raise HTTPException(status_code=403, detail="连接器已停用")
    api_key = await _resolve_api_key(cfg)
    connector = create_connector({**cfg, "api_key": api_key})
    identity = parse_identity_headers(request.headers)
    return await connector.call(body.operation, body.params or {}, identity=identity)


async def _resync() -> None:
    """连接器配置变更后：重建注册表 + 同步 tool_registry 条目状态。"""
    await sync_connector_registry()
    from ..storage.extensions_schema import sync_connector_instance_tools
    await sync_connector_instance_tools()

"""数据库只读连接管理路由（需求 §2.2 数据库只读直连）。

接口：
  GET    /api/db-connectors             — 连接列表
  POST   /api/db-connectors             — 新增连接
  PUT    /api/db-connectors/{id}        — 编辑连接
  DELETE /api/db-connectors/{id}        — 删除连接
  POST   /api/db-connectors/{id}/test   — 连通性测试（只读打开 + SELECT 1）
  POST   /api/db-connectors/{id}/query  — 只读查询（强制 SELECT/WITH/EXPLAIN）
"""
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..connectors import (
    ReadOnlyViolation,
    query_db,
    test_postgres,
    test_sqlite,
    validate_read_only_sql,
)
from ..storage import db_connector_repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/db-connectors", tags=["db-connectors"])
repo = db_connector_repo

VALID_DB_TYPES = {"sqlite", "postgres"}  # mysql 预留


class DbConnCreate(BaseModel):
    id: str
    name: str
    db_type: str = "sqlite"
    dsn: str
    enabled: bool = True
    max_rows: int = 100
    timeout_sec: int = 10


class DbConnUpdate(BaseModel):
    name: Optional[str] = None
    db_type: Optional[str] = None
    dsn: Optional[str] = None
    enabled: Optional[bool] = None
    max_rows: Optional[int] = None
    timeout_sec: Optional[int] = None


@router.get("")
async def list_connectors():
    return await repo.list_all()


@router.post("")
async def create_connector(body: DbConnCreate):
    if not body.id.strip() or not body.name.strip() or not body.dsn.strip():
        raise HTTPException(status_code=400, detail="id/name/dsn 不能为空")
    if body.db_type not in VALID_DB_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"db_type 仅支持 sqlite / postgres（已实现）；mysql 预留",
        )
    if await repo.get(body.id):
        raise HTTPException(status_code=409, detail=f"连接 {body.id} 已存在")
    conn = await repo.create({
        "id": body.id.strip(),
        "name": body.name.strip(),
        "db_type": body.db_type,
        "dsn": body.dsn.strip(),
        "enabled": body.enabled,
        "max_rows": body.max_rows,
        "timeout_sec": body.timeout_sec,
    })
    return conn


@router.put("/{conn_id:path}")
async def update_connector(conn_id: str, body: DbConnUpdate):
    conn = await repo.get(conn_id)
    if not conn:
        raise HTTPException(status_code=404, detail="连接不存在")
    fields: dict[str, Any] = {}
    for key, attr in (("name", "name"), ("db_type", "db_type"), ("dsn", "dsn"),
                      ("enabled", "enabled"), ("max_rows", "max_rows"),
                      ("timeout_sec", "timeout_sec")):
        value = getattr(body, key)
        if value is not None:
            fields[attr] = value
    if "db_type" in fields and fields["db_type"] not in VALID_DB_TYPES:
        raise HTTPException(status_code=400, detail="db_type 仅支持 sqlite / postgres（已实现）")
    updated = await repo.update(conn_id, fields)
    return updated


@router.delete("/{conn_id:path}")
async def delete_connector(conn_id: str):
    conn = await repo.get(conn_id)
    if not conn:
        raise HTTPException(status_code=404, detail="连接不存在")
    await repo.delete(conn_id)
    return {"ok": True}


@router.post("/{conn_id:path}/test")
async def test_connector(conn_id: str):
    conn = await repo.get(conn_id)
    if not conn:
        raise HTTPException(status_code=404, detail="连接不存在")
    if conn["dbType"] == "postgres":
        return await test_postgres(conn["dsn"], timeout_sec=conn.get("timeoutSec", 10))
    if conn["dbType"] != "sqlite":
        return {"ok": False, "error": f"{conn['dbType']} 适配器待接入"}
    return await test_sqlite(conn["dsn"], timeout_sec=conn.get("timeoutSec", 10))


class QueryRequest(BaseModel):
    sql: str


@router.post("/{conn_id:path}/query")
async def query_connector(conn_id: str, body: QueryRequest):
    conn = await repo.get(conn_id)
    if not conn:
        raise HTTPException(status_code=404, detail="连接不存在")
    if not conn.get("enabled"):
        raise HTTPException(status_code=403, detail="连接已停用")
    try:
        validate_read_only_sql(body.sql)
    except ReadOnlyViolation as exc:
        raise HTTPException(status_code=400, detail=f"只读校验拒绝: {exc}")
    return await query_db(conn, body.sql)

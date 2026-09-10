"""模型服务器连接管理路由（局域网/互联网大模型服务器）。

接口：
  GET    /api/llm-servers                 — 连接列表（不含密钥）
  POST   /api/llm-servers                 — 新增连接（密钥入 keychain）
  PUT    /api/llm-servers/{id}            — 编辑连接
  DELETE /api/llm-servers/{id}            — 删除连接（同时删 keychain 密钥）
  POST   /api/llm-servers/{id}/test       — 连通性测试
  POST   /api/llm-servers/{id}/sync-models— 拉取并缓存模型列表
  GET    /api/llm-servers/{id}/models     — 返回已缓存模型列表

协议说明：Ollama 与 OpenAI 兼容服务统一走 OpenAI 兼容端点（/v1/models、
/v1/chat/completions）；protocol=ollama 时自动补 /v1。
"""

import json
import logging
import time
from urllib.parse import unquote

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Optional

from ..security import keychain
from ..storage.llm_servers import LlmServerRepo

logger = logging.getLogger(__name__)


def _decode_server_id(server_id: str) -> str:
    """server_id 可含斜杠（如 z-ai/glm-5.3-free）。

    `{server_id:path}` 对 URL 编码的 %2F 在不同 ASGI 实现下解码行为不一，
    统一 unquote 兜底，保证删除/测试/同步等按原始 id 命中。
    """
    try:
        return unquote(server_id)
    except Exception:  # noqa: BLE001
        return server_id


router = APIRouter(prefix="/api/llm-servers", tags=["llm-servers"])
repo = LlmServerRepo()

_PLACEHOLDER = "**stored**"
# 连通性/模型探测统一超时（秒）
# 实测 agnes 网关 /v1/models 响应 2.3–5.1s，5s 阈值会把正常服务误判为超时，
# 放宽到 10s 避免健康巡检误报。
PROBE_TIMEOUT = 10.0


def normalize_v1_url(base_url: str) -> str:
    """规范化到 OpenAI 兼容 /v1 端点。"""
    url = base_url.rstrip("/")
    # 去除重复的 /v1（如 .../v1/v1 → .../v1），兼容历史脏数据
    while url.endswith("/v1/v1"):
        url = url[:-3]
    if url.endswith("/v1"):
        return url
    if "/v1/" in url:
        url = url.split("/v1/")[0] + "/v1"
    elif url.endswith("/api"):
        url = url[:-4]
    return url + "/v1"


def _mask(server: dict) -> dict:
    """对外隐藏密钥引用细节，仅标记是否有密钥。"""
    out = {k: v for k, v in server.items() if k != "api_key_ref"}
    out["has_api_key"] = bool(server.get("api_key_ref"))
    return out


def _filter_by_whitelist(models: list[str], allowed: list[str]) -> list[str]:
    """按白名单过滤模型；白名单为空时不限制。

    大小写不敏感：用户填 GLM-4.7 也能命中真实 id glm-4.7。
    """
    if not allowed:
        return models
    allowed_set = {m.strip().lower() for m in allowed if m.strip()}
    return [m for m in models if m.lower() in allowed_set]


async def _probe_models(
    base_url: str,
    api_key: str = "",
    allowed_models: list[str] = [],
    timeout: float = PROBE_TIMEOUT,
) -> list[str]:
    """向 /v1/models 探测模型列表（绕过环境代理，避免企业代理干扰内网）。"""
    url = normalize_v1_url(base_url).rstrip("/") + "/models"
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(
        trust_env=False, timeout=timeout, headers=headers
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        models = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
        return _filter_by_whitelist(models, allowed_models)


class ServerCreate(BaseModel):
    id: str
    name: str
    base_url: str
    protocol: str = "ollama"
    api_key: str = ""
    timeout_sec: int = 60
    enabled: bool = True
    allowed_models: list[str] = []


class ServerUpdate(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    protocol: Optional[str] = None
    api_key: Optional[str] = None
    timeout_sec: Optional[int] = None
    enabled: Optional[bool] = None
    allowed_models: Optional[list[str]] = None


@router.get("")
async def list_servers():
    servers = await repo.list_all()
    return [_mask(s) for s in servers]


@router.post("")
async def create_server(body: ServerCreate):
    if not body.id.strip() or not body.name.strip() or not body.base_url.strip():
        raise HTTPException(status_code=400, detail="id/name/base_url 不能为空")
    if body.protocol not in ("ollama", "openai"):
        raise HTTPException(status_code=400, detail="protocol 仅支持 ollama / openai")
    existing = await repo.get(body.id)
    if existing:
        raise HTTPException(status_code=409, detail=f"连接 {body.id} 已存在")

    api_key_ref = None
    if body.api_key and body.api_key != _PLACEHOLDER:
        ref = f"llm-server:{body.id}"
        try:
            await keychain.store(ref, body.api_key)
            api_key_ref = ref
        except Exception as exc:
            logger.warning(f"[llm-servers] keychain 不可用，明文存储（不推荐）: {exc}")
            api_key_ref = None

    server = await repo.create(
        {
            "id": body.id,
            "name": body.name,
            "base_url": normalize_v1_url(body.base_url),
            "protocol": body.protocol,
            "api_key_ref": api_key_ref,
            "timeout_sec": body.timeout_sec,
            "enabled": body.enabled,
            "allowed_models": body.allowed_models or [],
        }
    )
    return _mask(server)


@router.put("/{server_id:path}")
async def update_server(server_id: str, body: ServerUpdate):
    server_id = _decode_server_id(server_id)
    server = await repo.get(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="连接不存在")

    fields: dict[str, Any] = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.base_url is not None:
        fields["base_url"] = normalize_v1_url(body.base_url)
    if body.protocol is not None:
        if body.protocol not in ("ollama", "openai"):
            raise HTTPException(
                status_code=400, detail="protocol 仅支持 ollama / openai"
            )
        fields["protocol"] = body.protocol
    if body.timeout_sec is not None:
        fields["timeout_sec"] = body.timeout_sec
    if body.enabled is not None:
        fields["enabled"] = body.enabled
    if body.allowed_models is not None:
        fields["allowed_models"] = body.allowed_models
    if body.api_key is not None and body.api_key and body.api_key != _PLACEHOLDER:
        ref = f"llm-server:{server_id}"
        try:
            await keychain.store(ref, body.api_key)
            fields["api_key_ref"] = ref
        except Exception as exc:
            logger.warning(f"[llm-servers] keychain 不可用: {exc}")

    updated = await repo.update(server_id, fields)
    return _mask(updated) if updated else None


@router.delete("/{server_id:path}")
async def delete_server(server_id: str):
    # 兼容两种编码：`{server_id:path}` 收到 %2F 时可能保留编码形态，
    # 统一 unquote 兜底（server_id 本身允许含斜杠，如 z-ai/glm-5.3-free）
    server_id = _decode_server_id(server_id)
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
    server_id = _decode_server_id(server_id)
    server = await repo.get(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="连接不存在")
    api_key = ""
    ref = server.get("api_key_ref")
    if ref:
        api_key = keychain.retrieve_sync(ref) or ""
    started = time.time()
    try:
        models = await _probe_models(
            server["base_url"],
            api_key=api_key,
            allowed_models=server.get("allowed_models", []),
            timeout=PROBE_TIMEOUT,
        )
        latency_ms = int((time.time() - started) * 1000)
        await repo.save_health(server_id, True)
        return {
            "ok": True,
            "latency_ms": latency_ms,
            "model_count": len(models),
            "models": models[:20],
        }
    except Exception as exc:
        await repo.save_health(server_id, False)
        return {"ok": False, "error": str(exc)}


@router.post("/{server_id:path}/sync-models")
async def sync_models(server_id: str):
    server_id = _decode_server_id(server_id)
    server = await repo.get(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="连接不存在")
    api_key = ""
    ref = server.get("api_key_ref")
    if ref:
        api_key = keychain.retrieve_sync(ref) or ""
    try:
        models = await _probe_models(
            server["base_url"],
            api_key=api_key,
            allowed_models=server.get("allowed_models", []),
            timeout=PROBE_TIMEOUT,
        )
        await repo.save_models_cache(server_id, models)
        await repo.save_health(server_id, True)
        return {"ok": True, "models": models, "count": len(models)}
    except httpx.HTTPStatusError as exc:
        await repo.save_health(server_id, False)
        status = exc.response.status_code
        if status == 404:
            detail = "该服务不支持 /v1/models 模型列表接口，请手动在「允许模型」字段填入模型名称白名单"
        else:
            detail = f"同步模型列表失败 (HTTP {status}): {exc}"
        raise HTTPException(status_code=502, detail=detail)
    except Exception as exc:
        await repo.save_health(server_id, False)
        raise HTTPException(status_code=502, detail=f"同步模型列表失败: {exc}")


@router.get("/{server_id:path}/models")
async def get_models(server_id: str):
    server_id = _decode_server_id(server_id)
    server = await repo.get(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="连接不存在")
    return {"server_id": server_id, "models": server.get("models_cache") or []}


@router.get("/{server_id:path}/api-key")
async def get_api_key(server_id: str):
    """返回真实 API Key（编辑时回填用；仅本地回环 + Token 鉴权可访问）。"""
    server_id = _decode_server_id(server_id)
    server = await repo.get(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="连接不存在")
    ref = server.get("api_key_ref")
    api_key = ""
    if ref:
        api_key = keychain.retrieve_sync(ref) or ""
    return {"server_id": server_id, "api_key": api_key, "has_api_key": bool(api_key)}

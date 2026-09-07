"""企业身份与 SSO 路由（需求 §2.3 统一认证与权限）。

接口：
  GET    /api/enterprise/users            — 企业账号列表
  POST   /api/enterprise/users            — 新增/编辑企业账号
  DELETE /api/enterprise/users/{id:path}       — 删除企业账号
  GET    /api/enterprise/me?username=     — 查询身份（用于前端展示当前身份）
  GET    /api/enterprise/sso/providers    — SSO 提供方列表（含协议）
  PUT    /api/enterprise/sso/providers    — 配置 SSO 提供方（密钥入 keychain）
  DELETE /api/enterprise/sso/providers/{name:path} — 移除 SSO 配置
  GET    /api/enterprise/sso/{provider:path}/login     — 返回授权跳转 URL（state 生成）
  GET    /api/enterprise/sso/{provider:path}/callback  — code 换身份并写入企业账号
"""
import json
import logging
import secrets
import uuid
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..auth import (
    identity_from_enterprise_user,
    get_sso_registry,
    configure_sso_registry,
)
from ..security import keychain
from ..storage import enterprise_users_repo

router = APIRouter(prefix="/api/enterprise", tags=["enterprise"])
logger = logging.getLogger(__name__)

SSO_CONFIG_KEY = "sso_providers"


def _load_sso_providers() -> Dict[str, Dict[str, Any]]:
    from ..api.settings import _load_settings
    return _load_settings().get(SSO_CONFIG_KEY) or {}


def _save_sso_providers(providers: Dict[str, Dict[str, Any]]) -> None:
    from ..api.settings import _load_settings, _save_settings
    settings = _load_settings()
    settings[SSO_CONFIG_KEY] = providers
    _save_settings(settings)


# ---------- 企业账号 ----------

class UserIn(BaseModel):
    username: str
    display_name: str = ""
    role: str = "member"
    data_scope: str = "personal"
    department: str = ""


@router.get("/users")
async def list_users():
    return await enterprise_users_repo.list_all()


@router.post("/users")
async def upsert_user(u: UserIn):
    """按 username 幂等 upsert：已存在则更新角色/范围/部门，不存在则创建。"""
    existing = await enterprise_users_repo.get_by_username(u.username)
    user_id = existing["id"] if existing else str(uuid.uuid4())
    return await enterprise_users_repo.upsert(
        user_id=user_id,
        username=u.username,
        display_name=u.display_name,
        role=u.role,
        data_scope=u.data_scope,
        department=u.department,
        sso_provider=(existing or {}).get("ssoProvider") or "manual",
    )


@router.delete("/users/{user_id:path}")
async def delete_user(user_id: str):
    await enterprise_users_repo.remove(user_id)
    return {"ok": True}


@router.get("/me")
async def get_me(username: str = Query(default="")):
    """查询当前身份：传 username 返回该企业账号，否则返回默认本地身份。"""
    if username:
        user = await enterprise_users_repo.get_by_username(username)
        if not user:
            raise HTTPException(status_code=404, detail="enterprise_user_not_found")
        return identity_from_enterprise_user(user).to_dict()
    return {"user_id": "local", "username": "local", "role": "member",
            "data_scope": "personal", "is_enterprise": False}


# ---------- SSO 配置与登录 ----------

class SSOProviderIn(BaseModel):
    name: str
    protocol: str = "oidc"
    issuer: str = ""
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    authorize_endpoint: str = ""
    token_endpoint: str = ""
    userinfo_endpoint: str = ""
    scope: str = "openid profile email"


@router.get("/sso/providers")
async def list_sso_providers():
    providers = _load_sso_providers()
    # 只回显非敏感配置 + 是否有密钥
    safe = {}
    for name, cfg in providers.items():
        safe[name] = {k: v for k, v in cfg.items() if k != "client_secret"}
        safe[name]["hasSecret"] = bool(cfg.get("client_secret_ref"))
    return safe


@router.put("/sso/providers")
async def upsert_sso_provider(p: SSOProviderIn):
    providers = _load_sso_providers()
    secret_ref = providers.get(p.name, {}).get("client_secret_ref")
    if p.client_secret:
        secret_ref = f"sso:{p.name}"
        await keychain.store(secret_ref, p.client_secret)
    cfg = {
        "protocol": p.protocol,
        "issuer": p.issuer,
        "client_id": p.client_id,
        "client_secret_ref": secret_ref,
        "redirect_uri": p.redirect_uri,
        "authorize_endpoint": p.authorize_endpoint,
        "token_endpoint": p.token_endpoint,
        "userinfo_endpoint": p.userinfo_endpoint,
        "scope": p.scope,
    }
    providers[p.name] = cfg
    _save_sso_providers(providers)
    # 重建注册表
    configure_sso_registry(_providers_for_registry(providers))
    return {"ok": True, "name": p.name}


@router.delete("/sso/providers/{name:path}")
async def delete_sso_provider(name: str):
    providers = _load_sso_providers()
    if name not in providers:
        raise HTTPException(status_code=404, detail="sso_provider_not_found")
    providers.pop(name)
    _save_sso_providers(providers)
    configure_sso_registry(_providers_for_registry(providers))
    return {"ok": True}


def _providers_for_registry(providers: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """把存储的 SSO 配置转换为 provider 可读格式（注入解密后的 client_secret）。"""
    out = {}
    for name, cfg in providers.items():
        item = {k: v for k, v in cfg.items()}
        ref = cfg.get("client_secret_ref")
        if ref:
            try:
                item["client_secret"] = keychain.retrieve_sync(ref) or ""
            except Exception:
                item["client_secret"] = ""
        out[name] = item
    return out


@router.get("/sso/{provider:path}/login")
async def sso_login(provider: str):
    providers = _load_sso_providers()
    if provider not in providers:
        raise HTTPException(status_code=404, detail="sso_provider_not_found")
    reg = get_sso_registry()
    if not reg.get(provider):
        configure_sso_registry(_providers_for_registry(providers))
    p = reg.get(provider)
    if p is None:
        raise HTTPException(status_code=404, detail="sso_provider_not_loaded")
    state = secrets.token_urlsafe(16)
    try:
        url = await p.get_authorization_url(state)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc))
    return {"authorization_url": url, "state": state}


@router.get("/sso/{provider:path}/callback")
async def sso_callback(provider: str, code: str, state: str = ""):
    providers = _load_sso_providers()
    if provider not in providers:
        raise HTTPException(status_code=404, detail="sso_provider_not_found")
    reg = get_sso_registry()
    p = reg.get(provider) or (configure_sso_registry(
        _providers_for_registry(providers)).get(provider))
    if p is None:
        raise HTTPException(status_code=404, detail="sso_provider_not_loaded")
    try:
        tokens = await p.exchange_code(code)
        access_token = tokens.get("access_token", "")
        if not access_token:
            raise RuntimeError("access_token_missing")
        userinfo = await p.get_userinfo(access_token)
        mapped = p.map_userinfo(userinfo)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc))
    except Exception as exc:
        logger.warning("[auth] SSO 回调失败: %s", exc)
        raise HTTPException(status_code=502, detail=f"sso_callback_failed: {exc}")

    username = mapped["username"]
    existing = await enterprise_users_repo.get_by_username(username)
    user_id = existing["id"] if existing else str(uuid.uuid4())
    await enterprise_users_repo.upsert(
        user_id=user_id,
        username=username,
        display_name=mapped["display_name"],
        department=mapped["department"],
        role=existing["role"] if existing else "member",
        data_scope=existing["dataScope"] if existing else "personal",
        sso_provider=provider,
        sso_subject=mapped.get("sso_subject"),
    )
    user = await enterprise_users_repo.get(user_id)
    return {"identity": identity_from_enterprise_user(user).to_dict(),
            "token": json.dumps({"sub": username, "provider": provider})}

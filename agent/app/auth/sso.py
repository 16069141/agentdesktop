"""SSO 适配层（需求 §2.3：支持 CAS / LDAP / OAuth2 / OIDC 协议）。

P0 实现：
- OIDC/OAuth2：授权码流程（authorization endpoint → token exchange → userinfo），
  基于 HTTPS/TLS 信任 token endpoint，不本地验签（部署在可信内网通道）。
- CAS / LDAP：预留适配位 —— 配置可保存，选中时返回「适配器待接入」明确提示
  （与知识库连接器未实现平台的处理方式一致）。

企业侧信息（issuer / client_id / client_secret / userinfo 字段映射）由用户在
设置中配置，P0 提供配置接口与流程骨架。
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class SSOProvider:
    """SSO 提供方基类。"""

    protocol = "base"

    def __init__(self, config: Dict[str, Any]):
        self.config = config or {}
        self.name = config.get("name", "sso")

    async def get_authorization_url(self, state: str) -> str:
        raise NotImplementedError

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        raise NotImplementedError

    async def get_userinfo(self, access_token: str) -> Dict[str, Any]:
        raise NotImplementedError

    def map_userinfo(self, userinfo: Dict[str, Any]) -> Dict[str, Any]:
        """把平台用户信息映射为 enterprise_user 字段。"""
        return {
            "username": userinfo.get("preferred_username") or userinfo.get("email") or "sso_user",
            "display_name": userinfo.get("name") or userinfo.get("nickname") or "",
            "department": userinfo.get("department") or "",
            "sso_subject": userinfo.get("sub") or userinfo.get("id") or "",
        }


class OIDCProvider(SSOProvider):
    """OIDC / OAuth2 授权码流程实现。"""

    protocol = "oidc"

    async def _get_discovery(self) -> Dict[str, Any]:
        issuer = (self.config.get("issuer") or "").rstrip("/")
        discovery_url = issuer + "/.well-known/openid-configuration"
        with urllib.request.urlopen(discovery_url, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    async def get_authorization_url(self, state: str) -> str:
        issuer = (self.config.get("issuer") or "").rstrip("/")
        client_id = self.config.get("client_id", "")
        redirect_uri = self.config.get("redirect_uri", "")
        # 支持直接配置 authorize_endpoint（跳过 discovery）
        auth_ep = self.config.get("authorize_endpoint") or (
            issuer + "/protocol/openid-connect/auth"
        )
        params = urllib.parse.urlencode({
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": self.config.get("scope", "openid profile email"),
            "state": state,
        })
        return f"{auth_ep}?{params}"

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        token_ep = self.config.get("token_endpoint")
        if not token_ep:
            token_ep = (await self._get_discovery()).get("token_endpoint") or ""
        if not token_ep:
            raise RuntimeError("oidc_token_endpoint_missing")
        payload = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.config.get("redirect_uri", ""),
            "client_id": self.config.get("client_id", ""),
            "client_secret": self.config.get("client_secret", ""),
        }).encode("utf-8")
        req = urllib.request.Request(
            token_ep, data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    async def get_userinfo(self, access_token: str) -> Dict[str, Any]:
        userinfo_ep = self.config.get("userinfo_endpoint")
        if not userinfo_ep:
            userinfo_ep = (await self._get_discovery()).get("userinfo_endpoint") or ""
        if not userinfo_ep:
            raise RuntimeError("oidc_userinfo_endpoint_missing")
        req = urllib.request.Request(
            userinfo_ep, headers={"Authorization": f"Bearer {access_token}"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))


class CASProvider(SSOProvider):
    """CAS 适配位（预留）。"""

    protocol = "cas"

    async def get_authorization_url(self, state: str) -> str:
        raise NotImplementedError("CAS 适配器待接入：请配置 OIDC/OAuth2 或等待后续版本")

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        raise NotImplementedError("CAS 适配器待接入")


class LDAPProvider(SSOProvider):
    """LDAP 适配位（预留）。"""

    protocol = "ldap"

    async def get_authorization_url(self, state: str) -> str:
        raise NotImplementedError("LDAP 适配器待接入")

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        raise NotImplementedError("LDAP 适配器待接入")


class SSORegistry:
    """SSO 提供方注册表：按名称解析 provider，未配置时返回 None。"""

    _PROVIDER_CLASSES = {
        "oidc": OIDCProvider,
        "cas": CASProvider,
        "ldap": LDAPProvider,
    }

    def __init__(self, providers: Optional[Dict[str, Dict[str, Any]]] = None):
        self._providers: Dict[str, SSOProvider] = {}
        for name, cfg in (providers or {}).items():
            self._register(name, cfg)

    def _register(self, name: str, cfg: Dict[str, Any]) -> None:
        protocol = (cfg.get("protocol") or "oidc").lower()
        cls = self._PROVIDER_CLASSES.get(protocol)
        if cls is None:
            logger.warning("[auth] 未知 SSO 协议 %s，跳过 %s", protocol, name)
            return
        merged = {"name": name, **cfg}
        self._providers[name] = cls(merged)

    def get(self, name: str | None) -> Optional[SSOProvider]:
        if not name:
            return None
        return self._providers.get(name)

    def list(self) -> list[Dict[str, Any]]:
        return [
            {"name": name, "protocol": p.protocol, "configured": True}
            for name, p in self._providers.items()
        ]

    def upsert(self, name: str, cfg: Dict[str, Any]) -> None:
        self._register(name, cfg)

    def remove(self, name: str) -> None:
        self._providers.pop(name, None)


# 全局注册表（由 settings 配置驱动，main lifespan 或首用惰性加载）
_registry: Optional[SSORegistry] = None


def get_sso_registry() -> SSORegistry:
    global _registry
    if _registry is None:
        _registry = SSORegistry()
    return _registry


def configure_sso_registry(providers: Dict[str, Dict[str, Any]]) -> SSORegistry:
    global _registry
    _registry = SSORegistry(providers)
    return _registry

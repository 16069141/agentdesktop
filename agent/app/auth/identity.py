"""企业身份模型与权限透传。

身份来源：
1. 企业 SSO 登录后写入 enterprise_users 表；
2. 连接器 / 上层调用方通过 `X-User-Id` / `X-User-Name` / `X-User-Role`
   等请求头透传当前用户身份（权限透传，需求 §2.3）。

任何进入 Agent 的调用（chat / tool / connector）都应先解析出 IdentityContext，
用于：审计记录（Who）、数据范围过滤（看得到什么）、操作授权（能做什么）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

# 身份透传请求头（连接器 / MCP 调用时由调用方携带）
X_USER_ID = "X-User-Id"
X_USER_NAME = "X-User-Name"
X_USER_ROLE = "X-User-Role"
X_USER_DEPT = "X-User-Department"
X_USER_SCOPE = "X-User-Data-Scope"

DEFAULT_IDENTITY = "local"


@dataclass
class IdentityContext:
    """一次调用链上的用户身份上下文。"""

    user_id: str = "local"
    username: str = "local"
    role: str = "member"          # admin / manager / member
    data_scope: str = "personal"  # personal / department / global
    department: str = ""
    sso_provider: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_enterprise(self) -> bool:
        """是否为企业身份（区别于本地桌面身份）。"""
        return self.user_id != "local"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "role": self.role,
            "data_scope": self.data_scope,
            "department": self.department,
            "sso_provider": self.sso_provider,
            "is_enterprise": self.is_enterprise,
        }

    def display(self) -> str:
        return f"{self.username}@{self.role}/{self.data_scope}"


def parse_identity_headers(headers: Any) -> IdentityContext:
    """从请求头解析身份上下文（缺失时回退为本地身份）。

    本地桌面（无 X-User-* 透传头）= 本机所有者：授予 admin 级读写权限，
    允许 filesystem 写入自己的文件（如 Agent 生成 HTML 成品落盘）。
    只有企业 SSO / 连接器显式透传身份时才按角色限制操作。
    """
    def _h(name: str, default: str = "") -> str:
        try:
            return (headers.get(name) or "").strip() or default
        except Exception:
            return default

    uid = _h(X_USER_ID, "local")
    username = _h(X_USER_NAME, uid)
    is_local = uid == "local"
    return IdentityContext(
        user_id=uid,
        username=username,
        role=_h(X_USER_ROLE, "admin" if is_local else "member"),
        data_scope=_h(X_USER_SCOPE, "global" if is_local else "personal"),
        department=_h(X_USER_DEPT, ""),
    )


def identity_from_enterprise_user(user: Dict[str, Any]) -> IdentityContext:
    """从 enterprise_users 行构造身份上下文。"""
    return IdentityContext(
        user_id=user["id"],
        username=user["username"],
        role=user["role"],
        data_scope=user["dataScope"],
        department=user.get("department", ""),
        sso_provider=user.get("ssoProvider"),
    )

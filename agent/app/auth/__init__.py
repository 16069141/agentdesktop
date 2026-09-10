"""统一认证与权限模块（需求 §2.3）。

- SSO 适配层：OIDC/OAuth2（已实现），CAS/LDAP（预留适配位）
- 企业身份模型：enterprise_users 表 + X-User-* 身份透传
- 权限矩阵：数据范围（个人/部门/全局）× 操作分级（查询/创建/修改/删除）
- 高风险操作：requires_approval 工具走通用审批确认
"""
from .identity import (
    IdentityContext,
    parse_identity_headers,
    identity_from_enterprise_user,
)
from .permissions import (
    ROLE_DEFAULTS,
    check_operation_allowed,
    data_scope_visible,
    resolve_permission_for_tool,
)
from .sso import (
    SSOProvider,
    SSORegistry,
    OIDCProvider,
    CASProvider,
    LDAPProvider,
    get_sso_registry,
    configure_sso_registry,
)

__all__ = [
    "IdentityContext",
    "parse_identity_headers",
    "identity_from_enterprise_user",
    "ROLE_DEFAULTS",
    "check_operation_allowed",
    "data_scope_visible",
    "resolve_permission_for_tool",
    "SSOProvider",
    "SSORegistry",
    "OIDCProvider",
    "CASProvider",
    "LDAPProvider",
    "get_sso_registry",
    "configure_sso_registry",
]

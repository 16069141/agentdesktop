"""权限矩阵：数据范围 × 操作分级（需求 §2.3）。

角色默认值：
- admin   → 数据范围 global，全部操作
- manager → 数据范围 department，查询/创建/修改（删除需审批）
- member  → 数据范围 personal，仅查询

操作分级：read / create / update / delete。
高风险操作（delete、审批类、写生产数据）需额外审批流 —— 由工具级
requires_approval 或 `high_risk_operations` 命中规则触发。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# 角色 → (数据范围, 允许操作集合)
ROLE_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "admin": {"data_scope": "global", "operations": {"read", "create", "update", "delete"}},
    "manager": {"data_scope": "department", "operations": {"read", "create", "update"}},
    "member": {"data_scope": "personal", "operations": {"read"}},
}

# 数据范围可见性：用户能看到的范围是否覆盖目标范围
_SCOPE_RANK = {"personal": 0, "department": 1, "global": 2}

# 高风险操作关键字（命中即需二次确认 / 审批）
HIGH_RISK_ACTIONS = {"delete", "remove", "drop", "truncate", "approve", "pay", "transfer"}

# 工具 → 操作类型映射（用于操作分级授权判定；未列出的工具默认 read）
# 操作词表统一为 read/create/update/delete
TOOL_OPERATION = {
    "filesystem": {"read": {"read", "list", "search"}, "update": {"write"}},
    "shell": {"delete": {"rm", "del", "remove"}},
    "knowledge": {"read": set()},
    "code": {"read": set()},
}


def check_operation_allowed(identity: Any, tool_name: str, arguments: Dict[str, Any]) -> tuple[bool, str]:
    """判定某身份是否被允许以指定参数调用工具。

    返回 (allowed, reason)。拒绝原因用于审计与提示。
    规则：先查工具名级别操作（read 类人人可做），再按参数内容匹配高风险操作。
    """
    role = getattr(identity, "role", "member")
    allowed_ops = ROLE_DEFAULTS.get(role, ROLE_DEFAULTS["member"])["operations"]

    # 1) 工具级：操作分类
    op_map = TOOL_OPERATION.get(tool_name, {})
    # 默认：未声明操作映射的工具按 read 处理；shell 等系统级工具走工具自身审批
    op = "read"
    for op_kind, keywords in op_map.items():
        arg_text = str(arguments).lower()
        if any(k.lower() in arg_text for k in keywords):
            op = op_kind
            break

    if op not in allowed_ops:
        return False, f"role '{role}' 不允许执行 {op} 操作"

    # 2) 高风险操作：delete 类且角色非 admin → 必须走审批（requires_approval）
    if op == "delete" and role != "admin":
        return False, "高风险操作（delete）需管理员权限或审批流"

    # 3) 参数级高风险命中（任何角色）：如 rm/delete 等 —— 需审批
    arg_text = str(arguments).lower()
    for h in HIGH_RISK_ACTIONS:
        if h in arg_text.split():
            if role == "admin":
                break
            return False, f"参数命中高风险操作 '{h}'，需审批"

    return True, ""


def data_scope_visible(identity: Any, owner_scope: str = "global") -> bool:
    """数据范围可见性：用户数据范围是否覆盖目标数据范围。

    personal 用户只见 personal 数据；department 见 personal + department；
    global 见全部。
    """
    user_scope = getattr(identity, "data_scope", "personal")
    return _SCOPE_RANK.get(user_scope, 0) >= _SCOPE_RANK.get(owner_scope, 0)


def resolve_permission_for_tool(tool_meta: Dict[str, Any], identity: Any) -> Dict[str, Any]:
    """解析工具对当前身份的最终授权结论。

    供连接器 / 工具调度统一使用：返回 granted / requires_approval / denied + 原因。
    """
    role = getattr(identity, "role", "member")
    tool_requires_approval = bool(tool_meta.get("requiresApproval", False))
    perm_level = tool_meta.get("permissionLevel", "P2")

    # P4（系统调用级）无论角色如何都需要审批
    if perm_level == "P4":
        return {"granted": False, "requires_approval": True,
                "reason": "P4 系统调用级工具需二次确认", "permissionLevel": perm_level}

    if tool_requires_approval:
        return {"granted": False, "requires_approval": True,
                "reason": "工具声明 requires_approval", "permissionLevel": perm_level}

    # admin 放行一切；其余按角色默认
    if role == "admin":
        return {"granted": True, "requires_approval": False, "reason": "",
                "permissionLevel": perm_level}

    source = tool_meta.get("source", "")
    if source not in ("builtin", "trusted", "connector"):
        return {"granted": False, "requires_approval": False,
                "reason": f"来源未授权: {source}", "permissionLevel": perm_level}

    return {"granted": True, "requires_approval": False, "reason": "",
            "permissionLevel": perm_level}

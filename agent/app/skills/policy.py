"""Skill 四级安全策略（需求 §2.7 沙箱隔离策略：P1-P4）。

| 等级 | 文件系统 | 网络 | 系统调用 |
|---|---|---|---|
| P1 | 无（仅纯函数） | 禁止 | 禁止 |
| P2 | 受限（仅工作目录读写） | 禁止 | 禁止 |
| P3 | 只读（可读任意，不可写） | 受限白名单 | 禁止 |
| P4 | 受限 + 可写 | 允许 | 允许（需审批） |

策略由 manifest.security_level 声明；安装与运行前统一校验。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

VALID_LEVELS = {"P1", "P2", "P3", "P4"}

# 各等级允许的权限面
_LEVEL_POLICY: Dict[str, Dict[str, str]] = {
    "P1": {"filesystem": "none", "network": "none", "system_calls": "none"},
    "P2": {"filesystem": "workdir_rw", "network": "none", "system_calls": "none"},
    "P3": {"filesystem": "readonly", "network": "whitelist", "system_calls": "none"},
    "P4": {"filesystem": "workdir_rw", "network": "allowed", "system_calls": "approved"},
}


@dataclass
class PolicyVerdict:
    ok: bool
    errors: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok


def validate_level(level: str) -> PolicyVerdict:
    if level not in VALID_LEVELS:
        return PolicyVerdict(False, [f"安全等级必须为 P1-P4，收到: {level}"])
    return PolicyVerdict(True, [])


def validate_manifest_permissions(manifest: Dict[str, Any]) -> PolicyVerdict:
    """校验 manifest 声明权限是否与其 security_level 匹配。

    原则：声明权限不得超出等级允许面；超出即拒绝安装（安全优先）。
    """
    level = str(manifest.get("security_level", "P2"))
    v = validate_level(level)
    if not v.ok:
        return v

    allowed = _LEVEL_POLICY[level]
    declared = manifest.get("permissions", {}) or {}
    errors: List[str] = []

    for key in ("filesystem", "network", "system_calls"):
        declared_val = str(declared.get(key, "none")).lower()
        allowed_val = allowed[key]

        def _within(dv: str, av: str) -> bool:
            if dv == av:
                return True
            if dv in ("none", ""):
                return True  # 声明得更保守始终允许
            # workdir_rw ⊃ readonly；allowed ⊃ whitelist
            if av == "workdir_rw" and dv in ("readonly", "workdir_rw"):
                return True
            if av == "allowed" and dv in ("whitelist", "allowed"):
                return True
            return False

        if not _within(declared_val, allowed_val):
            errors.append(
                f"权限越界: {key}={declared_val} 超出 {level} 允许范围 {allowed_val}"
            )

    if errors:
        return PolicyVerdict(False, errors)
    return PolicyVerdict(True, [])


def effective_policy(level: str) -> Dict[str, str]:
    """返回某等级的完整策略面（未配置时给默认值）。"""
    return _LEVEL_POLICY.get(level, _LEVEL_POLICY["P2"])

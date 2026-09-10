"""RPA 模拟操作（需求 §2.2：适配无接口、无数据库权限的遗留系统）。

P1 骨架：Playwright 驱动预留。RPA 工具已注册，执行时返回「待接入 Playwright」
明确提示（与 browser 工具一致），但审计链路（record_tool_call）完整可用。
P2 落实 Playwright 执行器 + 页面操作审计。
"""
from __future__ import annotations

from typing import Any, Dict


async def rpa_execute(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """RPA 执行入口（占位）。action: open / click / input / extract。"""
    return {
        "success": False,
        "error": (
            "RPA 模拟操作适配器待接入：P1 已预留执行器骨架与审计链路，"
            "Playwright 驱动将在 P2 落实。当前请使用 API 直连或数据库只读方式接入。"
        ),
        "action": action,
        "params": params,
    }

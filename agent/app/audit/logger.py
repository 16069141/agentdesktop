"""审计服务（需求 §2.4）。

在工具执行链路上记录 Who / When / What / Params(脱敏) / Result(截断) / 会话，
追加只写（audit_logs 表无 UPDATE/DELETE 接口）。

统一入口：`record_tool_call(...)` —— 由 AgentOrchestrator.ToolNode 与连接器
调用层调用。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

from ..storage import audit_log_repo
from .redact import redact_json, redact_result, risk_level_for

logger = logging.getLogger(__name__)


async def record_tool_call(
    tool_name: str,
    arguments: Dict[str, Any],
    result: Any = None,
    conversation_id: str = "",
    session_id: str = "",
    actor: str = "local",
    action: str = "execute",
    approved: Optional[bool] = None,
    latency_ms: int = 0,
    error: Optional[str] = None,
) -> int:
    """记录一次工具调用的完整审计链路。

    - Params 落库前脱敏（redact_json）
    - Result 落库前脱敏 + 截断（redact_result）
    - 危险工具（shell / 写操作 / delete）标记 risk_level
    """
    params_redacted = redact_json(arguments)
    if error:
        result_preview = redact_result({"error": error})[:300]
    else:
        result_preview = redact_result(result)

    risk = risk_level_for(tool_name, arguments)
    if action in ("approve", "deny"):
        risk = "high"

    try:
        return await audit_log_repo.insert(
            conversation_id=conversation_id or "",
            session_id=session_id or "",
            actor=actor or "local",
            tool_name=tool_name,
            action=action,
            params_redacted=params_redacted,
            result_preview=result_preview,
            risk_level=risk,
            approved=approved,
            latency_ms=int(latency_ms),
        )
    except Exception as exc:
        # 审计失败不阻断业务（审计是旁路），但必须告警
        logger.error("[audit] 审计写入失败: %s", exc)
        return 0


async def record_approval(tool_name: str, arguments: Dict[str, Any],
                          approved: bool, conversation_id: str = "",
                          actor: str = "local", session_id: str = "") -> int:
    """记录一次审批决策（高风险操作二次确认链路）。"""
    return await record_tool_call(
        tool_name=tool_name,
        arguments=arguments,
        action="approve" if approved else "deny",
        approved=approved,
        conversation_id=conversation_id,
        actor=actor,
        session_id=session_id,
    )

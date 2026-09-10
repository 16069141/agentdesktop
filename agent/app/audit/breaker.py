"""熔断机制（需求 §2.4：异常高频调用自动熔断，防止 AI 误操作）。

实现：按 (工具名) 维护 60 秒滑动窗口调用计数；超过阈值（默认 60 次/分钟）
后熔断开启，后续调用直接拒绝并返回明确错误；窗口滑动后自动恢复。

设计为进程内单例（内存状态），附带查询接口供前端展示。
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD_PER_MIN = 60
WINDOW_SEC = 60


class CircuitBreaker:
    """按工具名的滑动窗口熔断器。"""

    def __init__(self, threshold: int = DEFAULT_THRESHOLD_PER_MIN):
        self.threshold = threshold
        self._calls: Dict[str, deque[float]] = defaultdict(deque)

    def check(self, tool_name: str) -> bool:
        """是否允许调用：True 放行，False 熔断。"""
        now = time.monotonic()
        q = self._calls[tool_name]
        # 清理窗口外的记录
        while q and now - q[0] > WINDOW_SEC:
            q.popleft()
        return len(q) < self.threshold

    def record(self, tool_name: str) -> None:
        """记录一次调用（无论成败）。"""
        now = time.monotonic()
        q = self._calls[tool_name]
        while q and now - q[0] > WINDOW_SEC:
            q.popleft()
        q.append(now)
        if len(q) >= self.threshold:
            logger.warning("[breaker] 工具 %s 调用频次达阈值 %d/分钟，触发熔断",
                           tool_name, self.threshold)

    def status(self) -> List[Dict[str, Any]]:
        """各工具熔断状态（供前端展示）。"""
        now = time.monotonic()
        out = []
        for tool_name, q in self._calls.items():
            while q and now - q[0] > WINDOW_SEC:
                q.popleft()
            out.append({
                "toolName": tool_name,
                "callsInWindow": len(q),
                "threshold": self.threshold,
                "windowSec": WINDOW_SEC,
                "open": len(q) >= self.threshold,
            })
        return sorted(out, key=lambda x: -x["callsInWindow"])

    def reset(self, tool_name: Optional[str] = None) -> None:
        if tool_name:
            self._calls.pop(tool_name, None)
        else:
            self._calls.clear()


# 全局单例
_breaker: Optional[CircuitBreaker] = None


def get_breaker() -> CircuitBreaker:
    global _breaker
    if _breaker is None:
        _breaker = CircuitBreaker()
    return _breaker


def check_and_record(tool_name: str) -> tuple[bool, str]:
    """工具执行前的统一熔断检查 + 记录。

    返回 (allowed, reason)。熔断开启时拒绝并给出明确提示。
    """
    breaker = get_breaker()
    if not breaker.check(tool_name):
        return False, (
            f"工具 '{tool_name}' 调用过于频繁，已触发熔断保护。"
            "请停止重复调用，或等待窗口恢复后再试。"
        )
    breaker.record(tool_name)
    return True, ""

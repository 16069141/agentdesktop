"""长期记忆（P1）：跨会话记住用户偏好 / 项目事实 / 重要结论。

- store：SQLite 存储 + FTS5 检索 + 近似去重
- extract：后台 LLM 从对话中提取候选记忆
- inject：对话开始时检索 top-k 注入上下文（orchestrator 调用）
"""
from . import store
from .extract import extract_from_pair

__all__ = ["store", "extract_from_pair"]


def memory_enabled(settings: dict | None = None) -> bool:
    """读取配置开关（默认开启；客户可在设置里关闭）。"""
    if settings is None:
        try:
            from ..api.settings import _load_settings
            settings = _load_settings()
        except Exception:  # noqa: BLE001
            settings = {}
    return bool(settings.get("memory_enabled", True))

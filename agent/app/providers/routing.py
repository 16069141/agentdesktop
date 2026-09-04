"""模型路由与 Provider 注册表（Phase 3）。

规格书 §9.6：
- classify_task：消息关键词粗分类（general/coding/rag）
- resolve_model：任务类型 → (model_id, provider_id)
- ProviderRegistry：Provider 注册与查询
"""
from __future__ import annotations

import logging
from typing import Optional

from .base import BaseProvider, ModelInfo

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """维护所有可用 Provider，支持按 kind/provider_id 查询。"""

    def __init__(self):
        self._providers: dict[str, BaseProvider] = {}

    def register(self, provider: BaseProvider):
        self._providers[provider.provider_id] = provider

    def get(self, provider_id: str) -> Optional[BaseProvider]:
        return self._providers.get(provider_id)

    def all(self) -> list[BaseProvider]:
        return list(self._providers.values())

    async def list_all_models(self) -> list[ModelInfo]:
        """合并所有 Provider 的可用模型。"""
        models: list[ModelInfo] = []
        for p in self._providers.values():
            try:
                models.extend(await p.list_models())
            except Exception as exc:
                logger.warning(f"[router] 获取模型失败 ({p.provider_id}): {exc}")
        return models

    async def health_check_all(self) -> dict[str, bool]:
        """对所有 Provider 执行健康检查。"""
        result = {}
        for pid, p in self._providers.items():
            result[pid] = await p.health_check()
            status = "healthy" if result[pid] else "unhealthy"
            logger.info(f"[router] {pid} 健康状态: {status}")
        return result


# 全局注册表（单例）
_registry: Optional[ProviderRegistry] = None


def get_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        from .base import build_providers
        _registry = ProviderRegistry()
        for p in build_providers():
            _registry.register(p)
    return _registry


def classify_task(message: str) -> str:
    """根据消息关键词粗粒度分类，用于模型路由。

    Returns:
        "coding" | "rag" | "general"
    """
    lower = message.lower()
    coding_keywords = [
        "代码", "编程", "函数", "bug", "报错", "语法", "实现",
        "python", "javascript", "typescript", "react", "vue",
        "写个", "帮我写", "分析这段代码", "重构",
    ]
    rag_keywords = [
        "文档", "知识库", "检索", "查询", "查找", "参考",
        "在文档中", "根据资料",
    ]
    for kw in coding_keywords:
        if kw in lower:
            return "coding"
    for kw in rag_keywords:
        if kw in lower:
            return "rag"
    return "general"


def resolve_model(
    registry: ProviderRegistry,
    task_type: str,
    user_preferred: Optional[str] = None,
) -> tuple[str, str]:
    """解析目标模型 ID 和 Provider ID。

    Args:
        task_type: "general" | "coding" | "rag"
        user_preferred: 用户显式选择的模型 ID（如 "deepseek-chat"），优先级最高

    Returns:
        (model_id, provider_id)
    """
    if user_preferred:
        for p in registry.all():
            models = [m.id for m in getattr(p, '_models', None) or []]
            if user_preferred in models or user_preferred == p.provider_id:
                return user_preferred, p.provider_id
        for p in registry.all():
            for m in getattr(p, '_models', None) or []:
                if m.id == user_preferred:
                    return user_preferred, p.provider_id
        logger.warning(f"[router] 未找到模型 {user_preferred}，回退到默认")

    # 实际安装的模型名称映射
    actual_models = {}
    for p in registry.all():
        # 安全访问 _models 属性
        models = getattr(p, '_models', None) or []
        for m in models:
            actual_models[m.id] = p.provider_id

    # 如果用户偏好的模型存在，使用它
    if user_preferred and user_preferred in actual_models:
        return user_preferred, actual_models[user_preferred]

    # 回退到默认路由（使用实际模型名称）
    routing = {
        "general": ("modelscope.cn/Qwen/Qwen2.5-7B-Instruct-GGUF:latest", "ollama"),
        "coding": ("modelscope.cn/Qwen/Qwen2.5-7B-Instruct-GGUF:latest", "ollama"),  # 暂时用同一个
        "rag": ("modelscope.cn/Qwen/Qwen2.5-7B-Instruct-GGUF:latest", "ollama"),     # 暂时用同一个
    }
    model_id, provider_id = routing.get(task_type, routing["general"])
    return model_id, provider_id

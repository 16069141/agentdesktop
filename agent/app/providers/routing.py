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

# Ollama 已安装模型的同步探测缓存：{"ids": [...], "ts": float}
_model_probe_cache: dict = {"ids": [], "ts": 0.0}
_MODEL_PROBE_TTL_SEC = 60.0


def _probe_ollama_models(base_url: str = "http://127.0.0.1:11434", timeout: float = 3.0) -> list[str]:
    """同步探测本地 Ollama 已安装的模型 id，带 60s 缓存。

    为什么需要它：Provider 的 `_models` 由异步 `list_models()` 懒加载，而
    `resolve_model()` 是同步函数，读不到。若不做探测，路由只能盲目相信硬编码
    的模型名，一旦本地没装该模型就会在真正推理时才报错。

    注意：必须绕过 HTTP(S)_PROXY —— 本机 127.0.0.1 的流量被企业代理拦截后会
    直接连接失败，这是本项目 Ollama「连不上」的最常见原因。
    """
    import json as _json
    import time as _time
    import urllib.request as _req

    now = _time.time()
    if _model_probe_cache["ids"] and now - _model_probe_cache["ts"] < _MODEL_PROBE_TTL_SEC:
        return _model_probe_cache["ids"]

    ids: list[str] = []
    try:
        opener = _req.build_opener(_req.ProxyHandler({}))  # 显式禁用代理
        with opener.open(f"{base_url.rstrip('/')}/v1/models", timeout=timeout) as resp:
            payload = _json.loads(resp.read().decode("utf-8"))
            ids = [m.get("id", "") for m in payload.get("data", []) if m.get("id")]
        _model_probe_cache["ids"] = ids
        _model_probe_cache["ts"] = now
    except Exception as exc:
        # 探测失败不阻断路由：回退到注册表里的模型，或直接使用首选候选
        logger.debug(f"[router] 探测 Ollama 模型列表失败: {exc}")
        if _model_probe_cache["ids"]:
            return _model_probe_cache["ids"]
        return []

    return ids


# 模型是否支持 function calling 的能力缓存：{model: (supported, ts)}
_tool_capability_cache: dict[str, tuple[bool, float]] = {}
_TOOL_CAPABILITY_TTL_SEC = 300.0


def _mark_model_tool_support(model: str, supported: bool) -> None:
    """记录模型的工具支持能力（含运行时探测到的结果）。"""
    import time as _time
    _tool_capability_cache[model] = (supported, _time.time())


def model_supports_tools(
    model: str,
    base_url: str = "http://127.0.0.1:11434",
    timeout: float = 5.0,
) -> bool:
    """查询模型是否支持 function calling（带 5 分钟缓存）。

    必要性：并非所有本地模型都支持 tools。例如 deepseek-coder:6.7b 的
    capabilities 只有 ["completion"]，一旦在请求里带上 tools，
    Ollama 会直接返回 400 "does not support tools"，整轮对话失败。
    因此必须先问能力，再决定要不要挂载工具。

    探测失败时保守返回 True：让请求照常发出，由运行时的 400 兜底降级。
    """
    import json as _json
    import time as _time
    import urllib.request as _req

    now = _time.time()
    cached = _tool_capability_cache.get(model)
    if cached and now - cached[1] < _TOOL_CAPABILITY_TTL_SEC:
        return cached[0]

    try:
        payload = _json.dumps({"model": model}).encode("utf-8")
        req = _req.Request(
            f"{base_url.rstrip('/')}/api/show",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        opener = _req.build_opener(_req.ProxyHandler({}))  # 绕过企业代理
        with opener.open(req, timeout=timeout) as resp:
            info = _json.loads(resp.read().decode("utf-8"))
        caps = info.get("capabilities") or []
        supported = "tools" in caps
        _mark_model_tool_support(model, supported)
        if not supported:
            logger.info(f"[router] 模型 {model} 不支持 tools（capabilities={caps}），将不挂载工具")
        return supported
    except Exception as exc:
        logger.debug(f"[router] 探测 {model} 工具能力失败，按支持处理: {exc}")
        return True


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
    require_tools: bool = False,
) -> tuple[str, str]:
    """解析目标模型 ID 和 Provider ID。

    Args:
        task_type: "general" | "coding" | "rag"
        user_preferred: 用户显式选择的模型 ID（如 "deepseek-chat"），优先级最高
        require_tools: 为 True 时只返回支持 function calling 的模型。
            Agent 编排器应当置 True —— 一个不能调用工具的 Agent 在绝大多数
            真实任务（读文件、查知识库、跑命令）上都无法闭环；
            而 deepseek-coder:6.7b 这类模型 capabilities 只有 completion，
            带上 tools 会直接 400，必须提前筛掉。

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

    # 实际可用的模型名称映射
    actual_models = {}
    for p in registry.all():
        # 安全访问 _models 属性
        models = getattr(p, '_models', None) or []
        for m in models:
            actual_models[m.id] = p.provider_id

    # 同步探测本地 Ollama 真实已安装的模型，补齐注册表尚未懒加载的部分。
    # 没有这一步，注册表里的 _models 为空，下面的「可用性校验」会全部落空。
    for m in _probe_ollama_models():
        actual_models.setdefault(m, "ollama")

    # 如果用户偏好的模型存在，使用它
    if user_preferred and user_preferred in actual_models:
        return user_preferred, actual_models[user_preferred]

    # 默认路由：按任务类型给出候选模型（按优先级排列）。
    # 运行时会挑选「实际已安装」的第一个候选，避免硬编码的模型名在换机/重装后失效。
    routing_candidates: dict[str, list[str]] = {
        # 通用对话：优先长上下文 Qwen2.5，其次本地已装的其他 Qwen
        "general": [
            "qwen2.5-1m-q4:latest",
            "modelscope.cn/Qwen/Qwen2.5-7B-Instruct-GGUF:latest",
            "qwen2.5:7b",
        ],
        # 编程：优先专用代码模型
        "coding": [
            "deepseek-coder:6.7b",
            "qwen2.5-1m-q4:latest",
            "modelscope.cn/Qwen/Qwen2.5-7B-Instruct-GGUF:latest",
        ],
        # RAG：需要较好中文理解与指令遵循
        "rag": [
            "qwen2.5-1m-q4:latest",
            "modelscope.cn/Qwen/Qwen2.5-7B-Instruct-GGUF:latest",
            "qwen2.5:7b",
        ],
    }

    candidates = routing_candidates.get(task_type) or routing_candidates["general"]

    # 1) 优先选择实际已安装的候选模型
    #    require_tools 时跳过不支持 function calling 的候选
    for name in candidates:
        if name not in actual_models:
            continue
        if require_tools and not model_supports_tools(name):
            logger.info(f"[router] 跳过不支持 tools 的候选模型: {name}")
            continue
        return name, actual_models[name]

    # 2) 候选均未安装（或被工具能力筛掉）：
    #    退化为 ollama 上任意可用的对话模型（排除 embedding 模型 bge-*）
    fallback_provider = actual_models.get(candidates[0], "ollama")
    for name, pid in actual_models.items():
        if pid != "ollama" or name.startswith("bge"):
            continue
        if require_tools and not model_supports_tools(name):
            continue
        logger.warning(f"[router] 任务 {task_type} 的首选模型均不可用，回退到 {name}")
        return name, pid

    # 3) 完全没有可用模型：仍返回首选名，由 Provider 层报出明确错误
    logger.error(f"[router] 无可用 ollama 模型，任务 {task_type} 将使用 {candidates[0]}")
    return candidates[0], fallback_provider

"""模型路由与 Provider 注册表（Phase 3）。

规格书 §9.6：
- classify_task：消息关键词粗分类（general/coding）
- resolve_model：任务类型 → (model_id, provider_id)
- ProviderRegistry：Provider 注册与查询
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from .base import BaseProvider, ModelInfo

logger = logging.getLogger(__name__)

# 默认路由候选：按任务类型给出候选模型（按优先级排列）。
# 运行时挑选「实际已安装」的第一个候选，避免硬编码模型名换机/重装后失效。
# 可通过环境变量 AGENT_ROUTING_CANDIDATES 覆盖，换机/换模型无需改代码：
#   AGENT_ROUTING_CANDIDATES='{"general":["model-a","model-b"],"coding":["model-c"]}'
DEFAULT_ROUTING_CANDIDATES: dict[str, list[str]] = {
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
}


def load_routing_candidates() -> dict[str, list[str]]:
    """读取路由候选模型表。

    优先取环境变量 AGENT_ROUTING_CANDIDATES（JSON 对象，值为字符串数组）；
    缺失/非法/形态不符时回退 DEFAULT_ROUTING_CANDIDATES。
    general 作为兜底键必须存在，缺失则补默认。
    """
    raw = os.environ.get("AGENT_ROUTING_CANDIDATES", "").strip()
    if not raw:
        return DEFAULT_ROUTING_CANDIDATES
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        logger.warning(f"[router] AGENT_ROUTING_CANDIDATES 非法 JSON，回退默认: {exc}")
        return DEFAULT_ROUTING_CANDIDATES
    if not isinstance(data, dict):
        logger.warning("[router] AGENT_ROUTING_CANDIDATES 不是 JSON 对象，回退默认")
        return DEFAULT_ROUTING_CANDIDATES
    cleaned: dict[str, list[str]] = {}
    for key, val in data.items():
        if isinstance(val, list) and val and all(
            isinstance(x, str) and x.strip() for x in val
        ):
            cleaned[str(key)] = [str(x) for x in val]
    if "general" not in cleaned:
        cleaned["general"] = DEFAULT_ROUTING_CANDIDATES["general"]
        logger.warning("[router] AGENT_ROUTING_CANDIDATES 缺少 general 键，已补默认")
    return cleaned


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

# 已配置服务器的模型同步探测缓存：{"provider_id": {"ids": [...], "ts": float}}
_model_probe_cache: dict = {}
_MODEL_PROBE_TTL_SEC = 60.0


def _probe_server_models() -> dict[str, list[str]]:
    """同步探测所有已启用服务器上可用的模型 id，带 60s 缓存。

    数据源：llm_servers 表（连接管理模块）。对每个 enabled 连接调用
    OpenAI 兼容端点 /v1/models；失败跳过，不阻断整体路由。

    注意：必须绕过 HTTP(S)_PROXY —— 内网/本机流量被企业代理拦截后会
    直接连接失败，这是模型服务器「连不上」的最常见原因。
    """
    import time as _time

    now = _time.time()
    if _model_probe_cache and now - _model_probe_cache.get("ts", 0) < _MODEL_PROBE_TTL_SEC:
        return _model_probe_cache.get("by_server", {})

    try:
        from ..storage.llm_servers import list_servers_sync
        servers = [s for s in list_servers_sync() if s.get("enabled")]
    except Exception:
        servers = []

    import json as _json
    import urllib.request as _req

    def _probe_one(s: dict) -> tuple[str, list[str]]:
        """探测单台服务器：密钥解析 + 候选 base 逐个试 /models。"""
        base_url = s.get("base_url", "").rstrip("/")
        # 读取 API Key（agnes 等需鉴权服务的 /models 也要求 Bearer token）
        api_key = ""
        ref = s.get("api_key_ref")
        if ref:
            try:
                from ..security import keychain as _kc
                api_key = _kc.retrieve_sync(ref) or ""
            except Exception:
                pass
        # 候选 base：原始路径优先（智谱自带版本号），失败才补 /v1（Ollama）
        candidates = [base_url]
        if not base_url.endswith("/v1"):
            candidates.append(base_url + "/v1")
        for cand in candidates:
            try:
                opener = _req.build_opener(_req.ProxyHandler({}))  # 显式禁用代理
                req = _req.Request(f"{cand}/models")
                if api_key:
                    req.add_header("Authorization", f"Bearer {api_key}")
                with opener.open(req, timeout=5.0) as resp:
                    payload = _json.loads(resp.read().decode("utf-8"))
                    ids = [m.get("id", "") for m in payload.get("data", []) if m.get("id")]
                # 按白名单过滤（空列表表示不限制；大小写不敏感）
                allowed = s.get("allowed_models") or []
                if allowed:
                    allowed_set = {m.strip().lower() for m in allowed if m.strip()}
                    ids = [m for m in ids if m.lower() in allowed_set]
                return s["id"], ids  # 找到可用 base 即停
            except Exception as exc:
                logger.debug(f"[router] 探测 {s.get('id')} 候选 {cand} 失败: {exc}")
                continue
        return s["id"], []

    # 各服务器探测互相独立（每台最多 2 候选 × 5s 超时），线程池并发：
    # 总耗时 ≈ 最慢一台，避免 N 台服务器串行累加超时。
    by_server: dict[str, list[str]] = {}
    if servers:
        from concurrent.futures import ThreadPoolExecutor
        max_workers = min(8, len(servers))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for sid, ids in pool.map(_probe_one, servers):
                by_server[sid] = ids

    _model_probe_cache["by_server"] = by_server
    _model_probe_cache["ts"] = now
    return by_server


# 模型是否支持 function calling 的能力缓存：{model: (supported, ts)}
_tool_capability_cache: dict[str, tuple[bool, float]] = {}
_TOOL_CAPABILITY_TTL_SEC = 300.0


def _mark_model_tool_support(model: str, supported: bool) -> None:
    """记录模型的工具支持能力（含运行时探测到的结果）。"""
    import time as _time
    _tool_capability_cache[model] = (supported, _time.time())


def model_supports_tools(
    model: str,
    base_url: str = "",
    timeout: float = 5.0,
) -> bool:
    """查询模型是否支持 function calling（带 5 分钟缓存）。

    纯云端/连接式架构下不再做本地 /api/show 探测：统一按支持处理，
    由 Agent 编排器在运行时收到 "does not support tools" 400 时降级关闭
    工具并回写能力标记（_mark_model_tool_support），避免请求被反复拒绝。
    """
    import time as _time

    now = _time.time()
    cached = _tool_capability_cache.get(model)
    if cached and now - cached[1] < _TOOL_CAPABILITY_TTL_SEC:
        return cached[0]
    return True


def get_registry(settings: dict | None = None) -> ProviderRegistry:
    global _registry
    # 每次调用都从 llm_servers 表读取最新连接并重建注册表：
    # 保证「新增 / 编辑 / 删除模型服务器连接」即时生效，无需重启后端。
    from .base import build_providers
    _registry = ProviderRegistry()
    for p in build_providers(settings):
        _registry.register(p)
    return _registry


def classify_task(message: str) -> str:
    """根据消息关键词粗粒度分类，用于模型路由。

    Returns:
        "coding" | "general"
    """
    lower = message.lower()
    coding_keywords = [
        "代码", "编程", "函数", "bug", "报错", "语法", "实现",
        "python", "javascript", "typescript", "react", "vue",
        "写个", "帮我写", "分析这段代码", "重构",
    ]
    for kw in coding_keywords:
        if kw in lower:
            return "coding"
    return "general"


def resolve_model(
    registry: ProviderRegistry,
    task_type: str,
    user_preferred: Optional[str] = None,
    require_tools: bool = False,
) -> tuple[str, str]:
    """解析目标模型 ID 和 Provider ID。

    Args:
        task_type: "general" | "coding"
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

    # 同步探测所有已启用服务器上真实可用的模型，补齐注册表尚未懒加载的部分。
    # 没有这一步，注册表里的 _models 为空，下面的「可用性校验」会全部落空。
    for server_id, ids in _probe_server_models().items():
        for m in ids:
            actual_models.setdefault(m, server_id)

    # 如果用户偏好的模型存在，使用它
    if user_preferred and user_preferred in actual_models:
        return user_preferred, actual_models[user_preferred]

    # 默认路由：按任务类型给出候选模型（按优先级排列，可用环境变量覆盖）。
    # 运行时会挑选「实际已安装」的第一个候选，避免硬编码的模型名在换机/重装后失效。
    routing_candidates = load_routing_candidates()

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
    #    退化为任一已连接服务器上可用的对话模型（排除 embedding 模型 bge-*）
    fallback_provider = actual_models.get(candidates[0])
    for name, pid in actual_models.items():
        if name.startswith("bge") or "embed" in name.lower():
            continue
        if require_tools and not model_supports_tools(name):
            continue
        logger.warning(f"[router] 任务 {task_type} 的首选模型均不可用，回退到 {name}")
        return name, pid

    # 3) 完全没有可用模型：仍返回首选名，由 Provider 层报出明确错误
    logger.error(f"[router] 无可用模型，任务 {task_type} 将使用 {candidates[0]}")
    return candidates[0], fallback_provider or "unknown"

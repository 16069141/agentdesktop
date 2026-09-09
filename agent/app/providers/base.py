"""Provider 层（统一，仅一份）。

规格书 §9.1：
- BaseProvider 抽象类：chat（异步流式）+ list_models
- OpenAICompatibleProvider：Ollama / vLLM / 私有 API / 互联网商业大模型共用
- 健康检查与健康降级逻辑在 routing.py 中实现
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, Optional

from ..security import keychain

logger = logging.getLogger(__name__)


def _sanitize_messages(messages: list[dict]) -> list[dict]:
    """清洗消息列表，确保发送给 LLM 的每条消息 content 合法。

    过滤规则：
    - content 为 None 且无 tool_calls 的消息直接丢弃
    - content 为 None 但有 tool_calls 的 assistant 消息保留，content 设为空字符串
    - content 为 list（多模态格式：text + image_url）直接保留，不做字符串转换
    - content 非字符串且非列表（如数字）转为字符串
    - role 为空的消息丢弃
    """
    cleaned = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "")
        if not role:
            continue
        content = m.get("content", "")
        # 多模态格式：content 为数组（text + image_url），直接保留
        if isinstance(content, list):
            cleaned.append({**m})
            continue
        if content is None:
            # assistant 带 tool_calls 的消息必须保留（后续 tool 消息依赖它）
            if role == "assistant" and m.get("tool_calls"):
                msg = {**m, "content": ""}
                cleaned.append(msg)
                continue
            # 其他 content 为 null 的消息丢弃
            logger.warning(f"[provider] 丢弃 content 为 null 的消息: role={role}")
            continue
        if not isinstance(content, str):
            content = str(content)
        msg = {**m, "content": content}
        cleaned.append(msg)
    return cleaned


@dataclass
class ModelInfo:
    id: str
    name: str
    provider: str  # "ollama" | "openai" | "private_api" | "internet"
    kind: str  # "chat" | "embedding"
    status: str  # "healthy" | "unhealthy"
    last_health_check: Optional[str] = None  # ISO 8601


class BaseProvider(ABC):
    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        model: str,
        **kwargs,
    ) -> AsyncIterator[dict]:
        """异步流式对话，产出事件 dict。

        事件格式（对齐 chat.py SSE 协议）：
            {"type": "text", "delta": "..."}
            {"type": "thinking", "delta": "..."}   # 可选
            {"type": "tool_calls", "calls": [...]}  # 结构化工具调用
            {"type": "done", "finish_reason": "...", "usage": {...}}
        """
        ...

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """列出该 Provider 可用的模型。"""
        ...

    async def health_check(self, model: str | None = None) -> bool:
        """健康检查，默认返回 True（占位）。"""
        return True


class OpenAICompatibleProvider(BaseProvider):
    """OpenAI 兼容接口：Ollama / vLLM / 私有 API / 互联网商业大模型共用。

    差异仅在于 base_url 与 api_key（可能为空字符串）。
    """

    def __init__(
        self,
        provider_id: str,
        name: str,
        base_url: str,
        api_key: str = "",
        model_map: Optional[dict[str, str]] = None,
    ):
        """
        Args:
            provider_id: 内部标识，如 "ollama" / "qwen-api" / "deepseek-api"
            name: 显示名称
            base_url: 如 "http://127.0.0.1:11434/v1"（Ollama）
                      或 "https://api.deepseek.com/v1"
            api_key: 空字符串表示无需鉴权（本地 Ollama）；否则从钥匙串读取
            model_map: {"local": "qwen2.5:7b", "coder": "deepseek-coder:6.7b"}
        """
        self.provider_id = provider_id
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or "sk-placeholder"  # Ollama 接受任意值
        self.model_map = model_map or {}
        self._models: list[ModelInfo] = []
        self._client = None  # 懒加载

    async def _get_client(self):
        """懒加载异步 OpenAI 客户端（供健康巡检 / 模型列表等一次性探测复用）。

        注意：不要把这个缓存客户端用于长会话流式请求——长连接上的坏连接
        （上游断连、TCP 半开、HTTP/2 坏帧）不会被 httpx 自动恢复，一旦污染，
        后续所有复用请求都会挂住直到 SDK 默认 600s 超时，用户侧表现为
        「无反应」。聊天请求应使用 _fresh_client() 每次新建。
        """
        if self._client is None:
            try:
                from openai import AsyncOpenAI
                # 创建自定义 httpx 客户端，禁用环境变量代理（避免企业代理干扰本地 Ollama）
                import httpx
                http_client = httpx.AsyncClient(
                    trust_env=False,
                    timeout=httpx.Timeout(30.0, connect=10.0),
                )
                self._client = AsyncOpenAI(
                    base_url=self.base_url,
                    api_key=self.api_key,
                    http_client=http_client,
                )
            except ImportError:
                logger.warning(
                    "[provider] openai 包未安装，ChatCompletion 不可用。"
                    "安装: pip install openai"
                )
                self._client = _MockClient(self.base_url, self.api_key)
        return self._client

    async def _fresh_client(self):
        """每次调用新建独立客户端（含显式超时），用后必须 await self._close_client()。

        用于流式 chat：彻底规避连接池污染导致的「一次坏连接永久卡死」。
        本地桌面单用户场景下，每次请求新建连接的代价可忽略。
        """
        from openai import AsyncOpenAI
        import httpx

        http_client = httpx.AsyncClient(
            trust_env=False,
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
        return AsyncOpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            http_client=http_client,
        )

    @staticmethod
    async def _close_client(client) -> None:
        """释放一次性客户端连接。"""
        try:
            await client.close()
        except Exception:  # noqa: BLE001
            pass

    async def chat(
        self,
        messages: list[dict],
        model: str,
        **kwargs,
    ) -> AsyncIterator[dict]:
        client = await self._fresh_client()
        try:
            # 防御性清洗：过滤 content 为 null 的消息，避免 OpenAI 兼容接口 400
            cleaned = _sanitize_messages(messages)
            if cleaned is not messages:
                logger.info(f"[provider] 消息清洗: {len(messages)} -> {len(cleaned)}")
            # 从 kwargs 中提取 stream，默认为 True
            stream = kwargs.pop('stream', True)

            async def _iter(stream_obj):
                """统一的流式事件产出：聚合工具调用增量 + 提取 usage。

                OpenAI 兼容网关在 include_usage 下，usage 通常位于
                finish_reason 之后的独立 chunk（choices 为空）。因此收到
                finish_reason 后不能立刻 return，要继续消费直到拿到 usage
                chunk 或流结束，否则 prompt_tokens 恒为 0。
                """
                # 流式工具调用增量累积缓冲（按 tool_call index）
                pending_tool_calls: dict[int, dict] = {}
                finish_reason_seen = ""
                done_emitted = False
                async for chunk in stream_obj:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    finish_reason = chunk.choices[0].finish_reason if chunk.choices else None
                    if delta is None and not finish_reason and not chunk.usage:
                        continue
                    if delta is not None:
                        if delta.content:
                            yield {"type": "text", "delta": delta.content}
                        # 思考过程：兼容 deepseek 系 reasoning_content 与通用 reasoning 字段
                        reasoning = getattr(delta, "reasoning_content", None)
                        if reasoning is None:
                            reasoning = getattr(delta, "reasoning", None)
                        if reasoning:
                            yield {"type": "thinking", "delta": reasoning}
                        if delta.tool_calls:
                            # 流式工具调用增量聚合：OpenAI 兼容接口会把一个工具调用拆成
                            # 多个 chunk（首块带 id/name，后续块仅带 arguments 片段）。
                            # 必须按 index 累积到完整调用后再一次性发出，否则下游会把
                            # 每个 token 片段当成独立工具调用执行（"工具 'None' 未注册"）。
                            for tc in delta.tool_calls:
                                idx = getattr(tc, "index", None)
                                if idx is None:
                                    idx = 0
                                entry = pending_tool_calls.setdefault(
                                    idx, {"id": "", "function": {"name": "", "arguments": ""}}
                                )
                                if tc.id:
                                    entry["id"] = tc.id
                                if tc.function:
                                    if tc.function.name:
                                        entry["function"]["name"] += tc.function.name
                                    if tc.function.arguments:
                                        entry["function"]["arguments"] += tc.function.arguments
                    if finish_reason and not finish_reason_seen:
                        finish_reason_seen = finish_reason
                        # 工具调用完整后统一发射
                        if pending_tool_calls and finish_reason == "tool_calls":
                            calls = []
                            for _idx in sorted(pending_tool_calls):
                                v = pending_tool_calls[_idx]
                                if not v["function"]["name"]:
                                    continue
                                calls.append({
                                    "id": v["id"],
                                    "type": "function",
                                    "function": {
                                        "name": v["function"]["name"],
                                        "arguments": v["function"]["arguments"] or "{}",
                                    },
                                })
                            if calls:
                                yield {"type": "tool_calls", "calls": calls}
                            pending_tool_calls = {}
                    # usage 到达（通常随 finish 后的独立 chunk）→ 立即收尾
                    if chunk.usage is not None and not done_emitted:
                        done_emitted = True
                        yield {
                            "type": "done",
                            "finish_reason": finish_reason_seen or finish_reason or "stop",
                            "usage": {
                                "promptTokens": chunk.usage.prompt_tokens or 0,
                                "completionTokens": chunk.usage.completion_tokens or 0,
                            },
                        }
                        return
                    if finish_reason_seen and finish_reason_seen == "stop" and not chunk.usage:
                        # 有 finish 但本 chunk 无 usage：继续等 usage chunk / 流结束
                        pass
                # 流结束仍无 usage（网关不支持 include_usage）：兜底 done
                if not done_emitted:
                    # 兜底发射未完成的工具调用，避免丢事件
                    if pending_tool_calls:
                        calls = []
                        for _idx in sorted(pending_tool_calls):
                            v = pending_tool_calls[_idx]
                            if not v["function"]["name"]:
                                continue
                            calls.append({
                                "id": v["id"],
                                "type": "function",
                                "function": {
                                    "name": v["function"]["name"],
                                    "arguments": v["function"]["arguments"] or "{}",
                                },
                            })
                        if calls:
                            yield {"type": "tool_calls", "calls": calls}
                    yield {
                        "type": "done",
                        "finish_reason": finish_reason_seen or "stop",
                        "usage": {},
                    }

            # 携带 usage 统计（OpenAI 兼容标准）；网关不支持时回退为普通流式
            try:
                stream = await client.chat.completions.create(
                    model=model,
                    messages=cleaned,
                    stream=True,
                    stream_options={"include_usage": True},
                    **kwargs,
                )
            except Exception as exc:
                logger.warning(f"[provider] 网关拒绝 stream_options，回退普通流式: {exc}")
                stream = await client.chat.completions.create(
                    model=model,
                    messages=cleaned,
                    stream=True,
                    **kwargs,
                )

            try:
                async for event in _iter(stream):
                    yield event
            except Exception as exc:
                msg = str(exc).lower()
                if ("stream_options" in msg or "include_usage" in msg
                        or "unknown parameter" in msg or "unexpected" in msg):
                    logger.warning(f"[provider] 网关迭代期拒绝 include_usage，回退重试: {exc}")
                    stream2 = await client.chat.completions.create(
                        model=model,
                        messages=cleaned,
                        stream=True,
                        **kwargs,
                    )
                    async for event in _iter(stream2):
                        yield event
                else:
                    raise
        except Exception as exc:
            logger.error(f"[provider] chat 异常: {type(exc).__name__}: {exc}", exc_info=True)
            # 某些异常 str 为空（如连接层错误），message 保留类型便于定位
            _detail = str(exc) or f"({type(exc).__name__})"
            yield {"type": "error", "message": _detail}
            return
        finally:
            # 每次请求独立连接，用后即焚：避免坏连接污染全局连接池
            await self._close_client(client)

    async def list_models(self) -> list[ModelInfo]:
        if self._models:
            return self._models
        client = await self._get_client()
        try:
            resp = await client.models.list()
            for m in resp.data:
                kind = "embedding" if "embed" in m.id.lower() else "chat"
                self._models.append(ModelInfo(
                    id=m.id,
                    name=m.id,
                    provider=self.provider_id,
                    kind=kind,
                    status="healthy",
                ))
        except Exception as exc:
            logger.warning(f"[provider] 获取模型列表失败 ({self.provider_id}): {exc}")
            # 回退到 model_map
            for alias, model_id in self.model_map.items():
                kind = "embedding" if "embed" in model_id.lower() else "chat"
                self._models.append(ModelInfo(
                    id=model_id,
                    name=alias,
                    provider=self.provider_id,
                    kind=kind,
                    status="healthy" if self.provider_id == "ollama" else "unknown",
                ))
        return self._models

    async def health_check(self, model: str | None = None) -> bool:
        """向 /v1/models 发一次轻量请求。"""
        try:
            client = await self._get_client()
            await client.models.list()
            return True
        except Exception:
            return False


class _MockClient:
    """openai 包未安装时的占位客户端，避免启动崩溃。"""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key
        self.chat = _MockChat(self)


class _MockChat:
    def __init__(self, client):
        self._client = client

    class completions:
        @staticmethod
        async def create(**kwargs):
            return _MockStream()


class _MockStream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


# ──────────────────────────────────────────────────────────────────────
# 预置 Provider 实例
# ──────────────────────────────────────────────────────────────────────

# base_url 解析缓存（进程级，TTL 300s）：避免每次构建 Provider 都同步探测
_BASE_RESOLVE_CACHE: dict[str, tuple[float, str]] = {}
_BASE_RESOLVE_TTL = 300.0


def _resolve_base_url(base_url: str, api_key: str = "", timeout: float = 6.0) -> str:
    """在「原始 base」与「补 /v1 的 base」之间选择可用者。

    背景：OpenAI 兼容网关对 /v1 的约定不统一——
    - Ollama（http://localhost:11434）需要补 /v1；
    - 智谱（https://open.bigmodel.cn/api/paas/v4）自带版本号，补 /v1 反而 404。
    策略：优先用原始 base 探测 /models；失败且未以 /v1 结尾时，再试 +/v1；
    全部失败回退原始 base（错误由上游返回，避免掩盖真实问题）。
    """
    import time as _time
    import urllib.request as _req

    base = (base_url or "").rstrip("/")
    if not base:
        return base_url or ""
    now = _time.time()
    hit = _BASE_RESOLVE_CACHE.get(base)
    if hit and now - hit[0] < _BASE_RESOLVE_TTL:
        return hit[1]

    candidates = [base]
    if not base.endswith("/v1"):
        candidates.append(base + "/v1")

    opener = _req.build_opener(_req.ProxyHandler({}))  # 禁用代理
    for cand in candidates:
        try:
            req = _req.Request(f"{cand}/models")
            if api_key:
                req.add_header("Authorization", f"Bearer {api_key}")
            with opener.open(req, timeout=timeout) as resp:
                if resp.status == 200:
                    _BASE_RESOLVE_CACHE[base] = (now, cand)
                    return cand
        except Exception:  # noqa: BLE001
            continue
    _BASE_RESOLVE_CACHE[base] = (now, base)
    return base


def build_providers(settings: dict | None = None) -> list[BaseProvider]:
    """按 llm_servers 连接表构建 Provider 列表（纯云端架构）。

    数据源：SQLite llm_servers 表（连接管理模块的权威存储）。
    仅构建 enabled=1 的连接；密钥经 keychain 同步解析，配置中只存引用名。
    """
    providers: list[BaseProvider] = []
    try:
        from ..storage.llm_servers import list_servers_sync
        servers = list_servers_sync()
    except Exception as exc:
        logger.warning(f"[provider] 读取 llm_servers 失败: {exc}")
        servers = []

    enabled_servers = [s for s in servers if s.get("enabled") and s.get("base_url", "").rstrip("/")]

    def _prepare(s: dict) -> dict:
        """单台服务器的阻塞准备：密钥解析（keychain）+ base 探测（urllib）。"""
        sid = s["id"]
        raw_base = s.get("base_url", "").rstrip("/")
        api_key = ""
        ref = s.get("api_key_ref")
        if ref:
            try:
                api_key = keychain.retrieve_sync(ref) or ""
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[provider] {sid} 密钥解析失败: {exc}")
        # 智能 base 解析：优先原始路径（智谱 /api/paas/v4 等自带版本号的
        # OpenAI 兼容服务），探测 /models 失败才补 /v1（Ollama 等）。
        return {"server": s, "sid": sid, "api_key": api_key,
                "base_url": _resolve_base_url(raw_base, api_key)}

    # 多台服务器的密钥解析/HTTP 探测互相独立，线程池并发 ——
    # 总耗时 ≈ 最慢一台，而非各台 6s 超时的累加。
    prepared: list[dict] = []
    if enabled_servers:
        from concurrent.futures import ThreadPoolExecutor
        max_workers = min(8, len(enabled_servers))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            prepared = [r for r in pool.map(_prepare, enabled_servers)]

    for item in prepared:
        s = item["server"]
        sid = item["sid"]
        api_key = item["api_key"]
        base_url = item["base_url"]
        providers.append(OpenAICompatibleProvider(
            provider_id=sid,
            name=s.get("name") or sid,
            base_url=base_url,
            api_key=api_key,
        ))
        # 从数据库 models_cache 预填充模型列表，避免路由解析时注册表为空
        cache_raw = s.get("models_cache")
        if cache_raw:
            try:
                import json as _json
                cached_ids = _json.loads(cache_raw) if isinstance(cache_raw, str) else cache_raw
                for mid in cached_ids:
                    kind = "embedding" if "embed" in str(mid).lower() else "chat"
                    providers[-1]._models.append(ModelInfo(
                        id=mid, name=mid, provider=sid, kind=kind, status="healthy",
                    ))
            except Exception:
                pass
        logger.info(f"[provider] 已加载模型服务器连接: {sid} -> {base_url} ({len(providers[-1]._models)} 个缓存模型)")

    if not providers:
        logger.warning("[provider] 未配置任何模型服务器连接，对话将不可用")
    return providers


def create_provider(
    protocol: str = "openai",
    *,
    base_url: str = "",
    api_key: str = "",
    timeout_sec: float = 60.0,
    provider_id: str = "",
    name: str = "",
) -> BaseProvider:
    """按连接配置创建单例 Provider（供健康巡检 / 测试等场景复用）。

    与 build_providers 保持同一构造逻辑：统一补 /v1、密钥解析由调用方完成。
    """
    base_url = (base_url or "").rstrip("/")
    if not base_url.endswith("/v1"):
        base_url = base_url + "/v1"
    pid = provider_id or name or "custom"
    return OpenAICompatibleProvider(
        provider_id=pid,
        name=name or pid,
        base_url=base_url,
        api_key=api_key,
    )

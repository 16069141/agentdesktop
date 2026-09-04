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

logger = logging.getLogger(__name__)


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
        ...

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        ...

    async def health_check(self, model: str | None = None) -> bool:
        return True


class OpenAICompatibleProvider(BaseProvider):
    def __init__(
        self,
        provider_id: str,
        name: str,
        base_url: str,
        api_key: str = "",
        model_map: Optional[dict[str, str]] = None,
    ):
        self.provider_id = provider_id
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or "sk-placeholder"
        self.model_map = model_map or {}
        self._models: list[ModelInfo] = []
        self._client = None

    async def _get_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
                self._client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key)
            except ImportError:
                logger.warning("[provider] openai 包未安装，ChatCompletion 不可用。安装: pip install openai")
                self._client = _MockClient(self.base_url, self.api_key)
        return self._client

    async def chat(self, messages, model, **kwargs):
        client = await self._get_client()
        try:
            stream = await client.chat.completions.create(model=model, messages=messages, stream=True, **kwargs)
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta is None:
                    continue
                if delta.content:
                    yield {"type": "text", "delta": delta.content}
                if delta.tool_calls:
                    calls = []
                    for tc in delta.tool_calls:
                        calls.append({"id": tc.id or "", "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}})
                    if calls:
                        yield {"type": "tool_calls", "calls": calls}
                finish_reason = chunk.choices[0].finish_reason if chunk.choices else None
                if finish_reason:
                    usage = chunk.usage if chunk.usage else None
                    usage_dict = {"promptTokens": usage.prompt_tokens or 0, "completionTokens": usage.completion_tokens or 0} if usage else {}
                    yield {"type": "done", "finish_reason": finish_reason, "usage": usage_dict}
                    return
        except Exception as exc:
            logger.error(f"[provider] chat 异常: {exc}")
            yield {"type": "error", "message": str(exc)}

    async def list_models(self):
        if self._models:
            return self._models
        client = await self._get_client()
        try:
            resp = await client.models.list()
            for m in resp.data:
                kind = "embedding" if "embed" in m.id.lower() else "chat"
                self._models.append(ModelInfo(id=m.id, name=m.id, provider=self.provider_id, kind=kind, status="healthy"))
        except Exception as exc:
            logger.warning(f"[provider] 获取模型列表失败 ({self.provider_id}): {exc}")
            for alias, model_id in self.model_map.items():
                kind = "embedding" if "embed" in model_id.lower() else "chat"
                self._models.append(ModelInfo(id=model_id, name=alias, provider=self.provider_id, kind=kind, status="healthy" if self.provider_id == "ollama" else "unknown"))
        return self._models

    async def health_check(self, model=None):
        try:
            client = await self._get_client()
            await client.models.list()
            return True
        except Exception:
            return False


class _MockClient:
    def __init__(self, base_url, api_key):
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


def build_providers():
    providers = []
    providers.append(OpenAICompatibleProvider(
        provider_id="ollama", name="Ollama（本地）",
        base_url="http://127.0.0.1:11434/v1", api_key="",
        model_map={"qwen2.5:7b": "qwen2.5:7b", "deepseek-coder:6.7b": "deepseek-coder:6.7b", "bge-m3": "bge-m3"},
    ))
    providers.append(_PrivateApiPlaceholder())
    providers.append(_InternetApiPlaceholder())
    return providers


class _PrivateApiPlaceholder(BaseProvider):
    provider_id = "private_api"
    name = "私有化 API"
    _models = []  # 添加 _models 属性避免 AttributeError

    async def chat(self, messages, model, **kwargs):
        yield {"type": "error", "message": "私有 API 未配置"}

    async def list_models(self):
        return self._models

    async def health_check(self, model=None):
        return False


class _InternetApiPlaceholder(BaseProvider):
    provider_id = "internet"
    name = "互联网商业大模型（公网）"
    _models = [
        ModelInfo(id="deepseek-chat", name="DeepSeek-V3", provider="internet", kind="chat", status="unknown"),
        ModelInfo(id="qwen-plus", name="通义千问 Plus", provider="internet", kind="chat", status="unknown"),
    ]

    async def chat(self, messages, model, **kwargs):
        yield {"type": "error", "message": "公网模型未配置，请在设置页配置 API Key"}

    async def list_models(self):
        return self._models

    async def health_check(self, model=None):
        return False


# 从 routing.py 导入路由相关符号
from .routing import ProviderRegistry, classify_task, resolve_model, get_registry

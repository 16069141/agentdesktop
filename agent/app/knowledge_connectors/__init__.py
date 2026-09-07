"""知识库连接适配器工厂。

按 knowledge_servers 连接的 platform 字段实例化对应适配器；
未实现的平台返回 UnsupportedConnector（明确提示，不静默失败）。
"""
from typing import Any, Dict, Optional

from .base import KnowledgeConnector
from .platforms import (
    ConfluenceConnector,
    DifyConnector,
    FastGPTConnector,
    FeishuWikiConnector,
    GenericConnector,
    NotionConnector,
    RAGFlowConnector,
    UnsupportedConnector,
    WikiJsConnector,
)

# 已实现平台 → 适配器
_PLATFORMS: Dict[str, type] = {
    "dify": DifyConnector,           # rag：Dify 知识库检索
    "fastgpt": FastGPTConnector,     # rag：FastGPT 知识库检索（P4）
    "ragflow": RAGFlowConnector,     # rag：RAGFlow 检索（P4）
    "confluence": ConfluenceConnector,  # wiki：Confluence 搜索
    "notion": NotionConnector,       # wiki：Notion Search（P4）
    "wiki_js": WikiJsConnector,      # wiki：Wiki.js GraphQL 检索（P5）
    "feishu_wiki": FeishuWikiConnector,  # wiki：飞书知识空间检索（P5）
    "generic": GenericConnector,     # 通用 HTTP 检索模板
}

# 可识别但尚未实现的平台（在设置界面可选，选择后给出明确提示）
KNOWN_PENDING: set[str] = set()


def create_connector(
    server: Dict[str, Any],
    api_key: str = "",
) -> KnowledgeConnector:
    """按连接配置创建适配器实例。"""
    platform = server.get("platform") or "generic"
    cls = _PLATFORMS.get(platform)
    kwargs = {
        "base_url": server.get("base_url", ""),
        "api_key": api_key,
        "extra": server.get("extra_config") or {},
        "timeout": server.get("timeout_sec") or 30,
    }
    if cls is None:
        return UnsupportedConnector(platform, **kwargs)
    return cls(**kwargs)


def list_platforms() -> list[dict]:
    """返回平台清单（前端下拉用）。"""
    implemented = sorted(_PLATFORMS.keys())
    pending = sorted(KNOWN_PENDING)
    return [
        {"platform": p, "implemented": True} for p in implemented
    ] + [
        {"platform": p, "implemented": False} for p in pending
    ]

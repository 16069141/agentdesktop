"""知识库连接适配器：统一检索接口。

每个平台实现 KnowledgeConnector 的两个方法：
- test() → bool：连通性探测
- search(query, top_k) → list[dict]：检索，统一输出
    [{title, snippet, source, url, score}]
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List


class KnowledgeConnector(ABC):
    platform: str = "base"

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        extra: Dict[str, Any] | None = None,
        timeout: int = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.extra = extra or {}
        self.timeout = timeout

    @abstractmethod
    async def test(self) -> bool:
        """连通性测试。"""

    @abstractmethod
    async def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """检索，返回统一引用结构。"""

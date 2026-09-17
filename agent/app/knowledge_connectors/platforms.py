"""常用知识库平台适配器实现。

首批提供三个覆盖「局域网 + 互联网」的稳定实现：
- DifyConnector（rag/dify）：Dify 知识库检索 API（标准 RAG 平台）
- ConfluenceConnector（wiki/confluence）：Confluence REST + CQL 搜索
- GenericConnector（generic）：用户自定义 HTTP 检索模板，覆盖任意平台

Phase B (P4) 补齐：
- FastGPTConnector（rag/fastgpt）：FastGPT 知识库检索 API
- RAGFlowConnector（rag/ragflow）：RAGFlow 检索 API
- NotionConnector（wiki/notion）：Notion Search API

其余平台（wiki_js / feishu_wiki）由 factory 映射为 UnsupportedConnector，
给出明确提示，后续按同样接口扩展。
"""
from typing import Any, Dict, List

import httpx

from .base import KnowledgeConnector


def _get_path(data: Any, path: str, default: Any = None) -> Any:
    """按点路径取值：results_path='data.items' → data['data']['items']。"""
    if not path:
        return default
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)]
        else:
            return default
        if cur is None:
            return default
    return cur


class DifyConnector(KnowledgeConnector):
    """Dify 知识库检索（POST /v1/datasets/{dataset_id}/retrieve）。"""

    platform = "dify"

    def _v1(self) -> str:
        url = self.base_url
        if not url.endswith("/v1"):
            url = url + "/v1"
        return url

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def test(self) -> bool:
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=self.timeout) as client:
                resp = await client.get(f"{self._v1()}/datasets", headers=self._headers())
                return resp.status_code < 400
        except Exception:
            return False

    async def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        dataset_id = self.extra.get("dataset_id") or self.extra.get("dataset")
        if not dataset_id:
            return [{
                "title": "配置缺失",
                "snippet": "请在该知识库连接的「高级配置」中填写 dataset_id（Dify 数据集 ID）。",
                "source": self.base_url,
                "url": "",
                "score": 0.0,
            }]
        body = {
            "query": query,
            "retrieval_model": {
                "search_method": "semantic_search",
                "top_k": top_k,
            },
        }
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self._v1()}/datasets/{dataset_id}/retrieve",
                    json=body,
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            return [{
                "title": "检索失败",
                "snippet": str(exc),
                "source": self.base_url,
                "url": "",
                "score": 0.0,
            }]

        results: List[Dict[str, Any]] = []
        for rec in data.get("records") or []:
            seg = rec.get("segment") or {}
            doc = seg.get("document") or {}
            results.append({
                "title": doc.get("name") or doc.get("title") or "未命名文档",
                "snippet": (seg.get("content") or "")[:500],
                "source": f"Dify·{doc.get('name', '知识库')}",
                "url": "",
                "score": float(rec.get("score") or 0.0),
            })
        return results


class ConfluenceConnector(KnowledgeConnector):
    """Confluence 搜索（GET /rest/api/search?cql=text~"...")."""

    platform = "confluence"

    def _headers(self) -> dict:
        headers = {"Accept": "application/json"}
        if self.api_key:
            # Confluence 云：Basic base64(user:token)；企业版可配 Personal Access Token
            import base64
            token = self.api_key
            if ":" in self.api_key and not self.api_key.startswith("Basic "):
                token = "Basic " + base64.b64encode(self.api_key.encode()).decode()
            headers["Authorization"] = token
        return headers

    async def test(self) -> bool:
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=self.timeout) as client:
                resp = await client.get(
                    f"{self.base_url}/rest/api/content?limit=1", headers=self._headers()
                )
                return resp.status_code < 400
        except Exception:
            return False

    async def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        import urllib.parse

        cql = f'text~"{query.replace(chr(34), chr(92) + chr(34))}"'
        space = self.extra.get("space")
        if space:
            cql += f' AND space="{space}"'
        url = (
            f"{self.base_url}/rest/api/search?cql={urllib.parse.quote(cql)}"
            f"&limit={top_k}"
        )
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=self.timeout) as client:
                resp = await client.get(url, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            return [{
                "title": "检索失败",
                "snippet": str(exc),
                "source": self.base_url,
                "url": "",
                "score": 0.0,
            }]

        results: List[Dict[str, Any]] = []
        for item in data.get("results") or []:
            content = item.get("content") or {}
            links = content.get("_links") or {}
            webui = links.get("webui") or ""
            results.append({
                "title": item.get("title") or content.get("title") or "未命名页面",
                "snippet": (item.get("excerpt") or "")[:500],
                "source": "Confluence",
                "url": f"{self.base_url}{webui}" if webui.startswith("/") else webui,
                "score": float(item.get("friendlyScore") or 0.0),
            })
        return results


class GenericConnector(KnowledgeConnector):
    """通用 HTTP 检索适配器：通过 extra_config 描述任意检索接口。

    extra_config 示例：
    {
      "method": "POST",              # GET / POST
      "path": "/search",             # 相对路径
      "headers": {},                 # 额外请求头（可含 {api_key} 占位）
      "body_template": {             # POST body 模板，{query}/{top_k} 占位
        "query": "{query}", "top_k": "{top_k}"
      },
      "query_param": "q",            # GET 时查询参数名
      "results_path": "data.items",  # 结果列表 JSON 路径
      "title_field": "title",
      "snippet_field": "snippet",
      "source_field": "source",
      "url_field": "url",
      "url_prefix": ""               # url 相对路径时的前缀
    }
    """

    platform = "generic"

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        cfg_headers = self.extra.get("headers") or {}
        for k, v in cfg_headers.items():
            headers[k] = str(v).replace("{api_key}", self.api_key or "")
        if self.api_key and not any("authorization" in k.lower() for k in headers):
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def test(self) -> bool:
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=self.timeout) as client:
                resp = await client.get(self.base_url, headers=self._headers())
                return resp.status_code < 500
        except Exception:
            return False

    async def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        method = (self.extra.get("method") or "POST").upper()
        path = (self.extra.get("path") or "/search").lstrip("/")
        url = f"{self.base_url}/{path}"

        def fill(o):
            if isinstance(o, dict):
                return {k: fill(v) for k, v in o.items()}
            if isinstance(o, list):
                return [fill(v) for v in o]
            if isinstance(o, str):
                return o.replace("{query}", query).replace("{top_k}", str(top_k))
            return o

        try:
            async with httpx.AsyncClient(trust_env=False, timeout=self.timeout) as client:
                if method == "GET":
                    param = self.extra.get("query_param") or "q"
                    resp = await client.get(
                        url, params={param: query, "limit": top_k, "top_k": top_k},
                        headers=self._headers(),
                    )
                else:
                    # 无 body_template 时兜底默认模板：兼容 /api/kb/search 这类
                    # POST 检索接口（否则发空 body {} 导致服务端 400"缺少检索词"）
                    tpl = self.extra.get("body_template")
                    if not tpl:
                        tpl = {"query": "{query}", "top_k": "{top_k}"}
                    resp = await client.post(
                        url, json=fill(tpl),
                        headers=self._headers(),
                    )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            return [{
                "title": "检索失败",
                "snippet": str(exc),
                "source": self.base_url,
                "url": "",
                "score": 0.0,
            }]

        items = _get_path(data, self.extra.get("results_path") or "data", [])
        if not isinstance(items, list):
            items = [data]
        prefix = self.extra.get("url_prefix") or ""
        results: List[Dict[str, Any]] = []
        for it in items[:top_k]:
            if not isinstance(it, dict):
                continue
            url = _get_path(it, self.extra.get("url_field") or "url", "")
            results.append({
                "title": str(_get_path(it, self.extra.get("title_field") or "title", "未命名") or "未命名"),
                "snippet": str(_get_path(it, self.extra.get("snippet_field") or "snippet", "") or "")[:500],
                "source": str(_get_path(it, self.extra.get("source_field") or "source", "知识库") or "知识库"),
                "url": f"{prefix}{url}" if url and url.startswith("/") else url,
                "score": float(_get_path(it, self.extra.get("score_field") or "score", 0.0) or 0.0),
            })
        return results


class FastGPTConnector(KnowledgeConnector):
    """FastGPT 知识库检索（POST /api/v1/dataset/search）。

    需在高级配置填 dataset_id；api_key 为 FastGPT 应用 API Key。
    """

    platform = "fastgpt"

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def test(self) -> bool:
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=self.timeout) as client:
                resp = await client.get(f"{self.base_url}/api/v1/dataset/list",
                                        headers=self._headers())
                return resp.status_code < 400
        except Exception:
            return False

    async def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        dataset_id = self.extra.get("dataset_id") or self.extra.get("dataset")
        if not dataset_id:
            return [{
                "title": "配置缺失",
                "snippet": "请在该知识库连接的「高级配置」中填写 dataset_id（FastGPT 数据集 ID）。",
                "source": self.base_url, "url": "", "score": 0.0,
            }]
        body = {"datasetId": dataset_id, "query": query, "limit": top_k}
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=self.timeout) as client:
                resp = await client.post(f"{self.base_url}/api/v1/dataset/search",
                                         json=body, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            return [{"title": "检索失败", "snippet": str(exc),
                     "source": self.base_url, "url": "", "score": 0.0}]
        records = data.get("data") or []
        if isinstance(records, dict):
            records = records.get("list") or records.get("results") or []
        results: List[Dict[str, Any]] = []
        for rec in records:
            results.append({
                "title": rec.get("name") or rec.get("title") or "未命名文档",
                "snippet": (rec.get("content") or rec.get("snippet") or "")[:500],
                "source": "FastGPT",
                "url": rec.get("url") or "",
                "score": float(rec.get("score") or 0.0),
            })
        return results


class RAGFlowConnector(KnowledgeConnector):
    """RAGFlow 知识库检索（POST /v1/retrieval）。

    api_key 为 RAGFlow API Key；需在高级配置填 dataset_ids（逗号分隔）。
    """

    platform = "ragflow"

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def test(self) -> bool:
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=self.timeout) as client:
                resp = await client.get(f"{self.base_url}/v1/datasets",
                                        headers=self._headers())
                return resp.status_code < 400
        except Exception:
            return False

    async def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        dataset_ids = self.extra.get("dataset_ids") or self.extra.get("dataset")
        if not dataset_ids:
            return [{
                "title": "配置缺失",
                "snippet": "请在该知识库连接的「高级配置」中填写 dataset_ids（RAGFlow 数据集 ID，多个用逗号分隔）。",
                "source": self.base_url, "url": "", "score": 0.0,
            }]
        ids = [x.strip() for x in str(dataset_ids).split(",") if x.strip()]
        body = {"question": query, "dataset_ids": ids,
                "similarity_threshold": float(self.extra.get("threshold", 0.2)),
                "top_k": top_k}
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=self.timeout) as client:
                resp = await client.post(f"{self.base_url}/v1/retrieval",
                                         json=body, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            return [{"title": "检索失败", "snippet": str(exc),
                     "source": self.base_url, "url": "", "score": 0.0}]
        chunks = ((data.get("data") or {}).get("chunks")) or []
        results: List[Dict[str, Any]] = []
        for ch in chunks:
            doc = ch.get("document_keyword") or ""
            results.append({
                "title": doc or ch.get("name") or "未命名文档",
                "snippet": (ch.get("content_with_weight") or ch.get("content") or "")[:500],
                "source": "RAGFlow",
                "url": ch.get("url") or "",
                "score": float(ch.get("similarity") or 0.0),
            })
        return results


class NotionConnector(KnowledgeConnector):
    """Notion Search API（POST /v1/search）。

    api_key 为 Notion Integration Token；可按需配置 space_id 过滤。
    """

    platform = "notion"

    def _headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def test(self) -> bool:
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=self.timeout) as client:
                resp = await client.post(f"{self.base_url}/v1/search", json={"page_size": 1},
                                         headers=self._headers())
                return resp.status_code < 400
        except Exception:
            return False

    async def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        body: Dict[str, Any] = {
            "query": query,
            "page_size": top_k,
            "filter": {"value": "page", "property": "object"},
        }
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=self.timeout) as client:
                resp = await client.post(f"{self.base_url}/v1/search", json=body,
                                         headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            return [{"title": "检索失败", "snippet": str(exc),
                     "source": self.base_url, "url": "", "score": 0.0}]
        results: List[Dict[str, Any]] = []
        for item in data.get("results") or []:
            props = item.get("properties") or {}
            title = ""
            for v in props.values():
                if isinstance(v, dict) and v.get("type") == "title":
                    title = "".join((rt.get("plain_text") or "") for rt in (v.get("title") or []))
                    break
            url = item.get("url") or ""
            results.append({
                "title": title or item.get("id") or "未命名页面",
                "snippet": (item.get("plain_text") or "")[:500],
                "source": "Notion",
                "url": url,
                "score": 0.0,
            })
        return results


class WikiJsConnector(KnowledgeConnector):
    """Wiki.js GraphQL 检索（POST /graphql）。

    api_key 为 Wiki.js 站点 API Token；GraphQL 拉取 pages.list 后本地过滤
    （Wiki.js GraphQL 不提供全文搜索参数，适合中小规模站点）。
    """

    platform = "wiki_js"

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _gql(self, query: str) -> Any:
        async with httpx.AsyncClient(trust_env=False, timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/graphql",
                                     json={"query": query}, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            if data.get("errors"):
                raise RuntimeError(str(data["errors"])[:200])
            return data.get("data") or {}

    async def test(self) -> bool:
        try:
            data = await self._gql("{ pages { list(limit: 1) { id } } }")
            return bool(data)
        except Exception:
            return False

    async def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        if not self.api_key:
            return [{
                "title": "配置缺失",
                "snippet": "请在该知识库连接中填写 API Token（Wiki.js 站点 API Token）。",
                "source": self.base_url, "url": "", "score": 0.0,
            }]
        q = query.strip().lower()
        try:
            data = await self._gql(
                "{ pages { list(limit: 200) { id title path content } } }")
        except Exception as exc:
            return [{"title": "检索失败", "snippet": str(exc),
                     "source": self.base_url, "url": "", "score": 0.0}]
        pages = (data.get("pages") or {}).get("list") or []
        results: List[Dict[str, Any]] = []
        for p in pages:
            title = p.get("title") or p.get("path") or "未命名页面"
            content = p.get("content") or ""
            if q and q not in title.lower() and q not in content.lower():
                continue
            results.append({
                "title": title,
                "snippet": content[:500] or "(空页面)",
                "source": "Wiki.js",
                "url": p.get("path") or "",
                "score": 0.0,
            })
            if len(results) >= top_k:
                break
        return results


class FeishuWikiConnector(KnowledgeConnector):
    """飞书知识库检索（开放平台 Wiki API）。

    api_key 为应用 App Secret；高级配置 extra.app_id 必填（应用 App ID）。
    检索流程：tenant_access_token → 知识空间列表（含节点检索），本地过滤标题。
    """

    platform = "feishu_wiki"

    def _token_url(self) -> str:
        base = (self.base_url or "https://open.feishu.cn").rstrip("/")
        return f"{base}/open-apis/auth/v3/tenant_access_token/internal"

    async def _access_token(self) -> str:
        app_id = self.extra.get("app_id")
        if not app_id or not self.api_key:
            raise ValueError("缺少 app_id / app_secret")
        async with httpx.AsyncClient(trust_env=False, timeout=self.timeout) as client:
            resp = await client.post(self._token_url(), json={
                "app_id": app_id, "app_secret": self.api_key,
            })
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(str(data.get("msg") or data)[:200])
            return data.get("tenant_access_token") or ""

    def _headers(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    async def _spaces(self, token: str) -> List[Dict[str, Any]]:
        base = (self.base_url or "https://open.feishu.cn").rstrip("/")
        async with httpx.AsyncClient(trust_env=False, timeout=self.timeout) as client:
            resp = await client.get(f"{base}/open-apis/wiki/v2/spaces?page_size=50",
                                    headers=self._headers(token))
            resp.raise_for_status()
            data = resp.json()
            return (data.get("data") or {}).get("items") or []

    async def test(self) -> bool:
        try:
            token = await self._access_token()
            return bool(token)
        except Exception:
            return False

    async def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        if not (self.extra.get("app_id") and self.api_key):
            return [{
                "title": "配置缺失",
                "snippet": "请填写 API 密钥（App Secret）并在「高级配置」中填写 app_id（应用 App ID）。",
                "source": self.base_url, "url": "", "score": 0.0,
            }]
        q = query.strip().lower()
        try:
            token = await self._access_token()
            spaces = await self._spaces(token)
        except Exception as exc:
            return [{"title": "检索失败", "snippet": str(exc),
                     "source": self.base_url, "url": "", "score": 0.0}]
        results: List[Dict[str, Any]] = []
        for sp in spaces:
            name = (sp.get("name") or "")
            desc = (sp.get("description") or "")
            if q and q not in name.lower() and q not in desc.lower():
                continue
            results.append({
                "title": name or sp.get("space_id") or "未命名空间",
                "snippet": desc or "（飞书知识空间）",
                "source": "飞书知识库",
                "url": sp.get("url") or "",
                "score": 0.0,
            })
            if len(results) >= top_k:
                break
        return results


class UnsupportedConnector(KnowledgeConnector):
    """未实现平台的占位连接器：给出明确提示，不静默失败。"""

    def __init__(self, platform: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.platform = platform
        self._msg = (
            f"平台适配器「{platform}」尚未实现。"
            "可先使用「通用检索（generic）」适配器配置该服务的检索接口，"
            "或提供该平台的接口文档后补充适配器。"
        )

    async def test(self) -> bool:
        return False

    async def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        return [{
            "title": "适配器未实现",
            "snippet": self._msg,
            "source": self.base_url,
            "url": "",
            "score": 0.0,
        }]

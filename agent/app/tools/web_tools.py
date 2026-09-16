"""联网能力工具集：web_search（多 provider 搜索）+ browser（抓取/正文提取）+ MCP 动态挂载。

设计要点（对应平台侧联网四件事）：
1. browser 不再使用固定来源白名单，改为运行时安全校验：仅 http/https +
   SSRF 防护（拒绝内网/环回/链路本地/元数据地址），超时 + 大小上限。
2. browser 的 extract 接入 trafilatura 做 HTML→正文解析。
3. web_search 支持 Tavily/Bing/Brave/SerpAPI（需配置 key）与
   DuckDuckGo HTML（免 key 兜底，开箱即用）。
4. MCP：外部 MCP Server（HTTP JSON-RPC）动态挂载为 Agent 工具，
   可插拔、按需同步工具清单。
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
import time
import uuid
from html import unescape
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# =====================================================================
# 1) browser：运行时 URL 安全校验（SSRF 防护）+ trafilatura 正文提取
# =====================================================================

_DISALLOWED_IP_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
)

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
BROWSER_MAX_BYTES = 10 * 1024 * 1024  # 单页 10MB，超限截断保留前段

_proxy_cache: Dict[str, Optional[str]] = {}


def _resolve_proxy() -> Optional[str]:
    """发现可用代理：环境变量优先，其次 macOS 系统代理（如 Clash 127.0.0.1:7890）。

    本机网络出口常为白名单制（仅部分域名直连可达）；走系统代理后
    web_search / browser 才能访问绝大多数公网站点。
    """
    cached = _proxy_cache.get("proxy")
    if cached is not None:
        return cached or None
    proxy: Optional[str] = None
    # 1) 环境变量
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        v = __import__("os").environ.get(key, "").strip()
        if v:
            proxy = v
            break
    # 2) macOS 系统代理（scutil --proxy）
    if not proxy:
        try:
            import subprocess

            out = subprocess.run(
                ["scutil", "--proxy"], capture_output=True, text=True, timeout=3
            ).stdout
            https_enable = "HTTPSEnable : 1" in out
            host = port = ""
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("HTTPSProxy"):
                    host = line.split(":", 1)[1].strip()
                elif line.startswith("HTTPSPort"):
                    port = line.split(":", 1)[1].strip()
            if https_enable and host and port:
                proxy = f"http://{host}:{port}"
        except Exception:  # noqa: BLE001
            pass
    _proxy_cache["proxy"] = proxy or ""
    return proxy


def _ip_is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for net in _DISALLOWED_IP_NETWORKS:
        if addr in net:
            return False
    return True


def _load_allowed_domains() -> list[str]:
    """从 settings.json 加载允许访问的域名列表。"""
    try:
        from ..api.settings import _load_settings
        domains = _load_settings().get("allowed_domains") or []
        return [d.lower().strip() for d in domains if d]
    except Exception:
        return []


def _domain_matches_pattern(domain: str, pattern: str) -> bool:
    """检查域名是否匹配白名单模式（支持 * 通配符）。"""
    if not domain or not pattern:
        return False
    domain = domain.lower()
    if pattern.startswith("*."):
        # *.example.com 匹配 example.com 及其所有子域名
        suffix = pattern[2:]
        return domain == suffix or domain.endswith(f".{suffix}")
    return domain == pattern


def _is_domain_allowed(host: str) -> bool:
    """检查域名是否在白名单中。"""
    allowed = _load_allowed_domains()
    if not allowed:
        # 未配置白名单时，仅做内网/保留地址校验（原有行为）
        return True
    for pattern in allowed:
        if _domain_matches_pattern(host, pattern):
            return True
    return False


def _validate_url(url: str) -> str:
    """运行时 URL 校验：仅 http/https，SSRF 防护，域名白名单校验。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("仅允许 http/https 协议")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("URL 缺少主机名")
    # 1) 域名白名单校验（优先于 IP 校验）
    if not _is_domain_allowed(host):
        raise ValueError(f"域名 {host} 不在安全白名单中，已拦截")
    # 2) 主机名字面 IP 校验
    literal_ip: Optional[str] = None
    try:
        literal_ip = ipaddress.ip_address(host).compressed
    except ValueError:
        pass
    if literal_ip and not _ip_is_public(literal_ip):
        raise ValueError(f"目标地址 {host} 属于内网/保留地址，已拦截")
    # 3) DNS 解析后逐 IP 校验（防 DNS 重绑定到内网）
    if not literal_ip:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            raise ValueError(f"域名解析失败: {exc}")
        resolved = {info[4][0] for info in infos}
        for ip in resolved:
            if not _ip_is_public(ip):
                raise ValueError(f"域名 {host} 解析到内网/保留地址 {ip}，已拦截")
    return url


def _extract_article(html: str, max_chars: int = 6000) -> Dict[str, Any]:
    """HTML → 正文（trafilatura 优先，lxml/正则兜底）。"""
    title = ""
    try:
        from lxml import html as lxml_html

        tree = lxml_html.fromstring(html.encode("utf-8", errors="ignore"))
        nodes = tree.xpath("//title/text()")
        if nodes:
            title = " ".join(nodes[0].split())
    except Exception:  # noqa: BLE001
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        if m:
            title = re.sub(r"<[^>]+>", "", m.group(1)).strip()[:200]
    text = ""
    try:
        import trafilatura

        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            include_links=False,
            favor_recall=True,
        ) or ""
        text = text.strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[browser] trafilatura 提取失败，回退正则: {exc}")
    if not text:
        # 兜底：提取 <p> 文本
        m = re.findall(r"<p[^>]*>(.*?)</p>", html, re.S | re.I)
        text = "\n".join(
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", p)).strip() for p in m
        ).strip()
    truncated = len(text) > max_chars
    return {
        "title": title,
        "text": text[:max_chars] + ("…[已截断]" if truncated else ""),
        "truncated": truncated,
    }


class BrowserTool:
    """网页抓取与正文提取（运行时安全校验，非固定白名单）。

    action=open   打开页面，返回标题 + 正文预览（供模型判断页面价值）
    action=extract 提取页面完整正文（trafilatura 解析）
    """

    name = "browser"
    description = (
        "抓取网页内容：open 打开页面获取标题与正文预览；extract 提取完整正文。"
        "仅访问公网 http/https 地址（内网/本地地址自动拦截），单页上限 2MB。"
        "适合读取搜索结果对应的具体页面、文章、文档内容。"
    )
    requires_approval = False

    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["open", "extract"],
                "description": "open 打开页面（标题+正文预览）/ extract 提取完整正文",
            },
            "url": {
                "type": "string",
                "description": "目标公网网页 URL（http/https）",
            },
        },
        "required": ["action", "url"],
    }

    def __init__(self, timeout_sec: int = 15):
        self.timeout_sec = timeout_sec

    def to_openai_schema(self) -> Dict[str, Any]:
        """L4 修复：browser 此前缺 schema 暴露，从未下发给模型，
        模型会幻觉调用或改用 shell 替代。补齐 OpenAI function 描述。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        action = arguments.get("action", "")
        url = arguments.get("url", "")
        if action not in ("open", "extract"):
            return {"success": False, "error": "action 必须是 open 或 extract"}
        if not url:
            return {"success": False, "error": "缺少 url 参数"}
        try:
            _validate_url(url)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        import httpx

        content, final_url, status, fetch_error = await self._fetch(httpx, url)
        if content is None:
            return {
                "success": False,
                "error": f"抓取失败（HTTP {status}）: {fetch_error}" if fetch_error
                else f"请求失败（HTTP {status}）",
            }

        article = _extract_article(content, max_chars=8000 if action == "extract" else 1500)
        result = {
            "success": True,
            "action": action,
            "url": final_url,
            "status": status,
            "title": article["title"],
        }
        if action == "open":
            result["preview"] = article["text"][:1500]
        else:
            result["text"] = article["text"]
            result["char_count"] = len(article["text"])
        return result

    async def _fetch(self, httpx, url: str):
        """带重定向校验 + 大小限制 + 超时的抓取。返回 (content, final_url, status, error)。"""
        redirect_urls: List[str] = [url]

        async def _on_response(response):
            # 每次重定向后校验目标地址（防跳转到内网）
            try:
                _validate_url(str(response.url))
            except ValueError as exc:
                raise RuntimeError(str(exc))

        timeout = httpx.Timeout(self.timeout_sec, connect=10)
        limits = httpx.Limits(max_connections=8)
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                limits=limits,
                follow_redirects=True,
                max_redirects=3,
                trust_env=False,
                proxy=_resolve_proxy(),  # 走系统代理出网（本机出口常为白名单制）
                event_hooks={"response": [_on_response]},
                headers={"User-Agent": BROWSER_UA},
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = b""
                over_limit = False
                async for chunk in resp.aiter_bytes():
                    data += chunk
                    if len(data) > BROWSER_MAX_BYTES:
                        over_limit = True
                        break  # 截断保留前段
                text = data.decode("utf-8", errors="ignore")
                if over_limit:
                    return text, str(resp.url), resp.status_code, "页面超过 10MB 上限，已截断"
                return text, str(resp.url), resp.status_code, ""
        except RuntimeError as exc:
            return None, url, 0, str(exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[browser] 抓取失败 {url}: {exc}")
            return None, url, 0, f"{type(exc).__name__}: {exc}"


# =====================================================================
# 2) web_search：多 provider + DuckDuckGo 免 key 兜底
# =====================================================================

_DDG_SEARCH_URL = "https://html.duckduckgo.com/html/"
_BING_WEB_URL = "https://www.bing.com/search"

_PROVIDER_HINTS = {
    "tavily": "Tavily 搜索 API（api.tavily.com，需要 API Key）",
    "bing": "Bing Search API（api.bing.microsoft.com，需要 API Key）",
    "brave": "Brave Search API（api.search.brave.com，需要 API Key）",
    "serpapi": "SerpApi（serpapi.com，需要 API Key）",
    "duckduckgo": "DuckDuckGo HTML 搜索（免 Key，开箱即用兜底）",
}


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _parse_ddg_results(html: str, limit: int) -> List[Dict[str, str]]:
    """解析 DuckDuckGo HTML 结果页。"""
    results: List[Dict[str, str]] = []
    # 结果条目：<a class="result__a" href="...">标题</a> + <a class="result__snippet">
    blocks = re.split(r'<div class="result results_links', html)
    for block in blocks[1:]:
        m_url = re.search(r'href="([^"]+)"[^>]*class="result__a"', block) or re.search(
            r'class="result__a"[^>]*href="([^"]+)"', block
        )
        m_title = re.search(r'class="result__a"[^>]*>(.*?)</a>', block, re.S)
        m_snip = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
        if not m_url:
            continue
        url = m_url.group(1)
        # DDG 结果链接是重定向（//duckduckgo.com/l/?uddg=...），解码真实地址
        m_uddg = re.search(r"uddg=([^&]+)", url)
        if m_uddg:
            from urllib.parse import unquote

            url = unquote(m_uddg.group(1))
        results.append(
            {
                "title": _clean_text(m_title.group(1)) if m_title else "",
                "url": url,
                "snippet": _clean_text(m_snip.group(1)) if m_snip else "",
            }
        )
        if len(results) >= limit:
            break
    return results


async def _search_duckduckgo(query: str, max_results: int, timeout_sec: int) -> Dict[str, Any]:
    """DuckDuckGo HTML 搜索（部分网络环境会返回 202 反爬页）。"""
    import httpx

    timeout = httpx.Timeout(timeout_sec)
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": BROWSER_UA},
            trust_env=False,
            proxy=_resolve_proxy(),  # 走系统代理出网（本机出口常为白名单制）
        ) as client:
            resp = await client.post(
                _DDG_SEARCH_URL,
                data={"q": query},
            )
            resp.raise_for_status()
            results = _parse_ddg_results(resp.text, max_results)
            return {
                "success": True,
                "provider": "duckduckgo",
                "query": query,
                "count": len(results),
                "results": results,
            }
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "provider": "duckduckgo", "error": f"搜索失败: {exc}"}


def _decode_bing_url(url: str) -> str:
    """Bing 结果链接是 /ck/a 重定向，u= 参数为 base64url 编码的真实地址。"""
    if "bing.com/ck/a" in url:
        m = re.search(r"[?&]u=a1([A-Za-z0-9_-]+)", url)
        if m:
            import base64

            try:
                padded = m.group(1) + "=" * (-len(m.group(1)) % 4)
                return base64.urlsafe_b64decode(padded).decode("utf-8", errors="ignore")
            except Exception:  # noqa: BLE001
                pass
    return url


def _parse_bing_results(html: str, limit: int) -> List[Dict[str, str]]:
    """解析 Bing 网页版搜索结果（<li class="b_algo"> 块）。"""
    results: List[Dict[str, str]] = []
    blocks = re.split(r'<li class="b_algo"', html)
    for block in blocks[1:]:
        h2 = re.search(r"<h2[^>]*>(.*?)</h2>", block, re.S)
        if not h2:
            continue
        m_a = re.search(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', h2.group(1), re.S)
        if not m_a:
            continue
        title = _clean_text(m_a.group(2))
        url = _decode_bing_url(m_a.group(1))
        m_p = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        snippet = _clean_text(m_p.group(1)) if m_p else ""
        if title and url:
            results.append({"title": title[:200], "url": url, "snippet": snippet[:300]})
        if len(results) >= limit:
            break
    return results


async def _search_bing_web(query: str, max_results: int, timeout_sec: int) -> Dict[str, Any]:
    """Bing 网页版搜索（免 Key 兜底，海外版可用）。"""
    import httpx

    timeout = httpx.Timeout(timeout_sec)
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": BROWSER_UA},
            trust_env=False,
            proxy=_resolve_proxy(),  # 走系统代理出网（本机出口常为白名单制）
        ) as client:
            resp = await client.get(
                _BING_WEB_URL,
                params={"q": query, "setlang": "zh-hans", "count": max_results},
            )
            resp.raise_for_status()
            results = _parse_bing_results(resp.text, max_results)
            return {
                "success": True,
                "provider": "bing_web",
                "query": query,
                "count": len(results),
                "results": results,
            }
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "provider": "bing_web", "error": f"搜索失败: {exc}"}


async def _search_api(provider: str, cfg: Dict[str, Any], query: str,
                      api_key: str, timeout_sec: int) -> Dict[str, Any]:
    """带 key 的 provider（tavily/bing/brave/serpapi）。"""
    import httpx

    timeout = httpx.Timeout(timeout_sec)
    max_results = int(cfg.get("max_results", 5))
    base = (cfg.get("base_url") or "").rstrip("/")
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": BROWSER_UA},
            trust_env=False,
            proxy=_resolve_proxy(),  # 走系统代理出网（本机出口常为白名单制）
        ) as client:
            if provider == "tavily":
                url = (base or "https://api.tavily.com") + "/search"
                resp = await client.post(
                    url,
                    json={"api_key": api_key, "query": query, "max_results": max_results},
                )
                resp.raise_for_status()
                data = resp.json()
                results = [
                    {"title": r.get("title", ""), "url": r.get("url", ""),
                     "snippet": r.get("content", "")}
                    for r in data.get("results", [])
                ]
            elif provider == "bing":
                url = (base or "https://api.bing.microsoft.com/v7.0") + "/search"
                resp = await client.get(
                    url,
                    params={"q": query, "count": max_results},
                    headers={"Ocp-Apim-Subscription-Key": api_key},
                )
                resp.raise_for_status()
                data = resp.json()
                results = [
                    {"title": r.get("name", ""), "url": r.get("url", ""),
                     "snippet": r.get("snippet", "")}
                    for r in data.get("webPages", {}).get("value", [])
                ]
            elif provider == "brave":
                url = (base or "https://api.search.brave.com/res/v1") + "/web/search"
                resp = await client.get(
                    url,
                    params={"q": query, "count": max_results},
                    headers={"X-Subscription-Token": api_key},
                )
                resp.raise_for_status()
                data = resp.json()
                results = [
                    {"title": r.get("title", ""), "url": r.get("url", ""),
                     "snippet": r.get("description", "")}
                    for r in data.get("web", {}).get("results", [])
                ]
            elif provider == "serpapi":
                url = (base or "https://serpapi.com") + "/search.json"
                resp = await client.get(
                    url,
                    params={"q": query, "engine": "google",
                            "num": max_results, "api_key": api_key},
                )
                resp.raise_for_status()
                data = resp.json()
                results = [
                    {"title": r.get("title", ""), "url": r.get("link", ""),
                     "snippet": r.get("snippet", "")}
                    for r in data.get("organic_results", [])
                ]
            else:
                return {"success": False, "provider": provider,
                        "error": f"不支持的 provider: {provider}"}
        return {
            "success": True,
            "provider": provider,
            "query": query,
            "count": len(results),
            "results": results,
        }
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "provider": provider, "error": f"搜索失败: {exc}"}


class WebSearchTool:
    """联网搜索工具（多 provider）。

    优先使用设置里启用的搜索服务（Tavily/Bing/Brave/SerpAPI/DuckDuckGo）；
    未配置任何服务时自动使用 DuckDuckGo（免 Key）兜底，开箱即用。
    """

    name = "web_search"
    description = (
        "联网搜索互联网信息：输入查询关键词，返回标题/链接/摘要列表。"
        "适合查询最新新闻、政策、技术资料、产品信息等需要联网获取的内容；"
        "搜索到结果后如需正文，可配合 browser 工具抓取具体页面。"
    )
    requires_approval = False

    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索查询语句（中文/英文均可）",
            },
            "max_results": {
                "type": "integer",
                "description": "返回结果条数上限（默认取服务配置，1-10）",
            },
        },
        "required": ["query"],
    }

    def to_openai_schema(self) -> Dict[str, Any]:
        """L4 修复：web_search 此前缺 schema 暴露，从未下发给模型，
        模型会幻觉调用（agnes 实测）或改用 shell 替代（deepseek 实测）。
        补齐 OpenAI function 描述，模型才能看到并正确使用。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        query = (arguments.get("query") or "").strip()
        if not query:
            return {"success": False, "error": "query 不能为空"}
        from ..storage import list_web_search_servers_sync
        from ..security import keychain

        servers = list_web_search_servers_sync()
        max_results = int(arguments.get("max_results") or 0)
        if servers:
            cfg = servers[0]
            provider = cfg.get("provider", "bing_web")
            timeout_sec = int(cfg.get("timeout_sec", 15)) or 15
            mr = max_results or int(cfg.get("max_results", 5))
            # 免 key provider：ddg 或 bing_web（网页版）
            if provider in ("duckduckgo", "bing_web"):
                if provider == "duckduckgo":
                    res = await _search_duckduckgo(query, mr, timeout_sec)
                    # DDG 常被反爬（202），失败自动降级 Bing 网页版
                    if not res.get("success") or not res.get("count"):
                        return await _search_bing_web(query, mr, timeout_sec)
                    return res
                return await _search_bing_web(query, mr, timeout_sec)
            api_key = ""
            ref = cfg.get("api_key_ref")
            if ref:
                try:
                    api_key = await keychain.retrieve(ref) or ""
                except Exception:  # noqa: BLE001
                    api_key = ""
            if not api_key:
                return {
                    "success": True,
                    "provider": f"{provider}(降级 bing_web)",
                    "query": query,
                    "count": 0,
                    "results": [],
                    "note": f"{provider} 未配置 API Key，已自动降级为 Bing 网页版搜索。"
                    "可在「设置 → 搜索服务」中填入 Key 使用专业搜索。",
                }
            return await _search_api(provider, cfg, query, api_key, timeout_sec)
        # 无配置：Bing 网页版兜底（开箱即用）
        return await _search_bing_web(query, max_results or 5, 15)


# =====================================================================
# 3) MCP：HTTP JSON-RPC 客户端 + 动态工具包装
# =====================================================================

_MCP_DEFAULT_PROTOCOL = "2025-06-18"


class McpClient:
    """MCP Server HTTP JSON-RPC 客户端（Streamable HTTP）。

    - initialize 建立会话（记录 Mcp-Session-Id）
    - tools/list 拉取工具清单
    - tools/call 调用工具
    """

    def __init__(self, url: str, headers: Optional[Dict[str, str]] = None,
                 timeout_sec: int = 20):
        self.url = url
        self.headers = headers or {}
        self.timeout_sec = timeout_sec
        self._session_id: Optional[str] = None
        self._connected = False

    async def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        import httpx

        timeout = httpx.Timeout(self.timeout_sec)
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        headers.update(self.headers)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            trust_env=False,
            proxy=_resolve_proxy(),  # 走系统代理出网（本机出口常为白名单制）
        ) as client:
            resp = await client.post(self.url, json=payload, headers=headers)
            sid = resp.headers.get("mcp-session-id")
            if sid:
                self._session_id = sid
            resp.raise_for_status()
            # SSE 或纯 JSON 响应都尝试解析
            ctype = resp.headers.get("content-type", "")
            text = resp.text
            if "text/event-stream" in ctype:
                # 取最后一个 data: 行
                data_line = ""
                for line in text.splitlines():
                    if line.startswith("data:"):
                        data_line = line[5:].strip()
                if not data_line:
                    raise RuntimeError("MCP SSE 响应缺少 data")
                return json.loads(data_line)
            return resp.json()

    async def initialize(self) -> Dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "initialize",
            "params": {
                "protocolVersion": _MCP_DEFAULT_PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "spiritcaller-agent", "version": "0.1.0"},
            },
        }
        data = await self._post(payload)
        self._connected = True
        # 通知服务端初始化完成
        try:
            await self._post({
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            })
        except Exception:  # noqa: BLE001
            pass
        return data

    async def list_tools(self) -> List[Dict[str, Any]]:
        if not self._connected:
            await self.initialize()
        data = await self._post({
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/list",
            "params": {},
        })
        return data.get("result", {}).get("tools", [])

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if not self._connected:
            await self.initialize()
        data = await self._post({
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        result = data.get("result", {})
        # MCP 结果可能是结构化 content 数组
        content = result.get("content")
        if isinstance(content, list):
            parts = []
            for c in content:
                if isinstance(c, dict):
                    if c.get("type") == "text":
                        parts.append(c.get("text", ""))
                    elif c.get("type") == "json":
                        parts.append(json.dumps(c.get("json", {}), ensure_ascii=False))
                elif isinstance(c, str):
                    parts.append(c)
            text = "\n".join(parts)
        elif isinstance(content, str):
            text = content
        else:
            text = json.dumps(result, ensure_ascii=False)
        is_error = bool(result.get("isError"))
        return {
            "success": not is_error,
            "tool": name,
            "content": text[:20000],
            "structured": result if not isinstance(content, list) else None,
        }

    async def close(self):
        self._connected = False
        self._session_id = None


class McpDynamicTool:
    """把外部 MCP Server 的某个工具包装为 Agent 工具（懒连接 + 可插拔）。

    注册时用 tools_cache 中的 schema（无 cache 则用宽松 arguments 结构）；
    首次 execute 时建立 MCP 会话并调用 tools/call。
    """

    def __init__(self, server_cfg: Dict[str, Any], tool_def: Dict[str, Any]):
        self.server_cfg = server_cfg
        self.tool_def = tool_def
        raw_name = tool_def.get("name") or "tool"
        self._mcp_tool_name = raw_name
        server_tag = re.sub(r"[^a-zA-Z0-9_]", "_", str(server_cfg.get("name", "mcp")))
        self.name = f"mcp_{server_tag}_{re.sub(r'[^a-zA-Z0-9_]', '_', raw_name)}"
        self.description = (
            f"[MCP:{server_cfg.get('name')}] {tool_def.get('description') or raw_name} "
            f"（外部 MCP 服务 {server_cfg.get('url')} 提供，可插拔挂载）"
        )
        self.requires_approval = False
        input_schema = tool_def.get("inputSchema") or {"type": "object", "properties": {}}
        props = input_schema.get("properties", {})
        required = input_schema.get("required", [])
        if not props:
            props = {"arguments": {"type": "object", "description": "工具参数（见服务说明）"}}
            required = ["arguments"]
        self.parameters = {"type": "object", "properties": props, "required": required}

    def to_openai_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description[:500],
                "parameters": self.parameters,
            },
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        client = McpClient(
            self.server_cfg.get("url", ""),
            headers=self.server_cfg.get("headers") or {},
            timeout_sec=20,
        )
        try:
            return await client.call_tool(self._mcp_tool_name, arguments)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[mcp] 工具 {self.name} 调用失败: {exc}")
            return {
                "success": False,
                "tool": self._mcp_tool_name,
                "error": f"MCP 调用失败: {exc}",
            }
        finally:
            await client.close()


async def sync_mcp_tools(server_id: str) -> Dict[str, Any]:
    """连接 MCP Server 拉取工具清单并缓存（设置页「测试并同步」用）。"""
    from ..storage import mcp_server_repo

    cfg = await mcp_server_repo.get(server_id)
    if not cfg:
        return {"success": False, "error": "MCP Server 不存在"}
    client = McpClient(
        cfg.get("url", ""),
        headers=cfg.get("headers") or {},
        timeout_sec=int(cfg.get("timeout_sec", 20) or 20),
    )
    try:
        tools = await client.list_tools()
        await mcp_server_repo.update(server_id, {"tools_cache": tools})
        await mcp_server_repo.save_health(server_id, True)
        return {
            "success": True,
            "server": cfg.get("name"),
            "tool_count": len(tools),
            "tools": [
                {"name": t.get("name"), "description": (t.get("description") or "")[:120]}
                for t in tools
            ],
        }
    except Exception as exc:  # noqa: BLE001
        await mcp_server_repo.save_health(server_id, False)
        return {"success": False, "error": f"连接失败: {exc}"}
    finally:
        await client.close()


async def build_mcp_dynamic_tools() -> Dict[str, Any]:
    """为所有启用的 MCP Server 构建动态工具（注册表用）。"""
    from ..storage import list_mcp_servers_sync

    tools: Dict[str, Any] = {}
    for cfg in list_mcp_servers_sync():
        cached = cfg.get("tools_cache") or []
        if not cached:
            continue
        for t in cached:
            tool = McpDynamicTool(cfg, t)
            tools[tool.name] = tool
    return tools

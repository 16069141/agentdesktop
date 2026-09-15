"""模型列表接口（纯云端架构：由 llm_servers 连接表动态探测）。

不内置任何本地/公网模型清单；模型列表完全来自用户配置的
模型服务器连接（局域网或互联网，Ollama / OpenAI 兼容）。
返回时按各连接的 allowed_models 白名单过滤。
"""
import asyncio
import time

from fastapi import APIRouter

from .settings import _load_settings

router = APIRouter(prefix="/api/models", tags=["models"])

# 无本地硬编码模型：未配置服务器时返回空列表
DEFAULT_MODELS: list = []

# 单连接探测超时：不可达服务器（如已失效的免费网关）不能拖慢启动
PROBE_TIMEOUT_SEC = 4.0
# 进程级 TTL 缓存：探测结果在窗口期内直接复用，避免每次请求都重探不可达连接
_CACHE_TTL_SEC = 30
_MODEL_CACHE: dict = {"ts": 0.0, "models": []}


def _get_allowed_map() -> dict[str, list[str]]:
    """读取各连接的白名单：{provider_id(server_id): [model_id, ...]}，空列表表示不限制。"""
    try:
        from ..storage.llm_servers import list_servers_sync
        return {s["id"]: (s.get("allowed_models") or []) for s in list_servers_sync()}
    except Exception:
        return {}


def _filter_by_allowed(models: list[dict], allowed_map: dict[str, list[str]]) -> list[dict]:
    """按 provider 白名单过滤模型列表。"""
    # 大小写不敏感：后台填 GLM-4.7 也能命中真实模型 id glm-4.7。
    result = []
    for m in models:
        allowed = allowed_map.get(m.get("providerId", ""), [])
        if not allowed:
            result.append(m)
            continue
        allowed_lower = {a.strip().lower() for a in allowed if a.strip()}
        if str(m.get("id", "")).lower() in allowed_lower:
            result.append(m)
    return result


def _classify_scope(base_url: str) -> str:
    """根据模型服务器地址判断数据去向：
    local=本机 / lan=局域网内网 / public=公网。
    前端据此把"数据不出内网"可视化给用户。"""
    u = (base_url or "").lower()
    if not u:
        return "unknown"
    host = u
    for prefix in ("http://", "https://"):
        if host.startswith(prefix):
            host = host[len(prefix):]
    host = host.split("/")[0].split(":")[0]
    if host in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
        return "local"
    if (
        host.startswith("10.")
        or host.startswith("192.168.")
        or host.startswith("172.")
    ):
        # 172.16.0.0/12
        try:
            second = int(host.split(".")[1])
            if 16 <= second <= 31:
                return "lan"
        except Exception:
            pass
        if host.startswith("172.16.") or host.startswith("172.17.") or host.startswith("172.18.") \
           or host.startswith("172.19.") or host.startswith("172.2") or host.startswith("172.30.") \
           or host.startswith("172.31."):
            return "lan"
        return "public"
    return "public"


async def _probe_all_providers_parallel(settings, allowed_map: dict | None = None) -> list[dict]:
    """并行探测所有模型服务器，单个连接失败/超时不影响整体。

    使用 asyncio.gather 并行 + wait_for 短超时，避免某个不可达连接
    串行卡住整个模型列表接口（此前会让客户端启动页等 15s+）。

    无 /models 列表接口的连接（如讯飞星火）探测为空时，用该连接的
    allowed_models 白名单兜底——白名单即用户手动声明的可用模型。
    """
    from ..providers.base import build_providers
    from ..storage.llm_servers import LlmServerRepo

    if allowed_map is None:
        allowed_map = _get_allowed_map()
    providers = build_providers(settings)

    async def probe(p):
        try:
            model_list = await asyncio.wait_for(p.list_models(), timeout=PROBE_TIMEOUT_SEC)
        except Exception as exc:
            print(f"[models] 探测 {p.provider_id} 失败: {exc}")
            model_list = []
        base_url = getattr(p, "base_url", "") or ""
        scope = _classify_scope(base_url)
        if model_list:
            # 探测成功 → 写回数据库缓存，下次启动 build_providers 直接预填充，接口秒回
            try:
                repo = LlmServerRepo()
                await repo.save_models_cache(
                    p.provider_id, [m.id for m in model_list]
                )
            except Exception:
                pass
            return [
                {
                    "id": m.id,
                    "name": m.name,
                    "providerId": m.provider,
                    "isPublic": False,  # 连接模型统一不标记为公网
                    "scope": scope,
                    "description": f"来自 {p.name}",
                }
                for m in model_list
            ]
        # 无模型列表接口（讯飞星火等）→ 白名单兜底，让用户手动填的模型可被选择
        allowed = [a.strip() for a in (allowed_map.get(p.provider_id) or []) if a.strip()]
        if allowed:
            return [
                {
                    "id": a,
                    "name": a,
                    "providerId": p.provider_id,
                    "isPublic": False,
                    "scope": scope,
                    "description": f"来自 {p.name}（无模型列表接口，白名单指定）",
                }
                for a in allowed
            ]
        return []

    results = await asyncio.gather(*(probe(p) for p in providers))
    return [m for group in results for m in group]


async def _get_models_cached(settings, force: bool = False, allowed_map: dict | None = None) -> list[dict]:
    """带 TTL 的模型列表获取：非强制刷新时复用进程级缓存，接口秒回。"""
    now = time.time()
    if (
        not force
        and _MODEL_CACHE["models"]
        and (now - _MODEL_CACHE["ts"]) < _CACHE_TTL_SEC
    ):
        return _MODEL_CACHE["models"]
    models = await _probe_all_providers_parallel(settings, allowed_map=allowed_map)
    _MODEL_CACHE["ts"] = now
    _MODEL_CACHE["models"] = models
    return models


@router.get("")
async def list_models():
    """动态获取模型列表：TTL 缓存 + 并行探测，失败返回空列表。"""
    try:
        settings = _load_settings()
        allowed_map = _get_allowed_map()
        models = await _get_models_cached(settings, allowed_map=allowed_map)
        return _filter_by_allowed(models, allowed_map)
    except Exception as exc:
        print(f"[models] 动态获取失败: {exc}")
        return DEFAULT_MODELS


@router.post("/refresh")
async def refresh_models():
    """强制刷新模型列表（清除缓存后重新探测）。"""
    try:
        settings = _load_settings()
        allowed_map = _get_allowed_map()
        from ..providers.base import build_providers
        for p in build_providers(settings):
            p._models = []  # 清除实例缓存
        models = await _get_models_cached(settings, force=True, allowed_map=allowed_map)
        filtered = _filter_by_allowed(models, allowed_map)
        return {"models": filtered, "count": len(filtered)}
    except Exception as exc:
        return {"error": str(exc), "models": []}

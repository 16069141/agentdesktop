"""设置与安全路由。

接口：
  GET  /api/settings              — 读取配置（不含密钥明文）
  PUT  /api/settings              — 更新配置（安全相关：shell 白名单、端口等）
  GET  /api/security/keychain/status — 钥匙串状态摘要
"""
import json
import logging
import os
import re
import shutil
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..security import keychain

router = APIRouter(prefix="/api", tags=["settings"])
logger = logging.getLogger(__name__)

# ---- 路径解析 ----------------------------------------------------------------
# 打包态：.../颤翎子AI助手.app/Contents/Resources/{agent,config}
# 开发态：v2/{agent,config}
_HERE = os.path.dirname(os.path.abspath(__file__))            # .../agent/app/api
_AGENT_DIR = os.path.abspath(os.path.join(_HERE, "..", ".."))  # .../agent
_RESOURCES_DIR = os.path.abspath(os.path.join(_AGENT_DIR, ".."))
# 包内「出厂默认配置」：只读，随 extraResources 打进 Resources/config
BUNDLED_CONFIG_PATH = os.path.join(_RESOURCES_DIR, "config", "settings.json")


def _writable_config_path() -> str:
    """可写配置路径：优先 AGENT_CONFIG_DIR（打包态由 Electron 指向 userData）。"""
    env_dir = os.environ.get("AGENT_CONFIG_DIR", "").strip()
    base = env_dir if env_dir else os.path.join(_RESOURCES_DIR, "config")
    return os.path.join(base, "settings.json")


def _home_roots() -> List[str]:
    home = os.path.expanduser("~")
    return [home, os.path.join(home, "Downloads"), os.path.join(home, "Desktop")]


_VAR_RE = re.compile(r"\$\{(\w+)\}|\$(\w+)")


def _expand_path(p: str) -> str:
    """展开配置里的路径模板：${HOME}/x、$HOME/x、~/x。跨机器分发必须，不能写死用户名。"""
    if not isinstance(p, str):
        return p

    def _sub(m: "re.Match[str]") -> str:
        name = m.group(1) or m.group(2) or ""
        if name == "HOME":
            return os.path.expanduser("~")
        if name in ("USER", "USERNAME"):
            return os.environ.get("USER") or os.environ.get("USERNAME") or name
        return os.environ.get(name, m.group(0))

    out = _VAR_RE.sub(_sub, p)
    if out.startswith("~"):
        out = os.path.expanduser(out)
    return out


# 默认配置（可被 PUT 覆盖到用户配置）
DEFAULT_SETTINGS = {
    "shell_whitelist": ["ls", "cat", "pwd", "echo", "head", "tail", "grep", "find", "wc", "sort", "uniq", "diff", "git", "python", "python3", "node", "npm", "curl", "wget", "pip", "pip3", "which", "env", "date", "whoami", "mkdir", "cp", "mv", "touch", "open"],
    "dangerous_patterns": ["rm -rf", "mkfs", "dd if=", ":() {", "> /"],
    "max_shell_timeout_sec": 10,
    "allowed_root_dirs": _home_roots(),
    "allowed_domains": ["github.com", "api.github.com", "raw.githubusercontent.com", "*.github.com"],
    # 在线技能市场根 URL（客户零配置：装好即见市场；环境变量 SKILL_HUB_URL / CLAWHUB_URL 追加生效）
    "skill_market_urls": [],
    "context_max_turns": 20,
    "context_system_ratio": 0.15,
    "context_history_ratio": 0.40,
    "context_generation_ratio": 0.45,
    # P1 长期记忆：跨会话记住用户偏好/项目事实，对话时自动检索注入
    "memory_enabled": True,
}

_bootstrapped = False


def _bootstrap_config() -> None:
    """首次启动：把包内默认配置复制到可写目录。

    打包态 Resources/ 在 /Applications 下对用户只读，直接写会 PermissionError；
    因此 Electron 会注入 AGENT_CONFIG_DIR=<userData>/config，配置只在那里落盘。
    """
    global _bootstrapped
    if _bootstrapped:
        return
    _bootstrapped = True
    dst = _writable_config_path()
    if os.path.exists(dst) or not os.path.exists(BUNDLED_CONFIG_PATH):
        return
    if os.path.abspath(dst) == os.path.abspath(BUNDLED_CONFIG_PATH):
        return
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(BUNDLED_CONFIG_PATH, dst)
        logger.info(f"[settings] 已初始化用户配置: {dst}")
    except OSError as e:
        logger.warning(f"[settings] 初始化用户配置失败（将仅用默认配置）: {e}")


def _load_settings() -> Dict[str, Any]:
    _bootstrap_config()
    try:
        with open(_writable_config_path(), "r", encoding="utf-8") as f:
            saved = json.load(f)
        merged = {**DEFAULT_SETTINGS, **saved}
    except (FileNotFoundError, json.JSONDecodeError):
        merged = DEFAULT_SETTINGS.copy()
    # 确保必选键存在
    for k, v in DEFAULT_SETTINGS.items():
        merged.setdefault(k, v)
    # 路径模板展开（${HOME} / ~），保证跨机器、跨用户可用
    roots = merged.get("allowed_root_dirs")
    if isinstance(roots, list):
        merged["allowed_root_dirs"] = [_expand_path(p) for p in roots if isinstance(p, str)]
    # 兜底：若展开后为空（配置被改坏），回落到家目录
    if not merged.get("allowed_root_dirs"):
        merged["allowed_root_dirs"] = _home_roots()
    return merged


def _save_settings(s: Dict[str, Any]) -> None:
    dst = _writable_config_path()
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


class SettingsUpdate(BaseModel):
    shell_whitelist: list[str] | None = None
    dangerous_patterns: list[str] | None = None
    max_shell_timeout_sec: int | None = None
    allowed_root_dirs: list[str] | None = None
    allowed_domains: list[str] | None = None
    skill_market_urls: list[str] | None = None
    context_max_turns: int | None = None
    context_system_ratio: float | None = None
    context_history_ratio: float | None = None
    context_generation_ratio: float | None = None
    memory_enabled: bool | None = None  # P1 长期记忆开关
    model_providers: list[dict] | None = None  # 私有模型提供商配置（迁移至 llm_servers）


# 占位符：配置文件中 apiKey 字段仅存此标记，真实值在钥匙串（apiKeyRef）
_PLACEHOLDER_KEY = "**stored**"


async def _persist_provider_keys(
    providers: list[dict], existing_map: Dict[str, dict]
) -> list[dict]:
    """把 provider 的明文 apiKey 写入钥匙串；配置只保留引用名与占位符。"""
    result: list[dict] = []
    for p in providers:
        pid = p.get("id", "")
        api_key = p.get("apiKey") or ""
        api_key_ref = p.get("apiKeyRef")
        out = {k: v for k, v in p.items() if k != "apiKey"}
        if api_key and api_key != _PLACEHOLDER_KEY:
            # 新密钥（或已修改）→ 写入钥匙串
            ref = f"provider:{pid}"
            try:
                await keychain.store(ref, api_key)
                out["apiKeyRef"] = ref
                out["apiKey"] = _PLACEHOLDER_KEY
            except Exception:
                # keyring 不可用：降级为明文存储并告警（不静默丢密钥，但明显不推荐）
                logger.warning(
                    f"[settings] keychain 不可用，provider {pid} 的 apiKey 将明文存储（不推荐）"
                )
                out["apiKeyRef"] = None
                out["apiKey"] = api_key
        else:
            # 未变更：沿用原引用；连引用都没有则视为无密钥
            prev = existing_map.get(pid, {})
            out["apiKeyRef"] = api_key_ref or prev.get("apiKeyRef")
            out["apiKey"] = (
                _PLACEHOLDER_KEY if (out["apiKeyRef"] or api_key == _PLACEHOLDER_KEY) else ""
            )
        result.append(out)
    return result


@router.get("/settings")
async def get_settings() -> Dict[str, Any]:
    s = _load_settings()
    providers = s.get("model_providers") or []
    for p in providers:
        # 任何情况下都不向前端返回明文密钥
        if p.get("apiKey"):
            p["apiKey"] = _PLACEHOLDER_KEY
    return s


@router.put("/settings")
async def update_settings(body: SettingsUpdate) -> Dict[str, Any]:
    current = _load_settings()
    updates = body.model_dump(exclude_none=True)
    # 处理 provider 密钥：明文 → 钥匙串
    if "model_providers" in updates:
        existing = current.get("model_providers") or []
        existing_map = {p.get("id"): p for p in existing}
        updates["model_providers"] = await _persist_provider_keys(
            updates["model_providers"], existing_map
        )
    current.update(updates)
    _save_settings(current)
    return current


@router.get("/security/keychain/status")
async def keychain_status() -> Dict[str, Any]:
    """返回钥匙串中已存储的 apiKeyRef 列表（不含实际值）。"""
    refs = await keychain.list_keys()
    backend_available = keychain._HAS_KEYRING and keychain.ensure_keyring()
    return {
        "keys_stored": len(refs),
        "keys": refs,
        "backend_available": backend_available,
    }

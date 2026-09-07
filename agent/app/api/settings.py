"""设置与安全路由。

接口：
  GET  /api/settings              — 读取配置（不含密钥明文）
  PUT  /api/settings              — 更新配置（安全相关：shell 白名单、端口等）
  GET  /api/security/keychain/status — 钥匙串状态摘要
"""
import json
import logging
import os
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..security import keychain

router = APIRouter(prefix="/api", tags=["settings"])
logger = logging.getLogger(__name__)

# 默认配置（可被 PUT 覆盖到 config/settings.json）
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "config", "settings.json")
DEFAULT_SETTINGS = {
    "shell_whitelist": ["ls", "cat", "pwd", "echo", "head", "tail", "grep", "find", "wc", "sort", "uniq", "diff", "git", "python", "node", "npm", "curl", "wget"],
    "dangerous_patterns": ["rm -rf", "mkfs", "dd if=", ":() {", "> /"],
    "max_shell_timeout_sec": 30,
    "allowed_root_dirs": [os.path.expanduser("~")],
    "context_max_turns": 20,
    "context_system_ratio": 0.15,
    "context_history_ratio": 0.40,
    "context_generation_ratio": 0.45,
}


def _load_settings() -> Dict[str, Any]:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        merged = {**DEFAULT_SETTINGS, **saved}
    except FileNotFoundError:
        merged = DEFAULT_SETTINGS.copy()
    # 确保必选键存在
    for k, v in DEFAULT_SETTINGS.items():
        merged.setdefault(k, v)
    return merged


def _save_settings(s: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


class SettingsUpdate(BaseModel):
    shell_whitelist: list[str] | None = None
    dangerous_patterns: list[str] | None = None
    max_shell_timeout_sec: int | None = None
    allowed_root_dirs: list[str] | None = None
    context_max_turns: int | None = None
    context_system_ratio: float | None = None
    context_history_ratio: float | None = None
    context_generation_ratio: float | None = None
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

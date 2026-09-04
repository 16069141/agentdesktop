"""设置与安全路由。

接口：
  GET  /api/settings              — 读取配置（不含密钥明文）
  PUT  /api/settings              — 更新配置（安全相关：shell 白名单、端口等）
  GET  /api/security/keychain/status — 钥匙串状态摘要
"""
import json
import os
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..security import keychain

router = APIRouter(prefix="/api", tags=["settings"])

# 默认配置（可被 PUT 覆盖到 config/settings.json）
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "config", "settings.json")
DEFAULT_SETTINGS = {
    "shell_whitelist": ["ls", "cat", "pwd", "echo", "head", "tail", "grep", "find", "wc", "sort", "uniq", "diff", "git", "python", "node", "npm", "curl", "wget"],
    "dangerous_patterns": ["rm -rf", "mkfs", "dd if=", ":() {", "> /"],
    "max_shell_timeout_sec": 30,
    "allowed_root_dirs": [os.path.expanduser("~")],
    "rag_chunk_size": 500,
    "rag_chunk_overlap": 50,
    "context_max_turns": 20,
    "context_system_ratio": 0.15,
    "context_history_ratio": 0.40,
    "context_rag_ratio": 0.20,
    "context_generation_ratio": 0.25,
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
    rag_chunk_size: int | None = None
    rag_chunk_overlap: int | None = None
    context_max_turns: int | None = None
    context_system_ratio: float | None = None
    context_history_ratio: float | None = None
    context_rag_ratio: float | None = None
    context_generation_ratio: float | None = None


@router.get("/settings")
async def get_settings() -> Dict[str, Any]:
    return _load_settings()


@router.put("/settings")
async def update_settings(body: SettingsUpdate) -> Dict[str, Any]:
    current = _load_settings()
    updates = body.model_dump(exclude_none=True)
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

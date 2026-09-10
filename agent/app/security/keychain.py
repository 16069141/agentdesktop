"""系统钥匙串（Keychain）封装。

跨平台抽象：
- macOS → Keychain (keyring)
- Windows → Credential Manager (keyring)
- Linux → SecretService / dbus (keyring)

配置中只存 apiKeyRef 引用名，运行时从钥匙串读取实际密钥。
禁止将密钥明文写入配置文件或日志。
"""
import logging
from typing import List, Optional

try:
    import keyring
    _HAS_KEYRING = True
except ImportError:
    _HAS_KEYRING = False

logger = logging.getLogger(__name__)
SERVICE_NAME = "private-ai-agent"


def ensure_keyring() -> bool:
    """返回是否可用 keyring 后端。"""
    if not _HAS_KEYRING:
        logger.warning("[keychain] keyring 包未安装，密钥存储功能不可用")
        return False
    try:
        backend = keyring.get_keyring().__class__.__name__
        logger.info("[keychain] 使用后端: %s", backend)
        return True
    except Exception as e:
        logger.warning("[keychain] keyring 初始化失败: %s", e)
        return False


async def store(key_ref: str, value: str) -> bool:
    """存储密钥到钥匙串。key_ref 即配置中的 apiKeyRef 字段。"""
    if not _HAS_KEYRING or not ensure_keyring():
        raise RuntimeError("keychain backend unavailable")
    try:
        keyring.set_password(SERVICE_NAME, key_ref, value)
        logger.info("[keychain] 已存储密钥引用: %s", key_ref)
        return True
    except Exception as e:
        logger.exception("[keychain] 存储失败: %s", key_ref)
        raise


def store_sync(key_ref: str, value: str) -> bool:
    """同步存储密钥（用于非 async 场景，如 Provider 构建）。"""
    if not _HAS_KEYRING or not ensure_keyring():
        return False
    try:
        keyring.set_password(SERVICE_NAME, key_ref, value)
        return True
    except Exception as e:
        logger.warning("[keychain] 同步存储失败: %s", key_ref)
        return False


async def retrieve(key_ref: str) -> Optional[str]:
    """从钥匙串读取密钥。"""
    if not _HAS_KEYRING:
        return None
    try:
        value = keyring.get_password(SERVICE_NAME, key_ref)
        if value is None:
            logger.warning("[keychain] 未找到密钥引用: %s", key_ref)
        return value
    except Exception as e:
        logger.exception("[keychain] 读取失败: %s", key_ref)
        return None


def retrieve_sync(key_ref: str) -> Optional[str]:
    """同步读取密钥（用于非 async 场景）。"""
    if not _HAS_KEYRING:
        return None
    try:
        return keyring.get_password(SERVICE_NAME, key_ref)
    except Exception as e:
        logger.warning("[keychain] 同步读取失败: %s", key_ref)
        return None


async def delete(key_ref: str) -> bool:
    """删除钥匙串中的密钥引用。"""
    if not _HAS_KEYRING:
        return False
    try:
        keyring.delete_password(SERVICE_NAME, key_ref)
        logger.info("[keychain] 已删除密钥引用: %s", key_ref)
        return True
    except Exception as e:
        logger.warning("[keychain] 删除失败（可能不存在）: %s", key_ref)
        return False


async def list_keys() -> List[str]:
    """列出钥匙串中本服务所有密钥引用（不含值）。
    
    注意：keyring 本身不直接支持 list，这里用 Windows/Linux 的 SecretService
    实现；macOS Keychain 只能通过导出列表解析。MVP 简化为：
    - 若后端支持 get_all_keyrings / list 则返回
    - 否则返回空列表（依赖调用方维护已知 ref 列表）
    """
    if not _HAS_KEYRING:
        return []
    try:
        # keyring 没有标准 list API，用 backend 特判
        backend = keyring.get_keyring()
        if hasattr(backend, "get_all_keyrings"):
            # SecretService 后端
            return [ref for ref in dir(backend) if ref.startswith("get_") or ref.startswith("list_")]
        # macOS/Windows 不支持遍历，返回空（调用方应维护已知 ref 列表）
        return []
    except Exception as e:
        logger.warning("[keychain] 列表失败: %s", e)
        return []


async def resolve_api_key(provider_config: dict) -> Optional[str]:
    """从 provider 配置中提取 apiKeyRef 并解析为真实密钥。
    
    若配置无 apiKeyRef（本地模型），返回 None。
    """
    ref = provider_config.get("apiKeyRef")
    if not ref:
        return None
    return await retrieve(ref)

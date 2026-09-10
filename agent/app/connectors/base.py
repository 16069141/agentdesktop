"""连接器抽象层（需求 §2.1 / §2.3 MCP 集成模块修改版）。

ConnectorBase 定义企业系统连接器的统一契约：
- 元数据（name / kind / permissions / version）
- 鉴权（api_key / basic / oauth2，密钥走 keychain）
- 调用（invoke 统一入口，透传 X-User-* 身份头）
- 审计（每次调用记录完整链路）
- 健康检查（test/health）

P1 起落地 ERP / CRM / OA 具体连接器；P0 先交付框架与注册机制。
"""
from __future__ import annotations

import logging
import time
import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ConnectorBase(ABC):
    """企业系统连接器基类。子类实现 test/invoke 两个核心方法。"""

    kind: str = "connector"            # 统一元数据中心 kind
    connector_type: str = "generic"    # erp / crm / oa / wms / ...
    name: str = "generic"
    description: str = "企业系统连接器"
    permission_level: str = "P3"       # 连接器默认 P3（网络访问 + 只读）
    operations: List[str] = ["read"]   # 声明支持的操作（read/create/update/delete）

    def __init__(self, config: Dict[str, Any]):
        self.config = config or {}
        self.connector_id = self.config.get("id") or f"conn_{uuid.uuid4().hex[:8]}"
        self.base_url = (self.config.get("base_url") or "").rstrip("/")
        self.auth_type = self.config.get("auth_type", "api_key")
        self.api_key = self.config.get("api_key", "")

    # ---- 元数据 ----
    def metadata(self) -> Dict[str, Any]:
        return {
            "id": self.connector_id,
            "kind": self.kind,
            "connectorType": self.connector_type,
            "name": self.name,
            "description": self.description,
            "permissionLevel": self.permission_level,
            "operations": self.operations,
            "baseUrl": self.base_url,
            "version": "0.1.0",
        }

    # ---- 鉴权头 ----
    def auth_headers(self) -> Dict[str, str]:
        headers = {}
        if self.auth_type == "api_key" and self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        elif self.auth_type == "basic":
            import base64
            token = base64.b64encode(self.api_key.encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        return headers

    def identity_headers(self, identity: Any) -> Dict[str, str]:
        """权限透传：以当前用户身份调用业务系统 API（需求 §2.3）。"""
        if identity is None:
            return {}
        return {
            "X-User-Id": getattr(identity, "user_id", "local"),
            "X-User-Name": getattr(identity, "username", "local"),
            "X-User-Role": getattr(identity, "role", "member"),
            "X-User-Data-Scope": getattr(identity, "data_scope", "personal"),
            "X-User-Department": getattr(identity, "department", ""),
        }

    # ---- 核心方法 ----
    @abstractmethod
    async def test(self) -> Dict[str, Any]:
        """连通性/健康检查：返回 {ok, latency_ms, detail}。"""

    @abstractmethod
    async def invoke(self, operation: str, params: Dict[str, Any],
                     identity: Any = None) -> Dict[str, Any]:
        """执行一次业务调用：返回统一结构 {success, data, error}。"""

    # ---- 审计封装 ----
    async def call(self, operation: str, params: Dict[str, Any],
                   identity: Any = None) -> Dict[str, Any]:
        """带审计的调用入口：invoke 的包装，记录完整链路。"""
        from ..audit.logger import record_tool_call

        t0 = time.monotonic()
        actor = getattr(identity, "username", "local") if identity else "local"
        try:
            result = await self.invoke(operation, params, identity=identity)
            success = bool(result.get("success"))
            payload = result if success else result
            error = result.get("error") if not success else None
            latency_ms = int((time.monotonic() - t0) * 1000)
            await record_tool_call(
                tool_name=f"{self.connector_type}.{operation}",
                arguments={"connector": self.connector_id, "operation": operation, **params},
                result=payload if success else None,
                error=error,
                actor=actor,
                latency_ms=latency_ms,
            )
            return result
        except Exception as exc:
            logger.exception("[connector] %s.%s 调用异常", self.connector_type, operation)
            await record_tool_call(
                tool_name=f"{self.connector_type}.{operation}",
                arguments={"connector": self.connector_id, "operation": operation, **params},
                error=str(exc), actor=actor,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
            return {"success": False, "error": str(exc)}


class ConnectorRegistry:
    """连接器注册表：元数据中心统一管理 connector 类条目。"""

    def __init__(self) -> None:
        self._connectors: Dict[str, ConnectorBase] = {}

    def register(self, connector: ConnectorBase) -> None:
        self._connectors[connector.connector_id] = connector
        logger.info("[connector] 注册连接器: %s (%s)",
                    connector.connector_id, connector.connector_type)

    def get(self, connector_id: str) -> Optional[ConnectorBase]:
        return self._connectors.get(connector_id)

    def list(self) -> List[Dict[str, Any]]:
        return [c.metadata() for c in self._connectors.values()]

    def unregister(self, connector_id: str) -> None:
        self._connectors.pop(connector_id, None)


# 全局注册表
_registry: Optional[ConnectorRegistry] = None


def get_connector_registry() -> ConnectorRegistry:
    global _registry
    if _registry is None:
        _registry = ConnectorRegistry()
    return _registry

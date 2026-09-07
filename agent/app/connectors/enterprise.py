"""企业系统连接器实现（需求 §2.1：ERP / CRM / OA 首批）。

每个连接器类型内置「操作 → REST 端点」默认模板，实例配置可整体覆盖或单操作覆盖。
统一契约（ConnectorBase）：
- test()    —— 连通性检查（GET base_url + 可选健康路径）
- invoke()  —— 按操作模板渲染路径/参数 → 带鉴权与身份透传的 REST 调用
- call()    —— 审计封装（基类提供）

其余连接器（WMS/EAM/HRM/MES/SRM/BI）P4 阶段按相同框架声明式补齐。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from .base import ConnectorBase, ConnectorRegistry, get_connector_registry

logger = logging.getLogger(__name__)

# ============ 操作模板 ============
# 每项：method / path（{param} 为路径占位）/ permission / risk / description
ERP_OPERATIONS: Dict[str, Dict[str, Any]] = {
    "order_list": {
        "method": "GET",
        "path": "/api/orders",
        "permission": "read",
        "risk": "low",
        "description": "订单列表查询",
    },
    "order_query": {
        "method": "GET",
        "path": "/api/orders/{order_id}",
        "permission": "read",
        "risk": "low",
        "description": "订单详情查询",
    },
    "order_create": {
        "method": "POST",
        "path": "/api/orders",
        "permission": "create",
        "risk": "medium",
        "description": "创建订单",
    },
    "inventory_list": {
        "method": "GET",
        "path": "/api/inventory",
        "permission": "read",
        "risk": "low",
        "description": "库存检索",
    },
    "inventory_query": {
        "method": "GET",
        "path": "/api/inventory/{sku}",
        "permission": "read",
        "risk": "low",
        "description": "按 SKU 查库存",
    },
    "finance_reconcile": {
        "method": "GET",
        "path": "/api/finance/reconcile",
        "permission": "read",
        "risk": "medium",
        "description": "财务对账查询",
    },
    "purchase_list": {
        "method": "GET",
        "path": "/api/purchases",
        "permission": "read",
        "risk": "low",
        "description": "采购单查询",
    },
    "purchase_create": {
        "method": "POST",
        "path": "/api/purchases",
        "permission": "create",
        "risk": "medium",
        "description": "创建采购单",
    },
    "schedule_query": {
        "method": "GET",
        "path": "/api/production/schedule",
        "permission": "read",
        "risk": "low",
        "description": "生产排程查询",
    },
}

CRM_OPERATIONS: Dict[str, Dict[str, Any]] = {
    "customer_list": {
        "method": "GET",
        "path": "/api/customers",
        "permission": "read",
        "risk": "low",
        "description": "客户列表",
    },
    "customer_query": {
        "method": "GET",
        "path": "/api/customers/{customer_id}",
        "permission": "read",
        "risk": "low",
        "description": "客户信息查询",
    },
    "customer_create": {
        "method": "POST",
        "path": "/api/customers",
        "permission": "create",
        "risk": "medium",
        "description": "新增客户",
    },
    "opportunity_list": {
        "method": "GET",
        "path": "/api/opportunities",
        "permission": "read",
        "risk": "low",
        "description": "商机列表",
    },
    "opportunity_query": {
        "method": "GET",
        "path": "/api/opportunities/{opportunity_id}",
        "permission": "read",
        "risk": "low",
        "description": "商机详情",
    },
    "opportunity_create": {
        "method": "POST",
        "path": "/api/opportunities",
        "permission": "create",
        "risk": "medium",
        "description": "新建商机",
    },
    "opportunity_update": {
        "method": "PUT",
        "path": "/api/opportunities/{opportunity_id}",
        "permission": "update",
        "risk": "medium",
        "description": "更新商机",
    },
    "followup_list": {
        "method": "GET",
        "path": "/api/followups",
        "permission": "read",
        "risk": "low",
        "description": "跟进记录查询",
    },
    "followup_create": {
        "method": "POST",
        "path": "/api/followups",
        "permission": "create",
        "risk": "medium",
        "description": "新增跟进记录",
    },
    "contract_list": {
        "method": "GET",
        "path": "/api/contracts",
        "permission": "read",
        "risk": "low",
        "description": "合同查询",
    },
    "payment_list": {
        "method": "GET",
        "path": "/api/payments",
        "permission": "read",
        "risk": "low",
        "description": "回款查询",
    },
    "funnel_query": {
        "method": "GET",
        "path": "/api/sales-funnel",
        "permission": "read",
        "risk": "low",
        "description": "销售漏斗分析",
    },
}

OA_OPERATIONS: Dict[str, Dict[str, Any]] = {
    "approval_list": {
        "method": "GET",
        "path": "/api/approvals",
        "permission": "read",
        "risk": "low",
        "description": "审批流程查询",
    },
    "approval_query": {
        "method": "GET",
        "path": "/api/approvals/{approval_id}",
        "permission": "read",
        "risk": "low",
        "description": "审批详情",
    },
    "approval_create": {
        "method": "POST",
        "path": "/api/approvals",
        "permission": "create",
        "risk": "high",
        "description": "发起审批流程",
    },
    "leave_create": {
        "method": "POST",
        "path": "/api/leaves",
        "permission": "create",
        "risk": "medium",
        "description": "请假申请",
    },
    "expense_create": {
        "method": "POST",
        "path": "/api/expenses",
        "permission": "create",
        "risk": "medium",
        "description": "报销申请",
    },
    "announcement_list": {
        "method": "GET",
        "path": "/api/announcements",
        "permission": "read",
        "risk": "low",
        "description": "公告通知查询",
    },
}

# ============ Phase B (P4)：剩余连接器（声明式补齐，§B.5） ============

WMS_OPERATIONS: Dict[str, Dict[str, Any]] = {
    "inbound_list": {
        "method": "GET",
        "path": "/api/inbound",
        "permission": "read",
        "risk": "low",
        "description": "入库单列表",
    },
    "inbound_query": {
        "method": "GET",
        "path": "/api/inbound/{inbound_id}",
        "permission": "read",
        "risk": "low",
        "description": "入库单详情",
    },
    "outbound_list": {
        "method": "GET",
        "path": "/api/outbound",
        "permission": "read",
        "risk": "low",
        "description": "出库单列表",
    },
    "outbound_create": {
        "method": "POST",
        "path": "/api/outbound",
        "permission": "create",
        "risk": "medium",
        "description": "创建出库单",
    },
    "stock_query": {
        "method": "GET",
        "path": "/api/stock/{sku}",
        "permission": "read",
        "risk": "low",
        "description": "按 SKU 查库存",
    },
    "stock_move": {
        "method": "POST",
        "path": "/api/stock/move",
        "permission": "update",
        "risk": "medium",
        "description": "库存移动/调拨",
    },
    "inventory_count": {
        "method": "GET",
        "path": "/api/inventory-count",
        "permission": "read",
        "risk": "low",
        "description": "盘点记录查询",
    },
}

EAM_OPERATIONS: Dict[str, Dict[str, Any]] = {
    "asset_list": {
        "method": "GET",
        "path": "/api/assets",
        "permission": "read",
        "risk": "low",
        "description": "设备资产列表",
    },
    "asset_query": {
        "method": "GET",
        "path": "/api/assets/{asset_id}",
        "permission": "read",
        "risk": "low",
        "description": "设备资产详情",
    },
    "asset_create": {
        "method": "POST",
        "path": "/api/assets",
        "permission": "create",
        "risk": "medium",
        "description": "新增设备资产",
    },
    "maintenance_plan": {
        "method": "GET",
        "path": "/api/maintenance/plans",
        "permission": "read",
        "risk": "low",
        "description": "保养计划查询",
    },
    "work_order_list": {
        "method": "GET",
        "path": "/api/work-orders",
        "permission": "read",
        "risk": "low",
        "description": "工单列表",
    },
    "work_order_create": {
        "method": "POST",
        "path": "/api/work-orders",
        "permission": "create",
        "risk": "medium",
        "description": "创建维修工单",
    },
    "inspection_record": {
        "method": "POST",
        "path": "/api/inspections",
        "permission": "create",
        "risk": "medium",
        "description": "点检记录上报",
    },
}

HRM_OPERATIONS: Dict[str, Dict[str, Any]] = {
    "employee_list": {
        "method": "GET",
        "path": "/api/employees",
        "permission": "read",
        "risk": "low",
        "description": "员工列表",
    },
    "employee_query": {
        "method": "GET",
        "path": "/api/employees/{employee_id}",
        "permission": "read",
        "risk": "low",
        "description": "员工信息查询",
    },
    "department_list": {
        "method": "GET",
        "path": "/api/departments",
        "permission": "read",
        "risk": "low",
        "description": "部门结构查询",
    },
    "attendance_list": {
        "method": "GET",
        "path": "/api/attendance",
        "permission": "read",
        "risk": "medium",
        "description": "考勤记录查询",
    },
    "leave_list": {
        "method": "GET",
        "path": "/api/leaves",
        "permission": "read",
        "risk": "low",
        "description": "请假记录查询",
    },
    "payroll_query": {
        "method": "GET",
        "path": "/api/payroll",
        "permission": "read",
        "risk": "high",
        "description": "薪酬查询（敏感）",
    },
}

MES_OPERATIONS: Dict[str, Dict[str, Any]] = {
    "work_order_list": {
        "method": "GET",
        "path": "/api/mes/work-orders",
        "permission": "read",
        "risk": "low",
        "description": "生产工单列表",
    },
    "work_order_query": {
        "method": "GET",
        "path": "/api/mes/work-orders/{work_order_id}",
        "permission": "read",
        "risk": "low",
        "description": "生产工单详情",
    },
    "production_report": {
        "method": "POST",
        "path": "/api/mes/reports",
        "permission": "create",
        "risk": "medium",
        "description": "生产报工",
    },
    "quality_inspection": {
        "method": "GET",
        "path": "/api/mes/quality",
        "permission": "read",
        "risk": "low",
        "description": "质量检验记录",
    },
    "machine_status": {
        "method": "GET",
        "path": "/api/mes/machines",
        "permission": "read",
        "risk": "low",
        "description": "设备运行状态",
    },
    "material_feed": {
        "method": "POST",
        "path": "/api/mes/material-feed",
        "permission": "create",
        "risk": "medium",
        "description": "物料上料",
    },
}

SRM_OPERATIONS: Dict[str, Dict[str, Any]] = {
    "supplier_list": {
        "method": "GET",
        "path": "/api/suppliers",
        "permission": "read",
        "risk": "low",
        "description": "供应商列表",
    },
    "supplier_query": {
        "method": "GET",
        "path": "/api/suppliers/{supplier_id}",
        "permission": "read",
        "risk": "low",
        "description": "供应商详情",
    },
    "supplier_create": {
        "method": "POST",
        "path": "/api/suppliers",
        "permission": "create",
        "risk": "medium",
        "description": "新增供应商",
    },
    "quotation_list": {
        "method": "GET",
        "path": "/api/quotations",
        "permission": "read",
        "risk": "low",
        "description": "询价/报价查询",
    },
    "purchase_order_list": {
        "method": "GET",
        "path": "/api/srm/purchase-orders",
        "permission": "read",
        "risk": "low",
        "description": "采购订单查询",
    },
    "delivery_schedule": {
        "method": "GET",
        "path": "/api/deliveries",
        "permission": "read",
        "risk": "low",
        "description": "供应商交期查询",
    },
}

BI_OPERATIONS: Dict[str, Dict[str, Any]] = {
    "report_list": {
        "method": "GET",
        "path": "/api/reports",
        "permission": "read",
        "risk": "low",
        "description": "报表列表",
    },
    "report_query": {
        "method": "GET",
        "path": "/api/reports/{report_id}",
        "permission": "read",
        "risk": "low",
        "description": "报表详情/结果",
    },
    "dashboard_list": {
        "method": "GET",
        "path": "/api/dashboards",
        "permission": "read",
        "risk": "low",
        "description": "看板列表",
    },
    "metric_query": {
        "method": "GET",
        "path": "/api/metrics/{metric_name}",
        "permission": "read",
        "risk": "low",
        "description": "指标查询",
    },
    "export_report": {
        "method": "POST",
        "path": "/api/reports/{report_id}/export",
        "permission": "create",
        "risk": "low",
        "description": "报表导出",
    },
    "data_source_list": {
        "method": "GET",
        "path": "/api/data-sources",
        "permission": "read",
        "risk": "low",
        "description": "数据源清单",
    },
}

# 类型 → (连接器类, 默认操作模板)
CONNECTOR_TYPES: Dict[str, Dict[str, Any]] = {
    "erp": {
        "name": "ERP",
        "description": "企业资源计划系统连接器（订单/库存/财务/采购/排程）",
        "operations": ERP_OPERATIONS,
    },
    "crm": {
        "name": "CRM",
        "description": "客户关系管理系统连接器（客户/商机/跟进/合同/漏斗）",
        "operations": CRM_OPERATIONS,
    },
    "oa": {
        "name": "OA",
        "description": "办公自动化系统连接器（审批/请假/报销/公告）",
        "operations": OA_OPERATIONS,
    },
    # P4：生态补齐（§B.5，复用声明式框架）
    "wms": {
        "name": "WMS",
        "description": "仓库管理系统连接器（入库/出库/库存/盘点）",
        "operations": WMS_OPERATIONS,
    },
    "eam": {
        "name": "EAM",
        "description": "设备资产管理连接器（资产/保养/工单/点检）",
        "operations": EAM_OPERATIONS,
    },
    "hrm": {
        "name": "HRM",
        "description": "人力资源管理系统连接器（员工/考勤/薪酬/部门）",
        "operations": HRM_OPERATIONS,
    },
    "mes": {
        "name": "MES",
        "description": "制造执行系统连接器（工单/报工/质检/设备）",
        "operations": MES_OPERATIONS,
    },
    "srm": {
        "name": "SRM",
        "description": "供应商关系管理连接器（供应商/询价/采购/交期）",
        "operations": SRM_OPERATIONS,
    },
    "bi": {
        "name": "BI",
        "description": "商业智能系统连接器（报表/看板/指标/导出）",
        "operations": BI_OPERATIONS,
    },
}


class EnterpriseConnector(ConnectorBase):
    """企业系统 REST 连接器基类（API 直连接入方式，需求 §2.2）。"""

    kind = "connector"
    permission_level = "P3"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.connector_type = config.get("type", "generic")
        meta = CONNECTOR_TYPES.get(self.connector_type, {})
        self.name = config.get("name") or meta.get("name", self.connector_type)
        self.description = meta.get("description", "")
        self.operations: Dict[str, Dict[str, Any]] = {
            **meta.get("operations", {}),
            **(config.get("operations") or {}),  # 实例覆盖
        }
        self.timeout_sec = config.get("timeout_sec", 30)
        self.health_path = config.get("health_path", "")

    def _resolve(self, operation: str) -> Dict[str, Any]:
        tmpl = self.operations.get(operation)
        if tmpl is None:
            raise KeyError(f"连接器 {self.connector_type} 不支持操作: {operation}")
        return tmpl

    def _render(
        self, tmpl: Dict[str, Any], params: Dict[str, Any]
    ) -> tuple[str, Dict[str, Any]]:
        """渲染路径占位符，剩余参数转为查询参数（GET）或请求体（POST/PUT）。"""
        path = tmpl["path"]
        body_params = dict(params)
        import re

        for m in re.findall(r"\{(\w+)\}", path):
            key = m
            if key in body_params:
                path = path.replace("{" + key + "}", str(body_params.pop(key)))
            else:
                path = path.replace("{" + key + "}", "")
        return path, body_params

    async def test(self) -> Dict[str, Any]:
        import httpx  # 延迟导入，避免启动时加载

        t0 = time.monotonic()
        url = self.base_url + (self.health_path or "/health")
        headers = self.auth_headers()
        try:
            async with httpx.AsyncClient(
                trust_env=False, timeout=self.timeout_sec
            ) as client:
                resp = await client.get(url, headers=headers)
                ok = resp.status_code < 500
                return {
                    "ok": ok,
                    "latency_ms": int((time.monotonic() - t0) * 1000),
                    "status_code": resp.status_code,
                    "detail": resp.text[:200],
                }
        except Exception as exc:
            return {
                "ok": False,
                "latency_ms": int((time.monotonic() - t0) * 1000),
                "error": str(exc),
            }

    async def invoke(
        self, operation: str, params: Dict[str, Any], identity: Any = None
    ) -> Dict[str, Any]:
        import httpx  # 延迟导入，避免启动时加载

        try:
            tmpl = self._resolve(operation)
        except KeyError as exc:
            return {"success": False, "error": str(exc)}

        path, body_params = self._render(tmpl, params or {})
        url = f"{self.base_url}{path}"
        method = tmpl["method"].lower()
        headers = {**self.auth_headers(), **self.identity_headers(identity)}
        request_kwargs: Dict[str, Any] = {
            "headers": headers,
            "timeout": self.timeout_sec,
        }
        if method in ("get", "delete"):
            request_kwargs["params"] = body_params
        else:
            request_kwargs["json"] = body_params

        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                resp = await client.request(method, url, **request_kwargs)
                data = resp.json() if resp.content else {}
            ok = resp.status_code < 400
            return {
                "success": ok,
                "operation": operation,
                "connector": self.connector_id,
                "status_code": resp.status_code,
                "data": data if ok else {},
                "error": None if ok else f"HTTP {resp.status_code}: {resp.text[:200]}",
            }
        except Exception as exc:
            logger.exception(
                "[connector] %s.%s 请求失败", self.connector_type, operation
            )
            return {
                "success": False,
                "operation": operation,
                "connector": self.connector_id,
                "error": str(exc),
            }


class ERPConnector(EnterpriseConnector):
    connector_type = "erp"


class CRMConnector(EnterpriseConnector):
    connector_type = "crm"


class OAConnector(EnterpriseConnector):
    connector_type = "oa"


# P4：剩余连接器（声明式子类，§B.5）
class WMSConnector(EnterpriseConnector):
    connector_type = "wms"


class EAMConnector(EnterpriseConnector):
    connector_type = "eam"


class HRMConnector(EnterpriseConnector):
    connector_type = "hrm"


class MESConnector(EnterpriseConnector):
    connector_type = "mes"


class SRMConnector(EnterpriseConnector):
    connector_type = "srm"


class BIConnector(EnterpriseConnector):
    connector_type = "bi"


# 类型 → 连接器类（随 CONNECTOR_TYPES 扩展）
_CONNECTOR_CLASSES: Dict[str, Any] = {
    "erp": ERPConnector,
    "crm": CRMConnector,
    "oa": OAConnector,
    "wms": WMSConnector,
    "eam": EAMConnector,
    "hrm": HRMConnector,
    "mes": MESConnector,
    "srm": SRMConnector,
    "bi": BIConnector,
}


def create_connector(config: Dict[str, Any]) -> ConnectorBase:
    """按配置类型创建连接器实例。"""
    ctype = config.get("type", "generic")
    cls = _CONNECTOR_CLASSES.get(ctype)
    if cls is None:
        raise ValueError(f"未知连接器类型: {ctype}，支持 {' / '.join(CONNECTOR_TYPES)}")
    return cls(config)


def list_connector_types() -> List[Dict[str, Any]]:
    """连接器类型清单（前端下拉 + 元数据）。"""
    return [
        {
            "type": t,
            "name": meta["name"],
            "description": meta["description"],
            "operations": [
                {"operation": op, **tmpl} for op, tmpl in meta["operations"].items()
            ],
        }
        for t, meta in CONNECTOR_TYPES.items()
    ]


async def sync_connector_registry() -> ConnectorRegistry:
    """从 connector_configs 表重建全局注册表（新增/编辑/删除即时生效）。"""
    from ..security import keychain
    from ..storage import connector_config_repo

    registry = get_connector_registry()
    configs = await connector_config_repo.list_all()
    for cfg in configs:
        api_key = ""
        ref = cfg.get("apiKeyRef")
        if ref:
            try:
                api_key = await keychain.retrieve(ref) or ""
            except Exception:
                api_key = ""
        merged = {**cfg, "api_key": api_key, "operations": cfg.get("operations") or {}}
        try:
            connector = create_connector(merged)
            registry.register(connector)
        except ValueError as exc:
            logger.warning("[connector] 跳过配置 %s: %s", cfg.get("id"), exc)
    return registry

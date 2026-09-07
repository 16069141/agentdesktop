"""Phase B (P3) 自动化编排：工作流 API（需求 §B.4.1 / §B.4.2）。

接口：
  GET    /api/workflows            — 工作流列表（含最近运行）
  POST   /api/workflows            — 创建
  GET    /api/workflows/templates  — 6 种预置模板
  GET    /api/workflows/runs       — 全部执行记录
  GET    /api/workflows/{id}       — 详情
  PUT    /api/workflows/{id}       — 更新
  DELETE /api/workflows/{id}       — 删除（级联执行记录）
  POST   /api/workflows/{id}/run   — 手动触发（body: payload 可选）
  GET    /api/workflows/{id}/runs  — 该工作流的执行记录

注意：/templates 与 /runs 必须注册在 /{workflow_id} 之前。
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..storage import workflow_repo, workflow_run_repo
from ..workflows import engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workflows", tags=["workflows"])

VALID_TRIGGER_TYPES = {"manual", "schedule", "event", "webhook", "db"}

# 6 种预置模板（需求 §B.4.1：模板化工作流）
TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "template_inventory_alert",
        "name": "库存低于阈值告警",
        "description": "数据库触发器：库存数量低于阈值时通知（钉钉/webhook）",
        "trigger_type": "db",
        "trigger_config": {
            "db_connector_id": "", "sql": "SELECT qty FROM inventory WHERE sku='A001'",
            "operator": "lt", "threshold": 20,
        },
        "steps": [
            {"id": "notify", "type": "notify",
             "config": {"message": "库存告警：SKU A001 低于阈值（${payload.value}）",
                        "channel": "audit"}},
        ],
    },
    {
        "id": "template_crm_to_erp",
        "name": "CRM 成交 → ERP 生成订单",
        "description": "Webhook 触发器：CRM 成交事件 → ERP 创建销售订单",
        "trigger_type": "webhook",
        "trigger_config": {"hook_id": "crm-events", "event": "deal.won"},
        "steps": [
            {"id": "erp_order", "type": "connector",
             "config": {"type": "erp", "operation": "create_sales_order",
                        "params": {"customer": "${payload.customer}",
                                   "amount": "${payload.amount}"}}},
        ],
    },
    {
        "id": "template_cross_system",
        "name": "跨系统编排：CRM 成交 → ERP 订单 → WMS 出库 → 通知",
        "description": "单规则串联三系统（§B.4.2 示例）",
        "trigger_type": "webhook",
        "trigger_config": {"hook_id": "crm-events", "event": "deal.won"},
        "steps": [
            {"id": "crm_deal", "type": "connector",
             "config": {"type": "crm", "operation": "get_deal",
                        "params": {"deal_id": "${payload.deal_id}"}}},
            {"id": "erp_order", "type": "connector",
             "config": {"type": "erp", "operation": "create_sales_order",
                        "params": {"customer": "${payload.customer}",
                                   "amount": "${payload.amount}",
                                   "order_no": "${step.crm_deal.output.deal_no}"}}},
            {"id": "wms_ship", "type": "connector",
             "config": {"type": "wms", "operation": "create_outbound",
                        "params": {"order_no": "${step.erp_order.output.order_no}",
                                   "sku": "${payload.sku}", "qty": "${payload.qty}"}}},
            {"id": "notify", "type": "notify",
             "config": {"message": "订单 ${step.erp_order.output.order_no} 已出库",
                        "channel": "audit"}},
        ],
    },
    {
        "id": "template_sync_schedule",
        "name": "定时数据同步",
        "description": "定时触发器：每小时同步数据并通知结果",
        "trigger_type": "schedule",
        "trigger_config": {"cron": "0 * * * *"},
        "steps": [
            {"id": "query", "type": "data",
             "config": {"db_connector_id": "", "sql": "SELECT COUNT(*) AS n FROM orders"}},
            {"id": "notify", "type": "notify",
             "config": {"message": "同步完成：共 ${step.query.output.rows[0].n} 条订单",
                        "channel": "audit"}},
        ],
    },
    {
        "id": "template_new_customer",
        "name": "新客户自动建档",
        "description": "Webhook 触发器：新客户事件 → CRM 创建客户 → 通知销售",
        "trigger_type": "webhook",
        "trigger_config": {"hook_id": "crm-events", "event": "lead.new"},
        "steps": [
            {"id": "crm_create", "type": "connector",
             "config": {"type": "crm", "operation": "create_customer",
                        "params": {"name": "${payload.name}",
                                   "phone": "${payload.phone}"}}},
            {"id": "notify", "type": "notify",
             "config": {"message": "新客户已建档：${payload.name}", "channel": "audit"}},
        ],
    },
    {
        "id": "template_task_reminder",
        "name": "项目任务提醒",
        "description": "定时触发器：每日生成项目任务提醒",
        "trigger_type": "schedule",
        "trigger_config": {"cron": "30 9 * * *"},
        "steps": [
            {"id": "task", "type": "project",
             "config": {"action": "create_task", "project_id": "",
                        "title": "每日工作流任务", "mode": "shared"}},
            {"id": "notify", "type": "notify",
             "config": {"message": "已生成每日任务提醒", "channel": "audit"}},
        ],
    },
]


class WorkflowCreate(BaseModel):
    id: Optional[str] = None
    name: str
    description: str = ""
    trigger_type: str
    trigger_config: Dict[str, Any] = {}
    steps: List[Dict[str, Any]]
    enabled: bool = True
    created_by: str = ""


class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    trigger_type: Optional[str] = None
    trigger_config: Optional[Dict[str, Any]] = None
    steps: Optional[List[Dict[str, Any]]] = None
    enabled: Optional[bool] = None


class WorkflowRunRequest(BaseModel):
    payload: Dict[str, Any] = {}


def _actor(request: Request) -> str:
    return request.headers.get("X-User-Name", "local")


@router.get("/templates")
async def templates():
    return TEMPLATES


@router.get("/runs")
async def runs(limit: int = 50):
    return await workflow_run_repo.list_all(limit=limit)


@router.get("")
async def list_workflows():
    """工作流列表（含最近一次运行摘要）。"""
    workflows = await workflow_repo.list_all()
    out = []
    for wf in workflows:
        recent = await workflow_run_repo.list_by_workflow(wf["id"], limit=1)
        wf = dict(wf)
        wf["lastRun"] = recent[0] if recent else None
        out.append(wf)
    return out


@router.post("")
async def create_workflow(body: WorkflowCreate, request: Request):
    if body.trigger_type not in VALID_TRIGGER_TYPES:
        return {"error": f"非法触发器类型: {body.trigger_type}，可选 "
                         f"{sorted(VALID_TRIGGER_TYPES)}"}
    errors = engine.validate_steps(body.steps)
    if errors:
        return {"error": "；".join(errors)}
    wf = await workflow_repo.create({
        "id": body.id, "name": body.name, "description": body.description,
        "trigger_type": body.trigger_type,
        "trigger_config": body.trigger_config, "steps": body.steps,
        "enabled": body.enabled, "created_by": _actor(request),
    })
    return wf


@router.get("/{workflow_id}")
async def get_workflow(workflow_id: str):
    wf = await workflow_repo.get(workflow_id)
    if not wf:
        return {"error": f"工作流不存在: {workflow_id}"}
    wf["recentRuns"] = await workflow_run_repo.list_by_workflow(workflow_id, limit=20)
    return wf


@router.put("/{workflow_id}")
async def update_workflow(workflow_id: str, body: WorkflowUpdate):
    wf = await workflow_repo.get(workflow_id)
    if not wf:
        return {"error": f"工作流不存在: {workflow_id}"}
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "trigger_type" in fields and fields["trigger_type"] not in VALID_TRIGGER_TYPES:
        return {"error": f"非法触发器类型: {fields['trigger_type']}"}
    if "steps" in fields:
        errors = engine.validate_steps(fields["steps"])
        if errors:
            return {"error": "；".join(errors)}
    return await workflow_repo.update(workflow_id, fields)


@router.delete("/{workflow_id}")
async def delete_workflow(workflow_id: str):
    wf = await workflow_repo.get(workflow_id)
    if not wf:
        return {"error": f"工作流不存在: {workflow_id}"}
    await workflow_repo.delete(workflow_id)
    return {"ok": True, "deleted": workflow_id}


@router.post("/{workflow_id}/run")
async def run_workflow(workflow_id: str, body: WorkflowRunRequest, request: Request):
    """手动触发：同步执行并返回 run 摘要（测试/调试用）。"""
    wf = await workflow_repo.get(workflow_id)
    if not wf:
        return {"error": f"工作流不存在: {workflow_id}"}
    if not wf.get("enabled"):
        return {"error": "工作流已停用，无法触发"}
    result = await engine.execute_workflow(
        wf, trigger="manual", payload=body.payload or {},
        actor=_actor(request))
    return result


@router.get("/{workflow_id}/runs")
async def workflow_runs(workflow_id: str, limit: int = 50):
    return await workflow_run_repo.list_by_workflow(workflow_id, limit=limit)

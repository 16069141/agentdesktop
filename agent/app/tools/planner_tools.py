"""任务规划工具（P0 智能体闭环：计划-执行-验证）。

模型通过 create_plan 建立步骤清单，执行中逐条 update_plan 更新状态；
每次变更产出一条 plan SSE 事件，由编排器透传给前端展示执行进度。

实现要点：
- PlanBus 挂在 contextvars 上：每个 run_stream 请求在自身 asyncio 任务内
  创建并设置，工具在请求任务内执行，天然隔离并发会话，无需把总线
  塞进全局工具实例（缓存编排器被并发请求共享时不会互相污染）。
- 工具本身无状态，可安全放进 create_tools 注册表。
"""
import contextvars
import json
import logging
from typing import Any, Dict, List, Optional

from ._foundation import BaseTool

logger = logging.getLogger(__name__)

_STEP_STATUS = ("pending", "running", "done", "failed", "skipped")


class PlanBus:
    """单次请求内的计划状态 + 事件队列（asyncio 单线程，无需锁）。"""

    def __init__(self) -> None:
        self.steps: List[Dict[str, Any]] = []
        self.goal: str = ""
        self._events: List[Dict[str, Any]] = []

    def set_plan(self, steps: List[Any], goal: str = "") -> Dict[str, Any]:
        """重建计划（覆盖旧计划），产出一条 plan 事件。"""
        self.steps = []
        self.goal = goal or ""
        for i, s in enumerate(steps, 1):
            if isinstance(s, str):
                s = {"title": s}
            elif not isinstance(s, dict):
                s = {}
            self.steps.append({
                "id": f"step-{i}",
                "title": str(s.get("title") or f"步骤 {i}"),
                "detail": str(s.get("detail") or ""),
                "status": "pending",
            })
        return self._emit()

    def update_step(self, step_id: str, status: str, note: str = "") -> Optional[Dict[str, Any]]:
        """更新某步状态，产出一条 plan 事件；步骤不存在返回 None。"""
        for s in self.steps:
            if s["id"] == step_id:
                s["status"] = status
                if note:
                    s["note"] = note
                return self._emit()
        return None

    def _emit(self) -> Dict[str, Any]:
        ev = {
            "type": "plan",
            "goal": self.goal,
            "steps": json.loads(json.dumps(self.steps, ensure_ascii=False)),
        }
        self._events.append(ev)
        return {"success": True, "goal": self.goal, "plan": ev["steps"]}

    def take_events(self) -> List[Dict[str, Any]]:
        """取走并清空事件队列（编排器每轮工具执行后调用）。"""
        evs, self._events = self._events, []
        return evs

    def snapshot(self) -> Dict[str, Any]:
        return {"goal": self.goal, "steps": list(self.steps)}


plan_bus_var: contextvars.ContextVar = contextvars.ContextVar("plan_bus", default=None)


def get_plan_bus() -> Optional[PlanBus]:
    """取当前请求的计划总线（run_stream 设置；请求外为 None）。"""
    return plan_bus_var.get()


def set_plan_bus(bus: PlanBus) -> contextvars.Token:
    """设置当前请求的计划总线，返回 token 供 reset。"""
    return plan_bus_var.set(bus)


class CreatePlanTool(BaseTool):
    name = "create_plan"
    description = (
        "创建任务执行计划：把复杂任务拆解为 3-8 个有序步骤。"
        "需要多步操作（查资料→处理→产出文件、多轮工具调用）的任务，开始前先调用一次"
        "（重复调用会清空旧计划）；执行中每开始/完成一步用 update_plan 更新状态。"
        "简单任务（1-2 步）不需要调用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "任务目标一句话描述",
            },
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "步骤标题（简短）"},
                        "detail": {"type": "string", "description": "可选：该步要做什么、产出什么"},
                    },
                    "required": ["title"],
                },
                "description": "有序步骤列表（3-8 个，最多 12 个）",
            },
        },
        "required": ["goal", "steps"],
    }
    requires_approval = False

    async def execute(self, arguments: dict) -> dict:
        bus = get_plan_bus()
        if bus is None:
            return {"success": False, "error": "当前会话未启用计划能力"}
        steps = arguments.get("steps")
        if not isinstance(steps, list) or not steps:
            return {"success": False, "error": "steps 必须是非空列表"}
        if len(steps) > 12:
            return {"success": False, "error": "步骤过多（最多 12 个），请合并同类步骤"}
        return bus.set_plan(steps, goal=str(arguments.get("goal") or ""))


class UpdatePlanTool(BaseTool):
    name = "update_plan"
    description = (
        "更新计划中某一步的状态：running=开始执行 / done=完成 / failed=失败 / skipped=跳过。"
        "每开始或完成一步都调用一次，让用户实时看到进度。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "step_id": {
                "type": "string",
                "description": "步骤 id（如 step-1，来自 create_plan 返回）",
            },
            "status": {
                "type": "string",
                "enum": ["running", "done", "failed", "skipped"],
                "description": "步骤新状态",
            },
            "note": {
                "type": "string",
                "description": "可选：该步结果摘要（一两句话）",
            },
        },
        "required": ["step_id", "status"],
    }
    requires_approval = False

    async def execute(self, arguments: dict) -> dict:
        bus = get_plan_bus()
        if bus is None:
            return {"success": False, "error": "当前会话未启用计划能力"}
        step_id = str(arguments.get("step_id") or "")
        status = str(arguments.get("status") or "")
        note = str(arguments.get("note") or "")
        if status not in _STEP_STATUS or status == "pending":
            return {"success": False, "error": f"非法状态: {status}（可选 running/done/failed/skipped）"}
        out = bus.update_step(step_id, status, note)
        if out is None:
            return {
                "success": False,
                "error": f"步骤不存在: {step_id}（请先用 create_plan 建立计划）",
            }
        return out

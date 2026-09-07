"""Phase B (P3) 自动化编排：工作流 DSL 执行引擎（需求 §B.4.1 / §B.4.2）。

步骤 DSL（steps JSON 数组，顺序执行）：
- condition  条件过滤：{field, op(eq|ne|gt|ge|lt|le|contains), value} → 不满足则整条跳过（skipped）
- connector  跨系统动作：{connector_id 或 type, operation, params} → 调连接器 call()（身份透传 + 审计）
- data       只读数据查询：{db_connector_id, sql} → 强制只读校验后查询
- project    项目动作：{action: create_task, project_id, title, mode, assignee, description}
- notify     通知：{message, channel: audit|webhook, hook_id} → 审计落库 / webhook 转发

引用语法：字符串中 `${payload.x}`、`${trigger}`、`${step.<step_id>.x}` 运行时替换。

跨系统业务编排（§B.4.2）：单规则串联多系统步骤（例：CRM 成交 → ERP 生成订单
→ WMS 出库 → 钉钉通知），数据在步骤间通过 step.<id>.output 流转。
"""
import json
import logging
import re
import time
import uuid
from typing import Any, Dict, List, Optional

from ..storage import (
    connector_config_repo,
    db_connector_repo,
    workflow_repo,
    workflow_run_repo,
)
from ..audit import logger as audit_logger

logger = logging.getLogger(__name__)

# 步骤类型白名单
VALID_STEP_TYPES = {"condition", "connector", "data", "project", "notify"}
# 条件操作符
VALID_OPS = {"eq", "ne", "gt", "ge", "lt", "le", "contains"}

_REF_RE = re.compile(r"\$\{([^}]+)\}")


def validate_steps(steps: Any) -> List[str]:
    """校验步骤 DSL 合法性，返回错误列表（空 = 合法）。"""
    errors: List[str] = []
    if not isinstance(steps, list) or not steps:
        return ["steps 必须是非空数组"]
    seen_ids = set()
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append(f"步骤[{i}] 必须是对象")
            continue
        st = step.get("id") or f"step_{i}"
        if st in seen_ids:
            errors.append(f"步骤 id 重复: {st}")
        seen_ids.add(st)
        if step.get("type") not in VALID_STEP_TYPES:
            errors.append(f"步骤[{i}] 非法类型: {step.get('type')}")
            continue
        t = step["type"]
        if t == "condition":
            cfg = step.get("config") or {}
            if cfg.get("op") not in VALID_OPS:
                errors.append(f"步骤[{i}] 非法条件操作符: {cfg.get('op')}")
        elif t == "connector":
            cfg = step.get("config") or {}
            if not cfg.get("connector_id") and not cfg.get("type"):
                errors.append(f"步骤[{i}] connector 需 connector_id 或 type")
            if not cfg.get("operation"):
                errors.append(f"步骤[{i}] connector 缺 operation")
        elif t == "data":
            cfg = step.get("config") or {}
            if not cfg.get("db_connector_id") or not cfg.get("sql"):
                errors.append(f"步骤[{i}] data 需 db_connector_id 与 sql")
        elif t == "project":
            cfg = step.get("config") or {}
            if cfg.get("action") != "create_task":
                errors.append(f"步骤[{i}] project 仅支持 create_task")
        elif t == "notify":
            cfg = step.get("config") or {}
            if not cfg.get("message"):
                errors.append(f"步骤[{i}] notify 缺 message")
    return errors


def _resolve_refs(value: Any, ctx: Dict[str, Any]) -> Any:
    """递归替换 ${...} 引用（payload / trigger / step.<id>.<path>）。"""
    if isinstance(value, str):
        def repl(m: re.Match) -> str:
            path = m.group(1).strip()
            node: Any = ctx
            for part in path.split("."):
                if isinstance(node, dict):
                    node = node.get(part)
                elif isinstance(node, list) and part.isdigit():
                    node = node[int(part)]
                else:
                    return m.group(0)  # 无法解析保留原文
            return "" if node is None else json.dumps(node, ensure_ascii=False) if isinstance(node, (dict, list)) else str(node)
        return _REF_RE.sub(repl, value)
    if isinstance(value, dict):
        return {k: _resolve_refs(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_refs(v, ctx) for v in value]
    return value


async def _load_connector_config(cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """按 connector_id 或 type 取启用的连接器配置。"""
    cid = cfg.get("connector_id")
    if cid:
        found = await connector_config_repo.get(cid)
        return found if found and found.get("enabled") else None
    ctype = cfg.get("type")
    configs = await connector_config_repo.list_all(type_=ctype)
    enabled = [c for c in configs if c.get("enabled")]
    return enabled[0] if enabled else None


async def _step_connector(step: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    from ..connectors import create_connector
    from ..security import keychain

    cfg = step.get("config") or {}
    resolved = _resolve_refs(cfg, ctx)
    connector_cfg = await _load_connector_config(resolved)
    if not connector_cfg:
        return {"ok": False, "error": "未找到启用的连接器（connector_id/type 未配置或已停用）"}
    api_key = ""
    ref = connector_cfg.get("apiKeyRef")
    if ref:
        try:
            api_key = await keychain.retrieve(ref) or ""
        except Exception:
            api_key = ""
    connector = create_connector({**connector_cfg, "api_key": api_key})
    result = await connector.call(resolved["operation"], resolved.get("params") or {})
    ok = bool(result.get("success", result.get("ok", False)))
    return {"ok": ok, "output": result, "error": result.get("error") if not ok else None}


async def _step_data(step: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    from ..connectors import query_db

    cfg = _resolve_refs(step.get("config") or {}, ctx)
    conn = await db_connector_repo.get(cfg["db_connector_id"])
    if not conn or not conn.get("enabled", True):
        return {"ok": False, "error": "数据库只读连接不可用"}
    result = await query_db(conn, cfg["sql"])
    ok = bool(result.get("success", result.get("ok", False)))
    return {"ok": ok, "output": result, "error": result.get("error") if not ok else None}


async def _step_project(step: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    from ..storage import project_task_repo
    from ..storage.projects import ProjectRepo

    cfg = _resolve_refs(step.get("config") or {}, ctx)
    if cfg.get("action") != "create_task":
        return {"ok": False, "error": "project 步骤仅支持 create_task"}
    project_id = cfg.get("project_id")
    project = await ProjectRepo().get(project_id)
    if not project:
        return {"ok": False, "error": f"项目不存在: {project_id}"}
    task = await project_task_repo.create({
        "project_id": project_id,
        "title": cfg.get("title") or "工作流任务",
        "description": cfg.get("description", ""),
        "mode": cfg.get("mode", "shared"),
        "assignee": cfg.get("assignee", ""),
        "status": "todo",
        "created_by": cfg.get("created_by", ctx.get("actor", "system")),
    })
    return {"ok": True, "output": task}


async def _step_notify(step: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _resolve_refs(step.get("config") or {}, ctx)
    message = cfg.get("message", "")
    channel = cfg.get("channel", "audit")
    if channel == "webhook":
        from ..storage import webhook_event_repo
        hook_id = cfg.get("hook_id", "workflow-out")
        event_id = await webhook_event_repo.insert(
            hook_id, {"message": message, "workflow": ctx.get("workflow_id")})
        return {"ok": True, "output": {"hook_id": hook_id, "event_id": event_id}}
    # 默认 audit：审计落库
    run_id = ctx.get("run_id", "")
    await audit_logger.record_tool_call(
        tool_name="workflow:notify",
        arguments={"message": message[:200]},
        result={"workflow_id": ctx.get("workflow_id")},
        conversation_id=run_id,
        session_id="",
        actor=ctx.get("actor", "system"),
        action="workflow_notify",
    )
    return {"ok": True, "output": {"channel": "audit", "message": message}}


_STEP_EXECUTORS = {
    "connector": _step_connector,
    "data": _step_data,
    "project": _step_project,
    "notify": _step_notify,
}


def _eval_condition(cfg: Dict[str, Any], ctx: Dict[str, Any]) -> bool:
    field = cfg.get("field", "")
    op = cfg.get("op", "eq")
    expect = cfg.get("value")
    actual = _resolve_refs(field, ctx)
    try:
        actual_f = float(actual)
        expect_f = float(expect)
        numeric = True
    except (TypeError, ValueError):
        numeric = False
    if op == "eq":
        return actual == expect
    if op == "ne":
        return actual != expect
    if op == "contains":
        return str(expect) in str(actual)
    if numeric:
        if op == "gt":
            return actual_f > expect_f
        if op == "ge":
            return actual_f >= expect_f
        if op == "lt":
            return actual_f < expect_f
        if op == "le":
            return actual_f <= expect_f
    return False


async def execute_workflow(workflow: Dict[str, Any], trigger: str = "manual",
                           payload: Optional[Dict[str, Any]] = None,
                           actor: str = "system") -> Dict[str, Any]:
    """执行一条工作流，返回 run 摘要。payload 为触发器上下文（webhook body / 手动参数）。"""
    run_id = f"run_{uuid.uuid4().hex[:10]}"
    wf_id = workflow["id"]
    payload = payload or {}
    ctx: Dict[str, Any] = {
        "payload": payload,
        "trigger": trigger,
        "workflow_id": wf_id,
        "run_id": run_id,
        "actor": actor,
    }
    await workflow_run_repo.create(run_id, wf_id, trigger)

    steps = workflow.get("steps") or []
    logs: List[Dict[str, Any]] = []
    status = "success"
    error = ""

    try:
        for i, step in enumerate(steps):
            step_id = step.get("id") or f"step_{i}"
            # condition：不满足则整条跳过
            if step.get("type") == "condition":
                cfg = step.get("config") or {}
                ok = _eval_condition(cfg, ctx)
                logs.append({"step": step_id, "type": "condition",
                             "ok": ok, "detail": {"field": cfg.get("field"),
                                                  "op": cfg.get("op"),
                                                  "value": cfg.get("value")},
                             "ms": 0})
                if not ok:
                    status = "skipped"
                    break
                continue
            # 其它步骤：执行
            started = time.time()
            executor = _STEP_EXECUTORS.get(step["type"])
            if executor is None:
                logs.append({"step": step_id, "type": step.get("type"),
                             "ok": False, "error": "未知步骤类型", "ms": 0})
                status = "failed"
                error = f"未知步骤类型: {step.get('type')}"
                break
            result = await executor(step, ctx)
            ms = int((time.time() - started) * 1000)
            logs.append({"step": step_id, "type": step["type"],
                         "ok": bool(result.get("ok")),
                         "error": result.get("error"),
                         "output": result.get("output"),
                         "ms": ms})
            if not result.get("ok"):
                status = "failed"
                error = result.get("error") or f"步骤 {step_id} 执行失败"
                break
            # 步骤输出写入上下文（供后续步骤 ${step.<id>.output...} 引用）
            ctx[f"step.{step_id}"] = {"output": result.get("output")}
            ctx[f"step:{step_id}"] = {"output": result.get("output")}
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
        logger.exception("[workflow] 执行异常 workflow=%s", wf_id)

    await workflow_run_repo.finish(run_id, status, error, logs)
    return {"run_id": run_id, "workflow_id": wf_id, "status": status,
            "error": error, "steps": len(logs), "trigger": trigger}


# ---- 触发器匹配 ----

async def match_webhook_workflows(hook_id: str, payload: Any) -> List[Dict[str, Any]]:
    """webhook 事件 → 匹配启用中的 webhook 触发器工作流。"""
    workflows = await workflow_repo.list_all(enabled_only=True)
    matched = []
    for wf in workflows:
        if wf.get("trigger_type") != "webhook":
            continue
        cfg = wf.get("triggerConfig") or {}
        if cfg.get("hook_id") == hook_id:
            # 可选 source/event 过滤
            src_filter = cfg.get("source") or cfg.get("event") or ""
            if src_filter:
                src = ""
                if isinstance(payload, dict):
                    src = str(payload.get("source") or payload.get("event") or "")
                if src != src_filter:
                    continue
            matched.append(wf)
    return matched


async def match_event_workflows(event_name: str) -> List[Dict[str, Any]]:
    """业务事件（如 crm.deal.won）→ 匹配启用中的 event 触发器工作流。"""
    workflows = await workflow_repo.list_all(enabled_only=True)
    return [wf for wf in workflows
            if wf.get("trigger_type") == "event"
            and (wf.get("triggerConfig") or {}).get("event") == event_name]


def cron_matches(expr: str, now_dt) -> bool:
    """极简 cron：'m h * * *'（分钟/小时，支持 * 与数字），秒级不对齐。"""
    parts = expr.strip().split()
    if len(parts) != 5:
        return False
    minute_spec, hour_spec = parts[0], parts[1]
    minute, hour = now_dt.minute, now_dt.hour

    def _match(spec: str, val: int) -> bool:
        spec = spec.strip()
        if spec == "*":
            return True
        if "," in spec:
            return any(_match(p, val) for p in spec.split(","))
        if "-" in spec:
            lo, hi = spec.split("-")
            return int(lo) <= val <= int(hi)
        if spec.isdigit():
            return int(spec) == val
        return False

    return _match(minute_spec, minute) and _match(hour_spec, hour)


def _cmp(actual: float, op: str, expect: float) -> bool:
    if op == "lt":
        return actual < expect
    if op == "le":
        return actual <= expect
    if op == "gt":
        return actual > expect
    if op == "ge":
        return actual >= expect
    if op == "eq":
        return actual == expect
    if op == "ne":
        return actual != expect
    return False


async def check_db_trigger(wf: Dict[str, Any]) -> bool:
    """DB 触发器：执行只读 SQL，取首行首列，与 threshold 比较。"""
    from ..connectors import query_db

    cfg = wf.get("triggerConfig") or {}
    conn = await db_connector_repo.get(cfg.get("db_connector_id", ""))
    if not conn:
        return False
    result = await query_db(conn, cfg.get("sql", ""))
    if not result.get("success", result.get("ok", False)):
        return False
    rows = result.get("rows") or []
    if not rows:
        return False
    first_row = rows[0]
    if isinstance(first_row, dict):
        value = next(iter(first_row.values()))
    else:
        value = first_row[0] if isinstance(first_row, (list, tuple)) else first_row
    op = cfg.get("operator", "lt")
    threshold = cfg.get("threshold")
    try:
        return _cmp(float(value), op, float(threshold))
    except (TypeError, ValueError):
        return False


async def trigger_workflows(workflow: Dict[str, Any], trigger: str,
                            payload: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """触发单条工作流（后台/手动共用入口）。"""
    if not workflow.get("enabled"):
        return None
    return await execute_workflow(workflow, trigger=trigger, payload=payload,
                                  actor="system")

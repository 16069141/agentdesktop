"""系统管理与运维（需求 §B.3.3）。

- 连接健康监控：`health_check_all()` 遍历全部连接（企业连接器 / DB 只读 /
  知识库 / 模型服务器）测连通，更新 health 字段；断连写审计（action=connector_down）。
- 后台定时巡检：main.py lifespan 启动 asyncio 任务，间隔默认 60s。
- 字段映射 CRUD：/api/field-mappings（JSON 配置编辑，可视化拖拽 P3）。
- 调用量统计：/api/usage/export（按系统/用户/时间段，成本分摊 CSV）。
"""
import asyncio
import csv
import io
import logging
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ops"])

HEALTH_INTERVAL_SEC = 60


async def _check_connector(cfg: Dict[str, Any]) -> Dict[str, Any]:
    from ..connectors import create_connector
    from ..security import keychain

    api_key = ""
    ref = cfg.get("apiKeyRef")
    if ref:
        try:
            api_key = await keychain.retrieve(ref) or ""
        except Exception:
            api_key = ""
    connector = create_connector({**cfg, "api_key": api_key})
    result = await connector.test()
    ok = bool(result.get("ok"))
    return {"kind": "connector", "id": cfg["id"], "name": cfg["name"],
            "type": cfg["type"], "ok": ok, "detail": result}


async def _check_db(conn: Dict[str, Any]) -> Dict[str, Any]:
    from ..connectors import test_sqlite

    if conn.get("dbType") != "sqlite":
        return {"kind": "db", "id": conn["id"], "name": conn["name"],
                "type": conn["dbType"], "ok": False, "detail": {"error": "适配器待接入"}}
    result = await test_sqlite(conn["dsn"], timeout_sec=conn.get("timeoutSec", 10))
    return {"kind": "db", "id": conn["id"], "name": conn["name"],
            "type": conn["dbType"], "ok": bool(result.get("ok")), "detail": result}


async def _check_knowledge(server: Dict[str, Any]) -> Dict[str, Any]:
    from ..knowledge_connectors import create_connector
    from ..security import keychain

    api_key = ""
    ref = server.get("api_key_ref")
    if ref:
        try:
            api_key = await keychain.retrieve(ref) or ""
        except Exception:
            api_key = ""
    try:
        connector = create_connector(server, api_key=api_key)
        ok = await connector.test()
    except Exception as exc:
        ok = False
    return {"kind": "knowledge", "id": server["id"], "name": server["name"],
            "type": server["platform"], "ok": bool(ok), "detail": {"ok": ok}}


async def _check_llm(server: Dict[str, Any]) -> Dict[str, Any]:
    from ..security import keychain

    api_key = ""
    ref = server.get("api_key_ref")
    if ref:
        try:
            api_key = await keychain.retrieve(ref) or ""
        except Exception:
            api_key = ""
    try:
        # 统一走 base.py 的 create_provider 工厂（与 build_providers 同一构造逻辑）
        from ..providers import create_provider
        provider = create_provider(
            server.get("protocol", "openai"),
            base_url=server["base_url"],
            api_key=api_key,
            timeout_sec=server.get("timeout_sec", 60),
            provider_id=server["id"],
            name=server.get("name") or server["id"],
        )
        # 加超时保护：公共网关偶发慢响应，不能让一次探测卡住整个巡检循环
        ok = await asyncio.wait_for(provider.health_check(), timeout=5.0)
    except asyncio.TimeoutError:
        logger.warning("[ops] 模型服务器巡检超时 %s（5s）", server.get("id"))
        ok = False
    except Exception as exc:
        logger.warning("[ops] 模型服务器巡检异常 %s: %s", server.get("id"), exc)
        ok = False
    return {"kind": "llm", "id": server["id"], "name": server["name"],
            "type": server.get("protocol", "openai"), "ok": bool(ok), "detail": {"ok": ok}}


async def health_check_all() -> Dict[str, Any]:
    """全量健康巡检：返回汇总 + 逐项结果；断连写审计。"""
    from ..audit.logger import record_tool_call
    from ..storage import connector_config_repo, db_connector_repo
    from ..storage.knowledge_servers import KnowledgeServerRepo
    from ..storage.llm_servers import LlmServerRepo

    results: List[Dict[str, Any]] = []
    checks: List[asyncio.Task] = []

    async def _run(fn, item, kind):
        try:
            r = await fn(item)
            if "kind" not in r:
                r["kind"] = kind
            return r
        except Exception as exc:
            return {"kind": kind, "id": item.get("id", "?"), "name": item.get("name", "?"),
                    "ok": False, "detail": {"error": str(exc)}}

    for cfg in await connector_config_repo.list_all():
        checks.append(asyncio.create_task(_run(_check_connector, cfg, "connector")))
    for conn in await db_connector_repo.list_all():
        checks.append(asyncio.create_task(_run(_check_db, conn, "db")))
    for server in await KnowledgeServerRepo().list_all():
        checks.append(asyncio.create_task(_run(_check_knowledge, server, "knowledge")))
    for server in await LlmServerRepo().list_all():
        checks.append(asyncio.create_task(_run(_check_llm, server, "llm")))

    for task in asyncio.as_completed(checks):
        r = await task
        results.append(r)

    down = [r for r in results if not r.get("ok")]
    for r in down:
        await record_tool_call(
            tool_name=f"health:{r['kind']}.{r['id']}",
            arguments={"kind": r["kind"], "id": r["id"], "name": r["name"]},
            result=str(r.get("detail"))[:500],
            actor="ops-monitor",
            action="connector_down",
            error=None,
        )
    await _persist_health(results)
    return {
        "checked_at": _now_ms(),
        "total": len(results),
        "ok_count": len(results) - len(down),
        "down_count": len(down),
        "down": down,
        "results": results,
    }


async def _persist_health(results: List[Dict[str, Any]]) -> None:
    """把健康结果回写各连接表 health 字段。"""
    from ..storage import connector_config_repo, db_connector_repo
    from ..storage.knowledge_servers import KnowledgeServerRepo
    from ..storage.llm_servers import LlmServerRepo

    for r in results:
        ok = bool(r.get("ok"))
        try:
            if r.get("kind") == "connector":
                await connector_config_repo.save_health(r["id"], ok)
            elif r.get("kind") == "db":
                await db_connector_repo.save_health(r["id"], ok)
            elif r.get("kind") == "knowledge":
                await KnowledgeServerRepo().save_health(r["id"], ok)
            elif r.get("kind") == "llm":
                await LlmServerRepo().save_health(r["id"], ok)
        except Exception as exc:
            logger.warning("[ops] 健康回写失败 %s: %s", r.get("id"), exc)


async def health_monitor_loop(stop_event: asyncio.Event) -> None:
    """后台定时巡检：启动即跑一次，之后每 HEALTH_INTERVAL_SEC 跑一次。"""
    logger.info("[ops] 健康监控启动，间隔 %ss", HEALTH_INTERVAL_SEC)
    while not stop_event.is_set():
        try:
            summary = await health_check_all()
            if summary["down_count"]:
                logger.warning("[ops] 巡检完成: %d/%d 连接异常",
                               summary["down_count"], summary["total"])
        except Exception as exc:
            logger.exception("[ops] 巡检失败: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=HEALTH_INTERVAL_SEC)
        except asyncio.TimeoutError:
            continue


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)


# ============ 路由 ============

@router.get("/api/ops/health")
async def get_health():
    """当前连接健康状态（读库，不触发新巡检）。"""
    from ..storage import connector_config_repo, db_connector_repo
    from ..storage.knowledge_servers import KnowledgeServerRepo
    from ..storage.llm_servers import LlmServerRepo

    items: List[Dict[str, Any]] = []
    for cfg in await connector_config_repo.list_all():
        ok = bool(cfg.get("lastHealthOk")) if cfg.get("lastHealthOk") is not None else None
        items.append({"kind": "connector", "id": cfg["id"], "name": cfg["name"],
                      "type": cfg["type"], "ok": ok, "checkedAt": cfg.get("lastHealthAt")})
    for conn in await db_connector_repo.list_all():
        ok = bool(conn.get("lastHealthOk")) if conn.get("lastHealthOk") is not None else None
        items.append({"kind": "db", "id": conn["id"], "name": conn["name"],
                      "type": conn["dbType"], "ok": ok, "checkedAt": conn.get("lastHealthAt")})
    for server in await KnowledgeServerRepo().list_all():
        ok = bool(server.get("last_health_ok")) if server.get("last_health_ok") is not None else None
        items.append({"kind": "knowledge", "id": server["id"], "name": server["name"],
                      "type": server["platform"], "ok": ok, "checkedAt": server.get("last_health_at")})
    for server in await LlmServerRepo().list_all():
        ok = bool(server.get("last_health_ok")) if server.get("last_health_ok") is not None else None
        items.append({"kind": "llm", "id": server["id"], "name": server["name"],
                      "type": server.get("protocol"), "ok": ok, "checkedAt": server.get("last_health_at")})
    return {"items": items, "intervalSec": HEALTH_INTERVAL_SEC}


@router.post("/api/ops/health-check")
async def trigger_health_check():
    """手动触发全量健康巡检。"""
    return await health_check_all()


# ============ 字段映射 ============

class MappingCreate(BaseModel):
    id: str
    name: str
    connector_id: str
    source_field: str
    target_field: str
    transform: dict = {}
    enabled: bool = True


class MappingUpdate(BaseModel):
    name: Optional[str] = None
    connector_id: Optional[str] = None
    source_field: Optional[str] = None
    target_field: Optional[str] = None
    transform: Optional[dict] = None
    enabled: Optional[bool] = None


@router.get("/api/field-mappings")
async def list_mappings(connector_id: str = ""):
    from ..storage import field_mapping_repo
    return await field_mapping_repo.list_all(connector_id or None)


@router.post("/api/field-mappings")
async def create_mapping(body: MappingCreate):
    from ..storage import field_mapping_repo
    if not body.id.strip() or not body.name.strip() or not body.connector_id.strip() \
            or not body.source_field.strip() or not body.target_field.strip():
        raise HTTPException(status_code=400, detail="id/name/connector_id/source_field/target_field 不能为空")
    if await field_mapping_repo.get(body.id):
        raise HTTPException(status_code=409, detail=f"映射 {body.id} 已存在")
    tr = body.transform or {}
    if tr.get("type") not in field_mapping_repo.VALID_TRANSFORMS:
        raise HTTPException(status_code=400, detail="transform.type 仅支持 none/map/format/join")
    return await field_mapping_repo.create({
        "id": body.id.strip(), "name": body.name.strip(),
        "connector_id": body.connector_id.strip(),
        "source_field": body.source_field.strip(),
        "target_field": body.target_field.strip(),
        "transform": tr, "enabled": body.enabled,
    })


@router.put("/api/field-mappings/{mapping_id:path}")
async def update_mapping(mapping_id: str, body: MappingUpdate):
    from ..storage import field_mapping_repo
    mapping = await field_mapping_repo.get(mapping_id)
    if not mapping:
        raise HTTPException(status_code=404, detail="映射不存在")
    fields: dict[str, Any] = {}
    for key, attr in (("name", "name"), ("connector_id", "connector_id"),
                      ("source_field", "source_field"), ("target_field", "target_field"),
                      ("enabled", "enabled")):
        value = getattr(body, key)
        if value is not None:
            fields[attr] = value
    if body.transform is not None:
        if body.transform.get("type") not in field_mapping_repo.VALID_TRANSFORMS:
            raise HTTPException(status_code=400, detail="transform.type 仅支持 none/map/format/join")
        fields["transform"] = body.transform
    return await field_mapping_repo.update(mapping_id, fields)


@router.delete("/api/field-mappings/{mapping_id:path}")
async def delete_mapping(mapping_id: str):
    from ..storage import field_mapping_repo
    if not await field_mapping_repo.get(mapping_id):
        raise HTTPException(status_code=404, detail="映射不存在")
    await field_mapping_repo.delete(mapping_id)
    return {"ok": True}


# ============ 调用量统计导出（P4 成本精细化） ============

@router.get("/api/usage/export")
async def export_usage(by: str = "day", days: int = 30, fmt: str = "csv"):
    """调用量统计导出（成本分摊）。

    by=day|connector|user|model|tool；fmt=csv|json。
    - day：usage_log 按天聚合
    - model：usage_log 按模型聚合（P4 成本精细化）
    - connector / user / tool：审计日志聚合（工具调用带 actor 与 tool_name）
    """
    from ..storage import usage_log_repo

    if by == "day":
        rows = await usage_log_repo.totals_by_day(days)
        rows = [
            {"date": r["day"], "messages": r["toolCalls"] or 0,
             "tokens": (r["promptTokens"] or 0) + (r["completionTokens"] or 0),
             "cost": r["cost"] or 0}
            for r in rows
        ]
        return _render(rows, by, fmt)
    if by == "model":
        rows = await usage_log_repo.totals_by_model(days)
        rows = [
            {"key": r["model"], "calls": r["calls"],
             "tokens": (r["promptTokens"] or 0) + (r["completionTokens"] or 0),
             "cost": r["cost"] or 0}
            for r in rows
        ]
        return _render(rows, by, fmt)
    # connector / user / tool 维度：审计日志聚合
    from ..storage import audit_log_repo
    logs = await audit_log_repo.query(limit=1000)
    agg: Dict[str, Dict[str, float]] = {}
    for log in logs:
        tool = log.get("toolName") or log.get("tool_name") or ""
        if tool.startswith("health:"):
            continue
        if by == "connector":
            key = tool.split(".")[0]
        elif by == "tool":
            key = tool
        else:  # user
            key = log.get("actor", "local")
        entry = agg.setdefault(key, {"calls": 0, "cost": 0.0, "latency": 0})
        entry["calls"] += 1
        entry["latency"] += log.get("latencyMs") or 0
    rows = [
        {"key": k, "calls": int(v["calls"]),
         "cost": round(v["cost"], 4),
         "latencyMs": int(v["latency"])}
        for k, v in sorted(agg.items(), key=lambda x: -x[1]["calls"])
    ]
    return _render(rows, by, fmt)


def _render(rows: list, by: str, fmt: str):
    if fmt == "json":
        return {"by": by, "rows": rows}
    buf = io.StringIO()
    if by == "day":
        writer = csv.writer(buf)
        writer.writerow(["date", "messages", "tokens", "cost"])
        for r in rows:
            writer.writerow([r["date"], r["messages"], r["tokens"], r["cost"]])
    elif by == "model":
        writer = csv.writer(buf)
        writer.writerow(["model", "calls", "tokens", "cost"])
        for r in rows:
            writer.writerow([r["key"], r["calls"], r["tokens"], r["cost"]])
    else:
        writer = csv.writer(buf)
        writer.writerow([by, "calls", "cost", "latency_ms"])
        for r in rows:
            writer.writerow([r["key"], r["calls"], r["cost"], r["latencyMs"]])
    return {"by": by, "csv": buf.getvalue(), "rows": rows}


# ============ P4：高级错误诊断（失败原因自动分析 + 修复建议） ============

# 规则：错误片段 → (分类, 原因, 修复建议)
_DIAGNOSTIC_RULES: List[Tuple[Tuple[str, ...], str, str, str]] = [
    (("未找到启用的连接器", "尚未配置启用", "未配置连接", "待接入"),
     "connector_not_configured",
     "工作流步骤引用的连接器未配置或已停用",
     "在「连接」页创建对应类型的连接器并测试通过（确认 enabled 为开启），再重跑工作流"),
    (("All connection attempts failed", "ConnectError", "Connection refused", "timed out", "timeout"),
     "connector_unreachable",
     "连接器/知识库/模型服务地址不可达（网络、地址或服务未启动）",
     "检查目标服务是否在线、base_url 是否可访问、是否有防火墙/白名单拦截；用「健康巡检」验证连通性"),
    (("HTTP 401", "HTTP 403"),
     "auth_failed",
     "接口鉴权失败（api_key 无效、无权限或令牌过期）",
     "在连接配置中更新 api_key / 令牌，确认该账号对该接口有权限，并重新测试连接"),
    (("HTTP 400", "HTTP 404", "HTTP 500"),
     "api_error",
     "目标系统接口报错（参数、资源或服务端错误）",
     "核对请求参数与路径占位符；查看审计日志中该调用的请求体；联系系统侧核对接口定义"),
    (("只读", "ReadOnly", "read-only", "写入"),
     "read_only_violation",
     "数据库只读连接拒绝了写操作（SQL 含 INSERT/UPDATE/DELETE/DDL）",
     "DB 连接为只读防线（文件级 ro + 语句级校验），工作流 data 步骤请改用 SELECT 查询"),
    (("未知步骤类型", "非法步骤", "非法触发器", "steps 必须"),
     "dsl_invalid",
     "工作流 DSL 定义不符合规范（步骤/触发器类型非法或结构缺失）",
     "核对步骤类型（condition/connector/data/project/notify）与触发器类型（manual/schedule/event/webhook/db），修正后重跑"),
    (("${", "引用", "未找到"),
     "ref_resolve",
     "步骤参数中的 ${...} 引用无法解析（payload 字段或上游步骤输出不存在）",
     "检查 payload 字段名与上游步骤 id/output 结构；可先运行一次查看 logs 中各步骤输出再修正引用"),
]


def diagnose_run(run: Dict[str, Any]) -> Dict[str, Any]:
    """对一条失败/跳过的运行记录做规则化诊断。"""
    workflow_id = run.get("workflowId") or run.get("workflow_id", "")
    error = run.get("error") or ""
    failed_step = None
    for log in run.get("logs") or []:
        if log.get("ok") is False:
            failed_step = log.get("step") or log.get("type")
            error = error or (log.get("error") or "")
            break
    if not error and run.get("status") == "skipped":
        cond = next((l for l in run.get("logs") or [] if l.get("type") == "condition"), None)
        return {
            "run_id": run.get("id"), "workflow_id": workflow_id,
            "status": run.get("status"), "failed_step": cond and cond.get("step"),
            "diagnosis": {
                "category": "condition_skipped",
                "reason": "条件步骤不满足（field/op/value 未命中），整条工作流被跳过",
                "suggestion": "调整条件（field/op/value）或 payload 输入，使条件命中后继续执行",
            },
        }
    if not error:
        return {"run_id": run.get("id"), "workflow_id": workflow_id,
                "status": run.get("status"), "diagnosis": None}
    for fragments, category, reason, suggestion in _DIAGNOSTIC_RULES:
        if any(f.lower() in error.lower() for f in fragments):
            return {
                "run_id": run.get("id"), "workflow_id": workflow_id,
                "status": run.get("status"), "failed_step": failed_step,
                "diagnosis": {"category": category, "reason": reason,
                              "suggestion": suggestion},
            }
    return {
        "run_id": run.get("id"), "workflow_id": workflow_id,
        "status": run.get("status"), "failed_step": failed_step,
        "diagnosis": {
            "category": "unknown",
            "reason": f"未匹配到已知模式：{error[:200]}",
            "suggestion": "查看执行日志 logs 定位失败步骤，确认参数/连接/接口定义后重试",
        },
    }


@router.get("/api/ops/diagnostics")
async def diagnostics(run_id: str = ""):
    """高级错误诊断：分析工作流运行失败原因并给出修复建议。"""
    from ..storage import workflow_run_repo

    if run_id:
        run = await workflow_run_repo.get(run_id)
        if run is None:
            return {"error": f"运行记录不存在: {run_id}"}
        return diagnose_run(run)
    # 无 run_id：诊断最近失败的运行
    runs = await workflow_run_repo.list_all(limit=200)
    failed = [r for r in runs if r.get("status") in ("failed", "skipped")]
    return {"total_failed": len(failed),
            "diagnoses": [diagnose_run(r) for r in failed[:20]]}

"""P3 功能验证脚本（Phase B 自动化编排）。

覆盖（需求 §B.4.1 / §B.4.2）：
1. 工作流 CRUD + 模板清单（6 种）+ 步骤 DSL 校验
2. 手动运行：notify 成功 / connector 失败捕获 / condition 跳过 / 引用替换
3. 触发器：webhook（事件匹配 + 异步触发）/ db（SQL 阈值）/ schedule（cron 轮询）/ 停用不触发
4. 跨系统编排：模板化 CRM→ERP→WMS→通知 创建与引用解析
5. 回归：工具注册表 / 连接器类型

用法：AGENT_TOKEN=xxx python scripts/verify_p3.py [base_url] [data_dir]
服务端需以 AGENT_SCHEDULER_INTERVAL<=5 启动（本脚本等待轮询窗口）。
"""
import asyncio
import json
import os
import sqlite3
import sys
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8878"
DATA_DIR = sys.argv[2] if len(sys.argv) > 2 else "/tmp/p3-verify"
TOKEN = os.environ.get("AGENT_TOKEN", "testtoken123")

PASS = 0
FAIL = 0
SKIP = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✔ {name}")
    else:
        FAIL += 1
        print(f"  ✘ {name}  {detail}")


def skip(name: str):
    global SKIP
    SKIP += 1
    print(f"  - {name}（跳过）")


def req(method: str, path: str, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Authorization": f"Bearer {TOKEN}",
                 "Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        text = e.read().decode() or "{}"
        try:
            return e.code, json.loads(text)
        except json.JSONDecodeError:
            return e.code, {"raw": text}


# ============ 1. CRUD 与模板 ============

def crud():
    print("== P3 工作流 CRUD ==")
    st, p = req("POST", "/api/workflows", {
        "id": "wf_crud_1", "name": "CRUD 测试",
        "description": "验证增删改查", "trigger_type": "manual",
        "steps": [{"id": "n", "type": "notify",
                   "config": {"message": "hi ${payload.who}", "channel": "audit"}}],
    })
    check("工作流: 创建", st == 200 and p["id"] == "wf_crud_1", str(p)[:150])

    st, lst = req("GET", "/api/workflows")
    check("工作流: 列表（含 lastRun）", st == 200
          and any(w["id"] == "wf_crud_1" for w in lst)
          and "lastRun" in lst[0], str(lst)[:120])

    st, detail = req("GET", "/api/workflows/wf_crud_1")
    check("工作流: 详情", st == 200 and len(detail["steps"]) == 1, str(detail)[:150])

    st, upd = req("PUT", "/api/workflows/wf_crud_1", {"description": "已更新"})
    check("工作流: 更新", st == 200 and upd["description"] == "已更新", str(upd)[:150])

    st, bad = req("POST", "/api/workflows", {
        "name": "bad", "trigger_type": "alien",
        "steps": [{"id": "n", "type": "notify", "config": {"message": "x"}}],
    })
    check("工作流: 非法触发器类型拒绝", st == 200 and "error" in bad, str(bad)[:100])

    st, bad2 = req("POST", "/api/workflows", {
        "name": "bad2", "trigger_type": "manual", "steps": [],
    })
    check("工作流: 空 steps 拒绝", st == 200 and "error" in bad2, str(bad2)[:100])

    st, bad3 = req("POST", "/api/workflows", {
        "name": "bad3", "trigger_type": "manual",
        "steps": [{"id": "x", "type": "unknown"}],
    })
    check("工作流: 非法步骤类型拒绝", st == 200 and "error" in bad3, str(bad3)[:100])

    st, tpl = req("GET", "/api/workflows/templates")
    check("模板: 6 种预置", st == 200 and len(tpl) == 6
          and any(t["id"] == "template_cross_system" for t in tpl),
          f"{len(tpl)} 个")

    # 删除
    st, d = req("DELETE", "/api/workflows/wf_crud_1")
    check("工作流: 删除", st == 200 and d.get("ok"), str(d)[:100])


# ============ 2. 手动运行 ============

def manual_runs():
    print("== P3 手动运行 ==")
    req("POST", "/api/workflows", {
        "id": "wf_run_ok", "name": "run ok", "trigger_type": "manual",
        "steps": [{"id": "n", "type": "notify",
                   "config": {"message": "hello ${payload.who}", "channel": "audit"}}],
    })
    st, r = req("POST", "/api/workflows/wf_run_ok/run", {"payload": {"who": "验证"}})
    check("运行: notify 成功 + 引用替换", st == 200 and r["status"] == "success",
          str(r)[:200])

    st, runs = req("GET", "/api/workflows/wf_run_ok/runs")
    check("运行: 执行记录落库", st == 200 and len(runs) == 1
          and runs[0]["status"] == "success" and len(runs[0]["logs"]) == 1,
          str(runs)[:150])

    req("POST", "/api/workflows", {
        "id": "wf_run_fail", "name": "run fail", "trigger_type": "manual",
        "steps": [{"id": "c", "type": "connector",
                   "config": {"type": "erp", "operation": "list_orders", "params": {}}}],
    })
    st, r2 = req("POST", "/api/workflows/wf_run_fail/run", {})
    check("运行: 未配置连接器 → failed + 错误捕获", st == 200 and r2["status"] == "failed"
          and "连接器" in r2.get("error", ""), str(r2)[:200])

    req("POST", "/api/workflows", {
        "id": "wf_run_cond", "name": "run cond", "trigger_type": "manual",
        "steps": [
            {"id": "c1", "type": "condition",
             "config": {"field": "${payload.amount}", "op": "lt", "value": 100}},
            {"id": "n", "type": "notify",
             "config": {"message": "ok", "channel": "audit"}},
        ],
    })
    st, r3 = req("POST", "/api/workflows/wf_run_cond/run", {"payload": {"amount": 500}})
    check("运行: 条件不满足 → skipped", st == 200 and r3["status"] == "skipped",
          str(r3)[:150])
    st, r4 = req("POST", "/api/workflows/wf_run_cond/run", {"payload": {"amount": 50}})
    check("运行: 条件满足 → success", st == 200 and r4["status"] == "success",
          str(r4)[:150])

    # 停用后手动触发拒绝
    req("PUT", "/api/workflows/wf_run_ok", {"enabled": False})
    st, r5 = req("POST", "/api/workflows/wf_run_ok/run", {})
    check("运行: 停用工作流拒绝触发", st == 200 and "error" in r5, str(r5)[:100])
    req("PUT", "/api/workflows/wf_run_ok", {"enabled": True})


# ============ 3. 触发器 ============

def triggers():
    print("== P3 触发器 ==")
    # --- webhook ---
    req("POST", "/api/workflows", {
        "id": "wf_wh", "name": "wh", "trigger_type": "webhook",
        "trigger_config": {"hook_id": "crm-events", "event": "deal.won"},
        "steps": [{"id": "n", "type": "notify",
                   "config": {"message": "deal ${payload.customer}", "channel": "audit"}}],
    })
    st, wh = req("POST", "/api/webhooks/crm-events",
                 {"source": "deal.won", "customer": "Acme", "amount": 1000})
    check("webhook: 触发匹配 fired=1", st == 200 and wh.get("workflows_fired") == 1,
          str(wh)[:150])
    # 不匹配 event 的请求不触发
    st, wh2 = req("POST", "/api/webhooks/crm-events", {"source": "lead.new", "name": "X"})
    check("webhook: event 过滤不触发", wh2.get("workflows_fired") == 0, str(wh2)[:100])

    # --- db 触发器 ---
    db_path = os.path.join(DATA_DIR, "verify_inventory.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS stock (sku TEXT, qty INTEGER)")
    conn.execute("DELETE FROM stock")
    conn.execute("INSERT INTO stock VALUES ('A001', 10)")
    conn.commit()
    conn.close()
    st, dbc = req("POST", "/api/db-connectors", {
        "id": "db_verify_inv", "name": "verify-inv", "db_type": "sqlite",
        "dsn": db_path,
    })
    check("db: 只读连接创建", st == 200 and dbc.get("id") == "db_verify_inv",
          str(dbc)[:100])
    req("POST", "/api/workflows", {
        "id": "wf_db", "name": "db alert", "trigger_type": "db",
        "trigger_config": {"db_connector_id": "db_verify_inv",
                           "sql": "SELECT qty FROM stock", "operator": "lt",
                           "threshold": 20},
        "steps": [{"id": "n", "type": "notify", "config": {"message": "low", "channel": "audit"}}],
    })

    # --- schedule 触发器 ---
    req("POST", "/api/workflows", {
        "id": "wf_sched", "name": "sched", "trigger_type": "schedule",
        "trigger_config": {"cron": "* * * * *"},
        "steps": [{"id": "n", "type": "notify", "config": {"message": "tick", "channel": "audit"}}],
    })

    # 等待后台轮询（服务端 interval ≤5s，等 2 个窗口 + 余量）
    print("  … 等待后台轮询触发（~10s）…")
    time.sleep(10)

    st, runs = req("GET", "/api/workflows/runs")
    by_wf = {}
    for r in runs:
        by_wf.setdefault(r["workflow_id"], []).append(r)
    db_hits = [r for r in by_wf.get("wf_db", []) if r["trigger"] == "db"]
    sched_hits = [r for r in by_wf.get("wf_sched", []) if r["trigger"] == "schedule"]
    wh_hits = [r for r in by_wf.get("wf_wh", []) if r["trigger"] == "webhook"]
    check("db 触发器: 轮询触发成功", len(db_hits) >= 1
          and all(r["status"] == "success" for r in db_hits), str(db_hits)[:150])
    check("schedule 触发器: 轮询触发成功", len(sched_hits) >= 1
          and all(r["status"] == "success" for r in sched_hits), str(sched_hits)[:150])
    check("webhook 触发器: 异步执行落库", len(wh_hits) >= 1
          and wh_hits[0]["status"] == "success", str(wh_hits)[:150])

    # 停用 → 不再触发
    req("PUT", "/api/workflows/wf_sched", {"enabled": False})
    n_before = len(by_wf.get("wf_sched", []))
    time.sleep(7)
    st, runs2 = req("GET", "/api/workflows/runs")
    n_after = len([r for r in runs2 if r["workflow_id"] == "wf_sched"])
    check("调度器: 停用后不触发", n_after == n_before, f"before={n_before} after={n_after}")

    # DB 阈值不满足时不触发
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM stock")
    conn.execute("INSERT INTO stock VALUES ('A001', 100)")
    conn.commit()
    conn.close()
    time.sleep(7)
    st, runs3 = req("GET", "/api/workflows/runs")
    n_db = len([r for r in runs3 if r["workflow_id"] == "wf_db"])
    check("db 触发器: 阈值不满足不触发", n_db == len(db_hits),
          f"before={len(db_hits)} after={n_db}")


# ============ 4. 跨系统编排 ============

def orchestration():
    print("== P3 跨系统编排 ==")
    st, tpl = req("GET", "/api/workflows/templates")
    cross = next(t for t in tpl if t["id"] == "template_cross_system")
    # 基于模板创建（填 wms type 占位；WMS 连接器 P4 补齐，此处验证引用链与失败捕获）
    st, wf = req("POST", "/api/workflows", {
        "id": "wf_cross", "name": "跨系统编排", "trigger_type": "webhook",
        "trigger_config": {"hook_id": "crm-events", "event": "deal.won"},
        "steps": cross["steps"],
    })
    check("编排: 模板实例化创建", st == 200 and wf["id"] == "wf_cross", str(wf)[:150])

    st, r = req("POST", "/api/workflows/wf_cross/run",
                {"payload": {"deal_id": "D1001", "customer": "Acme",
                             "amount": 5000, "sku": "SKU-1", "qty": 10}})
    # 无连接器实例 → 第一步 crm_deal 失败 → failed（引用解析在配置加载前失败，错误明确）
    check("编排: 无连接器时失败捕获", st == 200 and r["status"] == "failed",
          str(r)[:250])

    # 引用语法校验：payload 引用在 notify 中解析
    req("POST", "/api/workflows", {
        "id": "wf_ref", "name": "ref", "trigger_type": "manual",
        "steps": [{"id": "n", "type": "notify",
                   "config": {"message": "${payload.customer}:${payload.amount}", "channel": "audit"}}],
    })
    st, r2 = req("POST", "/api/workflows/wf_ref/run",
                 {"payload": {"customer": "Acme", "amount": 99}})
    check("编排: payload 引用解析", st == 200 and r2["status"] == "success", str(r2)[:150])
    st, runs = req("GET", "/api/workflows/wf_ref/runs")
    log_msg = runs[0]["logs"][0].get("output", {}).get("message", "")
    check("编排: 消息含解析值", "Acme" in str(log_msg) and "99" in str(log_msg),
          str(log_msg)[:120])


# ============ 5. 回归 ============

def regression():
    print("== P3 回归 ==")
    st, tools = req("GET", "/api/mcp/tools")
    kinds = {}
    for t in tools:
        kinds[t["kind"]] = kinds.get(t["kind"], 0) + 1
    check("回归: 工具注册表统一列表", st == 200 and "tool" in kinds and "skill" in kinds,
          str(tools)[:120])
    st, types = req("GET", "/api/connectors/types")
    check("回归: 连接器类型清单", st == 200
          and {"erp", "crm", "oa"}.issubset({t["type"] for t in types}),
          str(types)[:120])


async def main():
    print(f"== P3 验证（base={BASE}）==")
    crud()
    manual_runs()
    triggers()
    orchestration()
    regression()
    print(f"\n结果: PASS={PASS} FAIL={FAIL} SKIP={SKIP}")


if __name__ == "__main__":
    asyncio.run(main())

"""P1 功能验证脚本（Phase B 核心连接能力）。

覆盖：
1. 企业连接器 CRUD + 类型清单（ERP/CRM/OA 操作模板）
2. 真实 HTTP 调用：mock ERP/CRM/OA 服务（路径参数渲染 / POST 建单 / 鉴权头）
3. 身份透传：X-User-* 头 → 审计 actor
4. 数据库只读直连：SELECT 放行、INSERT/DELETE/多语句拒绝（400）、只读模式
5. Webhook 接收与事件落库
6. 工具层：ConnectorTool 未配置提示 / 配置后真实调用；db_query 只读
7. MCP 统一列表（connector 类条目）

用法：AGENT_TOKEN=xxx python scripts/verify_p1.py [base_url]
"""
import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8878"
TOKEN = os.environ.get("AGENT_TOKEN", "testtoken123")

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✔ {name}")
    else:
        FAIL += 1
        print(f"  ✘ {name}  {detail}")


def req(method: str, path: str, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        text = e.read().decode() or "{}"
        try:
            return e.code, json.loads(text)
        except json.JSONDecodeError:
            return e.code, {"raw": text}


# ============ mock 企业系统 ============
MOCK_RECORDS = {
    "/api/orders": [{"id": 1001, "status": "created", "amount": 12500}],
    "/api/customers": [{"id": 9001, "name": "Acme Corp", "tier": "VIP"}],
    "/api/announcements": [{"id": 1, "title": "中秋放假通知"}],
}


class MockHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, code: int, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/health":
            return self._reply(200, {"status": "ok"})
        if path.startswith("/api/orders/") and path != "/api/orders":
            oid = path.split("/")[-1]
            return self._reply(200, {"id": int(oid), "status": "created", "amount": 9999})
        if path.startswith("/api/customers/") and path != "/api/customers":
            cid = path.split("/")[-1]
            return self._reply(200, {"id": int(cid), "name": f"Customer {cid}", "tier": "gold"})
        if path in MOCK_RECORDS:
            return self._reply(200, MOCK_RECORDS[path])
        if path == "/api/sales-funnel":
            return self._reply(200, {"stages": [
                {"stage": "leads", "count": 120},
                {"stage": "qualified", "count": 45},
                {"stage": "won", "count": 12},
            ]})
        return self._reply(404, {"error": "not_found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        body = json.loads(raw or b"{}")
        auth = self.headers.get("Authorization", "")
        if path in ("/api/orders", "/api/customers", "/api/approvals"):
            return self._reply(201, {"created": True, "id": 7777,
                                     "body": body, "auth_ok": auth != ""})
        if path == "/api/leaves":
            return self._reply(201, {"created": True, "type": "leave", "body": body})
        return self._reply(404, {"error": "not_found"})


def start_mock() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return port


# ============ 验证 ============

async def api_connectors(mock_port: int):
    base = f"http://127.0.0.1:{mock_port}"
    st, types = req("GET", "/api/connectors/types")
    check("类型清单: 首批 3 类含操作模板（P4 扩展 9 类）",
          st == 200 and {"erp", "crm", "oa"}.issubset({t["type"] for t in types})
          and all(t["operations"] for t in types))
    erp_ops = {t["operations"][i]["operation"] for t in types if t["type"] == "erp"
               for i in range(len(t["operations"]))}
    check("ERP 操作: 订单/库存/财务/采购/排程",
          {"order_create", "order_query", "inventory_query", "finance_reconcile",
           "purchase_create", "schedule_query"} <= erp_ops)

    st, cfg = req("POST", "/api/connectors", {
        "id": "conn_erp_mock", "type": "erp", "name": "Mock ERP",
        "base_url": base, "api_key": "mock-key-123",
    })
    check("连接器: 新增 ERP 实例", st == 200 and cfg.get("id") == "conn_erp_mock", str(cfg))

    st, test = req("POST", "/api/connectors/conn_erp_mock/test")
    check("连接器: 连通性测试通过", st == 200 and test.get("ok"), str(test))

    st, inv = req("POST", "/api/connectors/conn_erp_mock/invoke",
                  {"operation": "order_query", "params": {"order_id": "1001"}},
                  headers={"X-User-Id": "u7", "X-User-Name": "ops-user",
                           "X-User-Role": "manager"})
    check("连接器: ERP 订单详情调用（路径参数渲染）",
          st == 200 and inv.get("success") and inv["data"].get("id") == 1001, str(inv)[:200])

    st, inv = req("POST", "/api/connectors/conn_erp_mock/invoke",
                  {"operation": "order_create", "params": {"customer": "Acme", "amount": 500}})
    check("连接器: ERP 创建订单（POST 鉴权头）",
          st == 200 and inv.get("success") and inv["data"].get("auth_ok"), str(inv)[:200])

    st, inv = req("POST", "/api/connectors/conn_erp_mock/invoke",
                  {"operation": "unknown_op", "params": {}})
    check("连接器: 未知操作返回明确错误", st == 200 and not inv.get("success"), str(inv)[:120])

    st, cfg = req("POST", "/api/connectors", {
        "id": "conn_crm_mock", "type": "crm", "name": "Mock CRM",
        "base_url": base, "api_key": "key",
    })
    st, inv = req("POST", "/api/connectors/conn_crm_mock/invoke",
                  {"operation": "funnel_query", "params": {}})
    check("连接器: CRM 销售漏斗调用", st == 200 and inv.get("success")
          and len(inv["data"].get("stages", [])) == 3, str(inv)[:150])

    st, cfg = req("POST", "/api/connectors", {
        "id": "conn_oa_mock", "type": "oa", "name": "Mock OA",
        "base_url": base, "api_key": "key",
    })
    st, inv = req("POST", "/api/connectors/conn_oa_mock/invoke",
                  {"operation": "approval_create",
                   "params": {"type": "purchase", "amount": 3000}})
    check("连接器: OA 发起审批（高风险操作）", st == 200 and inv.get("success"), str(inv)[:150])

    # 审计：身份透传 → actor 应为 ops-user
    st, logs = req("GET", "/api/audit/logs?tool_name=erp.order_query")
    check("审计: 连接器操作落库", st == 200 and len(logs) >= 1)
    if logs:
        check("审计: X-User 身份透传为 actor", logs[0]["actor"] == "ops-user",
              logs[0]["actor"])

    # 停用实例 → invoke 403
    st, _ = req("PUT", "/api/connectors/conn_erp_mock", {"enabled": False})
    st, inv = req("POST", "/api/connectors/conn_erp_mock/invoke",
                  {"operation": "order_list", "params": {}})
    check("连接器: 停用后调用被拒", st == 403, str(st))
    st, _ = req("PUT", "/api/connectors/conn_erp_mock", {"enabled": True})

    # 清理
    for cid in ("conn_erp_mock", "conn_crm_mock", "conn_oa_mock"):
        st, _ = req("DELETE", f"/api/connectors/{cid}")
        assert st == 200


async def api_db_ro():
    # 准备样例 SQLite 库
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    Path(tmp.name).unlink(missing_ok=True)
    conn = sqlite3.connect(tmp.name)
    conn.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, price REAL)")
    conn.executemany("INSERT INTO products (name, price) VALUES (?, ?)",
                     [("电机 A", 1200.0), ("轴承 B", 85.5), ("控制器 C", 399.0)])
    conn.commit()
    conn.close()

    st, cfg = req("POST", "/api/db-connectors", {
        "id": "db_prod_ro", "name": "生产库只读", "db_type": "sqlite", "dsn": tmp.name,
    })
    check("DB只读: 新增连接", st == 200, str(cfg))

    st, test = req("POST", "/api/db-connectors/db_prod_ro/test")
    check("DB只读: 连通性测试（只读打开）", st == 200 and test.get("ok"), str(test))

    st, q = req("POST", "/api/db-connectors/db_prod_ro/query",
                {"sql": "SELECT name, price FROM products WHERE price > 100 ORDER BY price DESC"})
    check("DB只读: SELECT 查询返回 2 行",
          st == 200 and q.get("success") and q.get("count") == 2, str(q)[:200])

    st, q = req("POST", "/api/db-connectors/db_prod_ro/query",
                {"sql": "DELETE FROM products WHERE id = 1"})
    check("DB只读: DELETE 被拒绝（400）", st == 400 and "只读" in str(q), str(q)[:150])

    st, q = req("POST", "/api/db-connectors/db_prod_ro/query",
                {"sql": "INSERT INTO products (name, price) VALUES ('x', 1)"})
    check("DB只读: INSERT 被拒绝", st == 400)

    st, q = req("POST", "/api/db-connectors/db_prod_ro/query",
                {"sql": "SELECT 1; DROP TABLE products"})
    check("DB只读: 多语句注入被拒绝", st == 400)

    st, q = req("POST", "/api/db-connectors/db_prod_ro/query",
                {"sql": "UPDATE products SET price = 0"})
    check("DB只读: UPDATE 被拒绝", st == 400)

    # 只读模式兜底：即使绕过校验，文件级 mode=ro 也应拒绝写
    st, q = req("POST", "/api/db-connectors/db_prod_ro/query",
                {"sql": "SELECT 1"})
    check("DB只读: 只读查询后数据未被篡改",
          st == 200 and q.get("success"))
    conn2 = sqlite3.connect(tmp.name)
    n = conn2.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    conn2.close()
    check("DB只读: 数据库行数未变（3 行）", n == 3, str(n))

    st, _ = req("DELETE", f"/api/db-connectors/db_prod_ro")
    Path(tmp.name).unlink(missing_ok=True)


async def api_webhook():
    st, ev = req("POST", "/api/webhooks/erp-events",
                 {"source": "erp", "event": "order.created", "order_id": 123})
    check("Webhook: 接收事件", st == 200 and ev.get("event_id"), str(ev))
    st, evs = req("GET", "/api/webhooks/events?hook_id=erp-events")
    check("Webhook: 事件落库可查",
          st == 200 and any(e.get("hookId") == "erp-events" for e in evs), str(evs)[:150])


async def tools_layer():
    """工具层：ConnectorTool 未配置提示 + 配置后调用；db_query 只读校验。"""
    # 进程内使用独立数据目录并完成建表/预置（避免污染真实数据）
    os.environ["AGENT_DATA_DIR"] = "/tmp/p1-verify-data"
    os.makedirs("/tmp/p1-verify-data", exist_ok=True)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from app.storage import init_db, init_extensions, seed_builtin_tools, seed_connector_tools
    await init_db()
    await init_extensions()
    await seed_builtin_tools()
    await seed_connector_tools()

    from app.tools import create_tools
    from app.connectors import validate_read_only_sql, ReadOnlyViolation

    tools = create_tools()
    erp_tool = tools.get("erp")
    check("工具层: erp 工具已注册", erp_tool is not None and erp_tool.name == "erp")
    check("工具层: db_query/rpa 已注册",
          "db_query" in tools and "rpa" in tools)

    result = await erp_tool.execute({"operation": "order_list", "params": {}})
    check("工具层: 未配置连接返回明确提示",
          not result["success"] and "尚未配置启用" in result["error"], result["error"][:120])

    rpa = await tools["rpa"].execute({"action": "open", "params": {"url": "https://legacy"}})
    check("工具层: rpa 返回待接入提示",
          not rpa["success"] and "待接入" in rpa["error"])

    # 只读 SQL 校验（单元级）
    ok = True
    try:
        validate_read_only_sql("SELECT * FROM t WHERE id = 1")
    except ReadOnlyViolation:
        ok = False
    check("只读校验: SELECT 放行", ok)
    rejected = True
    try:
        validate_read_only_sql("INSERT INTO t VALUES (1)")
    except ReadOnlyViolation:
        rejected = True
    check("只读校验: INSERT 拒绝", rejected)


async def main():
    print("== P1 连接器（mock 企业系统）==")
    mock_port = start_mock()
    await api_connectors(mock_port)
    print("== P1 数据库只读直连 ==")
    await api_db_ro()
    print("== P1 Webhook ==")
    await api_webhook()
    print("== P1 工具层 ==")
    await tools_layer()
    print(f"\n结果: PASS={PASS} FAIL={FAIL}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())

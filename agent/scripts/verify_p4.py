"""P4 功能验证脚本（Phase B 生态与高级运维）。

覆盖（需求 §B.5）：
1. 连接器补齐：类型清单 9 类（erp/crm/oa/wms/eam/hrm/mes/srm/bi）、新类型操作模板、
   WMS 连接器创建与真实调用失败捕获（不假成功）
2. 知识库适配器：平台清单（>=5 implemented）、FastGPT 连接与缺配置明确提示
3. 技能市场正式化：本地市场安装回归 + 在线市场（SKILL_HUB_URL）目录拉取与全链路安装
   （在线不可用则跳过对应断言）
4. 高级运维：失败原因自动诊断（分类+修复建议）、成本精细化导出（by=model/tool）、
   操作回放优化（summary 结构）
5. 回归：工具注册表统一列表 / 连接器类型清单

用法：AGENT_TOKEN=xxx python scripts/verify_p4.py [base_url] [data_dir]
服务端需以 SKILL_HUB_URL=http://127.0.0.1:8899 启动以启用在线市场（否则在线断言跳过）。
"""
import asyncio
import http.server
import json
import os
import shutil
import socketserver
import sys
import threading
import urllib.request
import zipfile
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8878"
DATA_DIR = sys.argv[2] if len(sys.argv) > 2 else "/tmp/p4-verify"
TOKEN = os.environ.get("AGENT_TOKEN", "testtoken123")
ONLINE_PORT = 8899

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


# ============ 1. 连接器补齐 ============

def connectors():
    print("== P4 连接器补齐 ==")
    st, types = req("GET", "/api/connectors/types")
    names = {t["type"]: t for t in types}
    check("连接器: 类型清单 9 类", st == 200 and len(types) == 9
          and {"erp", "crm", "oa", "wms", "eam", "hrm", "mes", "srm", "bi"}
          .issubset(names), f"{sorted(names)}")

    wms_ops = {o["operation"] for o in names["wms"]["operations"]}
    check("连接器: WMS 操作模板（出库/库存/盘点）",
          {"outbound_create", "stock_query", "inventory_count"}.issubset(wms_ops),
          str(sorted(wms_ops))[:120])
    mes_ops = {o["operation"] for o in names["mes"]["operations"]}
    check("连接器: MES 操作模板（工单/报工/质检）",
          {"work_order_list", "production_report", "quality_inspection"}.issubset(mes_ops),
          str(sorted(mes_ops))[:120])
    bi_ops = {o["operation"] for o in names["bi"]["operations"]}
    check("连接器: BI 操作模板（报表/指标/导出）",
          {"report_list", "metric_query", "export_report"}.issubset(bi_ops),
          str(sorted(bi_ops))[:120])

    # 新类型连接器实例化 + 真实调用失败捕获（不假成功）
    st, c = req("POST", "/api/connectors", {
        "id": "conn_wms_v", "type": "wms", "name": "验证WMS",
        "base_url": "http://127.0.0.1:1/api", "api_key": "",
    })
    check("连接器: WMS 实例创建", st == 200 and c.get("id") == "conn_wms_v", str(c)[:120])
    st, inv = req("POST", "/api/connectors/conn_wms_v/invoke",
                  {"operation": "outbound_create", "params": {"sku": "A1", "qty": 2}})
    check("连接器: 新类型调用失败明确返回（不假成功）",
          st == 200 and inv.get("success") is False and inv.get("error"),
          str(inv)[:160])


# ============ 2. 知识库适配器 ============

def knowledge():
    print("== P4 知识库适配器 ==")
    st, platforms = req("GET", "/api/knowledge-servers/platforms")
    impl = {p["platform"] for p in platforms if p["implemented"]}
    check("知识库: 平台清单（>=5 implemented）", st == 200 and len(impl) >= 5,
          f"{sorted(impl)}")
    check("知识库: P4 新适配器已实现",
          {"fastgpt", "ragflow", "notion"}.issubset(impl), str(sorted(impl))[:120])

    st, ks = req("POST", "/api/knowledge-servers", {
        "id": "kb_fastgpt_v", "name": "验证FastGPT", "platform": "fastgpt",
        "base_url": "http://127.0.0.1:1", "api_key": "",
    })
    check("知识库: FastGPT 连接创建", st == 200 and ks.get("id") == "kb_fastgpt_v",
          str(ks)[:120])
    st, sr = req("POST", "/api/knowledge-servers/kb_fastgpt_v/search",
                 {"query": "设备保养手册", "top_k": 3})
    hit = sr.get("results") or sr.get("items") or (sr if isinstance(sr, list) else [])
    text = json.dumps(sr, ensure_ascii=False)
    check("知识库: 缺 dataset_id 明确提示（不假成功）", "配置缺失" in text
          or "dataset_id" in text, text[:160])


# ============ 3. 技能市场正式化 ============

def _make_local_package() -> None:
    """在服务端数据目录造本地市场包。"""
    pkg = Path(DATA_DIR) / "skill_market" / "skill-report"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "manifest.json").write_text(json.dumps({
        "name": "销售报表生成",
        "version": "1.0.0",
        "description": "生成销售报表（本地市场）",
        "security_level": "P2",
        "permissions": {"filesystem": "workdir_rw", "network": "none",
                        "system_calls": "none"},
        "tools": [],
    }, ensure_ascii=False), encoding="utf-8")
    (pkg / "SKILL.md").write_text("# 销售报表生成\n\n本地市场技能包", encoding="utf-8")


class _OnlineMarketHandler(http.server.SimpleHTTPRequestHandler):
    """静态文件服务：/market.json + /manifests/ + /archives/。"""

    def log_message(self, *args):
        pass


class _OnlineMarketServer(socketserver.TCPServer):
    allow_reuse_address = True


def _prepare_online_market() -> str:
    """构造在线市场静态目录，返回根 URL。"""
    root = Path(DATA_DIR) / "online-market"
    (root / "manifests").mkdir(parents=True, exist_ok=True)
    (root / "archives").mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": "在线报表技能",
        "version": "2.0.0",
        "description": "从在线市场安装的报表技能",
        "security_level": "P2",
        "permissions": {"filesystem": "workdir_rw", "network": "none",
                        "system_calls": "none"},
        "tools": [],
    }
    (root / "manifests" / "skill-online.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    pkg = Path(DATA_DIR) / "online-pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False),
                                       encoding="utf-8")
    (pkg / "SKILL.md").write_text("# 在线报表技能\n\n在线市场安装包", encoding="utf-8")
    zip_path = root / "archives" / "skill-online.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in ("manifest.json", "SKILL.md"):
            zf.write(pkg / f, arcname=f"skill-online/{f}")
    catalog = {"items": [{
        "id": "skill-online",
        "name": "在线报表技能",
        "version": "2.0.0",
        "description": "从在线市场安装的报表技能",
        "security_level": "P2",
        "manifest_url": f"http://127.0.0.1:{ONLINE_PORT}/manifests/skill-online.json",
        "archive_url": f"http://127.0.0.1:{ONLINE_PORT}/archives/skill-online.zip",
    }]}
    (root / "market.json").write_text(json.dumps(catalog, ensure_ascii=False),
                                      encoding="utf-8")
    return str(root)


def skills_market():
    print("== P4 技能市场正式化 ==")
    _make_local_package()
    # 本地市场安装回归
    st, mkt = req("GET", "/api/skills/market")
    local = [i for i in (mkt.get("items") or []) if i.get("source") == "local_market"]
    check("市场: 本地市场清单含包", st == 200
          and any(i["id"] == "skill-report" for i in local), str(mkt)[:150])

    st, inst = req("POST", "/api/skills/install",
                   {"source": "market", "market_id": "skill-report"})
    check("市场: 本地市场安装", st == 200 and inst.get("ok"), str(inst)[:150])

    # 在线市场：起静态服务 + 目录拉取 + 全链路安装
    online_configured = bool(os.environ.get("SKILL_HUB_URL"))
    if not online_configured:
        skip("市场: 在线市场未配置 SKILL_HUB_URL，跳过在线安装链路")
        return
    root = _prepare_online_market()
    handler = lambda *a, **k: _OnlineMarketHandler(*a, directory=root, **k)
    with _OnlineMarketServer(("127.0.0.1", ONLINE_PORT), handler) as httpd:
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            st, mkt2 = req("GET", "/api/skills/market")
            online = [i for i in (mkt2.get("items") or [])
                      if i.get("source") == "online_market"]
            check("市场: 在线目录拉取（source=online_market）",
                  st == 200 and any(i["id"] == "skill-online" for i in online),
                  str(mkt2)[:200])
            if not online:
                skip("市场: 在线目录为空，跳过安装")
                return
            st, inst2 = req("POST", "/api/skills/install",
                            {"source": "market", "market_id": "skill-online"})
            check("市场: 在线包全链路安装（catalog→manifest→zip→落库）",
                  st == 200 and inst2.get("ok"), str(inst2)[:200])
            if inst2.get("ok"):
                installed_id = (inst2.get("skill") or {}).get("id", "")
                st, skills = req("GET", "/api/skills")
                check("市场: 在线包已入技能列表", st == 200 and installed_id
                      and any(s.get("id") == installed_id for s in skills),
                      f"installed={installed_id} {str(skills)[:150]}")
        finally:
            httpd.shutdown()


# ============ 4. 高级运维 ============

def ops():
    print("== P4 高级运维 ==")
    # 失败诊断
    req("POST", "/api/workflows", {
        "id": "wf_diag_v", "name": "diag", "trigger_type": "manual",
        "steps": [{"id": "c", "type": "connector",
                   "config": {"type": "hrm", "operation": "payroll_query", "params": {}}}],
    })
    st, r = req("POST", "/api/workflows/wf_diag_v/run", {})
    run_id = r.get("run_id", "")
    st, d = req("GET", f"/api/ops/diagnostics?run_id={run_id}")
    diag = d.get("diagnosis") or {}
    check("诊断: 失败原因自动分类", st == 200
          and diag.get("category") == "connector_not_configured"
          and diag.get("reason") and diag.get("suggestion"),
          str(d)[:200])

    # skipped 诊断
    req("POST", "/api/workflows", {
        "id": "wf_skip_v", "name": "skip", "trigger_type": "manual",
        "steps": [
            {"id": "c1", "type": "condition",
             "config": {"field": "${payload.n}", "op": "lt", "value": 10}},
            {"id": "n", "type": "notify", "config": {"message": "x", "channel": "audit"}},
        ],
    })
    st, r2 = req("POST", "/api/workflows/wf_skip_v/run", {"payload": {"n": 99}})
    st, d2 = req("GET", f"/api/ops/diagnostics?run_id={r2.get('run_id','')}")
    check("诊断: 条件跳过归类", st == 200
          and (d2.get("diagnosis") or {}).get("category") == "condition_skipped",
          str(d2)[:150])

    # 成本精细化导出
    st, m = req("GET", "/api/usage/export?by=model&fmt=json")
    check("导出: by=model 结构", st == 200 and m.get("by") == "model"
          and "rows" in m, str(m)[:120])
    st, t = req("GET", "/api/usage/export?by=tool&fmt=json")
    check("导出: by=tool 结构", st == 200 and t.get("by") == "tool"
          and "rows" in t, str(t)[:120])
    st, u = req("GET", "/api/usage/export?by=user&fmt=csv")
    check("导出: by=user CSV", st == 200 and "csv" in u, str(u)[:100])
    st, day = req("GET", "/api/usage/export?by=day&fmt=csv")
    check("导出: by=day 回归", st == 200 and "csv" in day, str(day)[:100])

    # 操作回放优化
    st, rp = req("GET", "/api/audit/replay?conversation_id=conv-p4-1&summary=true")
    check("回放: summary 结构", st == 200 and "summary" in rp
          and "steps" in rp and rp["summary"].get("total") is not None,
          str(rp)[:150])
    st, rp2 = req("GET", "/api/audit/replay?conversation_id=conv-p4-1")
    check("回放: 默认保持列表兼容", st == 200 and isinstance(rp2, list),
          str(rp2)[:100])


# ============ 5. 回归 ============

def regression():
    print("== P4 回归 ==")
    st, tools = req("GET", "/api/mcp/tools")
    kinds = {}
    for t in tools:
        kinds[t["kind"]] = kinds.get(t["kind"], 0) + 1
    check("回归: 工具注册表统一列表", st == 200 and "tool" in kinds and "skill" in kinds,
          str(tools)[:120])
    st, types = req("GET", "/api/connectors/types")
    check("回归: 连接器类型清单 9 类", st == 200 and len(types) == 9,
          f"{len(types)} 类")


async def main():
    print(f"== P4 验证（base={BASE}）==")
    connectors()
    knowledge()
    skills_market()
    ops()
    regression()
    print(f"\n结果: PASS={PASS} FAIL={FAIL} SKIP={SKIP}")


if __name__ == "__main__":
    asyncio.run(main())

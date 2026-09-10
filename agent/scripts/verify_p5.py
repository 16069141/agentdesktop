"""P5 功能验证脚本（Phase B 生态长尾）。

覆盖（需求 §B.5）：
1. 知识库适配器补齐：平台清单 8 类全 implemented、wiki_js（GraphQL 检索 + 缺 Token 提示）、
   feishu_wiki（tenant_access_token 换发 + 知识空间检索 + 缺配置提示）
2. 技能市场运营：/api/skills/market/stats（本地/在线/已安装/来源分布/最近安装）、
   /api/skills/market 在线目录合并
3. 操作回放可视化：/api/audit/replay/stats（tools/buckets/heat/trend/latency/summary）
4. 回归：P4 诊断 / by=model 导出 / replay summary 结构；平台清单；连接器 9 类

用法：AGENT_TOKEN=xxx python scripts/verify_p5.py [base_url] [data_dir]
服务端需以 SKILL_HUB_URL=http://127.0.0.1:8899 启动以覆盖在线市场统计（否则对应断言跳过）。
"""
import asyncio
import http.server
import json
import os
import socketserver
import sys
import threading
import urllib.request
import zipfile
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8878"
DATA_DIR = sys.argv[2] if len(sys.argv) > 2 else "/tmp/p5-verify"
TOKEN = os.environ.get("AGENT_TOKEN", "testtoken123")
ONLINE_PORT = 8899
MOCK_PORT = 8900

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


# ============ mock 知识库服务（wiki_js GraphQL + 飞书 token/space） ============

class _KbMockHandler(http.server.BaseHTTPRequestHandler):
    """8900：Wiki.js GraphQL + 飞书开放平台 mock。"""

    def log_message(self, *args):
        pass

    def _send(self, obj: dict, code: int = 200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}
        if self.path == "/graphql":
            query = body.get("query", "")
            if "pages" in query:
                self._send({"data": {"pages": {"list": [
                    {"id": "p1", "title": "库存盘点流程", "path": "/ops/inventory",
                     "content": "库存盘点操作手册：每月末执行盘点，差异需上报。",
                     },
                    {"id": "p2", "title": "设备保养计划", "path": "/ops/maintenance",
                     "content": "设备保养计划与责任人。",
                     },
                ]}}})
            else:
                self._send({"data": {}})
            return
        if self.path == "/open-apis/auth/v3/tenant_access_token/internal":
            self._send({"code": 0, "msg": "ok", "tenant_access_token": "mock-tenant-token"})
            return
        self._send({"code": 400, "msg": f"unhandled {self.path}"}, 400)

    def do_GET(self):
        if self.path.startswith("/open-apis/wiki/v2/spaces"):
            self._send({"code": 0, "data": {"items": [
                {"space_id": "s1", "name": "运维知识库",
                 "description": "设备与系统运维文档"},
                {"space_id": "s2", "name": "市场资料", "description": "营销物料归档"},
            ]}})
            return
        self._send({"code": 404, "msg": f"unhandled {self.path}"}, 404)


class _KbMockServer(socketserver.TCPServer):
    allow_reuse_address = True


# ============ 在线市场静态服务（运营统计用） ============

class _OnlineMarketHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class _OnlineMarketServer(socketserver.TCPServer):
    allow_reuse_address = True


def _make_local_package() -> None:
    pkg = Path(DATA_DIR) / "skill_market" / "skill-report-p5"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "manifest.json").write_text(json.dumps({
        "name": "库存预警技能",
        "version": "1.1.0",
        "description": "库存低于阈值自动预警（P5 本地市场）",
        "security_level": "P2",
        "permissions": {"filesystem": "workdir_rw", "network": "none",
                        "system_calls": "none"},
        "tools": [],
    }, ensure_ascii=False), encoding="utf-8")
    (pkg / "SKILL.md").write_text("# 库存预警技能\n\n本地市场技能包", encoding="utf-8")


def _prepare_online_market() -> str:
    root = Path(DATA_DIR) / "online-market"
    (root / "manifests").mkdir(parents=True, exist_ok=True)
    (root / "archives").mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": "在线看板技能",
        "version": "3.0.0",
        "description": "BI 看板生成技能（P5 在线市场）",
        "security_level": "P2",
        "permissions": {"filesystem": "workdir_rw", "network": "none",
                        "system_calls": "none"},
        "tools": [],
    }
    (root / "manifests" / "skill-online-p5.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    pkg = Path(DATA_DIR) / "online-pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False),
                                       encoding="utf-8")
    (pkg / "SKILL.md").write_text("# 在线看板技能\n\n在线市场安装包", encoding="utf-8")
    zip_path = root / "archives" / "skill-online-p5.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in ("manifest.json", "SKILL.md"):
            zf.write(pkg / f, arcname=f"skill-online-p5/{f}")
    # 同目录再提供 P4 的在线包（skill-online），保证 P4 回归在缓存窗口内也能通过
    p4_manifest = {
        "name": "在线报表技能",
        "version": "2.0.0",
        "description": "从在线市场安装的报表技能",
        "security_level": "P2",
        "permissions": {"filesystem": "workdir_rw", "network": "none",
                        "system_calls": "none"},
        "tools": [],
    }
    (root / "manifests" / "skill-online.json").write_text(
        json.dumps(p4_manifest, ensure_ascii=False), encoding="utf-8")
    p4_pkg = Path(DATA_DIR) / "online-pkg-p4"
    p4_pkg.mkdir(parents=True, exist_ok=True)
    (p4_pkg / "manifest.json").write_text(json.dumps(p4_manifest, ensure_ascii=False),
                                          encoding="utf-8")
    (p4_pkg / "SKILL.md").write_text("# 在线报表技能\n\n在线市场安装包", encoding="utf-8")
    with zipfile.ZipFile(root / "archives" / "skill-online.zip", "w") as zf:
        for f in ("manifest.json", "SKILL.md"):
            zf.write(p4_pkg / f, arcname=f"skill-online/{f}")
    catalog = {"items": [{
        "id": "skill-online-p5",
        "name": "在线看板技能",
        "version": "3.0.0",
        "description": "BI 看板生成技能（P5 在线市场）",
        "security_level": "P2",
        "manifest_url": f"http://127.0.0.1:{ONLINE_PORT}/manifests/skill-online-p5.json",
        "archive_url": f"http://127.0.0.1:{ONLINE_PORT}/archives/skill-online-p5.zip",
    }, {
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


# ============ 1. 知识库适配器补齐 ============

def knowledge_p5():
    print("== P5 知识库适配器补齐 ==")
    st, platforms = req("GET", "/api/knowledge-servers/platforms")
    impl = {p["platform"] for p in platforms if p["implemented"]}
    check("知识库: 平台清单 8 类全 implemented",
          st == 200 and len(impl) == 8
          and {"wiki_js", "feishu_wiki"}.issubset(impl), f"{sorted(impl)}")
    check("知识库: 无 pending 平台", st == 200
          and not any(not p["implemented"] for p in platforms),
          f"{[p for p in platforms if not p['implemented']]}")

    with _KbMockServer(("127.0.0.1", MOCK_PORT), _KbMockHandler) as httpd:
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            mock = f"http://127.0.0.1:{MOCK_PORT}"
            # wiki_js：带 Token 检索
            st, ks = req("POST", "/api/knowledge-servers", {
                "id": "kb_wiki_v", "name": "验证WikiJS", "platform": "wiki_js",
                "base_url": mock, "api_key": "wiki-token", "type": "wiki",
            })
            check("知识库: Wiki.js 连接创建", st == 200 and ks.get("id") == "kb_wiki_v",
                  str(ks)[:120])
            st, sr = req("POST", "/api/knowledge-servers/kb_wiki_v/search",
                         {"query": "库存", "top_k": 5})
            hits = sr.get("results") or (sr if isinstance(sr, list) else [])
            check("知识库: Wiki.js GraphQL 检索命中",
                  st == 200 and len(hits) >= 1
                  and any("库存" in h.get("title", "") for h in hits),
                  json.dumps(sr, ensure_ascii=False)[:180])
            st, sr2 = req("POST", "/api/knowledge-servers/kb_wiki_v/search",
                          {"query": "不存在的关键词xyz", "top_k": 5})
            hits2 = sr2.get("results") or (sr2 if isinstance(sr2, list) else [])
            check("知识库: Wiki.js 检索本地过滤（无关词为空）",
                  st == 200 and len(hits2) == 0, json.dumps(sr2, ensure_ascii=False)[:150])

            # wiki_js：缺 Token 明确提示
            st, ks3 = req("POST", "/api/knowledge-servers", {
                "id": "kb_wiki_notoken", "name": "WikiJS无Token", "platform": "wiki_js",
                "base_url": mock, "api_key": "", "type": "wiki",
            })
            st, sr3 = req("POST", "/api/knowledge-servers/kb_wiki_notoken/search",
                          {"query": "库存", "top_k": 3})
            text3 = json.dumps(sr3, ensure_ascii=False)
            check("知识库: Wiki.js 缺 Token 明确提示（不假成功）",
                  "配置缺失" in text3 or "API Token" in text3, text3[:150])

            # feishu_wiki：token 换发 + 空间检索
            st, kf = req("POST", "/api/knowledge-servers", {
                "id": "kb_feishu_v", "name": "验证飞书知识库", "platform": "feishu_wiki",
                "base_url": mock, "api_key": "app-secret", "type": "wiki",
                "extra_config": {"app_id": "cli_mock_app"},
            })
            check("知识库: 飞书连接创建", st == 200 and kf.get("id") == "kb_feishu_v",
                  str(kf)[:120])
            st, sf = req("POST", "/api/knowledge-servers/kb_feishu_v/search",
                         {"query": "运维", "top_k": 5})
            hitsf = sf.get("results") or (sf if isinstance(sf, list) else [])
            check("知识库: 飞书知识空间检索命中",
                  st == 200 and len(hitsf) >= 1
                  and any("运维" in h.get("title", "") for h in hitsf),
                  json.dumps(sf, ensure_ascii=False)[:180])

            # feishu_wiki：缺 app_id 明确提示
            st, kf2 = req("POST", "/api/knowledge-servers", {
                "id": "kb_feishu_noapp", "name": "飞书缺App", "platform": "feishu_wiki",
                "base_url": mock, "api_key": "app-secret", "type": "wiki",
            })
            st, sf2 = req("POST", "/api/knowledge-servers/kb_feishu_noapp/search",
                          {"query": "运维", "top_k": 3})
            textf = json.dumps(sf2, ensure_ascii=False)
            check("知识库: 飞书缺 app_id 明确提示（不假成功）",
                  "配置缺失" in textf or "app_id" in textf, textf[:150])
        finally:
            httpd.shutdown()


# ============ 2. 技能市场运营 ============

def market_ops():
    print("== P5 技能市场运营 ==")
    _make_local_package()
    st, inst = req("POST", "/api/skills/install",
                   {"source": "market", "market_id": "skill-report-p5"})
    check("市场运营: 本地包安装", st == 200 and inst.get("ok"), str(inst)[:150])

    online_configured = bool(os.environ.get("SKILL_HUB_URL"))
    root = _prepare_online_market() if online_configured else ""
    handler = lambda *a, **k: _OnlineMarketHandler(*a, directory=root, **k)
    with _OnlineMarketServer(("127.0.0.1", ONLINE_PORT), handler) as httpd:
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            if online_configured:
                st, inst2 = req("POST", "/api/skills/install",
                                {"source": "market", "market_id": "skill-online-p5"})
                check("市场运营: 在线包安装", st == 200 and inst2.get("ok"),
                      str(inst2)[:150])

            st, mkt = req("GET", "/api/skills/market")
            online_items = mkt.get("online") or []
            check("市场运营: /market 合并在线目录",
                  st == 200 and isinstance(online_items, list)
                  and (not online_configured or any(
                      i.get("id") == "skill-online-p5" and i.get("source") == "online_market"
                      for i in online_items)),
                  str(mkt)[:200])

            st, stats = req("GET", "/api/skills/market/stats")
            ok_stats = (st == 200
                        and stats.get("total_local") >= 1
                        and isinstance(stats.get("source_distribution"), dict)
                        and isinstance(stats.get("recent_installs"), list)
                        and stats.get("installed_count") >= 1)
            if online_configured:
                ok_stats = ok_stats and stats.get("total_online") >= 1
            check("市场运营: market/stats 结构（本地/在线/已安装/来源/最近）",
                  ok_stats, str(stats)[:220])
            if st == 200 and stats.get("installed_count"):
                check("市场运营: 来源分布含市场安装",
                      stats["source_distribution"].get("market", 0) >= 2,
                      str(stats["source_distribution"])[:120])
            if st == 200 and stats.get("recent_installs"):
                r0 = stats["recent_installs"][0]
                check("市场运营: 最近安装含时间与来源",
                      r0.get("installedAt") and r0.get("source"),
                      str(r0)[:120])
        finally:
            httpd.shutdown()


# ============ 3. 操作回放可视化 ============

def replay_stats():
    print("== P5 操作回放可视化 ==")
    conv = "conv-p5-replay"
    for tool, n in (("shell", 2), ("erp", 1)):
        for i in range(n):
            req("POST", "/api/mcp/audit", {
                "tool_name": tool,
                "params": {"cmd": f"task-{i}", "id_card": "110101199001011234"},
                "result": f"done-{tool}-{i}",
                "conversation_id": conv, "actor": "p5-user",
            })
    st, stats = req("GET", f"/api/audit/replay/stats?conversation_id={conv}")
    check("回放: stats 结构（tools/buckets/heat/trend/latency/summary）",
          st == 200 and "tools" in stats and "buckets" in stats
          and "heat" in stats and "trend" in stats and "latency" in stats,
          str(stats)[:180])
    check("回放: 工具排序（调用次数降序）",
          st == 200 and stats.get("tools") == ["shell", "erp"],
          str(stats.get("tools"))[:120])
    check("回放: 热力图含计数单元",
          st == 200 and len(stats.get("heat", [])) >= 2
          and all(len(cell) == 3 for cell in stats["heat"]),
          str(stats.get("heat"))[:150])
    check("回放: 趋势三系列累计正确",
          st == 200
          and stats["trend"]["ok"] == [1, 2, 3]
          and stats["trend"]["failed"] == [0, 0, 0]
          and stats["trend"]["rejected"] == [0, 0, 0],
          json.dumps(stats.get("trend"), ensure_ascii=False)[:150])
    check("回放: 延迟折线按步序",
          st == 200 and stats["latency"]["x"] == [1, 2, 3]
          and len(stats["latency"]["ms"]) == 3,
          json.dumps(stats.get("latency"), ensure_ascii=False)[:150])
    check("回放: summary 一致",
          st == 200 and stats.get("summary", {}).get("total") == 3
          and stats["summary"].get("tools", {}).get("shell") == 2,
          json.dumps(stats.get("summary"), ensure_ascii=False)[:150])

    # 工作流审计步骤（conversation_id=run_id）→ stats 可用
    req("POST", "/api/workflows", {
        "id": "wf_p5_audit", "name": "p5audit", "trigger_type": "manual",
        "steps": [{"id": "n", "type": "notify",
                   "config": {"message": "P5 审计", "channel": "audit"}}],
    })
    st, r = req("POST", "/api/workflows/wf_p5_audit/run", {})
    run_id = r.get("run_id", "")
    st, wstats = req("GET", f"/api/audit/replay/stats?conversation_id={run_id}")
    check("回放: 工作流审计步骤可聚合",
          st == 200 and wstats.get("summary", {}).get("total") >= 1
          and "workflow" in wstats.get("tools", []),
          str(wstats)[:180])


# ============ 4. 回归 ============

def regression():
    print("== P5 回归 ==")
    st, types = req("GET", "/api/connectors/types")
    check("回归: 连接器类型清单 9 类", st == 200 and len(types) == 9,
          f"{len(types)} 类")
    st, rp = req("GET", f"/api/audit/replay?conversation_id=conv-p5-replay")
    check("回归: 回放默认列表兼容", st == 200 and isinstance(rp, list)
          and len(rp) >= 1, str(rp)[:100])
    st, m = req("GET", "/api/usage/export?by=model&fmt=json")
    check("回归: by=model 导出", st == 200 and m.get("by") == "model",
          str(m)[:100])
    st, d = req("GET", "/api/ops/diagnostics")
    check("回归: 诊断默认聚合最近失败", st == 200
          and "total_failed" in (d if isinstance(d, dict) else {})
          and "diagnoses" in (d if isinstance(d, dict) else {}),
          str(d)[:120])


async def main():
    print(f"== P5 验证（base={BASE}）==")
    knowledge_p5()
    market_ops()
    replay_stats()
    regression()
    print(f"\n结果: PASS={PASS} FAIL={FAIL} SKIP={SKIP}")


if __name__ == "__main__":
    asyncio.run(main())

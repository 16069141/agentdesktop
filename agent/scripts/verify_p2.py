"""P2 功能验证脚本（Phase B 协同与增强）。

覆盖：
1. 项目空间：CRUD / 成员三级权限 / 任务三模式 / 资产库 / 配置共享 / 级联删除
2. Skills 运营形态：市场安装 / 对话式安装 / Git 仓库导入（本地 file:// 仓库）
3. 系统运维：健康巡检（断连检测 + 告警审计）/ 字段映射 CRUD / 调用量导出
4. 回归：工具注册表统一列表

用法：AGENT_TOKEN=xxx python scripts/verify_p2.py [base_url]
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8878"
# 服务端数据目录（市场包准备需与服务端一致；默认 /tmp/p2-verify）
DATA_DIR_FOR_MARKET = sys.argv[2] if len(sys.argv) > 2 else "/tmp/p2-verify"
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
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        text = e.read().decode() or "{}"
        try:
            return e.code, json.loads(text)
        except json.JSONDecodeError:
            return e.code, {"raw": text}


# ============ 1. 项目空间 ============

async def projects():
    print("== P2 项目空间 ==")
    st, p = req("POST", "/api/projects", {"name": "集团数据中台", "description": "跨系统数据协同"})
    check("项目: 创建（owner=local）", st == 200 and p["owner"] == "local", str(p)[:150])
    pid = p["id"]

    st, p2 = req("GET", f"/api/projects/{pid}")
    check("项目: 详情含成员/任务/资产", st == 200
          and any(m["username"] == "local" and m["role"] == "admin" for m in p2["members"])
          and p2["tasks"] == [] and p2["assets"] == [])

    st, m = req("POST", f"/api/projects/{pid}/members", {"username": "zhang.wei", "role": "editor"})
    check("项目: 添加 editor 成员", st == 200 and m["role"] == "editor")
    req("POST", f"/api/projects/{pid}/members", {"username": "li.na", "role": "viewer"})
    st, _ = req("POST", f"/api/projects/{pid}/members", {"username": "bad", "role": "owner"})
    check("项目: 非法角色被拒", st == 400)

    # viewer 不能创建任务（403），editor 可以
    st, _ = req("POST", f"/api/projects/{pid}/tasks",
                {"title": "越权任务"}, headers={"X-User-Name": "li.na"})
    check("项目: viewer 创建任务被拒(403)", st == 403, str(st))
    st, task = req("POST", f"/api/projects/{pid}/tasks",
                   {"title": "ERP 库存对账", "mode": "collaborative", "assignee": "zhang.wei"},
                   headers={"X-User-Name": "zhang.wei"})
    check("项目: editor 创建协作任务", st == 200 and task["mode"] == "collaborative", str(task)[:150])
    tid = task["id"]

    st, task = req("PUT", f"/api/projects/{pid}/tasks/{tid}",
                   {"status": "doing", "mode": "shared"})
    check("项目: 任务状态流转", st == 200 and task["status"] == "doing" and task["mode"] == "shared")

    st, task = req("POST", f"/api/projects/{pid}/tasks",
                   {"title": "独立任务", "mode": "independent"})
    check("项目: 独立模式任务", st == 200 and task["mode"] == "independent")
    req("DELETE", f"/api/projects/{pid}/tasks/{task['id']}")

    # 资产库
    st, asset = req("POST", f"/api/projects/{pid}/assets",
                    {"name": "ERP 接口文档", "type": "doc", "content": "金蝶云星空订单创建接口说明 v2"})
    check("项目: 添加资产", st == 200 and asset["type"] == "doc", str(asset)[:120])
    st, sr = req("POST", f"/api/projects/{pid}/assets/search", {"query": "订单创建"})
    check("项目: 资产 RAG 全文命中",
          st == 200 and any(r["assetName"] == "ERP 接口文档" for r in sr["results"]), str(sr)[:200])

    st, sr = req("POST", f"/api/projects/{pid}/assets/search", {"query": "不存在的内容xyz"})
    check("项目: 资产 RAG 无命中返回空", st == 200 and sr["results"] == [])

    # 配置共享
    st, cs = req("PUT", f"/api/projects/{pid}/config-sharing",
                 {"skills": True, "mcp": True, "agent": False})
    check("项目: 配置共享开关", st == 200 and cs == {"skills": True, "mcp": True, "agent": False})
    st, sc = req("GET", f"/api/projects/{pid}/shared-config")
    check("项目: 共享配置聚合（skills 启用项）",
          st == 200 and sc["sharing"]["skills"] and isinstance(sc.get("skills"), list), str(sc)[:150])

    # 成员列表 + 非成员 403
    st, members = req("GET", f"/api/projects/{pid}/members")
    check("项目: 成员列表", st == 200 and len(members) == 3)
    st, _ = req("GET", f"/api/projects/{pid}",
                headers={"X-User-Name": "outsider"})
    check("项目: 非成员访问被拒(403)", st == 403, str(st))

    # 删除（级联）
    st, _ = req("DELETE", f"/api/projects/{pid}")
    st, _ = req("GET", f"/api/projects/{pid}")
    check("项目: 删除后不可访问", st == 404)

    st, p = req("POST", "/api/projects", {"name": "临时项目"})
    req("DELETE", f"/api/projects/{p['id']}")


# ============ 2. Skills 运营形态 ============

def make_skill_package(root: Path, name: str, desc: str) -> Path:
    pkg = root / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "manifest.json").write_text(json.dumps({
        "name": name, "version": "1.0.0", "description": desc,
        "security_level": "P2",
        "permissions": {"filesystem": "workdir_rw", "network": "none", "system_calls": "none"},
        "dependencies": {"python": []},
        "tools": [{"name": f"{name}_do", "description": desc}],
    }, ensure_ascii=False), encoding="utf-8")
    (pkg / "SKILL.md").write_text(f"# {name}\n{desc}\n", encoding="utf-8")
    return pkg


async def skills_market():
    print("== P2 Skills 市场/对话式/Git ==")
    # 进程内准备市场目录（复用服务端数据目录，保证市场包对服务端可见）
    os.environ["AGENT_DATA_DIR"] = DATA_DIR_FOR_MARKET
    os.makedirs(DATA_DIR_FOR_MARKET, exist_ok=True)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from app.storage import init_db, init_extensions, seed_builtin_skills
    await init_db()
    await init_extensions()
    await seed_builtin_skills()
    from app.skills.loader import market_root
    market = market_root()
    make_skill_package(market, "skill-report", "销售报表自动生成与分析")
    make_skill_package(market, "skill-sla", "客户 SLA 跟进提醒")

    st, m = req("GET", "/api/skills/market")
    check("市场: 技能包清单", st == 200 and len(m.get("items", [])) >= 2, str(m)[:150])

    st, s = req("POST", "/api/skills/market/search", {"query": "销售报表"})
    check("市场: 检索命中（相关性排序）",
          st == 200 and s["items"] and s["items"][0]["id"] == "skill-report", str(s)[:150])

    st, r = req("POST", "/api/skills/install", {"source": "market", "market_id": "skill-report"})
    check("市场: 安装成功", st == 200 and r.get("ok") and r["skill"]["source"] == "market", str(r)[:200])

    st, r = req("POST", "/api/skills/install", {"source": "dialog", "query": "客户 SLA"})
    check("对话式: 自然语言安装", st == 200 and r.get("ok") and r["skill"]["source"] == "dialog",
          str(r)[:200])

    st, r = req("POST", "/api/skills/install", {"source": "market", "market_id": "not-exist"})
    check("市场: 未知包明确报错", st == 400 and "未找到" in str(r), str(r)[:150])

    # Git 导入（本地 file:// 仓库）
    git_bin = shutil.which("git")
    if not git_bin:
        skip("Git: 本机无 git CLI")
    else:
        tmp = tempfile.mkdtemp(prefix="p2-git-")
        repo_dir = Path(tmp) / "repo"
        repo_dir.mkdir()
        make_skill_package(repo_dir, "skill-git-demo", "git 导入演示")
        subprocess.run(["git", "-C", str(repo_dir), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(repo_dir), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo_dir), "-c", "user.email=t@t",
                        "-c", "user.name=t", "commit", "-q", "-m", "init"], check=True)
        st, r = req("POST", "/api/skills/install",
                    {"source": "git", "git_url": f"file://{repo_dir}"})
        check("Git: 仓库导入成功", st == 200 and r.get("ok")
              and r["skill"]["source"] == "git", str(r)[:200])
        shutil.rmtree(tmp, ignore_errors=True)

    st, r = req("POST", "/api/skills/install", {"source": "git", "git_url": ""})
    check("Git: 缺 git_url 报错", st == 400 and "git_url" in str(r))

    # 清理测试安装
    for sid in ("skill-report", "skill-sla", "skill-git-demo"):
        st, _ = req("DELETE", f"/api/skills/{sid}")
        assert st == 200


# ============ 3. 系统运维 ============

async def ops():
    print("== P2 系统运维 ==")
    # 断连连接器（不可达地址）
    st, cfg = req("POST", "/api/connectors", {
        "id": "conn_ops_down", "type": "erp", "name": "断连ERP",
        "base_url": "http://127.0.0.1:59999", "api_key": "k",
    })
    check("运维: 准备断连连接器", st == 200)

    st, health = req("POST", "/api/ops/health-check")
    check("运维: 健康巡检执行（检测到断连）",
          st == 200 and health["down_count"] >= 1
          and any(r["id"] == "conn_ops_down" for r in health["down"]), str(health)[:200])

    st, logs = req("GET", "/api/audit/logs?tool_name=health:connector.conn_ops_down")
    check("运维: 断连告警写审计", st == 200 and len(logs) >= 1
          and logs[0]["action"] == "connector_down", str(logs)[:150])

    st, cur = req("GET", "/api/ops/health")
    check("运维: 健康状态汇总可查", st == 200
          and any(i["id"] == "conn_ops_down" and i["ok"] is False for i in cur["items"]))
    req("DELETE", "/api/connectors/conn_ops_down")

    # 字段映射
    st, m = req("POST", "/api/field-mappings", {
        "id": "map_order_customer", "name": "订单客户映射",
        "connector_id": "conn_erp_mock", "source_field": "order.customer_id",
        "target_field": "crm.customer_code", "transform": {"type": "map"},
    })
    check("字段映射: 创建", st == 200 and m["transform"]["type"] == "map", str(m)[:150])
    st, m = req("PUT", "/api/field-mappings/map_order_customer", {"enabled": False})
    check("字段映射: 编辑", st == 200 and m["enabled"] is False)
    st, ms = req("GET", "/api/field-mappings?connector_id=conn_erp_mock")
    check("字段映射: 按连接器筛选", st == 200 and len(ms) == 1)
    st, m = req("POST", "/api/field-mappings", {
        "id": "bad_map", "name": "坏映射", "connector_id": "c",
        "source_field": "a", "target_field": "b", "transform": {"type": "evil"},
    })
    check("字段映射: 非法 transform 被拒", st == 400)
    req("DELETE", "/api/field-mappings/map_order_customer")
    req("DELETE", "/api/field-mappings/bad_map")

    # 调用量导出
    st, ex = req("GET", "/api/usage/export?by=day&days=7&fmt=csv")
    check("调用统计: 按天 CSV 导出", st == 200 and "date,messages,tokens,cost" in ex.get("csv", ""))
    st, ex = req("GET", "/api/usage/export?by=connector&fmt=json")
    check("调用统计: 按连接器 JSON 导出", st == 200 and isinstance(ex.get("rows"), list))
    st, ex = req("GET", "/api/usage/export?by=user&fmt=json")
    check("调用统计: 按用户 JSON 导出", st == 200 and isinstance(ex.get("rows"), list))


# ============ 4. 回归 ============

async def regression():
    print("== P2 回归 ==")
    st, tools = req("GET", "/api/mcp/tools")
    kinds = {}
    for t in tools:
        kinds[t["kind"]] = kinds.get(t["kind"], 0) + 1
    check("回归: 工具注册表统一列表", st == 200 and "tool" in kinds and "skill" in kinds)
    st, types = req("GET", "/api/connectors/types")
    check("回归: 连接器类型清单", st == 200
          and {"erp", "crm", "oa"}.issubset({t["type"] for t in types}),
          f"{len(types)} 类")


async def main():
    await projects()
    await skills_market()
    await ops()
    await regression()
    print(f"\n结果: PASS={PASS} FAIL={FAIL} SKIP={SKIP}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())

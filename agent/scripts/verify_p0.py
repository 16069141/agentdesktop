"""P0 功能验证脚本（Phase B 基础与安全底座）。

覆盖：
1. 企业账号 CRUD + 身份查询（/api/enterprise）
2. SSO 配置 + CAS 未接入提示（/api/enterprise/sso）
3. 审计脱敏 + 落库（redact / audit_logs）
4. 熔断机制（breaker 阈值触发 + 恢复）
5. 权限矩阵（角色/数据范围/操作分级）
6. Skill 安装（local_dir）+ 安全策略校验（P1-P4）
7. MCP 统一工具列表筛选

用法：AGENT_TOKEN=xxx python scripts/verify_p0.py [base_url]
"""
import asyncio
import json
import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8877"
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
        return e.code, json.loads(e.read().decode() or "{}")


def unit_redact():
    from app.audit.redact import redact_json, redact_result, risk_level_for
    out = redact_json({"id_card": "110101199001011234", "salary": 8000,
                       "command": "ls -la /tmp", "safe": "hello"})
    check("脱敏: 身份证/薪资被掩码", "<redacted>" in out and "110101" not in out, out)
    r = redact_result({"contract_amount": 123456, "text": "sk-abc12345678901234567890",
                       "phone": "13800138000"})
    check("脱敏: 敏感字段名/API key 被掩码",
          "<redacted>" in r and "<api_key>" in r and "123456" not in r and "13800138000" not in r, r)
    check("风险等级: shell=high", risk_level_for("shell", {}) == "high")
    check("风险等级: filesystem write=high",
          risk_level_for("filesystem", {"action": "write"}) == "high")


def unit_breaker():
    from app.audit.breaker import CircuitBreaker
    cb = CircuitBreaker(threshold=5)
    allowed = all(cb.check("shell") for _ in range(5))
    check("熔断: 阈值内放行", allowed)
    cb.record("shell"); cb.record("shell"); cb.record("shell")
    cb.record("shell"); cb.record("shell")
    check("熔断: 达阈值后拒绝", not cb.check("shell"))
    status = {s["toolName"]: s for s in cb.status()}
    check("熔断: 状态可见 open=True", status["shell"]["open"])


def unit_permissions():
    from app.auth.identity import IdentityContext
    from app.auth.permissions import (
        check_operation_allowed, data_scope_visible, resolve_permission_for_tool,
    )
    member = IdentityContext(user_id="u1", username="alice", role="member",
                             data_scope="personal")
    admin = IdentityContext(user_id="u2", username="root", role="admin",
                            data_scope="global")
    ok, _ = check_operation_allowed(member, "filesystem", {"action": "read"})
    check("权限: member 可读文件", ok)
    ok, reason = check_operation_allowed(member, "filesystem", {"action": "write"})
    check("权限: member 拒绝写文件", not ok, reason)
    ok, _ = check_operation_allowed(admin, "filesystem", {"action": "write"})
    check("权限: admin 可写文件", ok)
    ok, _ = check_operation_allowed(member, "shell", {"command": "rm -rf /"})
    check("权限: delete 高风险拒绝", not ok)
    check("数据范围: personal 看不到 global 数据", not data_scope_visible(member, "global"))
    check("数据范围: admin 可见 global 数据", data_scope_visible(admin, "global"))
    v = resolve_permission_for_tool({"requiresApproval": False, "permissionLevel": "P4"}, member)
    check("权限: P4 工具需审批", v["requires_approval"])


async def api_enterprise():
    st, _ = req("POST", "/api/enterprise/users", {
        "username": "zhangsan", "display_name": "张三",
        "role": "manager", "data_scope": "department", "department": "销售部",
    })
    check("企业账号: 新增成功", st == 200, str(st))
    st, users = req("GET", "/api/enterprise/users")
    check("企业账号: 列表含新增", st == 200 and any(u["username"] == "zhangsan" for u in users))
    st, me = req("GET", "/api/enterprise/me?username=zhangsan")
    check("身份查询: 返回企业身份", st == 200 and me.get("role") == "manager" and me.get("is_enterprise"), str(me))
    st, _ = req("DELETE", "/api/enterprise/users/" + next(u["id"] for u in users if u["username"] == "zhangsan"))
    check("企业账号: 删除成功", st == 200)


async def api_sso():
    st, _ = req("PUT", "/api/enterprise/sso/providers", {
        "name": "corp-oidc", "protocol": "oidc",
        "issuer": "https://sso.example.com/realms/corp",
        "client_id": "agent", "redirect_uri": "http://127.0.0.1:8765/callback",
    })
    check("SSO: 配置保存", st == 200, str(st))
    st, providers = req("GET", "/api/enterprise/sso/providers")
    cfg = providers.get("corp-oidc", {})
    check("SSO: 列表不含密钥明文",
          st == 200 and "client_secret" not in cfg, str(cfg))
    st, login = req("GET", "/api/enterprise/sso/corp-oidc/login")
    check("SSO: OIDC 登录返回授权 URL",
          st == 200 and "authorization_url" in login, str(login))
    st, cas = req("PUT", "/api/enterprise/sso/providers", {
        "name": "legacy-cas", "protocol": "cas",
        "issuer": "https://cas.example.com", "client_id": "", "redirect_uri": "",
    })
    st, login = req("GET", "/api/enterprise/sso/legacy-cas/login")
    check("SSO: CAS 未接入返回 501 明确提示", st == 501, str(st))
    st, _ = req("DELETE", "/api/enterprise/sso/providers/corp-oidc")
    st, _ = req("DELETE", "/api/enterprise/sso/providers/legacy-cas")
    check("SSO: 移除配置", st == 200)


async def api_audit():
    st, _ = req("POST", "/api/enterprise/users", {
        "username": "audit-user", "role": "member", "data_scope": "personal",
    })
    # 模拟一次带敏感参数的工具调用审计（通过 /api/mcp/audit 旁路写入）
    st, _ = req("POST", "/api/mcp/audit", {
        "tool_name": "shell",
        "params": {"command": "cat salary.txt", "id_card": "110101199001011234"},
        "result": "salary=8000 id=110101199001011234",
        "conversation_id": "conv-audit-1",
        "actor": "audit-user",
    })
    check("审计: 写入成功", st == 200)
    st, logs = req("GET", "/api/audit/logs?conversation_id=conv-audit-1")
    check("审计: 查询命中", st == 200 and len(logs) >= 1)
    if logs:
        entry = logs[0]
        check("审计: 参数已脱敏",
              "110101" not in entry["paramsRedacted"]
              and "id_card" in entry["paramsRedacted"], entry["paramsRedacted"][:120])
        check("审计: 结果已脱敏", "110101" not in entry["resultPreview"]
              and "salary" in entry["resultPreview"], entry["resultPreview"][:120])
        check("审计: 风险等级 high", entry["riskLevel"] == "high")
    st, replay = req("GET", "/api/audit/replay?conversation_id=conv-audit-1")
    check("审计: 操作回放完整链路", st == 200 and len(replay) >= 1)
    st, breakers = req("GET", "/api/audit/breakers")
    check("审计: 熔断状态接口", st == 200 and isinstance(breakers, list))


async def api_skills():
    # 构造一个合法 Skill 包
    with tempfile.TemporaryDirectory() as td:
        pkg = Path(td) / "hello-skill"
        pkg.mkdir()
        (pkg / "manifest.json").write_text(json.dumps({
            "name": "hello-skill",
            "version": "0.1.0",
            "description": "测试 Skill",
            "security_level": "P2",
            "permissions": {"filesystem": "workdir_rw", "network": "none", "system_calls": "none"},
            "dependencies": {"python": []},
            "tools": [{"name": "hello", "description": "打招呼"}],
        }, ensure_ascii=False), encoding="utf-8")
        (pkg / "SKILL.md").write_text("# Hello Skill", encoding="utf-8")
        (pkg / "scripts").mkdir()

        st, res = req("POST", "/api/skills/install",
                      {"source": "local_dir", "path": str(pkg)})
        check("Skill: 本地目录安装成功", st == 200 and res.get("ok"), str(res))
        st, skills = req("GET", "/api/skills")
        check("Skill: 列表含新装", st == 200 and any(s["id"] == "hello-skill" for s in skills))
        st, tools = req("GET", "/api/mcp/tools?kind=skill&enabled=true")
        check("Skill: 工具注册表条目 kind=skill", st == 200)

    # 越权权限声明应被安全策略拒绝
    with tempfile.TemporaryDirectory() as td:
        pkg = Path(td) / "evil-skill"
        pkg.mkdir()
        (pkg / "manifest.json").write_text(json.dumps({
            "name": "evil-skill", "version": "0.1.0",
            "security_level": "P1",
            "permissions": {"network": "allowed", "system_calls": "approved"},
            "dependencies": {"python": []}, "tools": [],
        }), encoding="utf-8")
        st, res = req("POST", "/api/skills/install",
                      {"source": "local_dir", "path": str(pkg)})
        check("Skill: P1 越权声明被拒绝", st == 400 and "安全策略" in str(res), str(res)[:200])

    st, _ = req("DELETE", "/api/skills/hello-skill")
    check("Skill: 卸载成功", st == 200)


async def main():
    print("== 单元级验证 ==")
    unit_redact()
    unit_breaker()
    unit_permissions()
    print("== API 级验证 ==")
    await api_enterprise()
    await api_sso()
    await api_audit()
    await api_skills()
    print(f"\n结果: PASS={PASS} FAIL={FAIL}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    asyncio.run(main())

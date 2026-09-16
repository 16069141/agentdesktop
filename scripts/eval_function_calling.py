#!/usr/bin/env python3
"""Function Calling 评测集（L4 数据闭环）。

对一组代表性任务逐条发起真实对话（craft 模式），检查模型是否：
- 选择了期望的工具（expect_tool）
- 指定了期望的动作/参数（expect_action，可选）
- （本地工具）工具结果成功

用法（后端需已运行）：
  .venv/bin/python scripts/eval_function_calling.py --limit 8
  .venv/bin/python scripts/eval_function_calling.py --all
  .venv/bin/python scripts/eval_function_calling.py --model deepseek-v4-pro

输出：逐任务 PASS / PARTIAL / FAIL + 汇总报告（通过率、按工具分组）。
评分标准：
  PASS    = 调用期望工具且（若工具不需要外部配置）结果成功
  PARTIAL = 调用了期望工具但工具失败（配置缺失/环境问题，属配置类）
  FAIL    = 未调用期望工具（模型选错工具）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from typing import Dict, List, Tuple

BASE = "http://127.0.0.1:8765"

# ── 评测任务集（覆盖主要工具族）────────────────────────────
# requires_config=True 的工具（db_query/knowledge/generate_image 等）：
# 只要模型选了正确工具即算 PARTIAL 而非 FAIL（服务未配置时工具本身会失败）。
TASKS: List[Dict] = [
    # filesystem
    {"id": "fs-read", "prompt": "读取 /Users/caojian/Desktop/agent/workdesktop/v2/AGENTS.md 的前 20 行，告诉我这个项目是什么",
     "expect_tool": "filesystem", "expect_action": "read", "desc": "读文件"},
    {"id": "fs-list", "prompt": "列出 /Users/caojian/Desktop/agent/workdesktop/v2 目录下有哪些文件",
     "expect_tool": "filesystem", "expect_action": "list", "desc": "列目录"},
    {"id": "fs-write", "prompt": "在我的桌面（/Users/caojian/Desktop）下创建文件 eval_l4_check.txt，内容写入 hello-from-eval",
     "expect_tool": "filesystem", "expect_action": "write", "desc": "写文件"},
    # code_locate
    {"id": "locate", "prompt": "客户端代码里 normalize_v1_url 这个函数在哪个文件哪一行？用代码定位工具",
     "expect_tool": "code_locate", "expect_action": None, "desc": "定位代码"},
    # code
    {"id": "code-an", "prompt": "分析一下 agent/app/agents/orchestrator.py 的代码结构（函数/类数量）",
     "expect_tool": "code", "expect_action": "analyze", "desc": "代码分析"},
    # shell
    {"id": "sh-py", "prompt": "用命令查看当前 Python 版本号",
     "expect_tool": "shell", "expect_action": None, "desc": "shell 命令"},
    # web_search
    {"id": "web-search", "prompt": "联网搜索一下 2026 年大模型行业的最新动态",
     "expect_tool": "web_search", "expect_action": None, "desc": "联网搜索"},
    # browser
    {"id": "web-browser", "prompt": "打开 https://github.com 看看首页有什么内容",
     "expect_tool": "browser", "expect_action": None, "desc": "抓取网页"},
    # db_query（配置类）
    {"id": "db", "prompt": "用数据库工具查一下当前只读连接里有哪些表",
     "expect_tool": "db_query", "expect_action": None, "desc": "数据库查询", "requires_config": True},
    # knowledge（配置类）
    {"id": "kb", "prompt": "查一下企业内部知识库里关于请假制度的规定",
     "expect_tool": "knowledge", "expect_action": None, "desc": "知识库检索", "requires_config": True},
    # subagent
    {"id": "sub", "prompt": "把「调研 Python 异步框架」和「调研前端状态管理」两个主题分别委派给两个研究员子智能体并行调研",
     "expect_tool": "subagent", "expect_action": None, "desc": "子智能体委派"},
    # plan
    {"id": "plan", "prompt": "给我建一个三步计划：先查资料、再写分析报告、最后生成一个 HTML 报告页面",
     "expect_tool": "create_plan", "expect_action": None, "desc": "创建计划"},
    # generate_image（配置类）
    {"id": "img", "prompt": "帮我生成一张秋天森林的风景图片",
     "expect_tool": "generate_image", "expect_action": None, "desc": "生成图片", "requires_config": True},
    # generate_ppt
    {"id": "ppt", "prompt": "帮我生成一个 5 页的 PPT，主题是「2026 年团队年中总结」",
     "expect_tool": "generate_ppt", "expect_action": None, "desc": "生成 PPT"},
]


def get_token() -> str:
    pid = subprocess.run(
        ["lsof", "-nP", "-iTCP:8765", "-sTCP:LISTEN", "-t"],
        capture_output=True, text=True,
    ).stdout.split()[0]
    env = subprocess.run(
        ["ps", "eww", pid], capture_output=True, text=True,
    ).stdout
    m = re.search(r"AGENT_TOKEN=(\S+)", env)
    return m.group(1)


def http_json(url: str, method: str, body: dict, token: str, timeout: float = 20) -> dict:
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    data = json.dumps(body).encode()
    with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def http_sse(url: str, body: dict, token: str, timeout: float = 300) -> str:
    req = urllib.request.Request(url, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data=json.dumps(body).encode(), timeout=timeout) as resp:
        return resp.read().decode()


def parse_sse(text: str) -> List[Dict]:
    events, ev = [], ""
    for ln in text.split("\n"):
        if ln.startswith("event: "):
            ev = ln[7:].strip()
        elif ln.startswith("data: ") and ev:
            try:
                events.append({"event": ev, "data": json.loads(ln[6:])})
            except json.JSONDecodeError:
                pass
    return events


def run_task(task: Dict, model: str, token: str) -> Tuple[str, str, str]:
    """返回 (verdict, detail, tool_calls)。"""
    cid = http_json(f"{BASE}/api/conversations", "POST",
                    {"title": f"eval-{task['id']}"}, token).get("id", "")
    if not cid:
        return "FAIL", "会话创建失败", ""
    body = {
        "conversation_id": cid,
        "message": task["prompt"],
        "model_id": model,
        "work_mode": "craft",
    }
    raw = http_sse(f"{BASE}/api/chat", body, token)
    events = parse_sse(raw)
    calls = [(e["data"].get("name", ""), e["data"].get("arguments", ""))
             for e in events if e["event"] == "tool_call"]
    results = [e["data"] for e in events if e["event"] == "tool_result"]

    if not calls:
        return "FAIL", "未调用任何工具", ""

    names = [n for n, _ in calls]
    expect = task["expect_tool"]
    if expect not in names:
        return "FAIL", f"工具选错（期望 {expect}，实际 {names[:3]}）", json.dumps(calls, ensure_ascii=False)[:200]

    # 期望 action 检查
    exp_action = task.get("expect_action")
    if exp_action:
        arg = next((a for n, a in calls if n == expect), "")
        try:
            args = json.loads(arg)
        except json.JSONDecodeError:
            args = {}
        got = args.get("action")
        if got != exp_action:
            return "FAIL", f"action 错误（期望 {exp_action}，实际 {got}）", json.dumps(calls, ensure_ascii=False)[:200]

    # 工具结果成功性（配置类工具只验证选对工具）
    if task.get("requires_config"):
        return "PARTIAL", "选对工具（配置类，结果依赖服务配置）", ""
    for r in results:
        rc = r.get("result", "")
        if isinstance(rc, str) and ('"success": false' in rc or "[工具执行失败]" in rc):
            return "PARTIAL", "调用了正确工具但执行失败", rc[:150]
    return "PASS", f"选对工具并执行成功（共 {len(calls)} 次调用）", ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=8, help="跑前 N 个任务")
    ap.add_argument("--all", action="store_true", help="跑全部任务")
    ap.add_argument("--model", default="agnes-2.5-flash")
    ap.add_argument("--out", default="", help="报告写入路径（可选）")
    args = ap.parse_args()

    token = get_token()
    tasks = TASKS if args.all else TASKS[: args.limit]
    print(f"评测模型: {args.model} | 任务数: {len(tasks)}\n")

    rows: List[Dict] = []
    for i, t in enumerate(tasks, 1):
        print(f"[{i}/{len(tasks)}] {t['id']}（{t['desc']}）…", flush=True)
        t0 = time.time()
        try:
            verdict, detail, calls = run_task(t, args.model, token)
        except Exception as exc:  # noqa: BLE001
            verdict, detail, calls = "FAIL", f"请求异常: {exc}", ""
        rows.append({"id": t["id"], "expect": t["expect_tool"], "verdict": verdict,
                     "detail": detail, "secs": round(time.time() - t0, 1)})
        mark = {"PASS": "✅", "PARTIAL": "🟡", "FAIL": "❌"}[verdict]
        print(f"    {mark} {verdict}  {detail}  ({rows[-1]['secs']}s)")

    n = len(rows)
    ok = sum(1 for r in rows if r["verdict"] == "PASS")
    partial = sum(1 for r in rows if r["verdict"] == "PARTIAL")
    fail = n - ok - partial
    print("\n" + "=" * 60)
    print(f"PASS {ok}/{n}（{ok/n*100:.0f}%） | PARTIAL {partial} | FAIL {fail}")
    by_tool: Dict[str, List] = {}
    for r in rows:
        by_tool.setdefault(r["expect"], []).append(r["verdict"])
    print("\n按工具分组：")
    for tool, vs in sorted(by_tool.items()):
        print(f"  {tool:16s} {'/'.join(vs)}")

    report = {"model": args.model, "total": n, "pass": ok, "partial": partial,
              "fail": fail, "rows": rows}
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n报告已写入: {args.out}")


if __name__ == "__main__":
    main()

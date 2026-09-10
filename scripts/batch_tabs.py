#!/usr/bin/env python3
"""Batch tab inspection: click each top tab, dump visible text, screenshot."""
import sys, os, json, time, subprocess

BASE = os.path.dirname(os.path.abspath(__file__))
def cdp(*args):
    r = subprocess.run([sys.executable, os.path.join(BASE, "cdp_client.py"), *args],
                       capture_output=True, text=True, timeout=120)
    return (r.stdout or "").strip() + (("\nERR:" + r.stderr[-300:]) if r.returncode else "")

TABS = {
    "用量": 577, "连接": 642, "项目": 707, "编排": 772,
    "审计": 837, "技能": 902, "设置": 967,
}
for name, x in TABS.items():
    print(f"\n{'='*20} {name} tab {'='*20}")
    print("click:", cdp("click", str(x), "25")[:60])
    time.sleep(1.2)
    txt = cdp("text")
    # print the content area (skip sidebar)
    lines = txt.split("\n")
    # content = after the 设置 tab line
    try:
        idx = lines.index("⚙️设置") + 1
        body = "\n".join(lines[idx:idx+38])
    except ValueError:
        body = "\n".join(lines[12:50])
    print(body[:1800])
    cdp("shot", os.path.join(os.path.dirname(BASE), "test-report", "shots", f"07-{name}.png"))
    print("shot: 07-" + name + ".png")

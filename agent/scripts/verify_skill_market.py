#!/usr/bin/env python3
"""技能市场链路隔离测试（AGENT_DATA_DIR 指向临时目录，不碰真实数据）。

覆盖：① market 安装 → installed/ 落盘 → SkillTool 挂载 → 执行 → 停用移除
      ② _online_market_urls 读取 settings 配置（skill_market_urls）与 env 兜底
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

AGENT_DIR = "/Users/caojian/Desktop/agent/workdesktop/v2/agent"
PKG_SRC = Path("/Users/caojian/Desktop/agent/workdesktop/v2/skill-hub/packages/skill-json-transform")

tmp = Path(tempfile.mkdtemp(prefix="skilltest_"))
os.environ["AGENT_DATA_DIR"] = str(tmp)
sys.path.insert(0, AGENT_DIR)

fails = []


def check(name, cond, detail=""):
    if cond:
        print(f"[ok] {name}")
    else:
        fails.append(name)
        print(f"[FAIL] {name} {detail}")


async def main() -> None:
    from app.storage.extensions_schema import init_and_seed
    from app.skills import loader

    await init_and_seed()
    print(f"[info] 隔离数据目录: {tmp}")

    # ── ② 配置读取：settings.skill_market_urls ──
    cfg_dir = tmp / "config"
    cfg_dir.mkdir(exist_ok=True)
    (cfg_dir / "settings.json").write_text(
        json.dumps({"skill_market_urls": ["https://cfg.example.github.io/hub"]}),
        encoding="utf-8",
    )
    os.environ["AGENT_CONFIG_DIR"] = str(cfg_dir)
    os.environ.pop("SKILL_HUB_URL", None)
    os.environ.pop("CLAWHUB_URL", None)
    urls = loader._online_market_urls()
    check("在线市场 URL 读取 settings 配置", urls == ["https://cfg.example.github.io/hub"], f"got={urls}")

    # env 追加去重
    os.environ["SKILL_HUB_URL"] = "https://cfg.example.github.io/hub"
    urls = loader._online_market_urls()
    check("env 与配置合并去重", urls == ["https://cfg.example.github.io/hub"], f"got={urls}")
    os.environ["CLAWHUB_URL"] = "https://second.example.github.io/hub"
    urls = loader._online_market_urls()
    check("多市场地址追加", len(urls) == 2 and urls[-1].endswith("second.example.github.io/hub"), f"got={urls}")

    # ── ① 本地市场安装 ──
    pkg_dir = loader.market_root() / "skill-json-transform"
    shutil.copytree(PKG_SRC, pkg_dir)
    result = await loader.install_skill({"source": "market", "market_id": "skill-json-transform"})
    check("市场安装成功", bool(result.get("ok")), f"err={result.get('error')} {result.get('issues')}")

    inst = loader.skills_root() / "installed" / "skill-json-transform"
    check("安装目录落盘", (inst / "scripts/main.py").is_file(), f"dir={inst}")

    # ── SkillTool 挂载 + 执行 ──
    from app.tools.skill_tools import sync_installed_skill_tools

    registry: dict = {}
    await sync_installed_skill_tools(registry)
    check("SkillTool 已挂载", "json_transform" in registry, f"registry={list(registry)}")
    tool = registry.get("json_transform")
    if tool:
        check("P1 技能无需审批", tool.requires_approval is False)
        out = await tool.execute(
            {"json_text": '{"data": {"items": [{"name": "a"}]}}', "path": "data.items.0.name"}
        )
        check("SkillTool 执行返回结果", out.get("success") and out.get("result") == "a", f"out={out}")
        bad = await tool.execute({"json_text": "{bad"})
        check("非法输入返回错误", bad.get("success") is False and bad.get("error"), f"out={bad}")

    # 停用 → 同步 → 工具移除
    await loader.set_enabled("skill-json-transform", False)
    await sync_installed_skill_tools(registry)
    check("停用后工具移除", "json_transform" not in registry, f"registry={list(registry)}")

    print("\n结果:", "全部通过" if not fails else f"失败: {fails}")


if __name__ == "__main__":
    asyncio.run(main())
    sys.exit(1 if fails else 0)

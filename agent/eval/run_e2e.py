#!/usr/bin/env python3
"""Phase 3 端到端验证：真实模型 + 真实工具调用闭环。

不经过 HTTP 服务，直接在进程内驱动 AgentOrchestrator，
避免后台服务被回收导致的调试困难。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AGENT_DATA_DIR", "/Users/caojian/Desktop/agent/workdesktop/agent/data")

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-7s %(name)s | %(message)s",
)
# 压制第三方噪音，突出 agent 自身日志
for noisy in ("httpx", "httpcore", "openai", "chromadb", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

from app.agents.orchestrator import AgentOrchestrator  # noqa: E402
from app.context.context_manager import ContextManager  # noqa: E402
from app.providers import get_registry  # noqa: E402


async def auto_approve(payload: dict) -> bool:
    """评测专用：自动批准 shell 命令。

    仅限本地验证使用。生产路径必须走 UI 确认（approval_callback=None 时默认拒绝）。
    这里仍然只放行只读白名单命令，避免验证脚本变成绕过安全控制的后门。
    """
    command = (payload or {}).get("command", "")
    read_only = ("ls", "cat", "pwd", "echo", "head", "tail", "wc", "find", "grep")
    first = command.strip().split()[0] if command.strip() else ""
    return first in read_only


async def run_case(orchestrator, name: str, message: str, show_chars: int = 400):
    print("\n" + "=" * 72)
    print(f"用例: {name}")
    print(f"输入: {message}")
    print("=" * 72)

    tool_calls: list[str] = []
    text_parts: list[str] = []
    model_id = None
    turns = 0
    started = time.time()

    async for event in orchestrator.run_stream(
        user_message=message,
        conversation_id=f"eval-{name}",
    ):
        etype = event.get("type", "")
        if etype == "meta":
            model_id = event.get("model_id")
            print(f"[模型] {model_id}")
        elif etype == "tool_call":
            tool_calls.append(event.get("name", ""))
            args = event.get("arguments", "")
            print(f"[工具调用] {event.get('name')}({args[:120]})")
        elif etype == "text":
            text_parts.append(event.get("delta", ""))
        elif etype == "done":
            print(f"[完成] usage={event.get('usage', {})}")
        elif etype == "error":
            print(f"[错误] {event.get('message')}")

    elapsed = time.time() - started
    answer = "".join(text_parts).strip()
    turns = len(tool_calls)

    print(f"\n[工具链] {tool_calls if tool_calls else '（无工具调用）'}")
    print(f"[耗时]   {elapsed:.1f}s")
    print(f"[回答]   {answer[:show_chars]}{'…' if len(answer) > show_chars else ''}")

    return {
        "name": name,
        "model": model_id,
        "tools": tool_calls,
        "turns": turns,
        "answer": answer,
        "elapsed": elapsed,
    }


async def main():
    registry = get_registry()
    cm = ContextManager(max_tokens=16384)

    print("=" * 72)
    print("Phase 3 端到端验证 —— 真实 Ollama 模型 + 真实工具")
    print("=" * 72)

    # 1) 工具注册检查
    orchestrator = AgentOrchestrator(
        context_manager=cm,
        system_prompt=(
            "你是一个智能助手。需要访问文件、执行命令或查询知识库时，必须调用可用工具，"
            "不要凭空编造结果。工具返回后基于真实结果作答。"
        ),
        approval_callback=auto_approve,
        allowed_root_dirs=["/Users/caojian/Desktop/agent/workdesktop"],
    )
    tools = orchestrator.get_tool_registry()
    print(f"\n已注册工具 ({len(tools)}): {sorted(tools.keys())}")
    for t in tools.values():
        schema = t.to_openai_schema()
        req = schema["function"]["parameters"].get("required", [])
        print(f"  - {schema['function']['name']}: required={req}")

    cases = [
        ("shell", "列出 /Users/caojian/Desktop/agent/workdesktop 目录下有哪些文件和文件夹"),
        ("filesystem", "读取 /Users/caojian/Desktop/agent/workdesktop/PHASE-PROMPTS.md 的开头内容"),
        ("coding", "帮我写一个快速排序的 Python 实现"),
    ]

    results = []
    for name, message in cases:
        try:
            results.append(await run_case(orchestrator, name, message))
        except Exception as exc:
            print(f"\n[异常] 用例 {name} 失败: {exc}")
            import traceback
            traceback.print_exc()
            results.append({"name": name, "error": str(exc), "tools": [], "turns": 0})

    # 汇总
    print("\n\n" + "=" * 72)
    print("汇总")
    print("=" * 72)
    print(f"{'用例':<14}{'模型':<34}{'工具调用':<26}{'轮次':<6}{'耗时':<8}")
    print("-" * 72)
    for r in results:
        model = (r.get("model") or "-")[:32]
        tools_str = ",".join(r.get("tools") or []) or "无"
        print(f"{r['name']:<14}{model:<34}{tools_str[:24]:<26}{r.get('turns', 0):<6}{r.get('elapsed', 0):<8.1f}")

    with_tools = [r for r in results if r.get("tools")]
    print("-" * 72)
    print(f"成功触发工具调用的用例: {len(with_tools)}/{len(results)}")

    out = "/Users/caojian/Desktop/agent/workdesktop/agent/eval/e2e_result.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"详细结果已写入: {out}")


if __name__ == "__main__":
    asyncio.run(main())

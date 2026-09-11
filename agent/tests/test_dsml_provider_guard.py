"""DSML 拦截在 provider 流式链路上的集成测试。

覆盖「工具调用必须早于 done 下发」这一关键时序 —— orchestrator 收到 done 就
break，晚到的 tool_calls 会被整体丢弃。
"""
from __future__ import annotations

import asyncio

from app.dsml import FW_PIPE
from app.providers.base import _dsml_event, _dsml_guard

START = f"<{FW_PIPE}{FW_PIPE}DSML{FW_PIPE}{FW_PIPE}>"
END = f"</{FW_PIPE}{FW_PIPE}DSML{FW_PIPE}{FW_PIPE}>"

DSML_BLOCK = (
    f"{START}\n"
    "| tool_calls>\n"
    '| invoke name="shell">\n'
    '| parameter name="command" string="true">ls -la | grep py\n'
    "| parameter>\n"
    "| invoke>\n"
    "| tool_calls>\n"
    f"{END}"
)


def run_guard(chunks):
    """把给定事件序列喂进 _dsml_guard，返回产出事件列表。"""

    async def _src():
        for c in chunks:
            yield c

    async def _collect():
        return [e async for e in _dsml_guard(_src())]

    return asyncio.run(_collect())


def test_tool_calls_emitted_before_done():
    """DSML 工具调用必须排在 done 之前，否则会被 orchestrator 丢弃。"""
    chunks = [
        {"type": "text", "delta": "我来查一下。"},
        {"type": "text", "delta": DSML_BLOCK},
        {"type": "done", "finish_reason": "stop", "usage": {"promptTokens": 1, "completionTokens": 2}},
    ]
    events = run_guard(chunks)
    types = [e["type"] for e in events]
    assert types == ["text", "tool_calls", "done"]
    assert events[1]["calls"][0]["function"]["name"] == "shell"
    assert "ls -la | grep py" in events[1]["calls"][0]["function"]["arguments"]
    # 正文里不能残留 DSML 源码
    assert all("DSML" not in e.get("delta", "") for e in events if e["type"] == "text")


def test_block_split_across_many_text_deltas():
    """DSML 块被切成逐字 delta 时仍能完整还原。"""
    chunks = [{"type": "text", "delta": ch} for ch in DSML_BLOCK]
    chunks.append({"type": "done", "finish_reason": "stop", "usage": {}})
    events = run_guard(chunks)
    calls = [e for e in events if e["type"] == "tool_calls"]
    assert len(calls) == 1
    assert calls[0]["calls"][0]["function"]["name"] == "shell"


def test_unclosed_block_flushed_before_done():
    """流结束仍未闭合：done 前冲刷，产出降级提示而非卡住。"""
    chunks = [
        {"type": "text", "delta": f'{START}\n| invoke name="shell">\n| parameter name="com'},
        {"type": "done", "finish_reason": "stop", "usage": {}},
    ]
    events = run_guard(chunks)
    types = [e["type"] for e in events]
    assert types.index("text") < types.index("done")
    assert "[系统提示]" in "".join(e.get("delta", "") for e in events if e["type"] == "text")


def test_dsml_error_maps_to_text_not_error():
    """降级提示必须走 text 通道：error 会中断整个 Agent 循环。"""
    ev = _dsml_event({"type": "dsml_error", "message": "解析失败"})
    assert ev["type"] == "text"
    assert "[系统提示]" in ev["delta"]


def test_thinking_and_native_tool_calls_passthrough():
    """thinking / 原生 tool_calls / error 事件原样透传，不受影响。"""
    chunks = [
        {"type": "thinking", "delta": "思考中"},
        {"type": "tool_calls", "calls": [{"id": "c1", "function": {"name": "code", "arguments": "{}"}}]},
        {"type": "error", "message": "boom"},
    ]
    events = run_guard(chunks)
    assert [e["type"] for e in events] == ["thinking", "tool_calls", "error"]


def test_no_dsml_plain_stream_untouched():
    chunks = [
        {"type": "text", "delta": "你好"},
        {"type": "text", "delta": "，这是一段普通回答。"},
        {"type": "done", "finish_reason": "stop", "usage": {}},
    ]
    events = run_guard(chunks)
    assert "".join(e["delta"] for e in events if e["type"] == "text") == "你好，这是一段普通回答。"

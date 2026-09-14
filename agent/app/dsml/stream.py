"""DSML 逃逸拦截的流式钩子（挂在 provider 事件流之上）。

放在 Agent 编排层而不是 Provider 层，原因：
- 编排层是**所有** Provider 事件汇入的唯一咽喉，新增非 OpenAI 兼容 Provider
  时自动生效，不会出现「换了个后端 DSML 又漏出来」；
- ``tool_calls → ToolNode → SSE tool 事件`` 的接线也在这里，拦截与消费同处一层，
  时序（工具调用必须早于 done）可控。
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

from .parser import DSMLStreamParser, parse_flat_block

logger = logging.getLogger(__name__)


def normalize_dsml_event(ev: dict) -> dict:
    """把 DSML 解析事件归一化为 Agent 事件。

    ``dsml_error`` 走 text 通道而非 error 通道：error 会让编排层直接 return、
    终止整个 Agent 循环；而需求要求「解析失败只推降级提示，不阻塞会话」。
    """
    if ev.get("type") == "dsml_error":
        return {
            "type": "text",
            "delta": f"\n[系统提示] {ev.get('message', '工具调用解析失败')}\n",
        }
    return ev


async def dsml_guard(events: AsyncIterator[dict]) -> AsyncIterator[dict]:
    """在 provider 事件流上挂载 DSML 逃逸拦截。

    - 正文 delta 先过 DSMLStreamParser：DSML 块被剥离，翻译成标准
      ``tool_calls`` 事件，交给既有 ToolNode 执行并推 SSE tool 事件；
    - 扁平伪 XML 块（``<tool_call><function=...>...</tool_call>``，agnes / MiniMax
      等模型的 ReAct 文本协议）只从 text 通道剥离；仅当本轮**未**收到原生
      ``tool_calls`` 事件时，才把缓存的 flat 块解析成 tool_calls 发出 ——
      避免与原生通道双重执行；
    - 收到 done / error 前先冲刷解析器 —— 编排层遇 ``done`` 即 break，
      晚于 done 到达的工具调用会被整体丢弃，必须抢在前面下发；
    - 流结束时再冲刷一次，处理未闭合块的补救解析。
    """
    parser = DSMLStreamParser()
    native_tool_calls_seen = False

    async def _drain_flat_as_tool_calls() -> AsyncIterator[dict]:
        if native_tool_calls_seen:
            return
        for block in parser.drain_flat_blocks():
            calls = parse_flat_block(block, start_index=parser._seq)
            if calls:
                parser._seq += len(calls)
                parser.calls_emitted += len(calls)
                yield {
                    "type": "tool_calls",
                    "calls": [c.to_openai_tool_call() for c in calls],
                    "source": "flat",
                }

    async for ev in events:
        etype = ev.get("type")
        if etype == "text":
            for out in parser.feed(ev.get("delta", "")):
                yield normalize_dsml_event(out)
        elif etype == "tool_calls":
            native_tool_calls_seen = True
            yield ev
        elif etype in ("done", "error"):
            for out in parser.close():
                yield normalize_dsml_event(out)
            async for ev2 in _drain_flat_as_tool_calls():
                yield ev2
            yield ev
        else:
            yield ev
    for out in parser.close():
        yield normalize_dsml_event(out)
    async for ev2 in _drain_flat_as_tool_calls():
        yield ev2

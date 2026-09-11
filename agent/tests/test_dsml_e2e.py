"""DSML 逃逸端到端测试：content 里的 DSML → 真实触发工具执行。

用桩 Provider 复现「DeepSeek 把工具调用写进 content」的场景，跑完整
AgentOrchestrator 循环，验证：
  1. DSML 源码不会出现在下发给前端的正文里；
  2. 解析出的工具调用真的进入 ToolNode 并执行；
  3. 前端能收到 tool_call / tool_result 事件（原有 SSE 协议不变）；
  4. 会话不阻塞 —— 工具执行后模型能继续给出最终回答。
"""
from __future__ import annotations

import asyncio

import pytest

import app.agents.orchestrator as orch
from app.agents.orchestrator import AgentOrchestrator
from app.context.context_manager import ContextManager
from app.dsml import FW_PIPE

START = f"<{FW_PIPE}{FW_PIPE}DSML{FW_PIPE}{FW_PIPE}>"
END = f"</{FW_PIPE}{FW_PIPE}DSML{FW_PIPE}{FW_PIPE}>"

DSML_BLOCK = (
    f"{START}\n"
    "| tool_calls>\n"
    '| invoke name="shell">\n'
    '| parameter name="command" string="true">echo hello-dsml | tr a-z A-Z\n'
    "| parameter>\n"
    "| invoke>\n"
    "| tool_calls>\n"
    f"{END}"
)

EXECUTED: list[dict] = []


class StubTool:
    name = "shell"
    requires_approval = False

    async def execute(self, arguments: dict) -> dict:
        EXECUTED.append(arguments)
        return {"success": True, "output": f"ran: {arguments.get('command')}"}

    def to_openai_schema(self) -> dict:
        return {"type": "function", "function": {"name": "shell", "parameters": {}}}


class StubProvider:
    """第一轮吐 DSML 逃逸内容，第二轮吐最终回答。"""

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, model, **kwargs):
        self.calls += 1
        if self.calls == 1:
            for ch in DSML_BLOCK:  # 逐字切分，模拟真实流式
                yield {"type": "text", "delta": ch}
            yield {"type": "done", "finish_reason": "stop", "usage": {"promptTokens": 10, "completionTokens": 5}}
        else:
            for ch in "已执行完成。":
                yield {"type": "text", "delta": ch}
            yield {"type": "done", "finish_reason": "stop", "usage": {"promptTokens": 10, "completionTokens": 5}}


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    EXECUTED.clear()
    monkeypatch.setattr(orch, "classify_task", lambda *_a, **_k: "chat")
    monkeypatch.setattr(orch, "model_supports_tools", lambda *_a, **_k: True)
    monkeypatch.setattr(orch, "resolve_model", lambda *_a, **_k: ("stub-model", "stub"))
    monkeypatch.setattr(orch, "get_registry", lambda: {"stub": StubProvider()})
    monkeypatch.setattr(orch, "init_tools", lambda **_k: {"shell": StubTool()})


def _run(user_message: str):
    agent = AgentOrchestrator(ContextManager())

    async def _collect():
        return [e async for e in agent.run_stream(user_message, "conv-dsml-test")]

    return asyncio.run(_collect())


def test_dsml_triggers_tool_execution():
    events = _run("帮我执行一条命令")
    types = [e["type"] for e in events]

    # 1) 工具真的被触发了两次机会中的一次，且参数就是 DSML 里的 command
    assert EXECUTED == [{"command": "echo hello-dsml | tr a-z A-Z"}]

    # 2) 前端能收到标准 tool_call / tool_result
    assert "tool_call" in types
    assert "tool_result" in types
    tc = [e for e in events if e["type"] == "tool_call"][0]
    assert tc["name"] == "shell"
    tr = [e for e in events if e["type"] == "tool_result"][0]
    assert "echo hello-dsml | tr a-z A-Z" in tr["result"]

    # 3) 正文里绝不能残留 DSML 源码
    body = "".join(e.get("delta", "") for e in events if e["type"] == "text")
    assert "DSML" not in body
    assert "invoke" not in body

    # 4) 会话未阻塞：工具执行后模型继续给出了最终回答
    assert "已执行完成。" in body

    # 5) tool_call / tool_result 必须排在 done 之前
    assert types.index("tool_call") < types.index("done")
    assert types.index("tool_result") < types.index("done")


def test_plain_text_no_tool_execution():
    class PlainProvider(StubProvider):
        async def chat(self, messages, model, **kwargs):
            yield {"type": "text", "delta": "这是一段普通回答。"}
            yield {"type": "done", "finish_reason": "stop", "usage": {}}

    import app.agents.orchestrator as o
    o.get_registry = lambda: {"stub": PlainProvider()}  # noqa: F811

    events = _run("你好")
    assert EXECUTED == []
    assert "这是一段普通回答。" in "".join(
        e.get("delta", "") for e in events if e["type"] == "text"
    )

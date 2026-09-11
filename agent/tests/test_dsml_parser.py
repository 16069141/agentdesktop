"""DSML 逃逸解析回归测试。

用例来源：DeepSeek-V4-Pro 深度思考模式下真实逃逸样本（用户提供）+ 流式切分/边界场景。
"""
from __future__ import annotations

import pytest

from app.dsml import (
    DSMLStreamParser,
    parse_dsml_block,
    resolve_tool_name,
    strip_dsml_text,
)

# ── 真实逃逸样本（用户提供）────────────────────────────────────────
REAL_SAMPLE = (
    '<｜｜DSML｜｜>\n'
    '| tool_calls>\n'
    '| invoke name="shell">\n'
    '| parameter name="command" string="true">'
    'find app -type f -name "*.py" -print0 | xargs -0 wc -l | sort -n\n'
    '| parameter>\n'
    '| invoke>\n'
    '| tool_calls>\n'
    '</｜｜DSML｜｜>'
)

EXPECTED_COMMAND = 'find app -type f -name "*.py" -print0 | xargs -0 wc -l | sort -n'


# ── 单次块解析 ────────────────────────────────────────────────────────

def test_parse_real_sample():
    calls = parse_dsml_block(REAL_SAMPLE)
    assert len(calls) == 1
    assert calls[0].tool_name == "shell"
    assert calls[0].command == EXPECTED_COMMAND
    assert calls[0].raw_name == "shell"
    assert calls[0].id == "dsml_0"


def test_to_openai_tool_call_shape():
    calls = parse_dsml_block(REAL_SAMPLE)
    tc = calls[0].to_openai_tool_call()
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "shell"
    assert '"command": "find app' in tc["function"]["arguments"]
    assert tc["id"] == "dsml_0"


def test_command_with_pipe_not_truncated():
    """命令里的 `|` 不能把内容截断 —— 这是本次修复的核心难点。"""
    calls = parse_dsml_block(REAL_SAMPLE)
    cmd = calls[0].command
    assert "xargs -0" in cmd
    assert "sort -n" in cmd
    assert cmd.count("|") == 2


def test_multiple_invokes_in_one_block():
    block = (
        "| tool_calls>\n"
        '| invoke name="shell">\n'
        '| parameter name="command" string="true">ls -la\n'
        "| parameter>\n"
        "| invoke>\n"
        '| invoke name="filesystem">\n'
        '| parameter name="action" string="true">list\n'
        '| parameter name="path" string="true">/tmp\n'
        "| parameter>\n"
        "| invoke>\n"
        "| tool_calls>\n"
    )
    calls = parse_dsml_block(block)
    assert [c.tool_name for c in calls] == ["shell", "filesystem"]
    assert calls[1].arguments == {"action": "list", "path": "/tmp"}
    assert [c.id for c in calls] == ["dsml_0", "dsml_1"]


def test_multiline_command_continuation():
    block = (
        '| invoke name="shell">\n'
        '| parameter name="command" string="true">find . -name "*.py" \\\n'
        "|   -print0 | xargs -0 wc -l\n"
        "| parameter>\n"
        "| invoke>\n"
    )
    calls = parse_dsml_block(block)
    assert len(calls) == 1
    assert calls[0].command == 'find . -name "*.py" \\\n  -print0 | xargs -0 wc -l'


def test_html_entity_unescape():
    block = (
        '| invoke name="shell">\n'
        '| parameter name="command" string="true">echo hi &gt; /tmp/a.txt &amp;&amp; ls\n'
        "| parameter>\n"
        "| invoke>\n"
    )
    calls = parse_dsml_block(block)
    assert calls[0].command == "echo hi > /tmp/a.txt && ls"


def test_string_false_parsed_as_json():
    block = (
        '| invoke name="filesystem">\n'
        '| parameter name="max_items" string="false">50\n'
        '| parameter name="action" string="true">list\n'
        "| parameter>\n"
        "| invoke>\n"
    )
    calls = parse_dsml_block(block)
    assert calls[0].arguments["max_items"] == 50
    assert calls[0].arguments["action"] == "list"


def test_tool_alias_mapping():
    assert resolve_tool_name("Bash") == "shell"
    assert resolve_tool_name("execute_command") == "shell"
    assert resolve_tool_name("read_file") == "filesystem"
    assert resolve_tool_name("unknown_thing") == "unknown_thing"


def test_shell_without_command_yields_nothing():
    block = (
        '| invoke name="shell">\n'
        '| parameter name="cwd" string="true">/tmp\n'
        "| parameter>\n"
        "| invoke>\n"
    )
    assert parse_dsml_block(block) == []


def test_empty_block():
    assert parse_dsml_block("") == []
    assert parse_dsml_block("   \n\n ") == []


def test_b_form_line_prefix():
    """B 式：每行以 <｜DSML｜ 开头（DeepSeek 行前缀形态）。"""
    block = (
        '<｜DSML｜invoke name="shell">\n'
        '<｜DSML｜parameter name="command" string="true">pwd\n'
        "<｜DSML｜parameter>\n"
        "<｜DSML｜invoke>\n"
    )
    calls = parse_dsml_block(block)
    assert len(calls) == 1
    assert calls[0].tool_name == "shell"
    assert calls[0].command == "pwd"


# ── 流式解析 ──────────────────────────────────────────────────────────

def _split_every(text: str, n: int):
    return [text[i:i + n] for i in range(0, len(text), n)]


@pytest.mark.parametrize("chunk_size", [1, 3, 7, 17, 64, 4096])
def test_stream_real_sample_any_chunking(chunk_size: int):
    p = DSMLStreamParser()
    texts, calls, errs = [], [], []
    for piece in _split_every(REAL_SAMPLE, chunk_size):
        for ev in p.feed(piece):
            {"text": texts, "tool_calls": calls, "dsml_error": errs}[ev["type"]].append(ev)
    for ev in p.close():
        {"text": texts, "tool_calls": calls, "dsml_error": errs}[ev["type"]].append(ev)

    assert len(calls) == 1
    assert calls[0]["calls"][0]["function"]["name"] == "shell"
    assert '"command": "find app' in calls[0]["calls"][0]["function"]["arguments"]
    assert errs == []
    # DSML 源码绝不能泄漏到正文
    leaked = "".join(t["delta"] for t in texts)
    assert "DSML" not in leaked
    assert "invoke" not in leaked


def test_stream_text_around_block():
    p = DSMLStreamParser()
    stream = "我先看一下目录结构。\n" + REAL_SAMPLE + "\n执行完毕。"
    texts, calls = [], []
    for ev in p.feed(stream):
        (texts if ev["type"] == "text" else calls).append(ev)
    for ev in p.close():
        (texts if ev["type"] == "text" else calls).append(ev)

    assert len(calls) == 1
    joined = "".join(t["delta"] for t in texts)
    assert joined == "我先看一下目录结构。\n\n执行完毕。" or joined == "我先看一下目录结构。\n\n执行完毕。"
    assert "DSML" not in joined


def test_stream_unclosed_block_waits_for_more():
    """未闭合标签必须留在 buffer，等后续 chunk 补齐，不能提前吐出源码。"""
    p = DSMLStreamParser()
    head = REAL_SAMPLE[: len(REAL_SAMPLE) // 2]
    evs = p.feed(head)
    assert all(e["type"] == "text" for e in evs)
    assert "DSML" not in "".join(e.get("delta", "") for e in evs)
    assert p.blocks_seen == 0

    tail = REAL_SAMPLE[len(REAL_SAMPLE) // 2:]
    evs2 = p.feed(tail)
    assert any(e["type"] == "tool_calls" for e in evs2)
    assert p.blocks_seen == 1


def test_stream_close_with_partial_start_tag():
    """流结束时 buffer 里只剩半个起始标记 → 必须作为普通文本吐出，不能吞掉。"""
    p = DSMLStreamParser()
    evs = p.feed("hello <｜｜DS")
    # 部分起始标记之前的正文可以安全吐出；半截标记必须留在 buffer
    assert [e["delta"] for e in evs] == ["hello "]
    rest = p.close()
    assert [e["delta"] for e in rest] == ["<｜｜DS"]


def test_stream_close_salvage_with_closer():
    """块未闭合但已有参数闭合行 → 补救解析，尽量救回调用。"""
    p = DSMLStreamParser()
    partial = (
        '<｜｜DSML｜｜>\n'
        '| invoke name="shell">\n'
        '| parameter name="command" string="true">ls -la\n'
        "| parameter>\n"
        "| invoke>\n"
    )
    p.feed(partial)
    evs = p.close()
    assert any(e["type"] == "tool_calls" for e in evs)
    tc = [e for e in evs if e["type"] == "tool_calls"][0]
    assert tc["calls"][0]["function"]["name"] == "shell"


def test_stream_close_unclosed_without_closer_degrades():
    """块未闭合且无闭合行 → 降级提示，绝不阻塞会话、绝不吐源码。"""
    p = DSMLStreamParser()
    p.feed('<｜｜DSML｜｜>\n| invoke name="shell">\n| parameter name="command"')
    evs = p.close()
    assert [e["type"] for e in evs] == ["dsml_error"]
    assert "DSML" in evs[0]["message"] or "工具调用" in evs[0]["message"]
    assert "invoke" not in evs[0]["message"]  # 提示里不能带源码


def test_stream_two_sequential_blocks():
    p = DSMLStreamParser()
    b1 = '<｜｜DSML｜｜>\n| invoke name="shell">\n| parameter name="command" string="true">echo 1\n| parameter>\n| invoke>\n</｜｜DSML｜｜>'
    b2 = '<｜｜DSML｜｜>\n| invoke name="shell">\n| parameter name="command" string="true">echo 2\n| parameter>\n| invoke>\n</｜｜DSML｜｜>'
    calls = []
    for ev in p.feed(b1 + "\n中间文本\n" + b2):
        if ev["type"] == "tool_calls":
            calls.append(ev)
    calls += [e for e in p.close() if e["type"] == "tool_calls"]

    assert len(calls) == 2
    assert '"echo 1"' in calls[0]["calls"][0]["function"]["arguments"]
    assert '"echo 2"' in calls[1]["calls"][0]["function"]["arguments"]


def test_stream_plain_text_passthrough():
    p = DSMLStreamParser()
    evs = p.feed("这是一段完全普通的回答，没有任何工具调用。")
    assert [e["type"] for e in evs] == ["text"]
    assert evs[0]["delta"].startswith("这是一段")


def test_stream_empty_chunk():
    p = DSMLStreamParser()
    assert p.feed("") == []
    assert p.feed(None) == []


def test_stream_block_overflow_protection():
    """超大 DSML 块必须被截断丢弃，不能无限增长。"""
    p = DSMLStreamParser(max_block_chars=1024)
    huge_cmd = "A" * 5000
    block = (
        '<｜｜DSML｜｜>\n'
        '| invoke name="shell">\n'
        f'| parameter name="command" string="true">{huge_cmd}\n'
        "| parameter>\n"
        "| invoke>\n"
        "</｜｜DSML｜｜>"
    )
    errs, calls = [], []
    for ev in p.feed(block):
        if ev["type"] == "dsml_error":
            errs.append(ev)
        elif ev["type"] == "tool_calls":
            calls.append(ev)
    assert calls == []
    assert len(errs) == 1
    assert p.overflowed is True


def test_stream_text_buffer_overflow_protection():
    p = DSMLStreamParser(max_text_chars=256)
    out = "".join(e["delta"] for e in p.feed("x" * 1000) if e["type"] == "text")
    assert len(out) >= 1000 - 10  # 允许极小尾部保留
    assert p.overflowed is True


def test_disabled_parser_passthrough():
    p = DSMLStreamParser(enabled=False)
    evs = p.feed(REAL_SAMPLE)
    assert "".join(e["delta"] for e in evs) == REAL_SAMPLE


def test_close_is_idempotent():
    p = DSMLStreamParser()
    p.feed("abc")
    assert p.close() == []  # 无起始标记时 feed 已吐完，close 不重复产出
    assert p.close() == []


# ── 文本剥离（落库 / 渲染兜底）───────────────────────────────────────

def test_strip_dsml_text_complete_block():
    s = "前面" + REAL_SAMPLE + "后面"
    assert strip_dsml_text(s) == "前面后面"


def test_strip_dsml_text_unclosed_trailing():
    s = "前面" + '<｜｜DSML｜｜>\n| invoke name="shell">\n| parameter name="c"'
    assert strip_dsml_text(s) == "前面"


def test_strip_dsml_text_noop():
    s = "普通文本 | 带竖线 | 也没事"
    assert strip_dsml_text(s) == s
    assert strip_dsml_text("") == ""


def test_strip_dsml_text_empty_and_none():
    assert strip_dsml_text("") == ""
    assert strip_dsml_text(None) is None

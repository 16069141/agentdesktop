"""DSML 逃逸拦截模块。

对外只暴露三件事：
- ``DSMLStreamParser``：挂到 provider 流式链路上，把 content 里的 DSML 块
  翻译成标准 ``tool_calls`` 事件；
- ``DSMLToolCall`` / ``parse_dsml_block``：单次块解析（可单测 / 可离线回放）；
- ``strip_dsml_text``：落库 / 渲染前的二次防御，剥离残留 DSML 源码。
"""
from .parser import (
    DSMLParseError,
    DSMLStreamParser,
    DSMLToolCall,
    END_TAG_A,
    FW_PIPE,
    START_TAG_A,
    parse_dsml_block,
    parse_flat_block,
    resolve_tool_name,
    strip_dsml_text,
)
from .stream import dsml_guard, normalize_dsml_event

__all__ = [
    "DSMLParseError",
    "DSMLStreamParser",
    "DSMLToolCall",
    "FW_PIPE",
    "START_TAG_A",
    "END_TAG_A",
    "parse_dsml_block",
    "parse_flat_block",
    "resolve_tool_name",
    "strip_dsml_text",
    "dsml_guard",
    "normalize_dsml_event",
]

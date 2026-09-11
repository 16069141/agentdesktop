"""DSML 逃逸流式解析器。

背景
----
DeepSeek 系列模型开启深度思考（reasoning）模式时，偶发把结构化工具调用以
DSML（DeepSeek Markup Language）标记输出到 ``content`` 文本字段，而不是标准的
``tool_calls`` 数组。典型形态::

    <｜｜DSML｜｜>
    | tool_calls>
    | invoke name="shell">
    | parameter name="command" string="true">find app -type f -name "*.py" -print0 | xargs -0 wc -l | sort -n
    | parameter>
    | invoke>
    | tool_calls>
    </｜｜｜DSML｜｜>

若不拦截，这段源码会被前端原样渲染给用户，工具不触发、会话停滞。

设计要点
--------
1. **流式安全**：``feed()`` 逐 chunk 追加；未闭合的块留在 buffer 中等后续 chunk
   补齐；文本态只吐出「不可能是起始标记前缀」的部分，避免半截标记泄漏到前端。
2. **内存有界**：文本态 buffer 与 DSML 块各有硬上限，超限即降级，绝不 OOM。
3. **绝不阻塞**：解析失败只推送 ``dsml_error`` 降级事件（由上层转成文本提示），
   不影响后续文本与会话流程。
4. **索引对齐**：``｜``(U+FF5C) → ``|`` 是一字符对一字符的替换，因此可在
   「归一化副本」上做正则匹配，再回原串按相同下标切片 —— 结构与内容两不误，
   ``find ... | xargs -0`` 这类含竖线的命令内容不会被误伤。
5. **两种形态**：
   - A 式（本需求指定）：``<｜｜DSML｜｜>`` ... ``</｜｜DSML｜｜>`` 整块包裹；
   - B 式（DeepSeek 行前缀式）：每行以 ``<｜DSML｜`` 开头，遇非 DSML 行或
     ``</｜DSML｜`` 结束。
"""
from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# 标记常量
# ─────────────────────────────────────────────────────────────────────

FW_PIPE = "\uff5c"  # 全角竖线 ｜（U+FF5C）

# 原始形态（含全角竖线）
START_TAG_A = f"<{FW_PIPE}{FW_PIPE}DSML{FW_PIPE}{FW_PIPE}>"
END_TAG_A = f"</{FW_PIPE}{FW_PIPE}DSML{FW_PIPE}{FW_PIPE}>"

# 归一化形态（全角竖线 → 半角），长度与原始形态一一对应
N_START_A = "<||DSML||>"
N_END_A = "</||DSML||>"
N_START_B = "<|DSML|"  # 行前缀式起始（B 式），无独立结束标记
N_END_B = "</|DSML|"  # 行前缀式可选显式闭合

_DEFAULT_MAX_TEXT_CHARS = 256 * 1024  # 文本态 buffer 上限
_DEFAULT_MAX_BLOCK_CHARS = 64 * 1024  # 单个 DSML 块上限


class DSMLParseError(Exception):
    """DSML 块解析失败（不影响会话，仅降级提示）。"""


# ─────────────────────────────────────────────────────────────────────
# 工具名映射
# ─────────────────────────────────────────────────────────────────────

# DSML 里模型可能用别名调工具，映射到本地注册表里的真实工具名。
TOOL_ALIASES: dict[str, str] = {
    "shell": "shell",
    "bash": "shell",
    "sh": "shell",
    "terminal": "shell",
    "execute_command": "shell",
    "run_command": "shell",
    "execute_shell_command": "shell",
    "read_file": "filesystem",
    "write_file": "filesystem",
    "list_files": "filesystem",
    "search_files": "filesystem",
    "filesystem": "filesystem",
    "file_system": "filesystem",
    "code": "code",
    "python": "code",
    "code_interpreter": "code",
    "web_search": "web_search",
    "search": "web_search",
    "browser": "browser",
    "fetch": "browser",
    "db_query": "db_query",
    "sql": "db_query",
}

# shell 工具的命令参数别名（模型可能写成 cmd / script / input）
_SHELL_COMMAND_KEYS = ("command", "cmd", "script", "command_line", "input", "shell_command")


def resolve_tool_name(raw_name: str) -> str:
    """把 DSML 里的 invoke name 映射为本地工具名（未命中则原样返回）。"""
    key = (raw_name or "").strip().strip("\"'").lower()
    return TOOL_ALIASES.get(key, key or raw_name or "")


# ─────────────────────────────────────────────────────────────────────
# 结构化工具调用对象
# ─────────────────────────────────────────────────────────────────────


@dataclass
class DSMLToolCall:
    """从 DSML 块中解析出的一个工具调用。"""

    id: str
    tool_name: str  # 已映射的本地工具名
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_name: str = ""  # DSML 中原始 invoke name

    @property
    def command(self) -> str:
        """便捷取 command 参数（shell 场景主用）。"""
        v = self.arguments.get("command")
        return v if isinstance(v, str) else ("" if v is None else str(v))

    def to_openai_tool_call(self) -> dict:
        """转成 OpenAI tool_calls 数组元素，供既有 ToolNode 直接消费。"""
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.tool_name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }


# ─────────────────────────────────────────────────────────────────────
# 行级词法
# ─────────────────────────────────────────────────────────────────────

# 行首分隔符：空白 + 竖线 + 至多一个分隔空格。
#
# 只吃「至多一个」空格是有意的：DSML 的行分隔符形态是 `| `（竖线+一个空格），
# 而续行内容自身可能带缩进（多行命令缩进、多行文件内容的层级缩进）。
# 若把全部前导空白吞掉，多行参数内容的缩进会被破坏（Python 代码等内容直接失真）。
# 两类竖线在同一位置等价，因此该 end() 下标对「原始行」与「归一化行」同时有效。
_LEAD_DELIM_RE = re.compile(r"^[ \t\u3000]*[\uff5c|]+[ \t]?")

# B 式行前缀：<｜DSML｜ / <|DSML|
_DSML_LINE_PREFIX_RE = re.compile(r"^\s*<\s*[\uff5c|]+\s*DSML\s*[\uff5c|]+\s*")

# 属性片段：只接受 attr="value" 形式，避免 command 里的 > 提前截断标签
_ATTRS = r'(?:\s+[\w.:-]+\s*=\s*"[^"]*")*'

_PARAM_OPEN_RE = re.compile(
    r'^parameter\s+name\s*=\s*"(?P<name>[^"]*)"(?P<attrs>' + _ATTRS + r')\s*>(?P<rest>.*)$',
    re.DOTALL,
)
_PARAM_CLOSE_RE = re.compile(r"^parameter\s*>(?P<rest>.*)$")
_INVOKE_OPEN_RE = re.compile(
    r'^invoke\s+name\s*=\s*"(?P<name>[^"]*)"(?P<attrs>' + _ATTRS + r')\s*>(?P<rest>.*)$',
    re.DOTALL,
)
_INVOKE_CLOSE_RE = re.compile(r"^invoke\s*>\s*$")
_TOOL_CALLS_RE = re.compile(r"^/?tool_calls\s*>\s*$")
_ATTR_RE = re.compile(r'([\w.:-]+)\s*=\s*"([^"]*)"')
# 显式闭合行（用于「未闭合块」的补救解析判定）
_CLOSER_LINE_RE = re.compile(r"^(?:invoke|parameter)\s*>\s*$")

_ENTITY_RE = re.compile(r"&(?:quot|amp|lt|gt|apos|#\d+|#x[0-9A-Fa-f]+);")


def _parse_attrs(attrs: str) -> dict[str, str]:
    return {k: v for k, v in _ATTR_RE.findall(attrs or "")}


def _decode_value(raw_value: str, is_string: bool | None) -> Any:
    """按 string 属性决定取值形态，并还原 HTML 实体转义。

    - ``string="true"`` → 原样字符串（模型已明确标注这是字符串）
    - ``string="false"``/未标注 → 先尝试 JSON 解析，失败回落字符串
    """
    v = raw_value
    if _ENTITY_RE.search(v):
        v = html.unescape(v)
    if is_string:
        return v
    s = v.strip()
    if not s:
        return v
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        return v


def _join_param_lines(lines: list[str]) -> str:
    """拼接参数内容（首段是标签同行余量，其后是整行内容）。"""
    buf = list(lines)
    while buf and not buf[0].strip():
        buf.pop(0)
    while buf and not buf[-1].strip():
        buf.pop()
    return "\n".join(buf)


# ─────────────────────────────────────────────────────────────────────
# 块解析（纯函数，便于单测）
# ─────────────────────────────────────────────────────────────────────


def parse_dsml_block(raw: str, *, start_index: int = 0) -> list[DSMLToolCall]:
    """解析一个完整 DSML 块的**内部内容**（不含起止包裹标记）。

    Args:
        raw: DSML 块内部原文（可含全角竖线）。
        start_index: 生成 tool_call id 的起始序号。

    Returns:
        解析出的工具调用列表；无有效调用时返回空列表（由调用方决定是否降级）。

    Raises:
        DSMLParseError: 结构严重畸形（如 invoke 未闭合且无法补救）时抛出。
    """
    if not raw or not raw.strip():
        return []

    norm = raw.replace(FW_PIPE, "|")
    raw_lines = raw.split("\n")
    norm_lines = norm.split("\n")
    if len(raw_lines) != len(norm_lines):  # 理论上不会发生，防御
        norm_lines = [ln.replace(FW_PIPE, "|") for ln in raw_lines]

    calls: list[DSMLToolCall] = []
    seq = start_index

    cur_name: str | None = None
    cur_params: dict[str, Any] = {}
    cur_pname: str | None = None
    cur_plines: list[str] = []
    cur_pstring: bool | None = None

    def _flush_param() -> None:
        nonlocal cur_pname, cur_plines, cur_pstring
        if cur_pname is None:
            cur_plines = []
            cur_pstring = None
            return
        cur_params[cur_pname] = _decode_value(_join_param_lines(cur_plines), cur_pstring)
        cur_pname = None
        cur_plines = []
        cur_pstring = None

    def _flush_invoke() -> None:
        nonlocal cur_name, cur_params, seq
        _flush_param()
        if not cur_name:
            cur_params = {}
            return
        tool_name = resolve_tool_name(cur_name)
        args = dict(cur_params)
        if tool_name == "shell" and not any(args.get(k) for k in _SHELL_COMMAND_KEYS):
            # shell 却没拿到命令：不盲发空调用，交由上层降级提示
            logger.warning("[dsml] invoke '%s' 未提取到 command 参数，跳过", cur_name)
            cur_name = None
            cur_params = {}
            return
        calls.append(DSMLToolCall(
            id=f"dsml_{seq}",
            tool_name=tool_name,
            arguments=args,
            raw_name=cur_name,
        ))
        seq += 1
        cur_name = None
        cur_params = {}

    for raw_line, norm_line in zip(raw_lines, norm_lines):
        rl = raw_line.rstrip("\r")
        nl = norm_line.rstrip("\r")

        # 统一去行首分隔符 / B 式前缀（在「原始行」上匹配，下标对两者同时有效）
        m = _LEAD_DELIM_RE.match(rl)
        if m:
            rl, nl = rl[m.end():], nl[m.end():]
        m = _DSML_LINE_PREFIX_RE.match(rl)
        if m:
            rl, nl = rl[m.end():], nl[m.end():]

        if not nl.strip():
            # 空行：若正在收集参数内容则保留（多行命令可能含空行），否则忽略
            if cur_pname is not None:
                cur_plines.append("")
            continue

        if _TOOL_CALLS_RE.match(nl):
            continue

        m = _INVOKE_OPEN_RE.match(nl)
        if m:
            # 上一个 invoke 未显式闭合 → 先收尾
            if cur_name:
                _flush_invoke()
            cur_name = m.group("name").strip()
            # 极少见：invoke 与首个 parameter 写在同一行
            rest = m.group("rest").strip()
            if rest:
                cur_plines = []
            continue

        if _INVOKE_CLOSE_RE.match(nl):
            if cur_name:
                _flush_invoke()
            continue

        m = _PARAM_OPEN_RE.match(nl)
        if m:
            _flush_param()  # 上一个 parameter 未闭合 → 先收尾
            cur_pname = m.group("name").strip()
            cur_pstring = _parse_attrs(m.group("attrs")).get("string", "").strip().lower() in (
                "true", "1", "yes",
            ) if m.group("attrs") else None
            # 内容在标签同行（最常见）：用「原始行」按同一 offset 切片，保留全角字符
            rest_raw = rl[m.start("rest"):] if m.start("rest") <= len(rl) else ""
            cur_plines = [rest_raw]
            continue

        m = _PARAM_CLOSE_RE.match(nl)
        if m:
            _flush_param()
            continue

        # 其他行：参数内容续行（如多行 shell 命令）
        if cur_pname is not None:
            cur_plines.append(rl)
            continue
        # 参数外的内容行：忽略（可能是模型夹杂的说明文字）

    # 收尾：未显式闭合的 invoke / parameter 一并兜底
    if cur_name:
        _flush_invoke()

    return calls


# ─────────────────────────────────────────────────────────────────────
# 流式解析器
# ─────────────────────────────────────────────────────────────────────


def _find_start(norm: str) -> tuple[int, int, str]:
    """在归一化串中定位 DSML 起始标记。

    Returns:
        ``(start_idx, tag_len, end_mark)``；``end_mark`` 为空串表示 B 式（行前缀）。
        未找到返回 ``(-1, 0, "")``。
    """
    ia = norm.find(N_START_A)
    ib = norm.find(N_START_B)
    if ia >= 0 and (ib < 0 or ia <= ib):
        return ia, len(N_START_A), N_END_A
    if ib >= 0:
        return ib, len(N_START_B), ""
    return -1, 0, ""


def _line_mode_end(nblock: str) -> int:
    """B 式块的结束位置（不含），未结束返回 -1。"""
    k = nblock.find(N_END_B)
    if k >= 0:
        gt = nblock.find(">", k)
        return gt + 1 if gt >= 0 else -1

    pos = 0
    first = True
    for seg in nblock.split("\n"):
        if first:
            first = False
            pos += len(seg)
            continue
        s = seg.strip()
        if not s:
            pos += 1
            continue
        if not s.startswith(N_START_B):
            return pos  # 该行起点即块结束（不含前导换行）
        pos += len(seg) + 1
    return -1


_MAX_HOLD = max(len(N_START_A), len(N_START_B)) - 1


def _tail_hold(nbuf: str) -> int:
    """文本态需保留的尾部长：任何可能是起始标记前缀的后缀都不能吐出。"""
    for k in range(min(len(nbuf), _MAX_HOLD), 0, -1):
        suf = nbuf[-k:]
        if N_START_A.startswith(suf) or N_START_B.startswith(suf):
            return k
    return 0


class DSMLStreamParser:
    """DSML 逃逸流式解析器：把 content 流里的 DSML 块翻译成结构化工具调用。

    典型用法（在 provider 流式循环里）::

        parser = DSMLStreamParser()
        for chunk in stream:
            for ev in parser.feed(chunk.content):
                ...   # ev: {"type": "text"|"tool_calls"|"dsml_error"}
        for ev in parser.close():
            ...
    """

    def __init__(
        self,
        *,
        max_text_chars: int = _DEFAULT_MAX_TEXT_CHARS,
        max_block_chars: int = _DEFAULT_MAX_BLOCK_CHARS,
        enabled: bool = True,
    ):
        self.max_text_chars = max_text_chars
        self.max_block_chars = max_block_chars
        self.enabled = enabled

        self._buf = ""  # 文本态：待输出原文
        self._nbuf = ""  # 文本态：归一化副本（与 _buf 下标一一对应）
        self._mode: str | None = None  # None | "block"(A式) | "lines"(B式)
        self._end_mark = ""
        self._block = ""
        self._nblock = ""
        self._seq = 0
        self.blocks_seen = 0
        self.calls_emitted = 0
        self.overflowed = False

    # ── 对外 API ─────────────────────────────────────────────────

    def feed(self, chunk: str) -> list[dict]:
        """追加一段流式文本，返回待下发的事件列表。

        事件类型：
        - ``{"type": "text", "delta": str}``  —— 已确认不含 DSML 的正文
        - ``{"type": "tool_calls", "calls": [...], "source": "dsml"}``
        - ``{"type": "dsml_error", "message": str}`` —— 降级提示，不阻塞会话
        """
        events: list[dict] = []
        if not chunk:
            return events
        if not self.enabled:
            return [{"type": "text", "delta": chunk}]

        if self._mode is None:
            self._buf += chunk
            self._nbuf += chunk.replace(FW_PIPE, "|")
        else:
            self._block += chunk
            self._nblock += chunk.replace(FW_PIPE, "|")

        self._pump(events)
        return events

    def close(self) -> list[dict]:
        """流结束：冲刷残留文本，并对未闭合块做一次补救解析。

        可重复调用（第二次起返回空列表）。
        """
        events: list[dict] = []
        if not self.enabled:
            if self._buf:
                events.append({"type": "text", "delta": self._buf})
                self._buf, self._nbuf = "", ""
            self._reset_block()
            return events

        # 未闭合块：仅在看得到显式闭合行时才补救，避免把半截命令当完整调用执行
        if self._mode is not None:
            raw = self._block
            nraw = self._nblock
            salvage = any(
                _CLOSER_LINE_RE.match(
                    _DSML_LINE_PREFIX_RE.sub("", _LEAD_DELIM_RE.sub("", ln)).strip()
                )
                for ln in nraw.replace(FW_PIPE, "|").split("\n")
            )
            self._reset_block()
            if salvage:
                try:
                    calls = parse_dsml_block(raw, start_index=self._seq)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[dsml] 补救解析异常: %s", exc)
                    calls = []
                if calls:
                    self._seq += len(calls)
                    self.calls_emitted += len(calls)
                    events.append({
                        "type": "tool_calls",
                        "calls": [c.to_openai_tool_call() for c in calls],
                        "source": "dsml",
                    })
                else:
                    events.append({
                        "type": "dsml_error",
                        "message": "模型输出了未闭合的工具调用标记（DSML），已忽略",
                    })
            else:
                events.append({
                    "type": "dsml_error",
                    "message": "模型输出了未闭合的工具调用标记（DSML），已忽略",
                })

        if self._buf:
            events.append({"type": "text", "delta": self._buf})
            self._buf, self._nbuf = "", ""
        return events

    # ── 内部状态机 ───────────────────────────────────────────────

    def _reset_block(self) -> None:
        self._mode = None
        self._end_mark = ""
        self._block = ""
        self._nblock = ""

    def _pump(self, events: list[dict]) -> None:
        guard = 0
        while True:
            guard += 1
            if guard > 1000:  # 防御：极端情况下的死循环熔断
                logger.error("[dsml] 状态机迭代超上限，强制冲刷")
                self._force_flush(events)
                return
            if self._mode is None:
                if not self._pump_text(events):
                    return
            else:
                if not self._pump_block(events):
                    return

    def _pump_text(self, events: list[dict]) -> bool:
        """文本态推进。返回 True 表示已切入块态，需要继续 pump。"""
        # 内存护栏：文本态 buffer 超限 → 强制吐出（放弃尾部保留，防 OOM）
        if len(self._buf) > self.max_text_chars:
            logger.warning("[dsml] 文本缓冲超限(%d)，强制冲刷", len(self._buf))
            self.overflowed = True
            if self._buf:
                events.append({"type": "text", "delta": self._buf})
                self._buf, self._nbuf = "", ""
            return False

        si, taglen, end_mark = _find_start(self._nbuf)
        if si < 0:
            hold = _tail_hold(self._nbuf)
            if hold:
                out = self._buf[:-hold]
                self._buf, self._nbuf = self._buf[-hold:], self._nbuf[-hold:]
            else:
                out, self._buf, self._nbuf = self._buf, "", ""
            if out:
                events.append({"type": "text", "delta": out})
            return False

        if si > 0:
            events.append({"type": "text", "delta": self._buf[:si]})

        # 起始标记之后的残余直接进入块态（先取余量，再清空文本缓冲）
        rest_raw = self._buf[si + taglen:]
        rest_norm = self._nbuf[si + taglen:]
        self._buf, self._nbuf = "", ""
        self._mode = "block" if end_mark else "lines"
        self._end_mark = end_mark
        self._block, self._nblock = rest_raw, rest_norm
        return True

    def _pump_block(self, events: list[dict]) -> bool:
        """块态推进。返回 True 表示块已结束（切回文本态），需要继续 pump。"""
        if len(self._block) > self.max_block_chars:
            logger.warning("[dsml] DSML 块超限(%d)，丢弃防溢出", len(self._block))
            self.overflowed = True
            self._reset_block()
            events.append({
                "type": "dsml_error",
                "message": "工具调用内容过长（超过 "
                           f"{self.max_block_chars // 1024}KB），已丢弃",
            })
            return False

        if self._mode == "block":
            ei = self._nblock.find(self._end_mark)
            if ei < 0:
                return False  # 未闭合：留在 buffer 等后续 chunk
            raw_block = self._block[:ei]
            rest_raw = self._block[ei + len(self._end_mark):]
            end = ei + len(self._end_mark)
        else:
            ei = _line_mode_end(self._nblock)
            if ei < 0:
                return False
            raw_block = self._block[:ei]
            rest_raw = self._block[ei:]
            end = ei

        rest_n = self._nblock[end:]
        self._reset_block()
        self._buf, self._nbuf = rest_raw, rest_n
        self._handle_block(raw_block, events)
        return True

    def _handle_block(self, raw_block: str, events: list[dict]) -> None:
        self.blocks_seen += 1
        try:
            calls = parse_dsml_block(raw_block, start_index=self._seq)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[dsml] 块解析失败: %s", exc)
            events.append({"type": "dsml_error", "message": f"工具调用解析失败：{exc}"})
            return
        if not calls:
            events.append({
                "type": "dsml_error",
                "message": "工具调用解析失败：DSML 块中未找到可执行的工具调用",
            })
            return
        self._seq += len(calls)
        self.calls_emitted += len(calls)
        events.append({
            "type": "tool_calls",
            "calls": [c.to_openai_tool_call() for c in calls],
            "source": "dsml",
        })

    def _force_flush(self, events: list[dict]) -> None:
        if self._buf:
            events.append({"type": "text", "delta": self._buf})
            self._buf, self._nbuf = "", ""
        self._reset_block()


# ─────────────────────────────────────────────────────────────────────
# 防御性工具：剥离文本中残留的 DSML（落库 / 兜底渲染前）
# ─────────────────────────────────────────────────────────────────────


def strip_dsml_text(text: str) -> str:
    """移除文本中残留的 DSML 片段（含未闭合的尾部）。

    用于把 assistant 文本落库前的二次防护：即便流式拦截漏过，也不会把
    DSML 源码写进会话历史、再渲染到前端。
    """
    if not text:
        return text
    if FW_PIPE not in text and "DSML" not in text:
        return text

    norm = text.replace(FW_PIPE, "|")
    out: list[str] = []
    i = 0
    while i < len(text):
        si, taglen, end_mark = _find_start(norm[i:])
        if si < 0:
            out.append(text[i:])
            break
        si += i
        out.append(text[i:si])
        j = si + taglen
        if end_mark:
            ei = norm.find(end_mark, j)
            if ei < 0:
                break  # 未闭合：丢弃尾部
            j = ei + len(end_mark)
        else:
            ei = _line_mode_end(norm[j:])
            if ei < 0:
                break
            j += ei
        i = j
    return "".join(out)

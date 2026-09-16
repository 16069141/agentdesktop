"""LangGraph Agent 编排（多轮工具调用闭环）。

规格书 §9.2：
    chat_node      → 组装预算上下文 → 路由模型 → LLM 生成（结构化 tool_calls）
    route_after_chat → 依据 last message 的 tool_calls 决定 tool / final
    tool_node      → 逐个执行 tool_calls，以 role="tool"+tool_call_id 回灌
    summary_node   → 长会话触发摘要压缩（可选）

状态流：
    messages (OpenAI 风格，含 role="tool")
    conversation_id
    tool_results (中间累积)
    token_usage (累计)
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import time
from typing import Any, AsyncIterator, Optional

from ..context.context_manager import ContextManager
from ..dsml import strip_dsml_text
from ..dsml.stream import dsml_guard
from ..tools.planner_tools import PlanBus, get_plan_bus, set_plan_bus
from ..providers import (
    _mark_model_tool_support,
    classify_task,
    get_registry,
    model_supports_tools,
    resolve_model,
)
from ..tools import init_tools

logger = logging.getLogger(__name__)

# P3 多智能体：当前请求的模型与会话上下文（subagent 工具据此委派子智能体，
# 不用新增参数穿透整个调用链；请求任务结束后上下文自然消亡）
_current_model: contextvars.ContextVar[str] = contextvars.ContextVar("current_model", default="")
_current_conversation: contextvars.ContextVar[str] = contextvars.ContextVar("current_conversation", default="")


def get_current_model() -> str:
    return _current_model.get()


def get_current_conversation() -> str:
    return _current_conversation.get()


# ── P0.5 自动续跑守卫 ─────────────────────────────────────────
# 小模型最常见的"假智能"：只输出「我来测试…开始全面自测。」这类意图声明，
# 一个工具都没调用就停住——把"计划"当成"答案"交付，用户问"怎么不继续"。
# 守卫逻辑：本轮没有任何工具调用，且回复文本像"意图声明"时，
# 注入系统提示并强制再跑一轮（上限 _AUTO_CONTINUE_MAX 次），让它真正动手。
_PROMISE_TAIL_MARKERS = (
    "开始", "开始吧", "开始执行", "现在开始", "开始全面", "着手", "开工",
    "接下来", "下一步", "下面", "先来", "我来", "让我", "我打算", "我将", "我要",
)
_PROMISE_MID_MARKERS = (
    "我来测试", "让我来", "我打算", "我将", "我要开始", "开始全面", "先执行",
    "计划如下", "步骤如下", "我们开始", "准备开始", "先做", "首先", "先来",
)
_AUTO_CONTINUE_MAX = 2
_AUTO_CONTINUE_HINT = (
    "[系统提示] 你刚才只写了打算，没有实际执行任何工具调用。"
    "请立即真正开始执行：多步骤任务先调用 create_plan 建立计划，然后逐项调用工具"
    "（filesystem / shell / code / db_query / web_search 等）落实。"
    "禁止再说「开始 / 接下来 / 让我」这类空话而不行动。直接调用工具干活。"
)


def _is_promise_text(text: str) -> bool:
    """判断回复是否只是「意图声明」而没有实际行动。"""
    t = (text or "").strip()
    if not t:
        return False
    # 尾段（最后 24 字）出现"开始/接下来/让我"等 → 典型的"说完就停"
    if any(m in t[-24:] for m in _PROMISE_TAIL_MARKERS):
        return True
    # 全文出现强意图短语（"我来测试/我打算/计划如下"等）→ 也视为声明
    if any(m in t for m in _PROMISE_MID_MARKERS):
        return True
    return False


# ── P0.5 三种工作模式（对齐 WorkBuddy Craft/Plan/Ask）────────────────
WORK_MODE_CRAFT = "craft"   # 你说我做：直接执行
WORK_MODE_PLAN = "plan"     # 先想后做：先建计划，用户确认后再执行
WORK_MODE_ASK = "ask"       # 只问不做：纯问答，不调用工具、不改动任何东西

_MODE_HINTS = {
    WORK_MODE_PLAN: (
        "\n\n## 当前工作模式：Plan（先想后做）\n"
        "- 需要多步执行的任务，第一步必须调用 create_plan 建立分步计划，然后立即停止，"
        "等待用户确认后再执行。\n"
        "- 用户确认前，禁止调用任何执行类工具（filesystem/shell/code/db_query/web_search 等），"
        "只能使用 create_plan / update_plan。\n"
        "- 简单问题（无需工具即可回答）可直接回答，不需要建计划。"
    ),
    WORK_MODE_ASK: (
        "\n\n## 当前工作模式：Ask（只问不做）\n"
        "- 只回答问题与提供建议，禁止调用任何工具，禁止创建/修改/删除任何文件或数据。\n"
        "- 即使任务看起来需要执行（如「帮我整理文件」「写个脚本」），也只能说明你会怎么做，"
        "不能实际执行。"
    ),
}

# Plan 模式未确认前唯一允许的工具
_PLAN_GATE_TOOLS = ("create_plan", "update_plan")
_PLAN_GATE_BLOCKED_MSG = (
    "【Plan 模式】用户尚未确认计划，此工具调用未执行。"
    "请先用 create_plan 建立分步计划并等待用户确认，不要执行任何操作。"
)


class AgentState:
    """Agent 状态（用类代替 TypedDict，避免强依赖 langgraph）。"""

    def __init__(
        self,
        messages: list[dict],
        conversation_id: str,
        system_prompt: str = "",
        model_id: str = "qwen2.5:7b",
        provider_id: str = "ollama",
        tool_results: list[dict] | None = None,
        token_usage: dict[str, int] | None = None,
        should_summarize: bool = False,
    ):
        self.messages = messages
        self.conversation_id = conversation_id
        self.system_prompt = system_prompt
        self.model_id = model_id
        self.provider_id = provider_id
        self.tool_results = tool_results or []
        self.token_usage = token_usage or {"input": 0, "output": 0}
        self.should_summarize = should_summarize


def _format_tool_error(tool_name: str, exc: Exception) -> str:
    """把工具异常映射为结构化错误（L3）：error_type / retryable / 修复建议。

    结构化文本直接作为 role=tool 消息回填下一轮上下文，模型据此自我纠正，
    而非看到一行裸异常后手足无措或重复相同调用。
    """
    msg = str(exc) or exc.__class__.__name__
    low = msg.lower()
    etype, retryable, suggestion = "unknown", False, (
        "请检查工具参数与运行环境后重试；若无法修复，换一种实现方式完成目标。"
    )
    if isinstance(exc, (FileNotFoundError,)):
        etype, retryable = "not_found", True
        suggestion = (
            "目标路径或文件不存在：先用 filesystem 列出父目录确认真实路径，"
            "或改用 search 定位后再操作。"
        )
    elif any(k in low for k in ("permission", "denied", "not authorized", "forbidden")):
        etype, retryable = "permission", False
        suggestion = (
            "权限不足：换用允许范围内的操作，或向用户说明所需权限、请其处理。"
        )
    elif any(k in low for k in ("timeout", "timed out")):
        etype, retryable = "timeout", True
        suggestion = (
            "操作超时：可重试一次；若仍超时，请缩小任务范围（如分批、按行处理）。"
        )
    elif any(k in low for k in ("connection", "network", "resolve", "refused", "unreachable")):
        etype, retryable = "network", True
        suggestion = (
            "网络或服务连接失败：稍后重试；若是远程服务，检查 base_url / key 配置。"
        )
    elif any(k in low for k in ("invalid", "argument", "bad request", "400", "422")):
        etype, retryable = "invalid_args", True
        suggestion = (
            "参数不合法：检查参数格式、取值与必填项（enum / required / 路径写法）后重试。"
        )
    return (
        f"[工具执行失败]\n"
        f"工具: {tool_name}\n"
        f"错误类型: {etype}\n"
        f"错误详情: {msg[:300]}\n"
        f"可重试: {'是' if retryable else '否'}\n"
        f"修复建议: {suggestion}\n"
        f"请依据以上信息自行纠正后继续，不要重复完全相同的调用。"
    )


class ToolNode:
    """工具执行节点：执行一轮模型返回的全部 tool_calls。

    两阶段执行：
    1. 顺序预检（无外部等待）：熔断检查 → 操作级权限 → 同参去重，
       拒绝/命中缓存的调用即时返回，不进入执行队列；
    2. 并发执行：剩余调用相互独立（如并行读多个文件、多个 web_search），
       用 asyncio.gather 并发，总耗时 ≈ 最慢的一个，而非各工具耗时之和。
       结果按下标写回，顺序与 tool_calls 严格一致。

    P0 集成：熔断检查（高频调用保护）+ 审计落库（Who/When/What/Params/Result）
    由本节点统一负责；shell 等高危工具的二次确认走工具自身审批链路。
    """

    def __init__(self, tool_registry: dict[str, Any]):
        self.registry = tool_registry

    async def execute(
        self,
        tool_calls: list[dict],
        cache: dict[str, str] | None = None,
        conversation_id: str = "",
        session_id: str = "",
        identity: Any = None,
    ) -> list[dict]:
        """执行工具调用列表，返回 role="tool" 消息。

        Args:
            cache: 可选的「(工具名, 参数) → 上次结果」缓存。传入后，
                完全相同的调用会直接复用上次结果而不重复执行 ——
                小模型常对同一参数反复调用同一工具，实测出现过连续 6 次
                相同调用，既拖慢响应又吃满上下文预算。
            identity: 调用链身份上下文（audit actor + 权限判定）。
        """
        from ..audit.breaker import check_and_record
        from ..audit.logger import record_tool_call
        from ..auth.permissions import check_operation_allowed

        actor = getattr(identity, "username", "local") if identity else "local"

        n = len(tool_calls)
        # 按下标预分配：并发任务各自写回自己的槽位，
        # 保证返回顺序与 tool_calls 严格一致（下游按 tool_call_id 关联、
        # SSE 事件按序 zip 配对）。
        results: list[dict | None] = [None] * n

        # 阶段 1：顺序预检。pending 收集需要真正执行的调用；
        # dup_slots 记录同批次内与 pending 重复的调用下标（等首个执行完复用结果）。
        pending: list[tuple[int, str, Any, dict, str, str]] = []
        dup_slots: list[tuple[int, str, str]] = []
        pending_keys: set[str] = set()

        for idx, tc in enumerate(tool_calls):
            tool_name = tc.get("function", {}).get("name", "")
            tool_args_str = tc.get("function", {}).get("arguments", "{}")
            tool_call_id = tc.get("id", f"call_{idx}")

            try:
                args = json.loads(tool_args_str) if tool_args_str else {}
            except json.JSONDecodeError:
                args = {}

            # ── P0：熔断检查 ──
            allowed, reason = check_and_record(tool_name)
            if not allowed:
                logger.warning("[agent] 工具 %s 触发熔断", tool_name)
                await record_tool_call(
                    tool_name=tool_name, arguments=args, error=reason,
                    conversation_id=conversation_id, session_id=session_id,
                    actor=actor, action="breaker_open",
                )
                results[idx] = {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": f"错误：{reason}",
                }
                continue

            # ── P0：操作级权限检查（查询/创建/修改/删除分级授权）──
            if identity is not None:
                op_allowed, op_reason = check_operation_allowed(identity, tool_name, args)
                if not op_allowed:
                    logger.warning("[agent] 权限拒绝: %s → %s (%s)", actor, tool_name, op_reason)
                    await record_tool_call(
                        tool_name=tool_name, arguments=args, error=f"permission_denied: {op_reason}",
                        conversation_id=conversation_id, session_id=session_id,
                        actor=actor, action="deny", approved=False,
                    )
                    results[idx] = {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": f"错误：权限不足，操作被拒绝（{op_reason}）",
                    }
                    continue

            # 去重键：工具名 + 规范化后的参数
            cache_key = f"{tool_name}|{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
            if cache is not None and cache_key in cache:
                logger.warning(
                    f"[agent] 检测到重复工具调用，复用上次结果: {tool_name}"
                )
                results[idx] = {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": (
                        f"{cache[cache_key]}\n\n"
                        "[系统提示] 这是与上一次完全相同的调用，结果未发生变化。"
                        "请不要重复调用同一工具与参数，直接基于已有结果给出最终回答。"
                    ),
                }
                continue

            # 同批次内重复：首个调用已在执行队列，本槽位等它完成后复用结果，
            # 避免相同调用被并发执行两遍（旧串行实现天然命中此优化）。
            if cache_key in pending_keys:
                dup_slots.append((idx, cache_key, tool_call_id))
                continue
            pending_keys.add(cache_key)
            pending.append((idx, tool_name, self.registry.get(tool_name),
                            args, cache_key, tool_call_id))

        # 阶段 2：并发执行。各工具调用互不依赖；gather 总耗时 ≈ 最慢工具。
        async def _run_one(
            idx: int,
            tool_name: str,
            tool_obj: Any,
            args: dict,
            cache_key: str,
            tool_call_id: str,
        ) -> None:
            t0 = time.monotonic()
            if tool_obj is None:
                result_text = (
                    f"[工具执行失败]\n工具: {tool_name}\n错误类型: not_registered\n"
                    f"错误详情: 工具 '{tool_name}' 未注册\n可重试: 否\n"
                    f"修复建议: 该工具不存在，请从已注册工具中选择，或用已有能力组合完成目标。"
                )
                logger.warning(f"[agent] 未知工具: {tool_name}")
            else:
                try:
                    ret = await tool_obj.execute(args)
                    if isinstance(ret, dict):
                        result_text = json.dumps(ret, ensure_ascii=False)
                        # L3：工具返回 success=false（业务失败）时，附可操作提示
                        # 引导模型修正参数或换路径，而非看到失败就停滞。
                        if ret.get("success") is False:
                            result_text += (
                                "\n\n[系统提示] 该工具返回失败（success=false）。"
                                "若失败原因可修复（如参数错误、路径不对），请修正后重试；"
                                "否则换一种方式完成目标，不要重复完全相同的调用。"
                            )
                    else:
                        result_text = str(ret)
                except Exception as exc:
                    result_text = _format_tool_error(tool_name, exc)
                    logger.error(f"[agent] {tool_name} 执行异常: {exc}")
            latency_ms = int((time.monotonic() - t0) * 1000)

            if cache is not None:
                cache[cache_key] = result_text

            results[idx] = {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": result_text,
            }
            logger.info(f"[agent] 工具 {tool_name} 执行完成，结果长度={len(result_text)}")

            # ── P0：审计落库（脱敏后参数 + 截断结果，aiosqlite 串行写入，并发安全）──
            try:
                await record_tool_call(
                    tool_name=tool_name, arguments=args, result=result_text,
                    conversation_id=conversation_id, session_id=session_id,
                    actor=actor, latency_ms=latency_ms,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(f"[agent] 审计记录失败: {exc}")

        if pending:
            await asyncio.gather(*(_run_one(*p) for p in pending))

        # 同批次重复调用：首个调用此刻已把结果写入 cache，复用并附加去重提示
        for idx, cache_key, tool_call_id in dup_slots:
            results[idx] = {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": (
                    f"{cache[cache_key]}\n\n"
                    "[系统提示] 这是与上一次完全相同的调用，结果未发生变化。"
                    "请不要重复调用同一工具与参数，直接基于已有结果给出最终回答。"
                ),
            }

        return [r for r in results if r is not None]


class AgentOrchestrator:
    """主 Agent 编排器：chat → route → tool → chat 循环。"""

    def __init__(
        self,
        context_manager: ContextManager,
        system_prompt: str = "",
        approval_callback=None,
        allowed_root_dirs: list[str] | None = None,
    ):
        self.cm = context_manager
        self.system_prompt = system_prompt
        self._registry = get_registry()
        # 轮次上限：原为 10，实测模型陷入循环时 10 轮要跑好几分钟才终止
        # （每轮 = 一次完整推理 + 工具执行，shell 慢时单轮可达 30s+）。
        # 降到 6：正常任务 3~4 轮足够，失控时更快兜底终止。
        self._max_turns = 12

        # 初始化工具注册表
        self._tool_registry = init_tools(
            approval_callback=approval_callback,
            allowed_root_dirs=allowed_root_dirs,
        )
        self._tool_node = ToolNode(self._tool_registry)
        self._approval_callback = approval_callback

        # 工具注册表（全部工具始终注册；knowledge 未配置连接时由工具自身返回提示）
        self._tool_registry = {k: v for k, v in self._tool_registry.items() if v is not None}

    def register_tool(self, name: str, tool_obj):
        self._tool_registry[name] = tool_obj

    def get_tool_registry(self) -> dict[str, Any]:
        return self._tool_registry

    async def run_stream(
        self,
        user_message: str,
        conversation_id: str,
        model_id: Optional[str] = None,
        history: Optional[list[dict]] = None,
        identity: Any = None,
        session_id: str = "",
        images: Optional[list[str]] = None,
        attachment_paths: Optional[list[str]] = None,
        workspace_dir: str = "",
        cancel_event: Optional[asyncio.Event] = None,
        mode: str = "craft",
        plan_confirmed: bool = False,
    ) -> AsyncIterator[dict]:
        """主入口：流式运行 Agent 循环，产出 SSE 兼容事件。

        Args:
            history: 该会话历史消息（role=user/assistant），用于多轮上下文。
                组装进 system 之后的对话消息，由上下文预算统一裁剪。
            identity: 调用链身份上下文（审计 Who + 权限判定）。
            session_id: 会话/请求标识（审计关联）。
            images: 粘贴的图片（base64 data URL），用于多模态视觉理解。
            attachment_paths: 上传文件已落盘的绝对路径列表。模型可通过
                filesystem 工具读取这些原始文件（如 docx 用 doc_to_html 转换）。
            cancel_event: 后台任务取消信号；置位后下一轮循环前停止并产出
                cancelled 事件（供 /api/tasks/cancel 使用）。None 表示不可取消。
            mode: P0.5 工作模式 craft/plan/ask（对齐 WorkBuddy）：
                craft=直接执行；plan=先建计划等待用户确认；ask=只答不动。
            plan_confirmed: Plan 模式下用户已确认计划（第二轮请求置 True，
                此后放行全部工具正常执行）。
        """

        # system prompt 不进 messages：它由 ContextManager.build_context 经
        # system_prompt 参数统一组装（full_messages 头部唯一一份）。
        # 旧实现同时把 system 塞进 messages，滑动窗口未裁掉它时每个请求
        # 都会携带两份相同 system，浪费 token 且可能干扰模型。
        # 注意：循环中途注入的 role="system" 防循环警告属于对话内消息，
        # 不在此列，仍随 messages 正常下发。
        messages: list[dict] = []
        if history:
            for h in history:
                role = h.get("role", "")
                content = h.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})

        # 当前用户消息：有图片时延迟到 resolve_model 之后统一构造
        # （图片消息需要知道 provider 是本机还是远程网关：本机才能用
        # image_url 指向本机文件；远程网关一律走 OCR 文本兜底）。
        if images:
            pass  # 图片消息在 resolve_model 后构造（含 user_message 文本）
        else:
            messages.append({"role": "user", "content": user_message})

        # 上传文件路径注入：告诉模型原始文件的落盘位置，
        # 需要时可用 filesystem 读取 / doc_to_html 转换（对接「读文档→提炼→生成」场景）
        if attachment_paths:
            path_hint = "\n".join(f"- {p}" for p in attachment_paths)
            messages.append({
                "role": "user",
                "content": (
                    "[附件] 本次对话上传的原始文件已保存到以下路径：\n"
                    f"{path_hint}\n"
                    "如果需要查看文件原始内容（如 Word/Excel/PDF 的未解析部分、"
                    "生成 HTML 成品文件等），可以使用 filesystem 或 doc_to_html 工具访问这些路径。"
                    "无需处理文件时忽略本条提示。"
                ),
            })

        # 工作目录注入：工作模式下会话绑定了工作区，告知模型在此目录下开展工作。
        # filesystem 用绝对路径读写；shell 通过 cwd 参数进入该目录执行。
        if workspace_dir and workspace_dir.strip():
            wd = workspace_dir.strip()
            messages.append({
                "role": "user",
                "content": (
                    "[工作区] 当前工作会话绑定的工作目录为：\n"
                    f"{wd}\n"
                    "请在此目录下开展工作：读写/搜索文件时使用该目录下的绝对路径；"
                    "执行 shell 命令时务必通过 cwd 参数指定此目录。"
                    "无文件或命令操作时忽略本条提示。"
                ),
            })
        # P1 长期记忆：跨会话检索注入（一次请求只注入一轮，检索命中才注入）
        try:
            from ..memory import memory_enabled
            from ..memory import store as memory_store

            if memory_enabled():
                _mem_rows = await memory_store.search_memories(
                    user_message, limit=6
                )
                if _mem_rows:
                    _kind_labels = {
                        "preference": "偏好", "fact": "事实", "conclusion": "结论",
                    }
                    _mem_block = (
                        "[长期记忆] 以下是关于用户/项目的跨会话记忆（供参考，"
                        "不要向用户复述这些条目本身）：\n"
                        + "\n".join(
                            f"- [{_kind_labels.get(r.get('kind'), r.get('kind'))}] "
                            f"{r.get('content', '')}"
                            for r in _mem_rows
                        )
                    )
                    messages.append({"role": "user", "content": _mem_block})
                    for _r in _mem_rows:
                        asyncio.ensure_future(memory_store.bump_memory(_r["id"]))
        except Exception as exc:  # noqa: BLE001
            logger.info("[agent] 记忆检索注入跳过: %s", exc)

        task_type = classify_task(user_message)
        # require_tools=True：Agent 的核心价值就是调用工具闭环，
        # 因此优先选择支持 function calling 的模型；不支持的候选会被跳过。
        # resolve_model 内部含同步网络探测（模型服务器 /models 健康探测，
        # 每服务器最多 5s）。必须在后台线程执行，否则会冻结整个事件循环，
        # 导致并发请求（含本会话的 meta/SSE）全部延迟。
        target_model, target_provider = await asyncio.to_thread(
            resolve_model,
            self._registry,
            task_type,
            user_preferred=model_id,
            require_tools=True,
        )

        # ── P0.9 图片消息构造（需 provider 信息，故放在 resolve_model 之后）──
        # 每张图：data URL → 落盘 → OCR 文字提取；本机网关额外附 image_url。
        # 远程网关：只注入 OCR 文本（网关访问不到本机 127.0.0.1 文件）。
        if images:
            try:
                from ..vision import is_local_base_url
                from ..vision import describe_image

                content_parts: list[dict] = []
                text_lines: list[str] = []
                if user_message and user_message.strip():
                    text_lines.append(user_message)
                elif not images:
                    text_lines.append("请描述这张图片的内容")
                _local_gateway = is_local_base_url(
                    getattr(target_provider, "base_url", "")
                )
                for _img in images:
                    if not isinstance(_img, str):
                        continue
                    if _img.startswith("data:image/"):
                        _desc = await asyncio.to_thread(describe_image, _img)
                        _ocr = _desc.get("ocr_text") or ""
                        if _ocr:
                            text_lines.append(
                                "以下文字是【用户上传截图的 OCR 识别结果】，"
                                "这是用户图片里的内容，不是本对话的历史消息，"
                                "请基于它回答用户的问题：\n"
                                f"{_ocr}"
                            )
                        _url = _desc.get("local_url")
                        if _local_gateway and _url:
                            content_parts.append({
                                "type": "image_url",
                                "image_url": {"url": _url},
                            })
                    elif _img.startswith(("http://", "https://")):
                        # 已是公网/内网 URL：直接给模型（模型侧需能访问）
                        content_parts.append({
                            "type": "image_url",
                            "image_url": {"url": _img},
                        })
                if text_lines:
                    content_parts.insert(0, {
                        "type": "text",
                        "text": "\n".join(text_lines),
                    })
                if content_parts:
                    messages.append({"role": "user", "content": content_parts})
            except Exception as _exc:  # noqa: BLE001
                logger.warning("[agent] 图片消息构造失败（回退纯文本）: %s", _exc)
                messages.append({"role": "user", "content": user_message})

        # 工具 schema 在整个会话中保持不变，循环外只构建一次。
        # 是否真的挂载取决于模型能力：不带 tools 的模型（如 deepseek-coder:6.7b）
        # 收到 tools 参数会直接 400，因此按能力探测结果决定。
        tool_schemas: list[dict] | None = None
        tools_enabled = model_supports_tools(target_model)

        # ── P0.5 工作模式初始化 ──
        _work_mode = mode if mode in (WORK_MODE_CRAFT, WORK_MODE_PLAN, WORK_MODE_ASK) else WORK_MODE_CRAFT
        _plan_confirmed = bool(plan_confirmed)
        # 模式提示词并入本轮 system（不污染缓存编排器的固定 system_prompt）
        _effective_system = self.system_prompt + _MODE_HINTS.get(_work_mode, "")
        if _work_mode == WORK_MODE_ASK:
            # Ask 模式：只答不动 —— 直接关闭工具挂载，模型拿不到任何工具 schema
            tools_enabled = False
            logger.info("[agent] Ask 模式：工具已禁用，仅问答")
        elif _work_mode == WORK_MODE_PLAN and not _plan_confirmed:
            logger.info("[agent] Plan 模式：等待用户确认计划，仅放行计划类工具")

        # 单次会话内的工具调用去重缓存（跨会话不复用，避免结果过期）
        tool_call_cache: dict[str, str] = {}
        # 同工具连续调用计数：小模型常见失效模式是反复调用同一工具
        # （每次换不同参数但仍陷在同一意图里）。连续超过阈值时强制终止，
        # 避免无意义的 6+ 轮空转。
        _last_tool: str | None = None
        _streak: int = 0

        state = AgentState(
            messages=messages,
            conversation_id=conversation_id,
            system_prompt=self.system_prompt,
            model_id=target_model,
            provider_id=target_provider,
        )

        # P3：暴露当前模型/会话给 subagent 工具（工具无参数穿透通道）
        _m_token = _current_model.set(target_model)
        _c_token = _current_conversation.set(conversation_id or "")

        yield {"type": "meta", "conversation_id": conversation_id, "model_id": target_model}

        # P0 智能体闭环：创建请求级计划总线（contextvar 随请求任务隔离，
        # 无需 reset —— 请求任务结束后上下文自然消亡，不会污染并发会话）。
        _plan_bus = PlanBus()
        set_plan_bus(_plan_bus)

        # 技能运行时同步：把「已启用且有可执行入口」的已安装技能挂进工具表
        # （SkillTool 动态注册）。安装/启停/卸载后下一次会话即生效，无需重启后端；
        # 同步失败只记日志，不阻断聊天。
        try:
            from ..tools.skill_tools import sync_installed_skill_tools

            await sync_installed_skill_tools(self._tool_registry, self._approval_callback)
        except Exception as exc:  # noqa: BLE001
            logger.error("[agent] 技能同步失败（不阻断会话）: %s", exc)

        turn = 0
        _auto_continue = 0  # P0.5 自动续跑次数（防"意图声明式回答"）
        # L3 动态轮次上限：计划模式确认后执行阶段任务复杂（探查→执行→验证多阶段），
        # 放宽上限避免多步骤任务被硬切；craft 保持 12 轮兜底防失控。
        if _work_mode == WORK_MODE_PLAN and _plan_confirmed:
            _loop_max = max(self._max_turns, 24)
        else:
            _loop_max = self._max_turns
        while turn < _loop_max:
            turn += 1

            # ── 后台任务取消检查（P0）：置位后立即停止，不启动下一轮 LLM ──
            if cancel_event is not None and cancel_event.is_set():
                logger.info("[agent] 收到取消信号，停止本轮任务")
                yield {"type": "cancelled", "message": "任务已取消"}
                return

            # ── 上下文预算裁剪 ──
            context = self.cm.build_context(
                system_prompt=_effective_system,
                messages=state.messages,
            )

            # ── 组装完整消息列表 ──
            full_messages = [
                {"role": "system", "content": context["system"]},
                *context["messages"],
            ]

            # ── 调用 LLM ──
            provider = self._registry.get(state.provider_id)
            if provider is None:
                yield {"type": "error", "message": f"Provider {state.provider_id} 不可用"}
                break

            assistant_content = ""
            reasoning_content = ""  # DeepSeek V4-Pro 深度思考内容，每轮完整回灌
            all_tool_calls: list[dict] = []
            last_tool_calls_event: list[dict] | None = None

            # 没有工具 schema，模型就不可能产出 tool_calls，下面的多轮工具闭环
            # 永远不会被执行 —— 这是 Phase 3 能否成立的关键。
            if tool_schemas is None:
                tool_schemas = [
                    t.to_openai_schema()
                    for t in self._tool_registry.values()
                    if hasattr(t, "to_openai_schema")
                ]
                if tool_schemas and tools_enabled:
                    logger.info(f"[agent] 已向模型注册 {len(tool_schemas)} 个工具: "
                                f"{[s['function']['name'] for s in tool_schemas]}")
                elif tool_schemas:
                    logger.info(f"[agent] 模型 {state.model_id} 不支持 tools，"
                                f"本轮不挂载工具（{len(tool_schemas)} 个工具已就绪但未下发）")

            # tools=None 会让部分 SDK/后端报参数错误，只在有工具时显式传入
            chat_kwargs: dict[str, Any] = {"stream": True}
            # L2 temperature 校准：工具执行/规划轮用低温保证调用稳定、参数不漂移；
            # 纯问答（ask）用中温让回答更自然；规划阶段略升一点保留方案多样性。
            if _work_mode == WORK_MODE_ASK:
                chat_kwargs["temperature"] = 0.7
            elif _work_mode == WORK_MODE_PLAN and not _plan_confirmed:
                chat_kwargs["temperature"] = 0.4
            else:
                chat_kwargs["temperature"] = 0.3
            if tool_schemas and tools_enabled:
                chat_kwargs["tools"] = tool_schemas

            # 每轮 LLM 调用的事件级硬超时：上游（如智谱）在工具结果后可能出现
            # “流式持续推空/长时间无下一事件”的挂死态——httpx 的 read 超时只在
            # 完全无字节时才触发，逐事件超时兜底，超时即中断本轮并报错，不再挂死。
            LLM_EVENT_TIMEOUT_SEC = 60.0

            try:
                stream = dsml_guard(provider.chat(
                    messages=full_messages,
                    model=state.model_id,
                    **chat_kwargs,
                ))
                while True:
                    try:
                        event = await asyncio.wait_for(
                            anext(stream), timeout=LLM_EVENT_TIMEOUT_SEC
                        )
                    except StopAsyncIteration:
                        break
                    etype = event.get("type", "")
                    if etype == "text":
                        assistant_content += event.get("delta", "")
                        yield {"type": "text", "delta": event.get("delta", "")}
                    elif etype == "thinking":
                        delta = event.get("delta", "")
                        reasoning_content += delta  # 累积本轮深度思考内容
                        yield {"type": "thinking", "delta": delta}
                    elif etype == "tool_calls":
                        last_tool_calls_event = event
                        # 防御：跳过残缺工具调用（无 name 的增量片段），
                        # 避免把不完整调用当成独立工具执行
                        for _c in event.get("calls", []):
                            if _c.get("function", {}).get("name"):
                                all_tool_calls.append(_c)
                    elif etype == "done":
                        usage = event.get("usage", {})
                        state.token_usage["input"] += usage.get("promptTokens", 0)
                        state.token_usage["output"] += usage.get("completionTokens", 0)
                        # 不在单轮结束时发 done：整个 Agent 循环结束后统一发一次，
                        # 否则下游（chat._event_stream）收到首个 done 就 break，
                        # 后续工具执行事件会被截断。
                        break
                    elif etype == "error":
                        yield {"type": "error", "message": event.get("message", "")}
                        return
            except asyncio.TimeoutError:
                logger.error(
                    f"[agent] 模型 {state.model_id} 响应超时（{LLM_EVENT_TIMEOUT_SEC}s 无数据），"
                    f"已中断本轮"
                )
                yield {
                    "type": "error",
                    "message": (
                        f"模型响应超时（{int(LLM_EVENT_TIMEOUT_SEC)} 秒无数据），"
                        "已中断本轮。可重试，或切换 agnes-2.5-flash 执行复杂任务。"
                    ),
                }
                return
            except Exception as exc:
                msg = str(exc)
                # 兜底降级：能力探测可能失效（非 Ollama 后端、探测超时等），
                # 运行时若明确报「不支持 tools」，就关掉工具重试本轮，
                # 而不是把整个对话判死。
                if tools_enabled and ("does not support tools" in msg.lower()):
                    logger.warning(
                        f"[agent] 模型 {state.model_id} 实际不支持 tools，关闭工具后重试: {msg}"
                    )
                    _mark_model_tool_support(state.model_id, False)
                    tools_enabled = False
                    assistant_content = ""
                    all_tool_calls = []
                    continue

                logger.error(f"[agent] LLM 调用异常: {exc}")
                yield {"type": "error", "message": f"模型调用失败: {exc}"}
                break

            # 将助理回复加入历史。
            # 关键：即便 assistant_content 为空（模型只产出 tool_calls、没有正文），
            # 也必须把这轮 assistant 消息连同 tool_calls 一起入列。否则后续追加的
            # role="tool" 消息没有前置的 tool_calls，OpenAI 兼容接口会直接报错：
            # "messages with role 'tool' must be a response to a preceeding message
            #  with 'tool_calls'" —— 表现为第二轮起整个工具闭环崩掉。
            # 二次防御：剥离可能残留的 DSML 源码。
            # 流式拦截已在 provider 层把 DSML 翻译成 tool_calls，这里只兜底
            # 「拦截漏过」的极端情况 —— 避免 DSML 源码被写进会话历史，
            # 下一轮又作为上下文回灌给模型、或直接渲染到前端。
            assistant_content = strip_dsml_text(assistant_content)

            assistant_msg: dict[str, Any] = {"role": "assistant", "content": assistant_content}
            # 深度思考上下文回灌：每一轮新请求必须原样传入上一轮完整的 reasoning_content
            if reasoning_content:
                assistant_msg["reasoning_content"] = strip_dsml_text(reasoning_content)
            if all_tool_calls:
                assistant_msg["tool_calls"] = all_tool_calls
                state.messages.append(assistant_msg)
            elif assistant_content:
                state.messages.append({"role": "assistant", "content": assistant_content})

            # ── 路由：有无工具调用？ ──
            if not all_tool_calls:
                # 无工具调用 → 常规情况结束本轮。
                # P0.5 自动续跑：若回复只是"意图声明"（说要做但没做），
                # 注入提示强制它真正执行，而不是把计划当答案交付。
                if _auto_continue < _AUTO_CONTINUE_MAX and _is_promise_text(assistant_content):
                    _auto_continue += 1
                    logger.info(
                        "[agent] 检测到意图式回答（无工具调用），注入续跑提示 #%d",
                        _auto_continue,
                    )
                    state.messages.append({"role": "user", "content": _AUTO_CONTINUE_HINT})
                    continue
                break

            # 有工具调用 → 执行工具，追加 role="tool" 消息后继续循环
            #
            # 去重：小模型常见的失效模式是「对同一参数反复调用同一工具」
            # （实测出现同一路径连续调用 6 次），每次都要等一轮完整推理，
            # 既拖慢响应又消耗上下文预算。这里识别完全重复的调用，
            # 命中时直接复用上次结果，不再真正执行。
            #
            # P0.5 Plan 门控：未确认前只放行 create_plan/update_plan，
            # 其余工具调用不执行，返回"等待确认"结果（防模型越权先干）。
            _plan_gate_active = _work_mode == WORK_MODE_PLAN and not _plan_confirmed
            if _plan_gate_active:
                _real: list[tuple[int, dict]] = []
                _blocked: list[tuple[int, dict]] = []
                for _i, _tc in enumerate(all_tool_calls):
                    _nm = _tc.get("function", {}).get("name", "")
                    (_real if _nm in _PLAN_GATE_TOOLS else _blocked).append((_i, _tc))
                if _blocked:
                    _blocked_map = {
                        i: {
                            "role": "tool",
                            "tool_call_id": tc.get("id", f"call_{i}"),
                            "content": _PLAN_GATE_BLOCKED_MSG,
                        }
                        for i, tc in _blocked
                    }
                    if _real:
                        _real_idx = [i for i, _ in _real]
                        _real_res = await self._tool_node.execute(
                            [tc for _, tc in _real],
                            cache=tool_call_cache,
                            conversation_id=conversation_id,
                            session_id=session_id,
                            identity=identity,
                        )
                        tool_results = []
                        _ri = 0
                        for _i in range(len(all_tool_calls)):
                            if _i in _blocked_map:
                                tool_results.append(_blocked_map[_i])
                            else:
                                tool_results.append(_real_res[_ri])
                                _ri += 1
                    else:
                        tool_results = [_blocked_map[i] for i in sorted(_blocked_map)]
                else:
                    tool_results = await self._tool_node.execute(
                        all_tool_calls,
                        cache=tool_call_cache,
                        conversation_id=conversation_id,
                        session_id=session_id,
                        identity=identity,
                    )
            else:
                tool_results = await self._tool_node.execute(
                    all_tool_calls,
                    cache=tool_call_cache,
                    conversation_id=conversation_id,
                    session_id=session_id,
                    identity=identity,
                )

            # P0：透传本轮工具执行中产生的计划事件（create_plan/update_plan）
            for _plan_ev in _plan_bus.take_events():
                yield _plan_ev

            # 产出工具事件（完整结果，供前端时间线展示/展开/复制）
            for tc in all_tool_calls:
                fn = tc.get("function", {})
                yield {"type": "tool_call", "name": fn.get("name", ""), "arguments": fn.get("arguments", "")}
            for tc, res in zip(all_tool_calls, tool_results):
                fn = tc.get("function", {})
                content = res.get("content", "")
                # 提取交付文件路径：工具返回 JSON 且含 path（如 filesystem write 成功）
                # → 前端可渲染「打开文件 / 在 Finder 中显示」按钮
                saved_files: list[str] = []
                if isinstance(content, str) and content.strip().startswith("{"):
                    try:
                        parsed = json.loads(content)
                        if isinstance(parsed, dict):
                            p = parsed.get("path")
                            if isinstance(p, str) and p.startswith("/") and parsed.get("success") is not False:
                                saved_files.append(p)
                    except Exception:  # noqa: BLE001
                        pass
                yield {
                    "type": "tool_result",
                    "name": fn.get("name", ""),
                    "result": content if isinstance(content, str) else str(content),
                    "is_error": (
                        content.startswith(("[工具执行失败]", "错误"))
                        or '"success": false' in content
                    ) if isinstance(content, str) else False,
                    "saved_files": saved_files,
                }

            # 工具结果入历史前统一摘要化（L3）：超长结果做结构化摘要
            # （JSON 保留结构骨架 / 文本保留头尾 + 省略指引），比硬截断省 token
            # 且防模型幻觉；SSE 已在上方透传完整结果，
            # 前端「展开查看完整结果 / 复制结果」不受影响。
            for _tr in tool_results:
                _c = _tr.get("content")
                if isinstance(_c, str) and len(_c) > self.cm.tool_result_max_chars:
                    _tr["content"] = self.cm.summarize_tool_result(_c)
            state.messages.extend(tool_results)
            state.tool_results.extend(tool_results)

            # P0.5 Plan 模式：计划已建立（PlanBus 有步骤）且未确认 →
            # 产出「等待确认」事件并停止本轮，等用户点确认后再续跑。
            if (
                _plan_gate_active
                and _plan_bus.steps
                and any(
                    tc.get("function", {}).get("name", "") == "create_plan"
                    for tc in all_tool_calls
                )
            ):
                _snap = _plan_bus.snapshot()
                yield {
                    "type": "plan_awaiting_confirm",
                    "goal": _snap.get("goal", ""),
                    "steps": _snap.get("steps", []),
                }
                break

            # ── 同工具连续调用检测（防循环）──
            # 小模型在 coding / 分析类任务上容易陷入「同一工具换不同参数反复调」
            # 的死循环（实测 code 工具被连续调用 6 次）。这里按单轮主工具名统计
            # 连续命中次数，达到阈值（默认 3）就注入系统警告并强制退出循环。
            #
            # 豁免 db_query：数据库探查是"多步合理工作流"（查表清单 → 查列 →
            # 查数据 / 分页补全），天然需要连续多轮调用同一工具。若参与计数，
            # 模型在完整探查 28 张表元数据的过程中就会被误判为循环而强制终止，
            # 导致后续生成 HTML/PPT 等成品步骤永远走不到（用户实测现象）。
            # 真正的同参死循环已由上面的 tool_call_cache 同参去重拦截（复用结果、
            # 不再执行），因此 db_query 连续调用不构成失控风险。
            called_names = [tc.get("function", {}).get("name", "") for tc in all_tool_calls]
            primary_tool = called_names[0] if called_names else None
            if primary_tool and primary_tool == _last_tool:
                # db_query / shell 豁免：数据库探查和 shell 建库/批量操作都是
                # 多步合理工作流（CREATE USER → CREATE DATABASE → GRANT，或
                # 批量文件处理），天然需要连续多轮调用同一工具。
                if primary_tool not in ("db_query", "shell"):
                    _streak += 1
            else:
                _last_tool = primary_tool
                _streak = 1

            # 阈值 2：对非豁免工具，连续调 2 次即终止，防失控循环。
            # 豁免工具（db_query / shell）不受此限，由 _max_turns=6 兜底。
            if _streak >= 2:
                logger.warning(
                    f"[agent] 工具 '{primary_tool}' 连续调用 {_streak} 次，疑似循环，注入警告并终止"
                )
                state.messages.append({
                    "role": "system",
                    "content": (
                        f"[系统警告] 工具 '{primary_tool}' 已被连续调用 {_streak} 次。"
                        "请立即停止重复调用，直接基于已有结果给出最终回答。"
                    ),
                })
                break

            logger.info(f"[agent] 第 {turn} 轮：执行了 {len(tool_results)} 个工具，继续对话")

        # ── 循环结束兜底：若最后一条消息不是 assistant（即模型还没给出最终文本，
        # 可能是刚执行完工具、或被同工具循环检测强制 break），再调用一次模型
        # （不带 tools）生成最终文本回答，避免用户只看到工具执行过程却没有结论。
        if state.messages and state.messages[-1].get("role") != "assistant":
            logger.info("[agent] 循环结束但模型未给出最终文本，补一轮纯文本生成")
            try:
                context = self.cm.build_context(
                    system_prompt=self.system_prompt,
                    messages=state.messages,
                )
                full_messages = [
                    {"role": "system", "content": context["system"]},
                    *context["messages"],
                    {"role": "user", "content": (
                        "请基于以上工具读取到的内容，直接用中文输出完整的最终回答："
                        "把分析结论、发现的问题/不足、改进建议逐条列清楚。"
                        "这是最后一步，不要再调用任何工具，也不要只写「让我分析/评价一下」"
                        "这类过渡话——直接给出成稿。"
                    )},
                ]
                provider = self._registry.get(state.provider_id)
                stream = dsml_guard(provider.chat(
                    messages=full_messages,
                    model=state.model_id,
                    stream=True,
                ))
                final_content = ""
                while True:
                    try:
                        event = await asyncio.wait_for(
                            anext(stream), timeout=LLM_EVENT_TIMEOUT_SEC
                        )
                    except StopAsyncIteration:
                        break
                    etype = event.get("type", "")
                    if etype == "text":
                        final_content += event.get("delta", "")
                        yield {"type": "text", "delta": event.get("delta", "")}
                    elif etype == "thinking":
                        delta = event.get("delta", "")
                        reasoning_content += delta  # 累积本轮深度思考内容
                        yield {"type": "thinking", "delta": delta}
                    elif etype == "done":
                        usage = event.get("usage", {})
                        state.token_usage["input"] += usage.get("promptTokens", 0)
                        state.token_usage["output"] += usage.get("completionTokens", 0)
                        break
                    elif etype == "error":
                        yield {"type": "error", "message": event.get("message", "")}
                        break
                if final_content:
                    final_content = strip_dsml_text(final_content)
                if final_content:
                    state.messages.append({"role": "assistant", "content": final_content})
            except Exception as exc:
                logger.error(f"[agent] 兜底文本生成失败: {exc}")

        # 最终收尾：整个 Agent 循环结束后统一产出 done（含累计用量）
        # 兜底冲刷计划事件（正常路径已在工具执行后透传，此处防遗漏）
        for _plan_ev in _plan_bus.take_events():
            yield _plan_ev
        logger.info(f"[agent] 对话结束，共 {turn} 轮，工具调用 {len(state.tool_results)} 次")
        yield {"type": "done", "usage": {
            "promptTokens": state.token_usage.get("input", 0),
            "completionTokens": state.token_usage.get("output", 0),
        }}

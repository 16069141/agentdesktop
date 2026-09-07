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
import json
import logging
import time
from typing import Any, AsyncIterator, Optional

from ..context.context_manager import ContextManager
from ..providers import (
    _mark_model_tool_support,
    classify_task,
    get_registry,
    model_supports_tools,
    resolve_model,
)
from ..tools import init_tools

logger = logging.getLogger(__name__)


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


class ToolNode:
    """工具执行节点：按 tool_call 列表逐一调用已注册工具。

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

        results = []
        for tc in tool_calls:
            tool_name = tc.get("function", {}).get("name", "")
            tool_args_str = tc.get("function", {}).get("arguments", "{}")
            tool_call_id = tc.get("id", f"call_{len(results)}")

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
                results.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": f"错误：{reason}",
                })
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
                    results.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": f"错误：权限不足，操作被拒绝（{op_reason}）",
                    })
                    continue

            # 去重键：工具名 + 规范化后的参数
            cache_key = f"{tool_name}|{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
            if cache is not None and cache_key in cache:
                logger.warning(
                    f"[agent] 检测到重复工具调用，复用上次结果: {tool_name}"
                )
                results.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": (
                        f"{cache[cache_key]}\n\n"
                        "[系统提示] 这是与上一次完全相同的调用，结果未发生变化。"
                        "请不要重复调用同一工具与参数，直接基于已有结果给出最终回答。"
                    ),
                })
                continue

            tool_obj = self.registry.get(tool_name)
            t0 = time.monotonic()
            if tool_obj is None:
                result_text = f"错误：工具 '{tool_name}' 未注册"
                logger.warning(f"[agent] 未知工具: {tool_name}")
            else:
                try:
                    ret = await tool_obj.execute(args)
                    if isinstance(ret, dict):
                        result_text = json.dumps(ret, ensure_ascii=False)
                    else:
                        result_text = str(ret)
                except Exception as exc:
                    result_text = f"工具执行失败: {exc}"
                    logger.error(f"[agent] {tool_name} 执行异常: {exc}")
            latency_ms = int((time.monotonic() - t0) * 1000)

            if cache is not None:
                cache[cache_key] = result_text

            results.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": result_text,
            })
            logger.info(f"[agent] 工具 {tool_name} 执行完成，结果长度={len(result_text)}")

            # ── P0：审计落库（脱敏后参数 + 截断结果）──
            try:
                await record_tool_call(
                    tool_name=tool_name, arguments=args, result=result_text,
                    conversation_id=conversation_id, session_id=session_id,
                    actor=actor, latency_ms=latency_ms,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(f"[agent] 审计记录失败: {exc}")

        return results


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
        self._max_turns = 10

        # 初始化工具注册表
        self._tool_registry = init_tools(
            approval_callback=approval_callback,
            allowed_root_dirs=allowed_root_dirs,
        )
        self._tool_node = ToolNode(self._tool_registry)

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
        """

        messages = [{"role": "system", "content": self.system_prompt}]
        if history:
            for h in history:
                role = h.get("role", "")
                content = h.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})

        # 当前用户消息：有图片时用 OpenAI 多模态格式（content 为数组）
        if images:
            content_parts: list[dict] = []
            if user_message and user_message.strip():
                content_parts.append({"type": "text", "text": user_message})
            else:
                content_parts.append({"type": "text", "text": "请描述这张图片的内容"})
            for img in images:
                content_parts.append({"type": "image_url", "image_url": {"url": img}})
            messages.append({"role": "user", "content": content_parts})
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

        # 工具 schema 在整个会话中保持不变，循环外只构建一次。
        # 是否真的挂载取决于模型能力：不带 tools 的模型（如 deepseek-coder:6.7b）
        # 收到 tools 参数会直接 400，因此按能力探测结果决定。
        tool_schemas: list[dict] | None = None
        tools_enabled = model_supports_tools(target_model)
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

        yield {"type": "meta", "conversation_id": conversation_id, "model_id": target_model}

        turn = 0
        while turn < self._max_turns:
            turn += 1

            # ── 上下文预算裁剪 ──
            context = self.cm.build_context(
                system_prompt=self.system_prompt,
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
            if tool_schemas and tools_enabled:
                chat_kwargs["tools"] = tool_schemas

            try:
                async for event in provider.chat(
                    messages=full_messages,
                    model=state.model_id,
                    **chat_kwargs,
                ):
                    etype = event.get("type", "")
                    if etype == "text":
                        assistant_content += event.get("delta", "")
                        yield {"type": "text", "delta": event.get("delta", "")}
                    elif etype == "thinking":
                        yield {"type": "thinking", "delta": event.get("delta", "")}
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
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": assistant_content}
            if all_tool_calls:
                assistant_msg["tool_calls"] = all_tool_calls
                state.messages.append(assistant_msg)
            elif assistant_content:
                state.messages.append({"role": "assistant", "content": assistant_content})

            # ── 路由：有无工具调用？ ──
            if not all_tool_calls:
                # 无工具调用 → 结束本轮
                break

            # 有工具调用 → 执行工具，追加 role="tool" 消息后继续循环
            #
            # 去重：小模型常见的失效模式是「对同一参数反复调用同一工具」
            # （实测出现同一路径连续调用 6 次），每次都要等一轮完整推理，
            # 既拖慢响应又消耗上下文预算。这里识别完全重复的调用，
            # 命中时直接复用上次结果，不再真正执行。
            tool_results = await self._tool_node.execute(
                all_tool_calls,
                cache=tool_call_cache,
                conversation_id=conversation_id,
                session_id=session_id,
                identity=identity,
            )
            state.messages.extend(tool_results)
            state.tool_results.extend(tool_results)

            # 产出工具事件，便于前端/评测观察闭环过程
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
                    "is_error": content.startswith("错误") if isinstance(content, str) else False,
                    "saved_files": saved_files,
                }

            # ── 同工具连续调用检测（防循环）──
            # 小模型在 coding / 分析类任务上容易陷入「同一工具换不同参数反复调」
            # 的死循环（实测 code 工具被连续调用 6 次）。这里按单轮主工具名统计
            # 连续命中次数，达到阈值（默认 3）就注入系统警告并强制退出循环。
            called_names = [tc.get("function", {}).get("name", "") for tc in all_tool_calls]
            primary_tool = called_names[0] if called_names else None
            if primary_tool and primary_tool == _last_tool:
                _streak += 1
            else:
                _last_tool = primary_tool
                _streak = 1

            if _streak >= 3:
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

        # 最终收尾：整个 Agent 循环结束后统一产出 done（含累计用量）
        logger.info(f"[agent] 对话结束，共 {turn} 轮，工具调用 {len(state.tool_results)} 次")
        yield {"type": "done", "usage": {
            "promptTokens": state.token_usage.get("input", 0),
            "completionTokens": state.token_usage.get("output", 0),
        }}

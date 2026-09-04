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

import json
import logging
from typing import Any, AsyncIterator, Optional

from ..context.context_manager import ContextManager
from ..providers import classify_task, get_registry, resolve_model
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
    """工具执行节点：按 tool_call 列表逐一调用已注册工具。"""

    def __init__(self, tool_registry: dict[str, Any]):
        self.registry = tool_registry

    async def execute(
        self,
        tool_calls: list[dict],
    ) -> list[dict]:
        """执行工具调用列表，返回 role="tool" 消息。"""
        results = []
        for tc in tool_calls:
            tool_name = tc.get("function", {}).get("name", "")
            tool_args_str = tc.get("function", {}).get("arguments", "{}")
            tool_call_id = tc.get("id", f"call_{len(results)}")

            try:
                args = json.loads(tool_args_str) if tool_args_str else {}
            except json.JSONDecodeError:
                args = {}

            tool_obj = self.registry.get(tool_name)
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

            results.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": result_text,
            })
            logger.info(f"[agent] 工具 {tool_name} 执行完成，结果长度={len(result_text)}")

        return results


class AgentOrchestrator:
    """主 Agent 编排器：chat → route → tool → chat 循环。"""

    def __init__(
        self,
        context_manager: ContextManager,
        system_prompt: str = "",
        rag_pipeline=None,
        approval_callback=None,
        allowed_root_dirs: list[str] | None = None,
    ):
        self.cm = context_manager
        self.system_prompt = system_prompt
        self._registry = get_registry()
        self._max_turns = 10

        # 初始化工具注册表
        self._tool_registry = init_tools(
            rag_pipeline=rag_pipeline,
            approval_callback=approval_callback,
            allowed_root_dirs=allowed_root_dirs,
        )
        self._tool_node = ToolNode(self._tool_registry)

        # 过滤掉 None 工具（如 rag 未初始化时 knowledge 为 None）
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
    ) -> AsyncIterator[dict]:
        """主入口：流式运行 Agent 循环，产出 SSE 兼容事件。"""

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message},
        ]
        task_type = classify_task(user_message)
        target_model, target_provider = resolve_model(
            self._registry, task_type, user_preferred=model_id
        )

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
            if context["rag_context"]:
                full_messages.append({
                    "role": "system",
                    "content": f"以下是知识库检索结果，请引用回答：\n{context['rag_context']}",
                })

            # ── 调用 LLM ──
            provider = self._registry.get(state.provider_id)
            if provider is None:
                yield {"type": "error", "message": f"Provider {state.provider_id} 不可用"}
                break

            assistant_content = ""
            all_tool_calls: list[dict] = []
            last_tool_calls_event: list[dict] | None = None

            try:
                async for event in provider.chat(
                    messages=full_messages,
                    model=state.model_id,
                    stream=True,
                ):
                    etype = event.get("type", "")
                    if etype == "text":
                        assistant_content += event.get("delta", "")
                        yield {"type": "text", "delta": event.get("delta", "")}
                    elif etype == "thinking":
                        yield {"type": "thinking", "delta": event.get("delta", "")}
                    elif etype == "tool_calls":
                        last_tool_calls_event = event
                        all_tool_calls.extend(event.get("calls", []))
                    elif etype == "done":
                        usage = event.get("usage", {})
                        state.token_usage["input"] += usage.get("promptTokens", 0)
                        state.token_usage["output"] += usage.get("completionTokens", 0)
                        yield {"type": "done", "usage": usage}
                        break
                    elif etype == "error":
                        yield {"type": "error", "message": event.get("message", "")}
                        return
            except Exception as exc:
                logger.error(f"[agent] LLM 调用异常: {exc}")
                yield {"type": "error", "message": f"模型调用失败: {exc}"}
                break

            # 将助理回复加入历史
            if assistant_content:
                state.messages.append({"role": "assistant", "content": assistant_content})

            # ── 路由：有无工具调用？ ──
            if not all_tool_calls:
                # 无工具调用 → 结束本轮
                break

            # 有工具调用 → 执行工具，追加 role="tool" 消息后继续循环
            tool_results = await self._tool_node.execute(all_tool_calls)
            state.messages.extend(tool_results)
            state.tool_results.extend(tool_results)

            logger.info(f"[agent] 第 {turn} 轮：执行了 {len(tool_results)} 个工具，继续对话")

        # 最终收尾（已在 done 事件中产出，此处仅标记循环结束）
        logger.info(f"[agent] 对话结束，共 {turn} 轮，工具调用 {len(state.tool_results)} 次")

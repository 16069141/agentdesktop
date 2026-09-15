"""上下文管理（token 预算 + 滑动窗口 + 工具结果截断）。

规格书 §9.5：
- 系统提示 + 历史 + 生成（比例可配）
- 滑动窗口默认 20 轮
- 超长工具结果截断/摘要后入上下文
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ContextManager:
    def __init__(
        self,
        max_tokens: int = 16384,
        system_ratio: float = 0.15,
        history_ratio: float = 0.40,
        generation_ratio: float = 0.45,
        max_turns: int = 20,
        tool_result_max_chars: int = 2000,
    ):
        self.max_tokens = max_tokens
        self.system_budget = int(max_tokens * system_ratio)
        self.history_budget = int(max_tokens * history_ratio)
        self.generation_budget = int(max_tokens * generation_ratio)
        self.max_turns = max_turns
        self.tool_result_max_chars = tool_result_max_chars

    def estimate_tokens(self, text: str) -> int:
        """粗略 token 估算（中文约 1 token/字，英文约 4 char/token）。"""
        chinese = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        rest = len(text) - chinese
        return chinese + rest // 4

    def truncate_tool_result(self, result: str) -> str:
        """截断超长工具结果。"""
        if len(result) <= self.tool_result_max_chars:
            return result
        return result[: self.tool_result_max_chars] + "\n...[truncated]"

    def build_context(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """组装上下文，返回 {system, messages, token_usage}。"""
        # 1) 系统提示
        sys_tokens = self.estimate_tokens(system_prompt)
        if sys_tokens > self.system_budget:
            system_prompt = system_prompt[: int(self.system_budget * 4)] + "...[truncated]"
            logger.warning("[context] 系统提示超出预算，已截断")

        # 2) 滑动窗口截断历史
        truncated_messages = messages[-self.max_turns * 2:] if len(messages) > self.max_turns * 2 else messages

        def _msg_text(m: Dict[str, Any]) -> str:
            """提取消息文本内容，兼容多模态 content（数组）。"""
            content = m.get("content", "")
            if isinstance(content, list):
                parts = []
                for p in content:
                    if isinstance(p, dict) and p.get("type") == "text":
                        parts.append(p.get("text", ""))
                return " ".join(parts)
            return content if isinstance(content, str) else str(content)

        hist_tokens = sum(self.estimate_tokens(_msg_text(m)) for m in truncated_messages)

        # 3) 总预算检查（历史超预算时从头部压缩）
        total = sys_tokens + hist_tokens
        if total > self.max_tokens:
            # 压缩历史
            excess = total - self.max_tokens
            while hist_tokens > self.history_budget * 0.5 and excess > 0:
                if truncated_messages:
                    removed = truncated_messages.pop(0)
                    hist_tokens -= self.estimate_tokens(_msg_text(removed))
                    excess -= self.estimate_tokens(_msg_text(removed))
                else:
                    break

        # 5) 清理孤儿 tool 消息。
        #    上面的滑动窗口/预算压缩是按条从头丢弃的，有可能把带 tool_calls 的
        #    assistant 消息丢掉、却留下它对应的 role="tool" 消息。OpenAI 兼容接口
        #    对这种「tool 消息没有前置 tool_calls」的情况是直接 400 报错的，
        #    因此在返回前必须把孤儿 tool 消息一并剔除。
        cleaned: List[Dict[str, Any]] = []
        pending_tool_ids: set[str] = set()
        for m in truncated_messages:
            role = m.get("role", "")
            if role == "assistant" and m.get("tool_calls"):
                pending_tool_ids = {
                    tc.get("id", "") for tc in m.get("tool_calls", []) if tc.get("id")
                }
                cleaned.append(m)
                continue
            if role == "tool":
                tool_call_id = m.get("tool_call_id", "")
                # 该 tool 消息还有对应的 tool_calls 才保留
                if tool_call_id and tool_call_id in pending_tool_ids:
                    cleaned.append(m)
                    pending_tool_ids.discard(tool_call_id)
                elif not tool_call_id:
                    # 拿不到 id 时无法判定，保守丢弃以避免 400
                    logger.warning("[context] 丢弃缺少 tool_call_id 的 tool 消息")
                continue
            cleaned.append(m)

        if len(cleaned) != len(truncated_messages):
            logger.warning(
                f"[context] 清理孤儿 tool 消息: {len(truncated_messages)} -> {len(cleaned)}"
            )

        # 6) 保证消息序列中至少有一条 user 消息。
        #    多轮工具循环 + 滑动窗口/预算压缩从头裁剪后，剩余序列可能只剩
        #    assistant/tool 消息（实测：长任务执行 40+ 步骤时首轮 user 被裁掉，
        #    部分 OpenAI 兼容网关直接 400 "No user query found in messages"）。
        #    OpenAI 规范允许纯 system/assistant/tool 序列，但网关不认；
        #    注入一条占位 user 消息即可恢复合法性，不改变语义。
        if not any(m.get("role") == "user" for m in cleaned):
            cleaned.append({"role": "user", "content": "请基于以上内容继续。"})
            logger.warning("[context] 消息序列无 user 消息，已注入占位 user（防网关 400）")

        return {
            "system": system_prompt,
            "messages": cleaned,
            "token_usage": {
                "system": sys_tokens,
                "history": hist_tokens,
                "total": sys_tokens + hist_tokens,
                "budget": self.max_tokens,
            },
        }

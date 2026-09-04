"""上下文管理（token 预算 + 滑动窗口 + 工具结果截断）。

规格书 §9.5：
- 系统提示 15% + 历史 40% + RAG 20% + 生成 25%（可配）
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
        rag_ratio: float = 0.20,
        generation_ratio: float = 0.25,
        max_turns: int = 20,
        tool_result_max_chars: int = 2000,
    ):
        self.max_tokens = max_tokens
        self.system_budget = int(max_tokens * system_ratio)
        self.history_budget = int(max_tokens * history_ratio)
        self.rag_budget = int(max_tokens * rag_ratio)
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
        rag_results: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """组装上下文，返回 {system, messages, rag, token_usage}。"""
        # 1) 系统提示
        sys_tokens = self.estimate_tokens(system_prompt)
        if sys_tokens > self.system_budget:
            system_prompt = system_prompt[: int(self.system_budget * 4)] + "...[truncated]"
            logger.warning("[context] 系统提示超出预算，已截断")

        # 2) 滑动窗口截断历史
        truncated_messages = messages[-self.max_turns * 2:] if len(messages) > self.max_turns * 2 else messages
        hist_tokens = sum(self.estimate_tokens(m.get("content", "")) for m in truncated_messages)

        # 3) RAG 引用注入
        rag_text = ""
        rag_tokens = 0
        if rag_results:
            rag_parts = []
            for i, r in enumerate(rag_results):
                snippet = r.get("snippet", "")
                source = r.get("source", "")
                page = r.get("page", 0)
                part = f"[引用{i+1}] {source} (第{page}页):\n{snippet}"
                rag_parts.append(part)
            rag_text = "\n\n".join(rag_parts)
            rag_tokens = self.estimate_tokens(rag_text)
            if rag_tokens > self.rag_budget:
                # 截断 RAG 结果
                while rag_tokens > self.rag_budget and rag_parts:
                    rag_parts.pop()
                    rag_text = "\n\n".join(rag_parts)
                    rag_tokens = self.estimate_tokens(rag_text)

        # 4) 总预算检查
        total = sys_tokens + hist_tokens + rag_tokens
        if total > self.max_tokens:
            # 压缩历史
            excess = total - self.max_tokens
            while hist_tokens > self.history_budget * 0.5 and excess > 0:
                if truncated_messages:
                    removed = truncated_messages.pop(0)
                    hist_tokens -= self.estimate_tokens(removed.get("content", ""))
                    excess -= self.estimate_tokens(removed.get("content", ""))
                else:
                    break

        return {
            "system": system_prompt,
            "messages": truncated_messages,
            "rag_context": rag_text,
            "token_usage": {
                "system": sys_tokens,
                "history": hist_tokens,
                "rag": rag_tokens,
                "total": sys_tokens + hist_tokens + rag_tokens,
                "budget": self.max_tokens,
            },
        }

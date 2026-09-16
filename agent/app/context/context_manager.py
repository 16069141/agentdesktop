"""上下文管理（token 预算 + 滑动窗口 + 工具结果截断）。

规格书 §9.5：
- 系统提示 + 历史 + 生成（比例可配）
- 滑动窗口默认 20 轮
- 超长工具结果截断/摘要后入上下文
"""
import json
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

    def summarize_tool_result(self, result: str) -> str:
        """超长工具结果 → 结构化摘要（L3：省 token 且防模型幻觉）。

        - JSON 对象/数组：保持结构骨架，逐字段截断，最终序列化长度 ≤ 上限；
        - 纯文本：保留头部（通常含结论）与尾部（通常含报错），中间省略；
        - 结果末尾附「完整内容读取指引」，模型可自行决定是否需要按需读取。
        """
        limit = self.tool_result_max_chars
        if len(result) <= limit:
            return result

        stripped = result.lstrip()
        if stripped[:1] in "[{":
            try:
                data = json.loads(result)
                obj = self._summarize_json(data)
                s = json.dumps(obj, ensure_ascii=False)
                if len(s) <= limit:
                    return s
                # 仍超限：压缩顶层字段值（键名保留），再不行折叠为字段清单
                if isinstance(obj, dict):
                    obj2 = {
                        str(k): self._summarize_json(v, 80)
                        for k, v in list(obj.items())[:12]
                    }
                    s2 = json.dumps(obj2, ensure_ascii=False)
                    if len(s2) <= limit:
                        return s2
                    return json.dumps(
                        {"_fields": [str(k) for k in obj.keys()], "_note": "值过长已省略"},
                        ensure_ascii=False,
                    )
            except Exception:
                pass

        omitted = len(result) - limit
        marker = (
            f"\n...[结果过长，中间省略约 {omitted} 字符；"
            "若需要完整内容，请用相应工具按需读取（如按行/分页读取）]...\n"
        )
        head = max(0, int((limit - len(marker)) * 0.6))
        tail = max(0, limit - len(marker) - head)
        if head + tail >= len(result):
            return result
        return result[:head] + marker + result[-tail:]

    def _summarize_json(self, data: Any, max_str: int = 150, _depth: int = 0) -> Any:
        """递归压缩 JSON 为可序列化对象：保留结构骨架、长字符串截断、深度≥4 折叠。

        返回 Python 对象（dict/list/标量），由调用方在最外层序列化。
        """
        if isinstance(data, str):
            return data if len(data) <= max_str else data[:max_str] + f"...[省略 {len(data)-max_str} 字符]"
        if _depth >= 4:
            return "[深层数据已折叠]"
        if isinstance(data, dict):
            return {str(k): self._summarize_json(v, max_str, _depth + 1) for k, v in data.items()}
        if isinstance(data, list):
            n = len(data)
            if n > 6:
                out = [self._summarize_json(x, max_str, _depth + 1) for x in data[:6]]
                out.append(f"...(共 {n} 项，其余省略)")
                return out
            return [self._summarize_json(x, max_str, _depth + 1) for x in data]
        return data

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

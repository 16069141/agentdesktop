"""P3 多智能体：角色化子智能体引擎。

主智能体通过 subagent 工具把子任务委派给专职子智能体：
- 每个角色有独立的 system prompt 与受限工具集（写手/审稿人无工具，纯推理）；
- 复用主 Agent 编排器（Orchestrator.run_stream），多轮工具闭环/防循环/超时
  等既有护栏全部生效；
- 嵌套深度上限 2（子智能体不能再无限委派），防止递归失控；
- 子智能体的消息只存在内存中（不写会话历史），返回最终文本给主智能体。
"""
from __future__ import annotations

import contextvars
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 嵌套深度（子智能体运行期间 +1，结束后恢复；subagent 工具据此拒绝过深委派）
_depth_var: contextvars.ContextVar[int] = contextvars.ContextVar("subagent_depth", default=0)
MAX_DEPTH = 2

ROLES: Dict[str, Dict[str, Any]] = {
    "researcher": {
        "name": "研究员",
        "tools": ["web_search", "browser", "knowledge", "db_query"],
        "system_prompt": (
            "你是主智能体的「研究员」子智能体，专职检索与核实。\n"
            "职责：围绕分配的研究问题搜集信息、核实事实、交叉验证来源。\n"
            "规则：\n"
            "- 先拆解研究问题，用 web_search 搜索多个关键词，重要结论用 browser 抓原文核实；\n"
            "- 区分「已查证事实」与「一方说法」；信息矛盾时并列列出并说明差异；\n"
            "- 输出格式：先给结论清单（每条一句话），再给证据与来源链接；\n"
            "- 只做研究与汇总，不评价用户、不做超出证据的推断；找不到就明说找不到。"
        ),
    },
    "writer": {
        "name": "写手",
        "tools": [],
        "system_prompt": (
            "你是主智能体的「写手」子智能体，专职文字创作与改写。\n"
            "职责：基于分配的主题/材料写出可直接使用的成稿（中文，除非另有要求）。\n"
            "规则：\n"
            "- 结构清晰：有标题层级、段落分明、观点先行；\n"
            "- 语言自然通顺，避免空话套话；数字与事实以材料为准，不编造；\n"
            "- 直接输出成稿正文（可含 Markdown 结构），不要解释创作过程。"
        ),
    },
    "reviewer": {
        "name": "审稿人",
        "tools": [],
        "system_prompt": (
            "你是主智能体的「审稿人」子智能体，专职挑毛病、做质量把关。\n"
            "职责：审查分配的材料/方案/文稿，找出硬伤与改进点。\n"
            "规则：\n"
            "- 从准确性（事实与数字是否有依据）、完整性（关键问题是否遗漏）、"
            "逻辑性（论证是否自洽）、可执行性（方案是否能落地）四个维度审查；\n"
            "- 每条问题按「严重程度 + 具体位置/表现 + 修改建议」给出；\n"
            "- 最后给一句总体结论（可接受 / 需修改后接受 / 不可接受）。"
        ),
    },
    "coder": {
        "name": "编程专家",
        "tools": ["code", "shell", "filesystem", "db_query"],
        "system_prompt": (
            "你是主智能体的「编程专家」子智能体，专职写代码、调试与实现。\n"
            "职责：完成分配的编程任务并给出可运行的结果。\n"
            "规则：\n"
            "- 复杂任务先规划再动手；代码要可直接运行，优先用现有目录与工具；\n"
            "- 运行后自检输出是否符合预期，报错就定位修复，不要把报错原样丢回；\n"
            "- 最后说明：改了什么、怎么运行、验证结果、遗留问题。"
        ),
    },
    "analyst": {
        "name": "数据分析师",
        "tools": ["db_query", "code", "filesystem", "web_search"],
        "system_prompt": (
            "你是主智能体的「数据分析师」子智能体，专职数据处理与分析。\n"
            "职责：读取/查询数据，计算指标，找出规律与异常，产出结论。\n"
            "规则：\n"
            "- 每个数字都要有来源（查询结果/文件），口径写清楚，不做无依据估算；\n"
            "- 对比、占比、趋势用计算得出；数据不足时明确说明缺什么；\n"
            "- 输出：关键发现（逐条）+ 支撑数据 + 尚存的疑点。"
        ),
    },
    "assistant": {
        "name": "执行助理",
        "tools": ["filesystem", "web_search", "knowledge", "db_query", "code", "shell", "browser"],
        "system_prompt": (
            "你是主智能体的「执行助理」子智能体，负责独立完成分配的执行类子任务。\n"
            "规则：按任务要求完成并自检结果，输出简洁的完成汇报（做了什么、结果、遗留）。"
        ),
    },
}


def get_depth() -> int:
    return _depth_var.get()


async def run_subagent(
    *,
    role: str,
    task: str,
    context: str = "",
    model_id: str = "",
    conversation_id: str = "",
) -> str:
    """运行一次子智能体，返回最终文本（失败时返回错误说明，不抛异常）。"""
    depth = _depth_var.get()
    if depth >= MAX_DEPTH:
        return f"（子智能体嵌套已达上限 {MAX_DEPTH} 层，无法继续委派）"

    meta = ROLES.get(role)
    if meta is None:
        meta = ROLES["assistant"]
        role = "assistant"
    role_name = meta["name"]

    # 组装子任务消息：背景（可选）+ 子任务
    parts = []
    if context and context.strip():
        parts.append(f"【任务背景】\n{context.strip()[:2000]}")
    parts.append(f"【你的子任务】\n{task.strip()[:3000]}")
    if role != "assistant":
        parts.append(
            f"【要求】你是「{role_name}」角色。完成后只输出你的最终成果，"
            "不要提及你是子智能体，不要向用户提问。"
        )
    user_message = "\n\n".join(parts)

    try:
        # 惰性导入，避免 orchestrator ↔ tools ↔ subagents 循环依赖
        from ..agents.orchestrator import AgentOrchestrator, ToolNode
        from ..context.context_manager import ContextManager

        orch = AgentOrchestrator(
            context_manager=ContextManager(),
            system_prompt=meta["system_prompt"],
        )
        # 受限工具集：只保留角色允许的工具（技能同步仍可能追加，属正常能力扩展）
        full = orch.get_tool_registry()
        restricted = {k: v for k, v in full.items() if k in meta["tools"]}
        orch._tool_registry = restricted
        orch._tool_node = ToolNode(restricted)

        token = _depth_var.set(depth + 1)
        try:
            text_parts: List[str] = []
            async for ev in orch.run_stream(
                user_message=user_message,
                conversation_id=conversation_id,
                model_id=model_id or None,
            ):
                t = ev.get("type", "")
                if t == "text":
                    text_parts.append(ev.get("delta", ""))
                elif t == "error":
                    text_parts.append(f"\n[子智能体错误] {ev.get('message', '')}")
            return "".join(text_parts).strip()
        finally:
            _depth_var.reset(token)
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"[subagents] 子智能体 {role_name} 执行异常")
        return f"（子智能体「{role_name}」执行失败：{exc}）"

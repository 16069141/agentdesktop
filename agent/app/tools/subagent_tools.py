"""P3 多智能体：subagent 委派工具。

主智能体在任务可拆分时调用本工具，把子任务委派给专职子智能体
（研究员/写手/审稿人/编程专家/数据分析师/执行助理）。

- 模型/会话从 orchestrator 暴露的 contextvar 读取（工具无参数穿透通道）；
- 嵌套深度由 subagents._depth_var 守卫（≤2 层）；
- 一次可并发委派多个（ToolNode 用 asyncio.gather 并发执行工具调用），
  多个 subagent 调用并行运行，总耗时 ≈ 最慢的一个。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ._foundation import BaseTool
from ..subagents import ROLES, get_depth, run_subagent

logger = logging.getLogger(__name__)


class SubagentTool(BaseTool):
    name = "subagent"
    description = (
        "把子任务委派给专职子智能体并行执行，返回该子智能体的最终成果文本。"
        "当任务可以自然拆分成多个相对独立的部分（例如：先调研资料+同时写初稿+"
        "最后审查把关；多来源并行搜集；让编程专家单独实现一个模块）时使用。"
        "可同时发起多个 subagent 调用，它们会并行工作。"
        "角色：researcher(研究员，检索核实)、writer(写手，纯写作)、"
        "reviewer(审稿人，挑毛病提建议)、coder(编程专家，写代码调试)、"
        "analyst(数据分析师，查数算数)、assistant(执行助理，通用执行)。"
        "注意：subagent 结果只是中间产物，你必须在拿到结果后继续汇总、"
        "组织并给出对用户的最终回答。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "role": {
                "type": "string",
                "enum": ["researcher", "writer", "reviewer", "coder", "analyst", "assistant"],
                "description": "子智能体角色",
            },
            "task": {
                "type": "string",
                "description": "交给子智能体的具体子任务指令（一句话到几句话）",
            },
            "context": {
                "type": "string",
                "description": "可选：给子智能体的背景信息（如用户原始需求、已掌握的材料），让它理解大局",
            },
        },
        "required": ["role", "task"],
    }

    async def execute(self, arguments: dict) -> dict:
        role = str(arguments.get("role") or "assistant").strip()
        task = str(arguments.get("task") or "").strip()
        context = str(arguments.get("context") or "").strip()
        if not task:
            return {"success": False, "content": "错误：subagent 需要 task 参数"}

        if get_depth() >= 2:
            return {"success": False, "content": "错误：子智能体嵌套已达上限，请在当前层级直接完成，不要再委派"}

        from ..agents.orchestrator import get_current_conversation, get_current_model

        model_id = get_current_model()
        conversation_id = get_current_conversation()
        role_name = ROLES.get(role, ROLES["assistant"])["name"]

        text = await run_subagent(
            role=role,
            task=task,
            context=context,
            model_id=model_id,
            conversation_id=conversation_id,
        )
        if not text:
            text = "（子智能体未返回内容）"
        # 结果截断防上下文溢出（完整结果仍通过 SSE tool_result 透传）
        summary = text[:6000]
        return {
            "success": True,
            "content": f"[子智能体·{role_name}] 已完成子任务：{task[:100]}\n\n{summary}",
        }

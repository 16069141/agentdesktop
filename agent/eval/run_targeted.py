#!/usr/bin/env python3
"""针对性评测：只跑指定用例（用于快速验证测试用例修正）。

用法: python3 run_targeted.py code_001 multi_001
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AGENT_DATA_DIR", "/Users/caojian/Desktop/agent/workdesktop/agent/data")

logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(name)s | %(message)s")
logger = logging.getLogger(__name__)

from app.agents.orchestrator import AgentOrchestrator  # noqa: E402
from app.context.context_manager import ContextManager  # noqa: E402
from app.rag.pipeline import RAGPipeline  # noqa: E402
from eval.golden_set import GoldenSetEvaluator, create_golden_set  # noqa: E402


TEST_DOC = """公司请假制度

一、请假类型
1. 事假：因个人事务需离岗，提前 1 天在系统提交申请。
2. 病假：凭医院证明，可事后补交，最长连续 30 天。
3. 年假：入职满 1 年享 5 天，满 3 年享 10 天，逐年递增。

二、审批流程
1. 请假 ≤3 天：直属主管审批。
2. 请假 3-7 天：主管 + 部门负责人审批。
3. 请假 >7 天：主管 + 部门负责人 + HR 审批。

三、薪资影响
病假期间发放基本工资的 80%；事假不发放当日工资；年假带薪。
"""


async def setup_rag():
    try:
        pipe = RAGPipeline()
        await pipe.ingest(
            filename="leave_policy.txt",
            content=TEST_DOC.encode("utf-8"),
            doc_id="test-leave-policy",
            meta={"category": "hr", "title": "公司请假制度"},
        )
        return pipe
    except Exception:
        return None


async def run_case(orchestrator, input_text, conv_id):
    tools, text_parts, model_id = [], [], None
    async for event in orchestrator.run_stream(user_message=input_text, conversation_id=conv_id):
        etype = event.get("type", "")
        if etype == "meta":
            model_id = event.get("model_id")
        elif etype == "tool_call":
            tools.append(event.get("name", ""))
        elif etype == "text":
            text_parts.append(event.get("delta", ""))
    return {"tools": tools, "model": model_id, "answer": "".join(text_parts).strip(),
            "turns": len(tools), "token_usage": {}}


async def main():
    target_ids = sys.argv[1:]
    rag = await setup_rag()
    orchestrator = AgentOrchestrator(
        context_manager=ContextManager(max_tokens=16384),
        system_prompt=(
            "你是一个智能助手。需要访问文件、执行命令或查询知识库时，必须调用可用工具。\n"
            "重要：当用户要求分多步完成时，你必须按顺序调用所有需要的工具，"
            "直到所有步骤都完成后再给出最终回答。不要在中途就输出答案。"
        ),
        rag_pipeline=rag,
        approval_callback=lambda p: True,
        allowed_root_dirs=["/Users/caojian/Desktop/agent/workdesktop"],
    )

    all_cases = {c.id: c for c in create_golden_set()}
    targets = [all_cases[i] for i in target_ids if i in all_cases]

    evaluator = GoldenSetEvaluator()
    for c in targets:
        evaluator.add_case(c)

    print(f"运行 {len(targets)} 个用例: {target_ids}")
    summary = await evaluator.run_all(lambda t, cid: run_case(orchestrator, t, cid))

    print(f"\n通过: {summary['passed']}/{summary['total_cases']}")
    for r in summary["details"]:
        status = "✅" if r.passed else "❌"
        print(f"  {status} {r.case_id}: score={r.score}, tools={r.actual_tools}, errors={r.errors}")
        print(f"     回答: {r.actual_answer[:150]}")


if __name__ == "__main__":
    asyncio.run(main())

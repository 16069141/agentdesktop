#!/usr/bin/env python3
"""Phase 3 Golden Set 评测运行器。

直接在进程内驱动 AgentOrchestrator，跑通 6 个基线用例：
  rag_001    - 知识库检索
  shell_001  - Shell 命令执行
  code_001   - 代码分析
  multi_001  - 多工具协作
  routing_001 - 编程模型路由
  routing_002 - 通用模型路由

运行前会向 RAG 库写入一份测试文档（请假制度），供 rag_001 / multi_001 使用。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AGENT_DATA_DIR", "/Users/caojian/Desktop/agent/workdesktop/agent/data")

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-7s %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)
for noisy in ("httpx", "httpcore", "openai", "chromadb", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

from app.agents.orchestrator import AgentOrchestrator  # noqa: E402
from app.context.context_manager import ContextManager  # noqa: E402
from app.providers import get_registry  # noqa: E402
from app.rag.pipeline import RAGPipeline  # noqa: E402
from eval.golden_set import GoldenSetEvaluator, create_golden_set  # noqa: E402


# 测试文档：模拟公司请假制度，供 RAG 检索用例使用
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


async def setup_rag() -> RAGPipeline | None:
    """初始化 RAG 并写入测试文档。失败返回 None（降级模式）。"""
    try:
        pipe = RAGPipeline()
        await pipe.ingest(
            filename="leave_policy.txt",
            content=TEST_DOC.encode("utf-8"),
            doc_id="test-leave-policy",
            meta={"category": "hr", "title": "公司请假制度"},
        )
        logger.info("[rag] 测试文档已入库")
        return pipe
    except Exception as exc:
        logger.warning(f"[rag] 初始化失败，降级为无 RAG 模式: {exc}")
        return None


async def run_case(orchestrator: AgentOrchestrator, input_text: str, conv_id: str) -> dict:
    """运行单个用例，收集工具调用与回答。"""
    tools: list[str] = []
    text_parts: list[str] = []
    model_id: str | None = None

    async for event in orchestrator.run_stream(user_message=input_text, conversation_id=conv_id):
        etype = event.get("type", "")
        if etype == "meta":
            model_id = event.get("model_id")
        elif etype == "tool_call":
            tools.append(event.get("name", ""))
        elif etype == "text":
            text_parts.append(event.get("delta", ""))
        elif etype == "error":
            logger.error(f"[eval] 用例错误: {event.get('message')}")

    answer = "".join(text_parts).strip()
    return {
        "tools": tools,
        "model": model_id,
        "answer": answer,
        "turns": len(tools),
        "token_usage": {},
    }


async def main():
    print("=" * 72)
    print("Phase 3 Golden Set 评测")
    print("=" * 72)

    # 1) RAG 准备
    rag = await setup_rag()

    # 2) 编排器
    orchestrator = AgentOrchestrator(
        context_manager=ContextManager(max_tokens=16384),
        system_prompt=(
            "你是一个智能助手。需要访问文件、执行命令或查询知识库时，必须调用可用工具，"
            "不要凭空编造结果。工具返回后基于真实结果作答。"
        ),
        rag_pipeline=rag,
        approval_callback=lambda p: True,
        allowed_root_dirs=["/Users/caojian/Desktop/agent/workdesktop"],
    )

    # 3) 评测
    evaluator = GoldenSetEvaluator()
    for case in create_golden_set():
        evaluator.add_case(case)

    print(f"\n用例数: {len(evaluator.cases)}")
    print("开始运行（每个用例约 30-120s）...\n")

    started = time.time()
    summary = await evaluator.run_all(
        lambda text, cid: run_case(orchestrator, text, cid)
    )
    elapsed = time.time() - started

    # 4) 报告
    print("\n" + "=" * 72)
    print("评测结果")
    print("=" * 72)
    print(f"通过: {summary['passed']}/{summary['total_cases']} "
          f"({summary['pass_rate']*100:.0f}%)")
    print(f"平均得分: {summary['avg_score']}")
    print(f"总耗时: {elapsed:.1f}s")

    print("\n详细:")
    for r in summary["details"]:
        status = "✅" if r.passed else "❌"
        print(f"  {status} {r.case_id}: score={r.score}, tools={r.actual_tools}, "
              f"model={r.actual_model}, turns={r.turns}")
        for err in r.errors:
            print(f"       ⚠ {err}")

    # 5) 写入报告
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "golden_set_result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "summary": {k: v for k, v in summary.items() if k != "details"},
            "details": [
                {
                    "case_id": r.case_id,
                    "passed": r.passed,
                    "score": r.score,
                    "actual_tools": r.actual_tools,
                    "actual_model": r.actual_model,
                    "turns": r.turns,
                    "errors": r.errors,
                    "answer": r.actual_answer[:300],
                }
                for r in summary["details"]
            ],
        }, f, ensure_ascii=False, indent=2)
    print(f"\n报告已写入: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())

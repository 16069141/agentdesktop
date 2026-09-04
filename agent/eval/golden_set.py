"""Agent 评估框架（golden set 基线）。

规格书 §11：
- 评测集：输入 → 期望工具调用序列/答案
- 指标：任务完成率、工具调用准确率、RAG Top-5 命中率、平均轮次
- 基线：原型中 4 场景（rag / shell / code / multi）
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class EvalCase:
    """单个评测用例。"""
    id: str
    input_text: str
    expected_tools: List[str] = field(default_factory=list)  # 期望调用的工具名列表
    expected_model: Optional[str] = None  # 期望使用的模型
    expected_answer_contains: List[str] = field(default_factory=list)  # 答案应包含的关键词
    category: str = "general"  # 评测类别：general / coding / rag / shell
    difficulty: str = "easy"  # 难度：easy / medium / hard


@dataclass
class EvalResult:
    """单个用例的评测结果。"""
    case_id: str
    passed: bool
    score: float  # 0.0 - 1.0
    actual_tools: List[str] = field(default_factory=list)
    actual_model: Optional[str] = None
    actual_answer: str = ""
    errors: List[str] = field(default_factory=list)
    turns: int = 0
    token_usage: Dict[str, int] = field(default_factory=dict)


class GoldenSetEvaluator:
    """Golden Set 评测器。"""

    def __init__(self):
        self.cases: List[EvalCase] = []
        self.results: List[EvalResult] = []

    def add_case(self, case: EvalCase):
        """添加评测用例。"""
        self.cases.append(case)
        logger.info(f"[eval] 添加用例: {case.id} ({case.category}/{case.difficulty})")

    def evaluate_case(
        self,
        case: EvalCase,
        actual_tools: List[str],
        actual_model: Optional[str],
        actual_answer: str,
        turns: int,
        token_usage: Dict[str, int],
    ) -> EvalResult:
        """评测单个用例，返回结果。"""
        errors = []
        score = 1.0

        # 1) 工具调用准确率
        expected_tools = set(case.expected_tools)
        actual_tools_set = set(actual_tools)
        if expected_tools:
            tool_precision = len(expected_tools & actual_tools_set) / max(1, len(actual_tools_set))
            tool_recall = len(expected_tools & actual_tools_set) / max(1, len(expected_tools))
            tool_f1 = 2 * tool_precision * tool_recall / max(0.001, tool_precision + tool_recall)
        else:
            tool_f1 = 1.0 if not actual_tools else 0.5

        if expected_tools and not expected_tools.issubset(actual_tools_set):
            missing = expected_tools - actual_tools_set
            errors.append(f"缺少工具调用: {missing}")
            score *= 0.7

        # 2) 模型路由检查
        if case.expected_model and actual_model != case.expected_model:
            errors.append(f"模型路由错误：期望 {case.expected_model}，实际 {actual_model}")
            score *= 0.8

        # 3) 答案内容检查
        if case.expected_answer_contains:
            answer_lower = actual_answer.lower()
            for keyword in case.expected_answer_contains:
                if keyword.lower() not in answer_lower:
                    errors.append(f"答案缺少关键词: '{keyword}'")
                    score *= 0.9

        # 4) 轮次检查（不应超过最大轮次）
        if turns > 10:
            errors.append(f"轮次过多: {turns}")
            score *= 0.8

        # 5) 最终判定
        passed = len(errors) == 0 and score >= 0.7

        result = EvalResult(
            case_id=case.id,
            passed=passed,
            score=round(score, 2),
            actual_tools=actual_tools,
            actual_model=actual_model,
            actual_answer=actual_answer[:500] + ("..." if len(actual_answer) > 500 else ""),
            errors=errors,
            turns=turns,
            token_usage=token_usage,
        )

        status = "✅ PASS" if passed else "❌ FAIL"
        logger.info(f"[eval] {status} {case.id}: score={score:.2f}, tools={actual_tools}, errors={errors}")

        return result

    def run_all(self, agent_runner) -> Dict[str, Any]:
        """运行全量评测。

        Args:
            agent_runner: 异步函数，接收 (input_text, conversation_id, model_id) 返回结果
        """
        results = []
        start_time = time.time()

        for case in self.cases:
            try:
                result = agent_runner(case)
                results.append(result)
            except Exception as exc:
                logger.error(f"[eval] 用例 {case.id} 执行失败: {exc}")
                results.append(EvalResult(
                    case_id=case.id,
                    passed=False,
                    score=0.0,
                    errors=[f"执行异常: {exc}"],
                ))

        elapsed = time.time() - start_time

        # 统计汇总
        total = len(results)
        passed = sum(1 for r in results if r.passed)
        avg_score = sum(r.score for r in results) / max(1, total)

        # 按类别统计
        by_category = {}
        for r in results:
            case = next((c for c in self.cases if c.id == r.case_id), None)
            if case:
                cat = case.category
                if cat not in by_category:
                    by_category[cat] = {"total": 0, "passed": 0, "scores": []}
                by_category[cat]["total"] += 1
                if r.passed:
                    by_category[cat]["passed"] += 1
                by_category[cat]["scores"].append(r.score)

        for cat in by_category:
            by_category[cat]["pass_rate"] = round(
                by_category[cat]["passed"] / max(1, by_category[cat]["total"]), 2
            )
            by_category[cat]["avg_score"] = round(
                sum(by_category[cat]["scores"]) / max(1, len(by_category[cat]["scores"])), 2
            )

        summary = {
            "total_cases": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round(passed / max(1, total), 2),
            "avg_score": round(avg_score, 2),
            "elapsed_sec": round(elapsed, 2),
            "by_category": by_category,
            "details": results,
        }

        logger.info(f"[eval] 评测完成: {passed}/{total} 通过, 平均得分 {avg_score:.2f}, 耗时 {elapsed:.1f}s")
        return summary

    def generate_report(self) -> str:
        """生成评测报告。"""
        if not self.results:
            return "暂无评测结果"

        lines = [
            "=" * 60,
            "Agent 评测报告（Golden Set Baseline）",
            "=" * 60,
            "",
        ]

        # 汇总
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        avg_score = sum(r.score for r in self.results) / max(1, total)

        lines.append(f"总用例数: {total}")
        lines.append(f"通过数: {passed}")
        lines.append(f"失败数: {total - passed}")
        lines.append(f"通过率: {passed/total*100:.1f}%")
        lines.append(f"平均得分: {avg_score:.2f}")
        lines.append("")

        # 按类别
        lines.append("─" * 40)
        lines.append("按类别统计:")
        lines.append("─" * 40)
        by_cat = {}
        for r in self.results:
            case = next((c for c in self.cases if c.id == r.case_id), None)
            if case:
                cat = case.category
                if cat not in by_cat:
                    by_cat[cat] = {"total": 0, "passed": 0, "scores": []}
                by_cat[cat]["total"] += 1
                if r.passed:
                    by_cat[cat]["passed"] += 1
                by_cat[cat]["scores"].append(r.score)

        for cat, data in sorted(by_cat.items()):
            pass_rate = data["passed"] / max(1, data["total"]) * 100
            avg = sum(data["scores"]) / max(1, len(data["scores"]))
            lines.append(f"  {cat}: {data['passed']}/{data['total']} 通过 ({pass_rate:.0f}%), 平均分 {avg:.2f}")

        lines.append("")

        # 详细结果
        lines.append("─" * 40)
        lines.append("详细结果:")
        lines.append("─" * 40)
        for r in self.results:
            case = next((c for c in self.cases if c.id == r.case_id), None)
            status = "✅" if r.passed else "❌"
            cat = case.category if case else "?"
            lines.append(f"  {status} [{cat}] {r.case_id}: score={r.score}")
            if r.errors:
                for e in r.errors:
                    lines.append(f"      错误: {e}")
            lines.append(f"      工具: {r.actual_tools}")
            lines.append(f"      模型: {r.actual_model}")
            lines.append(f"      轮次: {r.turns}")

        lines.append("")
        lines.append("=" * 60)

        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────
# Golden Set 基准用例（对齐原型 4 场景）
# ──────────────────────────────────────────────────────────────────────

def create_golden_set() -> List[EvalCase]:
    """创建 Golden Set 基准用例。"""
    cases = []

    # 场景 1: RAG 检索
    cases.append(EvalCase(
        id="rag_001",
        input_text="帮我查一下公司请假制度文档",
        expected_tools=["knowledge"],
        expected_answer_contains=["请假", "制度", "流程"],
        category="rag",
        difficulty="easy",
    ))

    # 场景 2: Shell 命令
    cases.append(EvalCase(
        id="shell_001",
        input_text="列出当前目录的文件",
        expected_tools=["shell"],
        expected_answer_contains=["文件", "目录"],
        category="shell",
        difficulty="easy",
    ))

    # 场景 3: 代码分析
    cases.append(EvalCase(
        id="code_001",
        input_text="分析这段 Python 代码的复杂度",
        expected_tools=["code"],
        expected_answer_contains=["复杂度", "时间", "空间"],
        category="coding",
        difficulty="medium",
    ))

    # 场景 4: 多工具调用
    cases.append(EvalCase(
        id="multi_001",
        input_text="查找项目进展文档并总结关键内容",
        expected_tools=["knowledge", "filesystem"],
        expected_answer_contains=["总结", "进展"],
        category="multi",
        difficulty="hard",
    ))

    # 场景 5: 模型路由 - 编程
    cases.append(EvalCase(
        id="routing_001",
        input_text="帮我写一个快速排序的 Python 实现",
        expected_model="deepseek-coder:6.7b",
        expected_answer_contains=["排序", "快速"],
        category="routing",
        difficulty="medium",
    ))

    # 场景 6: 模型路由 - 通用
    cases.append(EvalCase(
        id="routing_002",
        input_text="介绍一下机器学习的基本概念",
        expected_model="qwen2.5:7b",
        expected_answer_contains=["机器学习", "概念"],
        category="routing",
        difficulty="easy",
    ))

    return cases

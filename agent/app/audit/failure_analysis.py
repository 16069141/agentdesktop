"""失败模式分析 + 修正规则生成（L4 数据闭环）。

从审计库（audit_logs）聚合工具调用失败模式：
- 各工具调用数 / 失败数 / 失败率
- 失败工具的主要错误类型分布
- 产出「修正规则」文本，动态注入 system prompt（chat.py _build_system_prompt），
  让模型在动手前就知道近期高频踩坑点，直接避开。

缓存：规则文本 TTL 5 分钟（SQLite 查询很快，但每次对话都查没必要）。
"""
from __future__ import annotations

import json
import logging
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# 失败判定的特征串（result_preview 是脱敏+截断后的预览）
_FAIL_MARKERS = (
    '"success": false',
    '"[工具执行失败]"',
    '"error":',
    '工具执行失败',
    '错误：',
    'not_registered',
)

# 规则文本缓存：{ts, text}，TTL 秒
_CACHE: Dict[str, Any] = {"ts": 0.0, "text": ""}
_CACHE_TTL = 300
_MAX_RULES = 6


def _is_failure(result_preview: str) -> bool:
    return any(m in result_preview for m in _FAIL_MARKERS)


def _err_kind(result_preview: str) -> str:
    """把失败结果归类为可读的错误类型（用于聚合 top 原因）。"""
    low = result_preview.lower()
    if any(k in low for k in ("仅允许单条语句", "禁止分号", "分号拼接", "syntax error")):
        return "SQL 校验拒绝（单条语句/分号）"
    if any(k in low for k in ("not_found", "no such file", "does not exist", "不存在", "未能找到")):
        return "路径/对象不存在"
    if any(k in low for k in ("permission", "denied", "越权", "outside allowed", "not authorized", "forbidden")):
        return "权限/越权"
    if any(k in low for k in ("timeout", "timed out", "超时")):
        return "超时"
    if any(k in low for k in ("connection", "network", "refused", "unreachable", "连接失败")):
        return "网络/连接"
    if any(k in low for k in ("invalid", "bad request", "400", "422", "语法", "syntax", "未配置", "不能为空", "required")):
        return "参数/配置不合法"
    if "not_registered" in low:
        return "工具未注册"
    return "其他"


async def analyze_failures(limit: int = 300) -> Dict[str, Any]:
    """分析最近 limit 条**真实工具调用**审计记录（排除 health: 探活噪音）。

    返回 {total, per_tool, ranked}。
    """
    from ..storage.db import connect

    db = await connect()
    try:
        cur = await db.execute(
            "SELECT tool_name, params_redacted, result_preview, created_at "
            "FROM audit_logs WHERE tool_name NOT LIKE 'health:%' "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
    finally:
        await db.close()

    per_tool: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"calls": 0, "fails": 0, "errors": Counter(), "sample": ""}
    )
    for r in rows:
        tool = r["tool_name"] or "?"
        st = per_tool[tool]
        st["calls"] += 1
        rp = r["result_preview"] or ""
        if _is_failure(rp):
            st["fails"] += 1
            st["errors"][_err_kind(rp)] += 1
            if not st["sample"]:
                st["sample"] = rp[:120]
    ranked = sorted(per_tool.items(), key=lambda kv: -kv[1]["fails"])
    return {"total": len(rows), "per_tool": per_tool, "ranked": ranked}


async def build_correction_rules(limit: int = 300, force: bool = False) -> str:
    """从失败模式生成「修正规则」文本（注入 system prompt 用）。

    只有有意义的高频失败才生成规则：失败次数 ≥2，或失败率 ≥30% 且至少失败 1 次。
    文本精简（≤ ~600 字符），避免挤占模型上下文。
    """
    now = time.time()
    if not force and _CACHE["text"] and now - _CACHE["ts"] < _CACHE_TTL:
        return _CACHE["text"]

    try:
        stats = await analyze_failures(limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[failure_analysis] 分析失败（不阻断对话）: %s", exc)
        return ""

    lines: List[str] = []
    for tool, st in stats["ranked"]:
        if st["calls"] == 0:
            continue
        rate = st["fails"] / st["calls"]
        if st["fails"] >= 2 or (rate >= 0.3 and st["fails"] >= 1):
            top_err = st["errors"].most_common(1)[0][0] if st["errors"] else "其他"
            lines.append(
                f"- {tool}：近期失败 {st['fails']}/{st['calls']} 次，"
                f"主要错误「{top_err}」——先按该错误对应的修复建议纠正再执行，"
                "不要原样重试失败命令/参数。"
            )
    if not lines:
        _CACHE.update(ts=now, text="", )
        return ""

    text = "\n\n## 近期工具失败预警（依据审计自动生成，供参考）\n" + "\n".join(lines[:_MAX_RULES])
    _CACHE.update(ts=now, text=text)
    return text

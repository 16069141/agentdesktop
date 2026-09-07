"""敏感字段脱敏（需求 §2.4：敏感字段不出现在审计与模型上下文中）。

策略：
1. 按配置的字段名掩码（salary / id_card / contract_amount 等）
2. 按常见敏感值正则掩码（身份证号、手机号、银行卡、金额）
3. 嵌套结构递归处理；长文本截断
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

# 默认敏感字段名（命中即掩码）
SENSITIVE_FIELD_NAMES = {
    "password", "passwd", "secret", "token", "api_key", "apikey", "apiKey",
    "access_token", "refresh_token", "client_secret", "authorization",
    "salary", "wage", "bonus", "id_card", "idcard", "id_number", "idno",
    "contract_amount", "amount", "bank_account", "bankcard", "card_number",
    "ssn", "phone", "mobile", "credit_card", "cvv",
}

# 常见敏感值正则
_SENSITIVE_PATTERNS = [
    (re.compile(r"\b\d{17}[\dXx]\b"), "<id_card>"),            # 身份证
    (re.compile(r"\b1[3-9]\d{9}\b"), "<phone>"),               # 手机号
    (re.compile(r"\b\d{16,19}\b"), "<bank_card>"),             # 银行卡
    (re.compile(r"(sk-[A-Za-z0-9]{16,})"), "<api_key>"),       # OpenAI 风格 key
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer <token>"),
]

# 掩码替换后的最大长度
RESULT_PREVIEW_MAX = 500
PARAMS_MAX = 800


def redact_value(value: Any, path: str = "", depth: int = 0) -> Any:
    """递归脱敏。返回脱敏后的结构。"""
    if depth > 8:
        return "<nested>"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if str(k).lower() in SENSITIVE_FIELD_NAMES:
                out[k] = "<redacted>"
            else:
                out[k] = redact_value(v, f"{path}.{k}", depth + 1)
        return out
    if isinstance(value, list):
        return [redact_value(v, path, depth + 1) for v in value]
    if isinstance(value, str):
        return redact_string(value)
    return value


def redact_string(text: str) -> str:
    """对字符串做敏感值正则掩码。"""
    for pattern, repl in _SENSITIVE_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def redact_json(value: Any) -> str:
    """脱敏并序列化为字符串（用于审计参数列）。"""
    try:
        return json.dumps(redact_value(value), ensure_ascii=False)[:PARAMS_MAX]
    except Exception:
        return "{}"


def redact_result(result: Any) -> str:
    """审计结果预览：脱敏 + 截断。"""
    if isinstance(result, dict):
        text = json.dumps(redact_value(result), ensure_ascii=False)
    else:
        text = redact_string(str(result))
    if len(text) > RESULT_PREVIEW_MAX:
        text = text[:RESULT_PREVIEW_MAX] + "…[truncated]"
    return text


def risk_level_for(tool_name: str, arguments: Dict[str, Any]) -> str:
    """按工具与参数判定风险等级（low / medium / high）。"""
    if tool_name == "shell":
        return "high"
    if tool_name == "filesystem":
        action = str(arguments.get("action", ""))
        return "high" if action == "write" else "medium"
    arg_text = str(arguments).lower()
    if any(k in arg_text for k in ("delete", "remove", "drop", "truncate", "update ")):
        return "high"
    return "low"

"""Skill 标准包结构与 manifest 解析（需求 §2.7 标准包结构规范）。

标准包结构：
    skill-name/
      SKILL.md          — 人类可读说明（可选）
      manifest.json     — 元信息：版本、安全等级、权限声明、依赖项、工具定义
      scripts/          — 可执行脚本（可选）
      references/       — 参考资料（可选）
      examples/         — 示例（可选）

manifest 必填字段：name / version / security_level
可选字段：description / permissions / dependencies / tools / entry
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

REQUIRED_MANIFEST_FIELDS = ["name", "version", "security_level"]
ALLOWED_SOURCES = {"builtin", "market", "dialog", "private"}

# 允许出现在包根目录的文件/目录（防止路径穿越与无关文件进入）
ALLOWED_PACKAGE_ENTRIES = {
    "SKILL.md", "manifest.json", "scripts", "references", "examples",
    "README.md", "LICENSE", "icons",
}


def parse_manifest(text: str) -> Dict[str, Any]:
    """解析 manifest.json 文本，做字段校验。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"manifest.json 不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("manifest.json 顶层必须是 JSON 对象")

    missing = [f for f in REQUIRED_MANIFEST_FIELDS if f not in data]
    if missing:
        raise ValueError(f"manifest 缺少必填字段: {', '.join(missing)}")

    data.setdefault("description", "")
    data.setdefault("permissions", {})
    data.setdefault("dependencies", {})
    data.setdefault("tools", [])
    return data


def load_manifest_from_dir(package_dir: Path) -> Dict[str, Any]:
    """从包目录读取并校验 manifest.json。"""
    manifest_path = package_dir / "manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"缺少 manifest.json: {package_dir}")
    return parse_manifest(manifest_path.read_text(encoding="utf-8"))


def validate_package_structure(package_dir: Path) -> List[str]:
    """检查包目录结构合法性，返回问题列表（空 = 合法）。"""
    problems: List[str] = []
    if not package_dir.is_dir():
        return ["包目录不存在"]
    for entry in package_dir.iterdir():
        if entry.name in ALLOWED_PACKAGE_ENTRIES:
            continue
        problems.append(f"不允许的包条目: {entry.name}")
    scripts_dir = package_dir / "scripts"
    if scripts_dir.exists() and not scripts_dir.is_dir():
        problems.append("scripts 必须是目录")
    return problems


def dependency_plan(manifest: Dict[str, Any]) -> Dict[str, List[str]]:
    """解析依赖声明 → 安装计划（运行时检测 + 报告缺口）。"""
    deps = manifest.get("dependencies", {}) or {}
    return {
        "python": deps.get("python", []),
        "node": deps.get("node", []),
        "runtimes": deps.get("runtimes", []),
    }

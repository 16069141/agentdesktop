"""技能运行时：把已安装技能动态挂载为 Agent 工具（缺口③）。

技能包（manifest.json + scripts/）安装后统一落在 data/skills/installed/<skill_id>/。
SkillTool 把 manifest.tools[] 的声明转换为 BaseTool：

  - 模型调用时以 **stdin 传参数 JSON**，运行 entry 脚本，读 **stdout JSON**；
  - 按扩展名选解释器（.py -> venv python / .sh -> sh / .js -> node）；
  - 脚本工作目录固定在技能包目录内，entry 解析越界一律拒绝；
  - 超时终止（P1–P3 默认 60s，P4 默认 120s）；
  - P4 技能需审批（approval_callback 缺失时直接拒绝，不静默放行）。

sync_installed_skill_tools() 在 Agent 每次 run_stream 前增量同步：读 skills 表，
把「已启用且有可执行入口」的技能注册进运行时工具表；停用/卸载后下次会话即移除，
无需重启后端。

安全说明：与内置 shell 工具同级信任模型（本机所有者 + 声明式 P1–P4 策略 +
超时 + P4 审批）。免费市场阶段仅允许自有上架包；开放第三方投稿前应增加签名/审核。
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from ._foundation import BaseTool
from ..skills.loader import skills_root

logger = logging.getLogger(__name__)

# 运行时默认值
SKILL_RUNTIME_TIMEOUT_SEC = 60      # P1–P3
SKILL_RUNTIME_TIMEOUT_P4_SEC = 120  # P4（需审批）
OUTPUT_MAX_CHARS = 20000            # stdout 回传上限，防上下文溢出
STDERR_MAX_CHARS = 4000

# 模块级：tool_name -> skill_id，供增量同步（卸载/停用时移除对应工具）
_loaded: Dict[str, str] = {}


def _installed_dir(skill_id: str) -> Optional[Path]:
    d = skills_root() / "installed" / skill_id
    return d if d.is_dir() else None


def _resolve_entry(pkg: Path, entry: str) -> Optional[Path]:
    """把 entry 解析到技能包目录内；越界/不存在返回 None。"""
    try:
        resolved = (pkg / entry).resolve()
        resolved.relative_to(pkg.resolve())
    except (ValueError, OSError):
        return None
    return resolved if resolved.is_file() else None


def _pick_interpreter(script: Path) -> Optional[List[str]]:
    ext = script.suffix.lower()
    if ext == ".py":
        return [sys.executable or "python3"]
    if ext == ".sh":
        return ["sh"]
    if ext == ".js":
        return ["node"]
    return None


class SkillTool(BaseTool):
    """由已安装技能声明的工具（manifest.tools[] 的一项）。"""

    def __init__(
        self,
        skill_id: str,
        tool_name: str,
        description: str,
        parameters: Optional[Dict[str, Any]],
        entry: str,
        security_level: str,
        approval_callback=None,
    ):
        self._skill_id = skill_id
        self.name = tool_name
        self.description = description or f"技能 {skill_id} 提供的工具 {tool_name}"
        self.parameters = parameters or {"type": "object", "properties": {}, "required": []}
        self._entry = entry
        self._security_level = security_level
        self.requires_approval = security_level == "P4"
        self._timeout_sec = (
            SKILL_RUNTIME_TIMEOUT_P4_SEC if self.requires_approval
            else SKILL_RUNTIME_TIMEOUT_SEC
        )
        self._approval_callback = approval_callback

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        # P4：审批门（本地 admin 自动放行；审批服务模式弹窗；无回调一律拒绝）
        if self.requires_approval:
            if self._approval_callback is None:
                return {
                    "success": False,
                    "error": "P4 技能需用户审批，但当前无审批通道，已拒绝执行",
                    "approved": False,
                }
            try:
                approved = await self._approval_callback({
                    "tool": self.name,
                    "skill": self._skill_id,
                    "command": f"技能 {self._skill_id} / {self.name}",
                    "arguments": arguments,
                })
            except Exception as exc:  # noqa: BLE001
                logger.error("[skill] %s 审批调用异常，按拒绝处理: %s", self.name, exc)
                approved = False
            if not approved:
                return {"success": False, "error": "用户未批准该技能调用", "approved": False}

        pkg = _installed_dir(self._skill_id)
        if pkg is None:
            return {"success": False, "error": f"技能 {self._skill_id} 未安装或安装目录缺失"}
        entry = _resolve_entry(pkg, self._entry)
        if entry is None:
            return {"success": False, "error": f"技能入口不存在或越界: {self._entry}"}
        interp = _pick_interpreter(entry)
        if interp is None:
            return {"success": False, "error": f"不支持的脚本类型: {entry.suffix}"}

        try:
            proc = await asyncio.create_subprocess_exec(
                *interp, str(entry),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(pkg),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[skill] %s 启动失败: %s", self.name, exc)
            return {"success": False, "error": f"启动技能失败: {exc}"}

        payload = json.dumps(arguments or {}, ensure_ascii=False)
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=payload.encode("utf-8")),
                timeout=self._timeout_sec,
            )
        except asyncio.TimeoutError:
            proc.kill()
            return {"success": False, "error": f"技能执行超时（{self._timeout_sec}s），已终止"}
        except Exception as exc:  # noqa: BLE001
            logger.error("[skill] %s 执行异常: %s", self.name, exc)
            return {"success": False, "error": f"技能执行异常: {exc}"}

        stdout_s = stdout_b.decode("utf-8", errors="replace")[:OUTPUT_MAX_CHARS]
        stderr_s = stderr_b.decode("utf-8", errors="replace")[:STDERR_MAX_CHARS]

        # 结果契约：stdout 首段合法 JSON 优先解析；否则原样回传文本
        try:
            result = json.loads(stdout_s)
            if isinstance(result, dict):
                if result.get("success") is False:
                    result.setdefault("stderr", stderr_s)
                    return result
                result.setdefault("success", True)
                result.setdefault("stderr", stderr_s)
                return result
            return {"success": True, "output": stdout_s, "stderr": stderr_s}
        except json.JSONDecodeError:
            return {
                "success": proc.returncode == 0,
                "output": stdout_s,
                "stderr": stderr_s,
                "returncode": proc.returncode,
            }


async def sync_installed_skill_tools(
    registry: Dict[str, Any],
    approval_callback=None,
) -> None:
    """增量同步：把「已启用且有可执行入口」的技能挂进运行时工具表。

    registry 为 Agent 的 _tool_registry 字典（ToolNode 持有同一引用）。
    调用失败只记日志，不阻断会话（技能同步失败不应拖垮聊天）。
    """
    global _loaded
    try:
        from ..storage import skills_repo

        skills = await skills_repo.list_all()
    except Exception as exc:  # noqa: BLE001
        logger.error("[skill] 读取技能列表失败，跳过本轮同步: %s", exc)
        return

    desired: Dict[str, SkillTool] = {}
    for rec in skills:
        if not rec.get("enabled"):
            continue
        manifest = rec.get("manifest") or {}
        pkg = _installed_dir(rec["id"])
        if pkg is None:
            continue
        tools = manifest.get("tools") or []
        entry_default = manifest.get("entry") or "scripts/main.py"
        for t in tools:
            tname = t.get("name", "")
            if not tname:
                continue
            entry = t.get("entry") or entry_default
            if _resolve_entry(pkg, entry) is None:
                continue
            desired[tname] = SkillTool(
                skill_id=rec["id"],
                tool_name=tname,
                description=t.get("description", rec.get("description", "")),
                parameters=t.get("parameters"),
                entry=entry,
                security_level=str(
                    rec.get("permissionLevel") or manifest.get("security_level") or "P2"
                ),
                approval_callback=approval_callback,
            )

    # 移除：已加载但不再需要（停用 / 卸载 / 入口缺失 / 版本变化后重建）
    stale = [name for name in _loaded if name not in desired]
    for name in stale:
        registry.pop(name, None)
        _loaded.pop(name, None)

    # 新增/刷新：未注册或指向技能变化时更新
    for name, tool in desired.items():
        if _loaded.get(name) != tool._skill_id or registry.get(name) is None:
            registry[name] = tool
            _loaded[name] = tool._skill_id

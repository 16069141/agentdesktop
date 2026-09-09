"""工具基础设施：基类、落盘目录、文件路径沙箱。

从 tools/__init__.py 下沉为叶子模块，供 __init__ 与各工具子模块
（doc_tools / web_tools …）共用，避免循环导入。
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


def _data_uploads_dir(*sub: str) -> Path:
    """Agent 生成文件的落盘根目录。

    优先使用 AGENT_DATA_DIR（打包 App 中指向用户数据目录，避免写入
    App 包内导致替换时丢失）；开发版回退到 agent/data/uploads。
    """
    env = os.environ.get("AGENT_DATA_DIR")
    if env:
        root = Path(env) / "uploads"
    else:
        root = Path(__file__).resolve().parent.parent.parent / "data" / "uploads"
    if sub:
        root = root.joinpath(*sub)
    root.mkdir(parents=True, exist_ok=True)
    return root


class BaseTool:
    """工具基类。"""

    name: str = "base"
    description: str = "Base tool"
    requires_approval: bool = False

    # OpenAI function-calling 风格的参数 JSON Schema。
    # 没有它，模型就无从知道该传什么参数，工具调用闭环无法成立。
    parameters: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def to_openai_schema(self) -> Dict[str, Any]:
        """转换为 OpenAI/Ollama function-calling 的工具描述。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ── 文件路径沙箱（filesystem / code / doc_to_html 等多工具共用）──────────

# 凭据/密钥类目录：任何动作（含读取）一律禁止，防止私钥、云凭据被模型读出外泄
_CREDENTIAL_DIR_NAMES = {".ssh", ".gnupg", ".aws", ".kube", ".docker"}


def _resolve_in_roots(path_str: str, allowed_roots: list[Path]) -> Path:
    """把路径解析到允许根目录内；越权抛 PermissionError。

    相对路径按 cwd 解析后同样校验；所有允许根目录逐一匹配，
    任一命中即放行（旧实现只查第一个根，多根配置时其余根失效）。
    """
    p = Path(path_str)
    if not p.is_absolute():
        p = p.resolve()
    resolved = p.resolve()
    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            break
        except ValueError:
            continue
    else:
        raise PermissionError(f"path outside allowed roots: {path_str}")

    # 凭据目录保护：路径任意层级命中即拒绝（读也不行）
    if any(part in _CREDENTIAL_DIR_NAMES for part in resolved.parts):
        raise PermissionError(f"凭据/密钥目录禁止访问: {path_str}")
    return p


def _assert_writable(p: Path, allowed_roots: list[Path]) -> None:
    """写保护：禁止写入根目录下一级的隐藏文件/目录。

    ~/.zshrc、~/.bash_profile、~/.ssh/、~/.config/ 等 shell/应用配置
    不属于 Agent 工作产物范围；如确需修改应走 shell 工具并经用户确认。
    工作产物目录（uploads、工作区）均非 dotfile，不受影响。
    """
    resolved = p.resolve()
    for root in allowed_roots:
        try:
            rel = resolved.relative_to(root)
        except ValueError:
            continue
        first = rel.parts[0] if rel.parts else ""
        if first.startswith("."):
            raise PermissionError(
                f"禁止写入隐藏配置文件/目录（{first}）：shell 与应用配置不在 "
                f"Agent 写入范围内；如确需修改请使用 shell 工具并经用户确认"
            )
        return

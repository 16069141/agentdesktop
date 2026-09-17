"""自我认知注入（P0）：让模型知道它运行在颤翎子客户端内、代码库在哪、可改自己的代码。

背景（对齐豆包差距分析 L2）：
- 同题实测中模型给出"教程式"回答而不读代码，根因是 system prompt 从未告诉它
  「这是你自己的代码库，你可以读取并修改」。
- 本模块在每次组装 system prompt 时注入：
  1) 运行环境（dev 仓库 / App 打包）与代码库路径
  2) 代码库关键文件地图（dev 从 AGENTS.md 提取；App 从运行时目录生成）
  3) 行为纪律：客户端内部问题 → 读代码定位修复而非给教程；搜索官方信息纪律
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# 允许通过环境变量显式指定仓库根（App 打包或特殊部署时）
_ENV_REPO_ROOT = "SPIRITCALLER_REPO_ROOT"

# AGENTS.md 中注入上限（行数）：只取架构/协议精华，避免占满 context
_AGENTS_MD_MAX_LINES = 90


def _find_repo_root() -> str:
    """定位客户端代码库根目录。

    优先级：环境变量 > 从本文件上溯找含 AGENTS.md 的目录（dev 仓库）。
    返回 None 表示无法定位（App 打包等场景）。
    """
    env_root = os.environ.get(_ENV_REPO_ROOT)
    if env_root and Path(env_root).is_dir():
        return env_root
    # app/agents/self_awareness.py → 上溯：agent/ → v2/（repo 根）
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "AGENTS.md").is_file():
            return str(parent)
    return ""


def _extract_agents_map(repo_root: str) -> str:
    """从 AGENTS.md 提取架构总览 + 关键协议 + 常用命令（前 N 行）。"""
    agents_md = Path(repo_root) / "AGENTS.md"
    try:
        lines = agents_md.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        logger.warning(f"[self-awareness] 读取 AGENTS.md 失败: {exc}")
        return ""
    picked = []
    for line in lines[:_AGENTS_MD_MAX_LINES]:
        # 跳过空行外的装饰线，保留有信息量的行
        if line.startswith("```") and not picked:
            continue
        picked.append(line)
        if len("".join(picked)) > 2600:  # 硬上限 ~2.6KB
            break
    text = "\n".join(picked).strip()
    return text if text else ""


def _gen_app_map(agent_root: str) -> str:
    """App 打包环境（无 AGENTS.md）：从 agent/ 目录生成两层结构地图。"""
    root = Path(agent_root)
    parts = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirpath_p = Path(dirpath)
        depth = len(dirpath_p.relative_to(root).parts)
        if depth > 2:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "__pycache__", "data", "models"))]
        rel = dirpath_p.relative_to(root)
        prefix = "  " * depth
        parts.append(f"{prefix}{'/' if depth else ''}{rel}/")
        for f in sorted(filenames)[:8]:
            if f.endswith(".py"):
                parts.append(f"{prefix}  {f}")
        if len(parts) > 60:
            break
    return "\n".join(parts[:60])


def code_root() -> str:
    """返回模型可读/可改的客户端代码根（dev=仓库根含 desktop；App=agent 根）。

    供 filesystem 的 allowed_root_dirs 使用，避免「自我认知让模型去读代码、
    却被权限边界拦在门外」的矛盾（App 打包场景代码在 /Applications/...）。
    """
    repo_root = _find_repo_root()
    if repo_root and (Path(repo_root) / "AGENTS.md").is_file():
        return repo_root
    return str(Path(__file__).resolve().parent.parent)


def build_self_awareness_block() -> str:
    """构建注入 system prompt 的自我认知块（恒非空，探测失败也有兜底）。"""
    repo_root = _find_repo_root()
    # dev 模式：仓库根存在且含 AGENTS.md（地图可信）；否则按 App 打包处理
    dev_mode = bool(repo_root) and (Path(repo_root) / "AGENTS.md").is_file()

    if dev_mode:
        code_map = _extract_agents_map(repo_root)
        location = f"代码库根目录：`{repo_root}`（含 AGENTS.md 架构说明）"
    else:
        # App 打包：agent 后端位于 Resources/agent（与 runtime 平级）
        agent_root = str(Path(__file__).resolve().parent.parent)
        code_map = _gen_app_map(agent_root)
        location = f"已打包客户端，后端代码位于 `{agent_root}`（App 内 Resources/agent）"

    map_block = f"\n关键代码位置：\n```\n{code_map}\n```" if code_map else ""

    return (
        "\n\n## 运行环境与自我认知\n"
        f"- 你运行在「颤翎子AI助手」客户端内（本地桌面应用：Electron/React 前端 + FastAPI 后端 "
        "（端口 8766））。用户就在这台电脑前使用你，你的工具能直接操作它的文件系统。\n"
        f"- {location}\n"
        f"{map_block}\n"
        "- **当用户的问题涉及客户端自身行为（报错、模型/服务器连接、设置、功能缺陷、界面、"
        "打包）时，默认这是你自己代码库里的问题：用 filesystem/shell 读取相关代码定位根因并"
        "直接修复，而不是只给建议或教程。**\n"
        "- 改代码前先 Read 相关文件确认现状；改完运行相关测试/重启后端验证，再汇报结果。\n"
        "- 搜索官方信息时优先用 site: 限定官方域名（如讯飞 xfyun.cn）；第一次搜索没有拿到"
        "权威原文必须换关键词或换源重试，禁止用一次失败的结果当结论，也不要把推测说成官方原文。\n"
        "- 修复涉及持久数据或对外行为时，先说明影响再动手；不确定根因时先读代码取证，"
        "不要靠猜。"
    )


def build_tool_routing_block() -> str:
    """工具路由提示（L2）：教模型按场景选工具，减少选错/空转/反复试错。

    纯静态提示文本，不依赖工具实现细节；与「智能体工作法」互补：
    工作法管流程（计划-执行-验证），路由管「这一步用哪个工具」。
    """
    return (
        "\n\n## 工具路由（L2：选对工具再动手）\n"
        "需要动手时按此优先级选择工具，避免选错或空转：\n"
        "1. 读/写/列文件、查目录、找文件 → filesystem（路径不存在时先列出父目录确认真实路径）；\n"
        "2. 运行命令/脚本/服务、查看进程 → shell；定位/分析/修改代码 → code + code_locate；\n"
        "3. 查公司制度、内部资料、历史知识 → knowledge（内部知识检索）；\n"
        "4. 查数据库 → db_query（先用 show tables / describe 摸清表结构再查询，避免裸查报错）；\n"
        "5. 抓取网页内容 → browser（优先官方文档/权威来源）；本地能力能解决的别急着上网；\n"
        "6. 生成图片 → generate_image；生成视频 → generate_video（服务未配置时按返回提示引导用户配置）；\n"
        "7. 任务可拆分为独立部分 → subagent 并行委派；多步骤任务 → create_plan / update_plan；\n"
        "8. 已安装并启用的技能 → 对应技能工具（SkillTool）；\n"
        "9. 以上都不匹配、纯问答/闲聊 → 直接回答，不调用工具。\n"
        "纪律：同一意图不要连续换工具反复试；工具调用失败后按结果里的「修复建议」纠正，"
        "最多重试一次，仍失败就换实现方式或如实告知用户。"
    )


def self_awareness_block_for_test(repo_root: str = "") -> str:
    """供单测直接调用（可指定仓库根）。"""
    if repo_root:
        _old = os.environ.get(_ENV_REPO_ROOT)
        os.environ[_ENV_REPO_ROOT] = repo_root
        try:
            return build_self_awareness_block()
        finally:
            if _old is None:
                os.environ.pop(_ENV_REPO_ROOT, None)
            else:
                os.environ[_ENV_REPO_ROOT] = _old
    return build_self_awareness_block()

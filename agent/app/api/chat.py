"""流式对话接口（SSE）。

协议（对齐规格书 §7.1）：
    event: meta         data: {"message_id": ..., "model_id": ...}
    event: thinking     data: {"delta": "..."}
    event: text         data: {"delta": "..."}
    event: done         data: [DONE]

两条关键工程约束：
1. 每个事件必须以空行（\\n\\n）结尾，且 data 必须是单行 JSON —— 前端按空行切分并缓冲跨 chunk 行。
2. 必须检测客户端断开（用户点「停止生成」），否则生成器会继续空转并往已关闭的连接写数据。

Phase 1 用「回声式」模拟生成验证全链路；Phase 2 接入 LangGraph Agent 后
只需替换 `generate_reply()` 的事件产出，传输层无需改动。
"""

import asyncio
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..storage import ConversationRepo, MessageRepo, usage_log_repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])

# ──────────────────────────────────────────────────────────────
# System Prompt：内容生成排版引导规则
# ──────────────────────────────────────────────────────────────
SYSTEM_PROMPT_CHAT = """你是一个友好、自然的日常聊天助手，名叫「颤翎子」。

## 交互规则
- 用口语化、亲切自然的中文回复，像朋友聊天一样
- 回答简洁轻量，先直接给结论，再视需要补充
- 知识问答、闲聊、思路探讨、普通咨询都保持轻松语气
- 除非用户明确要求，不要主动输出 Markdown 结构、代码块、表格或方案文档
- 不要过度使用工具：只有用户明确要求处理文件、上网、查资料等具体动作时才考虑调用工具
- 联网搜索规则：当用户要求"搜索 / 查一下 / 查最新 / 上网查 / 联网 / 新闻 / 实时信息"时，
  必须调用 web_search 工具联网获取，禁止仅凭内部知识编造最新信息；
  搜索结果中值得展开的页面可用 browser 工具抓取正文
- 当用户明确要求"生成/输出 HTML、PPT、Word、Excel、表格、报告文件"时，必须调用对应工具
  （generate_html / generate_ppt / doc_to_html 等）产出真实文件，并在完成时告知文件路径，不能只用文字假装输出
- 不知道就说不知道，不编造

## 风格
- 简短、有温度、不啰嗦
- 中文简体
"""

SYSTEM_PROMPT = """你是一个专业的内容创作与智能助手，帮助用户完成各类任务。
## 生成 HTML 时的规则
- 优先使用 generate_html 工具，通过模板引擎渲染，不要从零手写 CSS
- 工具会根据你提供的结构化 JSON 内容自动套用 CSS 设计系统
- 可用的模板类型：report（分析报告）、dashboard（数据看板）、comparison（对比分析）、landing（落地页）
- 可用的主题：corporate_blue、tech_dark、minimal_white、warm_earth
- 图表用 Chart.js，在 sections 中使用 type="chart" 的 section，提供 chart 配置 JSON
- 中文排版要求：段落行高 1.75、正文 16px、标题层级清晰、使用无衬线中文字体
- 如果需要生成简单 HTML 页面且无图表需求，可使用 filesystem 工具直接写文件，但必须内联完整 CSS

## 生成 PPT 时的规则
- 使用 generate_ppt 工具，通过结构化 JSON 生成，不要只提供 markdown
- 每页 3-5 条要点，单条不超过 40 字
- 为每页指定 layout 类型：cover（封面）、section（章节页）、content（内容页）、two_column（双栏）、data（数据图表页）、image_text（图文页）、closing（结束页）
- 可用主题：corporate_blue、tech_dark、minimal_white
- 数据页用图表而非文字罗列数字
- 保持视觉一致性：同一份 PPT 使用同一套主题

## 通用规则
- 生成文件后告知用户文件路径
- 内容要有实质信息密度，不要用空话填充
- 中文内容使用简体中文
"""

conv_repo = ConversationRepo()
msg_repo = MessageRepo()

# Agent 编排器懒加载（延迟导入重模块，加速启动）
_agent_orchestrator = None
# 双模式编排器缓存：chat（对话）/ work（工作）各一个独立 system_prompt 实例
_orchestrators: dict[str, Any] = {}

# Shell 审批：Electron 主进程通过本机 HTTP 服务弹出 UI 确认。
# 未配置（如直接跑后端脚本）时安全优先，一律拒绝 —— 与「无回调默认拒绝」策略一致。
APPROVAL_URL = os.environ.get("AGENT_APPROVAL_URL", "").strip()
# 审批服务共享密钥：由 Electron 启动时随机生成并注入，回调必须原样携带，
# 防止本机其他进程扫描到审批端口后伪造批准请求。
APPROVAL_SECRET = os.environ.get("AGENT_APPROVAL_SECRET", "").strip()


async def _request_shell_approval(payload: dict) -> bool:
    """把待执行的 shell 命令发给 Electron 审批服务，返回用户是否允许执行。"""
    command = ((payload or {}).get("command") or "").strip()
    if not APPROVAL_URL or not command:
        logger.warning(
            "[chat] 未配置 AGENT_APPROVAL_URL 或命令为空，拒绝执行 shell 命令（安全优先）"
        )
        return False

    import urllib.request as _urllib

    body = json.dumps({"command": command}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if APPROVAL_SECRET:
        headers["X-Approval-Secret"] = APPROVAL_SECRET
    req = _urllib.Request(
        f"{APPROVAL_URL.rstrip('/')}/approve",
        data=body,
        headers=headers,
        method="POST",
    )

    def _call() -> dict:
        try:
            with _urllib.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.warning(f"[chat] 审批服务调用异常: {exc}")
            return {}

    try:
        result = await asyncio.to_thread(_call)
    except Exception as exc:
        logger.warning(f"[chat] 审批请求异常，拒绝执行: {exc}")
        return False

    approved = bool(result.get("approved"))
    if not approved:
        logger.info("[chat] 用户拒绝执行 shell 命令")
    return approved


def _load_allowed_root_dirs() -> list[str]:
    """读取允许根目录：优先用户设置，缺省为当前用户主目录 + 上传目录。"""
    dirs: list[str] = []
    try:
        from ..api.settings import _load_settings

        dirs = _load_settings().get("allowed_root_dirs") or []
    except Exception as exc:
        logger.warning(f"[chat] 读取 allowed_root_dirs 失败: {exc}")
    if not dirs:
        # 缺省白名单：用户主目录 + 上传落盘目录（模型读取上传的原始文件）
        dirs = [os.path.expanduser("~")]
    uploads = str(Path(__file__).resolve().parent.parent.parent / "data" / "uploads")
    if uploads not in dirs:
        dirs.append(uploads)
    return [os.path.expanduser(d) for d in dirs]


async def _build_system_prompt(key: str) -> str:
    """组装 system prompt：基础提示 + 联网搜索能力 + 已启用只读数据库连接清单。"""
    prompt = SYSTEM_PROMPT_CHAT if key == "chat" else SYSTEM_PROMPT
    try:
        # ── 联网搜索能力说明（web_search / browser 开箱即用）──
        try:
            from ..storage import list_web_search_servers_sync

            search_servers = list_web_search_servers_sync()
            provider_desc = "、".join(
                f"{s.get('provider')}({s.get('name')})" for s in search_servers
            ) or "duckduckgo（免 Key 默认）"
            web_block = (
                "\n\n## 联网搜索能力（web_search / browser 工具）\n"
                "- 需要查询最新信息、新闻、政策、技术资料、外部网站内容时，"
                "使用 web_search 搜索关键词，再用 browser 抓取具体页面正文。\n"
                f"- 当前已配置搜索服务：{provider_desc}。"
                "没有结果或信息不足时可换关键词再搜，必要时直接抓取搜索结果的链接。\n"
                "- 搜索与抓取只访问公网地址；内网/本地地址会被安全拦截。"
            )
            prompt = prompt + web_block
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[chat] 注入联网能力说明失败: {exc}")

        from ..storage import db_connector_repo

        conns = await db_connector_repo.list_all()
        enabled = [c for c in conns if c.get("enabled")]
        if not enabled:
            return prompt
        lines = []
        for c in enabled:
            db_type = c.get("dbType", "sqlite")
            dsn = c.get("dsn", "") or ""
            if db_type == "postgres":
                m = re.search(
                    r"://([^:/@]+):[^@/]+@([^:/]+):(\d+)/([^/]+)", dsn
                )
                if m:
                    summary = (
                        f"PostgreSQL host={m.group(2)}:{m.group(3)} "
                        f"db={m.group(4)} user={m.group(1)}"
                    )
                else:
                    summary = "PostgreSQL（DSN 未解析，直接使用连接 id）"
            else:
                summary = f"SQLite file={dsn.replace('sqlite://', '')}"
            lines.append(f"- id: {c.get('id')}（{c.get('name')}）· {summary}")
        block = (
            "\n\n## 已配置的只读数据库连接（可用 db_query 工具查询，仅 SELECT/WITH/EXPLAIN）\n"
            + "\n".join(lines)
            + "\n当用户提到查询数据库、查表、查数据、看库结构时，"
              "先从上面的连接中选匹配的 id，用 db_query 工具执行只读查询。"
              "\n查询效率硬性要求："
              "① 一次查询尽量取全所需字段，用 information_schema 一条 SQL 拿全部表/列/类型（string_agg 聚合），禁止分批、分页、逐表多次查询；"
              "② 元数据类任务通常 1~2 次 db_query 即可拿够信息；"
              "③ 拿到足够信息后立即产出最终结果（如调用 generate_html 生成报告文件），不得反复追加查询。"
        )
        return prompt + block
    except Exception:
        return prompt


async def _get_orchestrator(mode: str = "chat"):
    """懒加载 Agent 编排器：首次调用时才导入并初始化，避免启动时加载重模块。

    按模式（chat/work）各自持有独立实例：system_prompt 不同，
    会话上下文由 conversations.mode 隔离，互不串扰。
    连接清单指纹：只读数据库连接增删/启停后自动重建 system_prompt，无需重启。
    """
    global _agent_orchestrator
    key = mode if mode in ("chat", "work") else "chat"
    try:
        from ..storage import db_connector_repo, list_web_search_servers_sync, list_mcp_servers_sync

        conns, search_servers, mcp_servers = await asyncio.gather(
            db_connector_repo.list_all(),
            # 同步 sqlite3 读取放线程，避免阻塞事件循环
            asyncio.to_thread(list_web_search_servers_sync),
            asyncio.to_thread(list_mcp_servers_sync),
        )
        fingerprint = (
            tuple(
                (c.get("id"), c.get("name"), c.get("enabled"), c.get("dbType"))
                for c in conns
            ),
            # 联网搜索服务与 MCP 工具缓存变更后同样触发重建（无需重启）
            tuple((s.get("id"), s.get("provider"), s.get("enabled")) for s in search_servers),
            tuple(
                (s.get("id"), s.get("enabled"), len(s.get("tools_cache") or []))
                for s in mcp_servers
            ),
        )
    except Exception:
        fingerprint = None
    cached = _orchestrators.get(key)
    if cached and cached[0] == fingerprint:
        return cached[1]
    from ..agents.orchestrator import AgentOrchestrator
    from ..context.context_manager import ContextManager

    _context_manager = ContextManager(
        max_tokens=32768,
        tool_result_max_chars=2500,
        max_turns=15,
    )
    prompt = await _build_system_prompt(key)

    def _build_orchestrator() -> AgentOrchestrator:
        # 构造链路含同步阻塞 IO：get_registry → build_providers →
        # _resolve_base_url（urllib 探测 /models，每台最多 6s、串行），
        # 以及 init_tools 里的磁盘读取。放线程执行，避免冻结事件循环。
        return AgentOrchestrator(
            context_manager=_context_manager,
            system_prompt=prompt,
            approval_callback=_request_shell_approval,
            allowed_root_dirs=_load_allowed_root_dirs(),
        )

    _agent_orchestrator = await asyncio.to_thread(_build_orchestrator)
    _orchestrators[key] = (fingerprint, _agent_orchestrator)
    return _agent_orchestrator


class Attachment(BaseModel):
    """文档附件：由前端调用 /api/files/upload 解析后随消息提交。"""

    filename: str
    kind: str = "text"  # text / pdf / docx / xlsx
    extracted_text: str
    char_count: int = 0
    saved_path: str = ""  # 上传落盘后的绝对路径（模型可用 filesystem 读取原始文件）


class ChatRequest(BaseModel):
    conversation_id: str
    message: str
    model_id: str
    images: list[str] = []
    attachments: list[Attachment] = []
    mode: str = "chat"


def _sse(event: str, data: Any) -> bytes:
    """格式化单条 SSE 事件。data 为字符串时原样输出（用于 [DONE] 哨兵）。"""
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


async def _generate_reply(
    user_message: str,
    conversation_id: str,
    model_id: str,
    identity=None,
    session_id: str = "",
    images: list[str] | None = None,
    attachment_paths: list[str] | None = None,
    mode: str = "chat",
) -> AsyncIterator[dict]:
    """Phase 3: 使用 AgentOrchestrator 生成真实回复（流式）。"""
    try:
        # 加载会话历史作为多轮上下文（排除刚写入的当前用户消息，由 run_stream 追加）
        history = await msg_repo.list_by_conversation(conversation_id)
        prev_history = [
            {"role": m["role"], "content": m["content"]}
            for m in history[:-1]
            if m["role"] in ("user", "assistant") and m.get("content")
        ]
        async for event in (await _get_orchestrator(mode)).run_stream(
            user_message=user_message,
            conversation_id=conversation_id,
            model_id=model_id,
            history=prev_history,
            identity=identity,
            session_id=session_id,
            images=images,
            attachment_paths=attachment_paths,
        ):
            yield event
    except Exception as exc:
        logger.error(f"[chat] Agent 编排失败: {exc}")
        yield {"type": "error", "message": f"Agent 执行失败: {exc}"}


async def _event_stream(
    request: Request,
    conversation_id: str,
    user_message: str,
    model_id: str,
    assistant_id: str,
    identity=None,
    session_id: str = "",
    images: list[str] | None = None,
    attachments: list[Attachment] | None = None,
    mode: str = "chat",
) -> AsyncIterator[bytes]:
    """产出 SSE 字节流，并在客户端断开时及时停止。"""
    collected: list[str] = []
    aborted = False
    finished = False  # 是否已收到 orchestrator 的 done 事件（正常收尾）
    tool_call_count = 0
    usage_info: Dict[str, Any] = {}
    saved_files_set: list[str] = []  # 本轮全部工具产出文件（持久化到消息 metadata）

    try:
        # 1) meta：告知前端本条助理消息的真实 id
        yield _sse(
            "meta",
            {
                "message_id": assistant_id,
                "conversation_id": conversation_id,
                "model_id": model_id,
                "actor": getattr(identity, "username", "local")
                if identity
                else "local",
            },
        )

        # Phase 3: 使用 AgentOrchestrator 生成真实回复
        try:
            collected.clear()
            attach_paths = [a.saved_path for a in (attachments or []) if a.saved_path]
            async for event in _generate_reply(
                user_message,
                conversation_id,
                model_id,
                identity=identity,
                session_id=session_id,
                images=images,
                attachment_paths=attach_paths or None,
                mode=mode,
            ):
                if await request.is_disconnected():
                    aborted = True
                    break
                etype = event.get("type", "")
                if etype == "text":
                    delta = event.get("delta", "")
                    collected.append(delta)
                    # LLM 本身已是流式（Provider 逐 chunk 产出），直接透传，
                    # 不再人为加 sleep —— 旧实现每分片 sleep 45ms，
                    # 一条 500 分片的回复会凭空多出 20+ 秒。
                    yield _sse("text", {"delta": delta})
                elif etype == "thinking":
                    delta = event.get("delta", "")
                    yield _sse("thinking", {"delta": delta})
                elif etype == "tool_call":
                    # 透传给前端，用于事件时间线展示
                    tool_call_count += 1
                    yield _sse(
                        "tool_call",
                        {
                            "name": event.get("name", ""),
                            "arguments": event.get("arguments", ""),
                        },
                    )
                elif etype == "tool_result":
                    saved = event.get("saved_files", [])
                    if saved:
                        for fp in saved:
                            if fp not in saved_files_set:
                                saved_files_set.append(fp)
                    yield _sse(
                        "tool_result",
                        {
                            "name": event.get("name", ""),
                            "result": event.get("result", ""),
                            "is_error": bool(event.get("is_error", False)),
                            "saved_files": saved,
                        },
                    )
                elif etype == "error":
                    error_msg = event.get("message", "") or ""
                    # 原始错误先落日志（便于排查），再友好化展示给用户
                    logger.error(f"[chat] 模型/Agent 错误事件: {error_msg}")
                    friendly = _friendly_error(error_msg) or "模型调用失败（未返回错误详情）"
                    collected.append(f"\n[错误] {friendly}")
                    yield _sse("text", {"delta": f"\n[错误] {friendly}\n"})
                    aborted = True
                    break
                elif etype == "done":
                    usage_info = event.get("usage", {}) or {}
                    yield _sse(
                        "done", {"message_id": assistant_id, "usage": usage_info}
                    )
                    finished = True
                    break
        except Exception as exc:
            logger.error(f"[chat] Agent 编排异常: {exc}")
            collected.append(f"\n[错误] {exc}")
            yield _sse("text", {"delta": f"\n[错误] {exc}\n"})
            aborted = True

        # 4) done 收尾：
        # - 正常结束（finished，已透传 orchestrator 的 done + 真实 usage）：
        #   不再补发 done 数据帧，避免重复事件/估算 usage 覆盖真实用量；
        # - 中断（aborted，错误事件/异常/客户端断开）：补发 aborted 帧，
        #   前端据此保留已生成的部分内容；
        # - 流被截断（既无 done 也无 error）：兜底补发正常 done，
        #   防止前端永久停在 generating 状态。
        # 三种路径最后都统一发 [DONE] 哨兵结束 SSE 流。
        final_text = "".join(collected)
        if aborted or not finished:
            # 优先透传 Provider 真实用量；缺失时才用估算兜底
            final_usage = (
                usage_info
                if usage_info.get("promptTokens") is not None
                or usage_info.get("completionTokens") is not None
                else _usage(final_text, model_id)
            )
            if aborted:
                yield _sse(
                    "done",
                    {"aborted": True, "message_id": assistant_id,
                     "usage": final_usage},
                )
            else:
                yield _sse(
                    "done", {"message_id": assistant_id, "usage": final_usage}
                )
        yield _sse("done", "[DONE]")

    finally:
        # 落库：即便中途断开（生成器被关闭 / 请求被取消）也会执行到此处。
        # 用 asyncio.shield 保护保存任务：客户端断开会取消请求协程，
        # 若直接 await 保存，写入可能被一同中断而回滚；屏蔽取消后保存任务
        # 仍会在事件循环中跑完，确保「停止生成」时已生成的内容不丢。
        final_text = "".join(collected)
        if final_text.strip():
            save_task = asyncio.ensure_future(
                msg_repo.create(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=final_text,
                    model_id=model_id,
                    metadata={"savedFiles": saved_files_set}
                    if saved_files_set
                    else None,
                )
            )
            try:
                await asyncio.shield(save_task)
            except asyncio.CancelledError:
                # 请求被取消（用户停止），保存任务已在后台继续，不等待其完成
                pass
            except Exception as exc:  # noqa: BLE001
                print(f"[chat] 落库失败: {exc}", flush=True)

        # 用量统计写入（真实 provider usage 优先，缺失时用估算兜底）
        usage_task = asyncio.ensure_future(
            usage_log_repo.insert(
                conversation_id=conversation_id,
                model_id=model_id,
                prompt_tokens=int(usage_info.get("promptTokens", 0) or 0),
                completion_tokens=int(
                    usage_info.get("completionTokens", 0)
                    or max(1, len(final_text) // 2)
                ),
                tool_calls=tool_call_count,
                cost=0.0,
            )
        )
        try:
            await asyncio.shield(usage_task)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            print(f"[chat] usage_log 写入失败: {exc}", flush=True)


def _friendly_error(message: str) -> str:
    """把模型/API 原始错误提炼成用户可读的简短说明。

    原始错误往往是超长 JSON（OpenAI 兼容接口 400/401/429 等），直接展示
    会让用户看到一屏乱码。这里识别常见模式，其余截断到 160 字。
    """
    msg = (message or "").strip()
    if not msg:
        return ""
    low = msg.lower()
    if "invalid json data" in low or "failed to deserialize" in low:
        return "模型返回了无法解析的数据（工具调用历史异常），已自动终止本轮，请重试或换一种问法"
    if "does not support tools" in low:
        return "当前模型不支持工具调用，已自动关闭工具继续回答"
    if "authentication" in low or "unauthorized" in low or "401" in low:
        return "模型服务鉴权失败（API Key 无效或已过期），请在「设置 → 模型服务器」更新密钥"
    if "rate limit" in low or "429" in low:
        return "模型服务请求过于频繁（限流），请稍后重试"
    if "readerror" in low or "read error" in low or "connection reset" in low:
        return "模型服务连接被中断（可能请求过大或服务端超时），已终止本轮；建议把任务拆小重试"
    if "timeout" in low or "timed out" in low:
        return "模型服务响应超时，请检查网络或服务器状态后重试"
    if len(msg) > 160:
        return msg[:160] + "…"
    return msg


def _usage(text: str, model_id: str) -> Dict[str, Any]:
    """占位用量统计（Phase 2 由 Provider 真实返回）。"""
    used = max(1, len(text) // 2)
    total = 16384
    remaining = max(0, total - used)
    return {
        "promptTokens": 0,
        "completionTokens": used,
        "remaining": remaining,
        "total": total,
        "modelId": model_id,
    }


def _build_effective_message(message: str, attachments: list[Attachment]) -> str:
    """把文档附件内容拼接到用户消息前，作为本轮上下文注入。

    格式：
        【附件：report.pdf（PDF，12,345 字符）】
        <提取文本>

        【附件：data.xlsx（Excel，5,678 字符）】
        <提取文本>

        ——— 用户问题 ———
        <原始消息>
    """
    if not attachments:
        return message

    kind_labels = {
        "text": "文本",
        "pdf": "PDF",
        "docx": "Word",
        "xlsx": "Excel",
        "pptx": "PowerPoint",
    }
    blocks: list[str] = []
    for att in attachments:
        label = kind_labels.get(att.kind, att.kind)
        count = att.char_count or len(att.extracted_text or "")
        blocks.append(
            f"【附件：{att.filename}（{label}，{count:,} 字符）】\n"
            f"{(att.extracted_text or '').strip()}"
        )

    prefix = "\n\n".join(blocks)
    if message and message.strip():
        return f"{prefix}\n\n——— 用户问题 ———\n{message.strip()}"
    return prefix + "\n\n——— 用户问题 ———\n请基于以上附件内容进行总结或回答。"


@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    conv = await conv_repo.get(req.conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    if not req.message.strip() and not req.images and not req.attachments:
        raise HTTPException(status_code=400, detail="消息内容不能为空")

    # 身份透传：企业调用方可携带 X-User-* 头；本地桌面默认 local 身份
    from ..auth import parse_identity_headers

    identity = parse_identity_headers(request.headers)

    # 先落用户消息（保存原始文本，不含附件展开内容，避免历史记录臃肿）
    await msg_repo.create(
        conversation_id=req.conversation_id,
        role="user",
        content=req.message,
        model_id=None,
    )

    # 把附件内容拼接到用户消息中，作为本轮生成的上下文
    effective_message = _build_effective_message(req.message, req.attachments or [])

    assistant_id = str(uuid.uuid4())

    return StreamingResponse(
        _event_stream(
            request=request,
            conversation_id=req.conversation_id,
            user_message=effective_message,
            model_id=req.model_id or conv["modelId"],
            assistant_id=assistant_id,
            identity=identity,
            session_id=str(uuid.uuid4())[:12],
            images=req.images or None,
            attachments=req.attachments or [],
            mode=req.mode if req.mode in ("chat", "work") else "chat",
        ),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # 关闭 nginx 类反代的缓冲，否则流式会被攒成一坨
            "X-Accel-Buffering": "no",
        },
    )

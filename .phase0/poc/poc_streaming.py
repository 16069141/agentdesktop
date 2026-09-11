#!/usr/bin/env python3
"""
Phase 0 PoC — Ollama 本地模型 + OpenAI 兼容接口跑通最小流式对话

【定位】独立验证脚本，不进正式工程，不引入 Electron / FastAPI 等完整框架。
【目标】验证两件事：
  1. OpenAI 兼容端点（Ollama /v1/chat/completions）的流式链路可用；
  2. 冻结协议（meta / text / done / error）的 SSE 帧格式可被 curl 正确接收。

【模式】
  # 模式 A：直连流式（默认，验证链路）
  python3 poc_streaming.py --model qwen2.5:7b --prompt "用一句话介绍你自己"

  # 模式 B：启动 SSE 服务（验证协议，配合 curl 验收 DoD）
  python3 poc_streaming.py --serve --port 8765
  curl -N -X POST http://127.0.0.1:8765/api/chat \
       -H 'Content-Type: application/json' \
       -d '{"message":"你好","conversation_id":"poc-1"}'

  # 模式 C：无模型环境也能验证协议（mock 模型，不依赖 Ollama）
  python3 poc_streaming.py --serve --mock

【依赖】默认零依赖（纯标准库）；可选 --sdk 走官方 openai 包。
【参考】DEVELOPMENT-GUIDE.md §7.1（SSE 协议）、§9.1（Provider 层）
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------- 配置

DEFAULT_BASE_URL = "http://localhost:11434/v1"  # Ollama OpenAI 兼容端点
DEFAULT_MODEL = "qwen2.5:7b"
# 本地 Ollama 无鉴权，但协议要求 key 非空，故占位
PLACEHOLDER_KEY = "ollama"

# ---------------------------------------------------------------- 错误码（冻结）

ERROR_CODES = {
    "model_not_found": "请求的模型不存在或未提供",
    "tool_timeout": "工具执行超时",
    "tool_denied": "工具被拒绝执行（未授权或已停用）",
    "context_overflow": "上下文超出 token 预算",
    "rate_limited": "请求频率超限",
    "internal": "服务内部错误",
}


# ---------------------------------------------------------------- Provider（PoC 版）

def _iter_sse_lines(resp):
    """从 HTTPResponse 中逐行产出 SSE 的 data: 载荷（处理跨 chunk 的半包）。"""
    buf = b""
    while True:
        chunk = resp.read1(4096) if hasattr(resp, "read1") else resp.read(4096)
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.decode("utf-8", errors="replace").rstrip("\r")
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload:
                    yield payload
    if buf:
        line = buf.decode("utf-8", errors="replace").strip()
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload:
                yield payload


def stream_chat_stdlib(base_url, model, message, timeout=120):
    """用标准库直连 OpenAI 兼容端点，产出文本增量（零依赖，验证裸协议）。"""
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": message}],
        "stream": True,
    }).encode("utf-8")

    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {PLACEHOLDER_KEY}",
            "Accept": "text/event-stream",
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"无法连接 {url}（{e.reason}）。请确认 Ollama 已启动：ollama serve"
        ) from e

    with resp:
        for payload in _iter_sse_lines(resp):
            if payload == "[DONE]":
                return
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            for choice in obj.get("choices", []):
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    yield delta["content"]


def stream_chat_sdk(base_url, model, message):
    """可选路径：用官方 openai 包（与 Phase 1 的 provider 实现保持一致）。"""
    from openai import OpenAI  # noqa: PLC0415

    client = OpenAI(base_url=base_url, api_key=PLACEHOLDER_KEY)
    stream = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": message}],
        stream=True,
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def stream_chat_mock(message):
    """无模型环境下的替身流：用于验证 SSE 帧格式本身，不代表模型能力。"""
    words = [
        "[mock]", "这是", "一个", "不依赖", "Ollama", "的",
        "替身流", "，仅用于", "验证", "meta/text/done", "事件帧格式", "。",
    ]
    for w in words:
        time.sleep(0.06)
        yield w


def pick_stream(args, message):
    if args.mock:
        return stream_chat_mock(message)
    fn = stream_chat_sdk if args.sdk else stream_chat_stdlib
    return fn(args.base_url, args.model, message)


# ---------------------------------------------------------------- SSE 帧封装（冻结格式）

def sse(event: str, data: dict) -> bytes:
    """按冻结格式封装：data: {json}\\n\\n"""
    return f"data: {json.dumps({**data, 'event': event}, ensure_ascii=False)}\n\n".encode("utf-8")


# ---------------------------------------------------------------- SSE 服务（模式 B/C）

class ChatHandler(BaseHTTPRequestHandler):
    args = None

    def log_message(self, fmt, *a):  # 静音默认访问日志
        pass

    def _send_headers(self, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        # /api/chat 是一次性流：发完 [DONE] 即关闭连接，避免客户端（curl）空等
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")  # 防 nginx 缓冲
        self.end_headers()

    def do_POST(self):
        if self.path != "/api/chat":
            self._send_headers(404)
            self.wfile.write(sse("error", {"code": "internal", "message": "not found"}))
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_headers(400)
            self.wfile.write(sse("error", {"code": "internal", "message": "invalid JSON body"}))
            return

        message = (req.get("message") or "").strip()
        if not message:
            self._send_headers(400)
            self.wfile.write(sse("error", {"code": "internal", "message": "message is required"}))
            return

        conv_id = req.get("conversation_id") or f"poc-{uuid.uuid4().hex[:8]}"
        msg_id = f"msg-{uuid.uuid4().hex[:12]}"
        model = "mock" if self.args.mock else self.args.model

        self._send_headers()

        # 1) meta —— 会话/消息元信息
        self.wfile.write(sse("meta", {
            "message_id": msg_id,
            "model": model,
            "conversation_id": conv_id,
        }))
        self.wfile.flush()

        # 2) text —— 增量文本（流式逐块下发）
        t0 = time.time()
        first_delay_ms = None
        chunks = 0
        try:
            for delta in pick_stream(self.args, message):
                if first_delay_ms is None:
                    first_delay_ms = int((time.time() - t0) * 1000)
                chunks += 1
                self.wfile.write(sse("text", {"delta": delta}))
                self.wfile.flush()
        except Exception as e:  # noqa: BLE001
            self.wfile.write(sse("error", {
                "code": "model_not_found" if "无法连接" in str(e) or "404" in str(e) else "internal",
                "message": str(e),
            }))
            self.wfile.flush()
            return

        # 3) done —— 正常结束
        self.wfile.write(sse("done", {
            "usage": {"prompt_tokens": -1, "completion_tokens": chunks},
            "finish_reason": "stop",
            "first_token_ms": first_delay_ms,
        }))
        self.wfile.flush()

        # 4) [DONE] 哨兵（客户端必须兼容）
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

        print(f"[{conv_id}] 首字 {first_delay_ms}ms / {chunks} 块", file=sys.stderr)


# ---------------------------------------------------------------- 入口

def run_once(args):
    print(f"模型: {args.model if not args.mock else 'mock'}")
    print(f"端点: {args.base_url if not args.mock else '(mock, 不调用模型)'}")
    print(f"提示: {args.prompt}\n" + "-" * 56)
    t0 = time.time()
    first = None
    n = 0
    try:
        for delta in pick_stream(args, args.prompt):
            if first is None:
                first = int((time.time() - t0) * 1000)
            n += 1
            print(delta, end="", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"\n[失败] {e}")
        return 1
    print("\n" + "-" * 56)
    print(f"首字延迟: {first}ms  文本块: {n}  总耗时: {int((time.time()-t0)*1000)}ms")
    return 0


def run_serve(args):
    ChatHandler.args = args
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), ChatHandler)
    mode = "mock" if args.mock else f"{args.base_url} @ {args.model}"
    print(f"SSE 服务已启动: http://127.0.0.1:{args.port}/api/chat  [{mode}]")
    print("验收命令:")
    print(f"  curl -N -X POST http://127.0.0.1:{args.port}/api/chat \\")
    print("       -H 'Content-Type: application/json' \\")
    print("       -d '{\"message\":\"用一句话介绍你自己\"}'")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        srv.server_close()
    return 0


def main():
    p = argparse.ArgumentParser(description="Phase 0 PoC — 最小流式对话验证")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL, help="OpenAI 兼容端点基址")
    p.add_argument("--model", default=DEFAULT_MODEL, help="模型名")
    p.add_argument("--prompt", default="用一句话介绍你自己", help="直连模式的提示词")
    p.add_argument("--sdk", action="store_true", help="使用官方 openai 包（默认用标准库）")
    p.add_argument("--mock", action="store_true", help="使用替身流，不调用真实模型")
    p.add_argument("--serve", action="store_true", help="启动 SSE 服务（供 curl 验收）")
    p.add_argument("--port", type=int, default=8765, help="SSE 服务端口")
    args = p.parse_args()

    return run_serve(args) if args.serve else run_once(args)


if __name__ == "__main__":
    sys.exit(main())

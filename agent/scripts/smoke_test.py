#!/usr/bin/env python3
"""Phase 1 端到端冒烟测试。

直接跑真实服务（子进程），覆盖 DoD：
  1. 后端仅监听 127.0.0.1
  2. 无 token 请求被拒绝（401）
  3. 新建 / 删除 / 切换会话
  4. 发消息收到 SSE 流式回复
  5. 客户端中断（模拟「停止生成」）后已生成内容仍被保存
  6. 重启服务后会话记录不丢失

用法：
    python scripts/smoke_test.py
"""
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import codecs

HOST = "127.0.0.1"
PORT = int(os.environ.get("SMOKE_PORT", "8791"))
BASE = f"http://{HOST}:{PORT}"
TOKEN = secrets.token_hex(32)

AGENT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PY = sys.executable

passed = 0
failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name}{(' — ' + detail) if detail else ''}")


def req(method: str, path: str, body: dict | None = None, token: str | None = TOKEN):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return e.code, raw


def wait_ready(timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE}/healthz", timeout=2):
                return True
        except Exception:
            time.sleep(0.3)
    return False


def sse_events(path: str, body: dict, stop_after_second: float | None = None):
    """以流式方式读取 SSE，逐事件产出；stop_after_second 用于模拟中断。"""
    data = json.dumps(body).encode()
    r = urllib.request.Request(f"{BASE}{path}", data=data, method="POST")
    r.add_header("Content-Type", "application/json")
    r.add_header("Authorization", f"Bearer {TOKEN}")
    r.add_header("Accept", "text/event-stream")

    conn = urllib.request.urlopen(r, timeout=30)
    buf = ""
    started = time.time()
    # 增量解码器：正确处理跨分片的多字节 UTF-8（对齐前端 TextDecoder({stream:true})）
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    try:
        while True:
            chunk = conn.read(64)  # 故意用小缓冲，制造半包 / 粘包场景
            if not chunk:
                break
            buf += decoder.decode(chunk, False)

            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                ev = parse_block(block)
                if ev:
                    yield ev

            if stop_after_second and (time.time() - started) > stop_after_second:
                return
    finally:
        try:
            conn.close()
        except Exception:
            pass


def parse_block(block: str):
    name, data_lines = "", []
    for line in block.split("\n"):
        if not line or line.startswith(":"):
            continue
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            v = line[5:]
            data_lines.append(v[1:] if v.startswith(" ") else v)
    if not data_lines:
        return None
    raw = "\n".join(data_lines)
    if raw.strip() == "[DONE]":
        return ("done", "[DONE]")
    try:
        return (name or "?", json.loads(raw))
    except json.JSONDecodeError:
        return (name or "?", raw)


def start_server():
    env = {**os.environ, "AGENT_TOKEN": TOKEN, "AGENT_HOST": HOST, "AGENT_PORT": str(PORT)}
    proc = subprocess.Popen(
        [PY, "-m", "app.main", "--port", str(PORT)],
        cwd=AGENT_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if not wait_ready():
        proc.kill()
        raise RuntimeError("服务未在 30s 内就绪")
    return proc


def main() -> int:
    print(f"\n=== Phase 1 冒烟测试 (port {PORT}) ===\n")
    proc = start_server()
    try:
        print("[1] 安全与网络基线")
        status, body = req("GET", "/api/conversations", token=None)
        check("无 token 请求返回 401", status == 401, f"实际 {status}")

        status, _ = req("GET", "/api/conversations", token="wrong-token-value")
        check("错误 token 返回 401", status == 401, f"实际 {status}")

        status, body = req("GET", "/healthz", token=None)
        check("/healthz 免鉴权可达", status == 200 and body.get("ok") is True, str(body))

        # 非回环地址不可达：尝试连接本机局域网 IP 上的同端口
        lan = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            lan = s.getsockname()[0]
            s.close()
        except Exception:
            pass
        if lan:
            try:
                with socket.create_connection((lan, PORT), timeout=1.5):
                    reachable = True
            except Exception:
                reachable = False
            check(f"外部地址 {lan}:{PORT} 不可达（仅回环）", not reachable)

        print("\n[2] 会话 CRUD")
        status, conv = req("POST", "/api/conversations", {"title": "冒烟测试会话", "modelId": "qwen2.5:7b"})
        check("新建会话成功", status == 200 and "id" in conv, str(conv))
        conv_id = conv["id"]
        check("会话字段为 camelCase", "modelId" in conv and "createdAt" in conv, str(conv.keys()))

        status, convs = req("GET", "/api/conversations")
        check("会话列表包含新会话", status == 200 and any(c["id"] == conv_id for c in convs))

        print("\n[3] SSE 流式对话")
        events = list(sse_events("/api/chat", {
            "conversation_id": conv_id,
            "message": "你好，这是一条冒烟测试消息",
            "model_id": "qwen2.5:7b",
        }))
        types = [e[0] for e in events]
        check("流中包含 meta 事件", "meta" in types, str(types))
        check("流中包含 thinking 事件", "thinking" in types, str(types))
        check("流中包含多个 text 分片（真流式）",
              types.count("text") > 10, f"text 事件数={types.count('text')}")
        check("流以 [DONE] 哨兵结束", events and events[-1] == ("done", "[DONE]"), str(events[-1:]))

        full = "".join(e[1].get("delta", "") for e in events if e[0] == "text")
        check("正文可完整拼接且含中文", "冒烟测试" in full, full[:60])

        print("\n[4] 消息持久化")
        status, detail = req("GET", f"/api/conversations/{conv_id}")
        msgs = detail.get("messages", [])
        roles = [m["role"] for m in msgs]
        check("用户消息已落库", "user" in roles, str(roles))
        check("助理消息已落库", "assistant" in roles, str(roles))
        check("落库内容与流内容一致",
              any(m["role"] == "assistant" and m["content"] == full for m in msgs))
        check("时间字段为 camelCase", all("createdAt" in m for m in msgs))

        print("\n[5] 中断生成（模拟「停止生成」）")
        status, conv2 = req("POST", "/api/conversations", {"title": "中断测试", "modelId": "qwen2.5:7b"})
        conv2_id = conv2["id"]
        partial_events = list(sse_events("/api/chat", {
            "conversation_id": conv2_id,
            "message": "这条消息会在中途被中断",
            "model_id": "qwen2.5:7b",
        }, stop_after_second=1.2))
        partial = "".join(e[1].get("delta", "") for e in partial_events if e[0] == "text")
        check("中断前已收到部分内容", len(partial) > 0, f"len={len(partial)}")

        time.sleep(1.5)  # 等服务端的 finally 完成落库
        _, d2 = req("GET", f"/api/conversations/{conv2_id}")
        asst = [m for m in d2.get("messages", []) if m["role"] == "assistant"]
        check("中断后部分内容仍被保存", len(asst) == 1 and len(asst[0]["content"]) > 0,
              str([len(m["content"]) for m in asst]))

        print("\n[6] 重启后数据不丢失")
        proc.terminate()
        proc.wait(timeout=10)
        proc = start_server()  # 重启（复用同一个 SQLite 文件）

        status, after = req("GET", f"/api/conversations/{conv_id}")
        check("重启后会话仍存在", status == 200 and after.get("id") == conv_id, f"status={status}")
        check("重启后消息条数不变", len(after.get("messages", [])) == len(msgs),
              f"{len(after.get('messages', []))} vs {len(msgs)}")

        print("\n[7] 删除会话（级联清理消息）")
        status, _ = req("DELETE", f"/api/conversations/{conv2_id}")
        check("删除会话成功", status == 200, f"status={status}")
        status, _ = req("GET", f"/api/conversations/{conv2_id}")
        check("删除后不可再访问", status == 404, f"status={status}")

        db_path = os.path.join(AGENT_DIR, "data", "conversations.db")
        if os.path.exists(db_path):
            import sqlite3
            conn = sqlite3.connect(db_path)
            orphans = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conv2_id,)
            ).fetchone()[0]
            conn.close()
            check("级联删除未留孤儿消息", orphans == 0, f"孤儿消息 {orphans} 条")
        else:
            check("数据库文件存在", False, db_path)

    finally:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            proc.kill()

    print(f"\n=== 结果：{passed} 通过 / {failed} 失败 ===\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

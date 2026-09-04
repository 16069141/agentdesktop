"""PrivateAI Agent —— 本地后端服务入口（FastAPI）。

安全基线（规格书 §10）：
- 仅绑定 127.0.0.1，不对外暴露；
- 除 /healthz 外，所有请求必须携带 Authorization: Bearer <一次性随机 token>；
- token 由 Electron 主进程生成并通过环境变量注入，不落盘、不进日志。
"""
import os
import secrets
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .api import (
    chat_router,
    conversations_router,
    models_router,
    mcp_router,
    knowledge_router,
    usage_router,
    settings_router,
)
from .storage import init_db, init_extensions, seed_builtin_tools, DATA_DIR, DB_PATH

AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "")
AGENT_HOST = os.environ.get("AGENT_HOST", "127.0.0.1")
AGENT_PORT = int(os.environ.get("AGENT_PORT", "8765"))

# 健康检查豁免鉴权：Electron 主进程启动 Agent 后靠它轮询就绪状态
PUBLIC_PATHS = {"/healthz"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动：建库建表 + Phase 2 扩展表 + 预置内置工具
    await init_db()
    await init_extensions()
    await seed_builtin_tools()
    print(f"[agent] 数据目录: {DATA_DIR}", flush=True)
    print(f"[agent] 数据库:   {DB_PATH}", flush=True)
    print(f"[agent] 监听地址: {AGENT_HOST}:{AGENT_PORT}", flush=True)
    print(f"[agent] Token 已配置: {bool(AGENT_TOKEN)}", flush=True)
    yield
    # 关闭：交由 uvicorn / 父进程信号处理


app = FastAPI(
    title="PrivateAI Agent",
    version="0.1.0",
    description="私有域 AI 桌面客户端的本地后端（仅监听 127.0.0.1）",
    lifespan=lifespan,
)


@app.middleware("http")
async def authenticate(request: Request, call_next):
    """Bearer Token 鉴权中间件。

    使用 secrets.compare_digest 做定长比较，避免时序侧信道。
    """
    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)

    if not AGENT_TOKEN:
        # 未注入 token 属于部署错误，直接拒绝而不是放行
        return JSONResponse(
            status_code=503,
            content={"error": "agent_token_not_configured"},
        )

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    provided = auth_header[len("Bearer ") :]
    if not secrets.compare_digest(provided, AGENT_TOKEN):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    return await call_next(request)


# ---------- 路由 ----------
app.include_router(conversations_router)
app.include_router(models_router)
app.include_router(chat_router)
app.include_router(mcp_router)
app.include_router(knowledge_router)
app.include_router(usage_router)
app.include_router(settings_router)


@app.get("/healthz")
async def healthz():
    return {"ok": True, "token_configured": bool(AGENT_TOKEN)}


@app.get("/")
async def root():
    return {"service": "private-ai-agent", "version": "0.1.0"}


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="PrivateAI Agent 本地后端")
    parser.add_argument("--host", default=AGENT_HOST, help="监听地址（仅允许回环）")
    parser.add_argument("--port", type=int, default=AGENT_PORT, help="监听端口")
    args = parser.parse_args()

    # 安全兜底：即便配置被改，也不允许监听到非回环地址
    host = args.host
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"[agent] 拒绝非回环监听地址 {host}，已强制回退到 127.0.0.1", flush=True)
        host = "127.0.0.1"

    uvicorn.run(
        app,
        host=host,
        port=args.port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()

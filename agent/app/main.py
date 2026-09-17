"""PrivateAI Agent —— 本地后端服务入口（FastAPI）。

安全基线（规格书 §10）：
- 仅绑定 127.0.0.1，不对外暴露；
- 除 /healthz 外，所有请求必须携带 Authorization: Bearer <一次性随机 token>；
- token 由 Electron 主进程生成并通过环境变量注入，不落盘、不进日志。
"""

import asyncio
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
    llm_servers_router,
    knowledge_servers_router,
    usage_router,
    settings_router,
    enterprise_router,
    audit_router,
    skills_router,
    connectors_router,
    db_connectors_router,
    webhooks_router,
    projects_router,
    ops_router,
    workflows_router,
    files_router,
    web_search_servers_router,
    mcp_servers_router,
    tasks_router,
    memory_router,
    schedule_router,
    speech_router,
)
from .storage import (
    init_and_seed,
    DATA_DIR,
    DB_PATH,
)

AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "")
AGENT_HOST = os.environ.get("AGENT_HOST", "127.0.0.1")
AGENT_PORT = int(os.environ.get("AGENT_PORT", "8766"))

# 健康检查豁免鉴权：Electron 主进程启动 Agent 后靠它轮询就绪状态
PUBLIC_PATHS = {"/healthz"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动：单连接批量建表 + 迁移 + 播种内置工具/技能/连接器（启动加速）
    await init_and_seed()
    # 重建连接器注册表（配置变更即时生效的权威来源）
    from .connectors import sync_connector_registry

    await sync_connector_registry()
    # P2：后台健康巡检（60s 间隔；断连告警写审计）
    from .api.ops import health_monitor_loop

    stop_health = asyncio.Event()
    health_task = asyncio.create_task(health_monitor_loop(stop_health))
    # P3：工作流后台触发器（schedule / db 轮询）
    from .workflows import workflow_scheduler_loop

    stop_wf = asyncio.Event()
    wf_task = asyncio.create_task(workflow_scheduler_loop(stop_wf))
    # P2：Agent 主动触发调度循环（定时任务）
    from .api.schedule import scheduler_loop

    stop_sched = asyncio.Event()
    sched_task = asyncio.create_task(scheduler_loop(stop_sched))
    print(f"[agent] 数据目录: {DATA_DIR}", flush=True)
    print(f"[agent] 数据库:   {DB_PATH}", flush=True)
    print(f"[agent] 监听地址: {AGENT_HOST}:{AGENT_PORT}", flush=True)
    print(f"[agent] Token 已配置: {bool(AGENT_TOKEN)}", flush=True)
    yield
    stop_health.set()
    try:
        health_task.cancel()
    except Exception:
        pass
    stop_wf.set()
    try:
        wf_task.cancel()
    except Exception:
        pass
    stop_sched.set()
    try:
        sched_task.cancel()
    except Exception:
        pass


app = FastAPI(
    title="PrivateAI Agent",
    version="0.1.0",
    description="颤翎子AI助手（Spiritcaller）桌面客户端的本地后端（仅监听 127.0.0.1）",
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
app.include_router(llm_servers_router)
app.include_router(knowledge_servers_router)
app.include_router(usage_router)
app.include_router(settings_router)
app.include_router(enterprise_router)
app.include_router(audit_router)
app.include_router(skills_router)
app.include_router(connectors_router)
app.include_router(db_connectors_router)
app.include_router(webhooks_router)
app.include_router(projects_router)
app.include_router(ops_router)
app.include_router(workflows_router)
app.include_router(files_router)
app.include_router(web_search_servers_router)
app.include_router(mcp_servers_router)
app.include_router(tasks_router)
app.include_router(memory_router)
app.include_router(schedule_router)
app.include_router(speech_router)


@app.get("/healthz")
async def healthz():
    return {"ok": True, "token_configured": bool(AGENT_TOKEN)}


@app.get("/")
async def root():
    return {"service": "private-ai-agent", "version": "0.1.0"}


def _setup_file_logging() -> None:
    """把后端日志同时写入数据目录 agent.log。

    Windows 打包态 Electron 无控制台（stdout 被丢弃），错误现场只能靠
    落盘日志排查；开发态同样受益。日志位于 {数据目录}/agent.log。
    """
    import logging

    from .storage.db import DATA_DIR

    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        handler = logging.FileHandler(
            os.path.join(DATA_DIR, "agent.log"), encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        logging.getLogger("uvicorn.error").propagate = True
    except Exception as exc:  # noqa: BLE001
        print(f"[agent] 日志文件初始化失败: {exc}", flush=True)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="PrivateAI Agent 本地后端")
    parser.add_argument("--host", default=AGENT_HOST, help="监听地址（仅允许回环）")
    parser.add_argument("--port", type=int, default=AGENT_PORT, help="监听端口")
    args = parser.parse_args()

    _setup_file_logging()  # 日志落盘（Windows 无控制台时排障必需）

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

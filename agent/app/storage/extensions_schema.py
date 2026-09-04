"""Phase 2 扩展建表（tool_registry + usage_log）。

由 main.py lifespan 调用，也可在迁移时单独执行。
"""
import aiosqlite

from .db import connect

EXT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tool_registry (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    description       TEXT NOT NULL,
    source            TEXT NOT NULL,
    enabled           INTEGER DEFAULT 1,
    requires_approval INTEGER DEFAULT 0,
    last_loaded_at    INTEGER,
    last_used_at      INTEGER
);

CREATE TABLE IF NOT EXISTS usage_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id     TEXT NOT NULL,
    model_id            TEXT NOT NULL,
    prompt_tokens       INTEGER NOT NULL,
    completion_tokens   INTEGER NOT NULL,
    tool_calls          INTEGER DEFAULT 0,
    cost                REAL DEFAULT 0,
    created_at          INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_usage_conv
    ON usage_log(conversation_id, created_at);
"""


async def init_extensions() -> None:
    db = await connect()
    try:
        await db.executescript(EXT_SCHEMA_SQL)
        await db.commit()
    finally:
        await db.close()


# 预置内置工具集（幂等 upsert）
BUILTIN_TOOLS = [
    {
        "id": "filesystem",
        "name": "文件系统",
        "description": "文件读写、目录搜索（受限根目录，审计）",
        "source": "builtin",
        "enabled": True,
        "requires_approval": False,
    },
    {
        "id": "shell",
        "name": "Shell 命令",
        "description": "命令执行（白名单 + UI 确认 + 超时 + 全量审计）",
        "source": "builtin",
        "enabled": True,
        "requires_approval": True,
    },
    {
        "id": "knowledge",
        "name": "知识库检索",
        "description": "企业 RAG 检索（带引用溯源）",
        "source": "builtin",
        "enabled": True,
        "requires_approval": False,
    },
    {
        "id": "browser",
        "name": "浏览器自动化",
        "description": "Playwright 自动化（来源白名单）",
        "source": "builtin",
        "enabled": False,
        "requires_approval": False,
    },
    {
        "id": "code",
        "name": "代码分析",
        "description": "Tree-sitter 静态分析（只读）",
        "source": "builtin",
        "enabled": True,
        "requires_approval": False,
    },
]


async def seed_builtin_tools() -> None:
    from .extensions import tool_registry_repo
    for t in BUILTIN_TOOLS:
        await tool_registry_repo.upsert(
            tool_id=t["id"],
            name=t["name"],
            description=t["description"],
            source=t["source"],
            enabled=t["enabled"],
            requires_approval=t.get("requires_approval", False),
        )

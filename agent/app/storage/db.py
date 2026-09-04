"""SQLite 连接与建表。

要点：
- 所有连接强制开启 `PRAGMA foreign_keys = ON`，
  否则 messages 表上的 ON DELETE CASCADE 不会生效，删除会话会留下孤儿消息。
- 数据目录默认落在 agent/data/，可用环境变量 AGENT_DATA_DIR 覆盖。
"""
import os
import aiosqlite

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))

DATA_DIR = os.environ.get("AGENT_DATA_DIR") or os.path.join(_PROJECT_ROOT, "data")
DB_PATH = os.path.join(DATA_DIR, "conversations.db")


def ensure_data_dir() -> str:
    """确保数据目录存在，返回其路径。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    return DATA_DIR


async def connect() -> aiosqlite.Connection:
    """建立连接并开启外键约束与行工厂。"""
    ensure_data_dir()
    db = await aiosqlite.connect(DB_PATH)
    # 外键级联依赖此开关，且是「连接级」设置，每次连接都必须执行
    await db.execute("PRAGMA foreign_keys = ON")
    db.row_factory = aiosqlite.Row
    return db


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    model_id    TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id               TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL
                     REFERENCES conversations(id) ON DELETE CASCADE,
    role             TEXT NOT NULL,
    content          TEXT NOT NULL,
    model_id         TEXT,
    created_at       INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conv
    ON messages(conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_conversations_updated
    ON conversations(updated_at DESC);
"""


async def init_db() -> None:
    """建库建表（幂等，可重复调用）。"""
    ensure_data_dir()
    db = await connect()
    try:
        await db.executescript(SCHEMA_SQL)
        await db.commit()
    finally:
        await db.close()

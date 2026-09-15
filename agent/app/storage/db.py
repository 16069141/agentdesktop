"""SQLite 连接与建表。

要点：
- 所有连接强制开启 `PRAGMA foreign_keys = ON`，
  否则 messages 表上的 ON DELETE CASCADE 不会生效，删除会话会留下孤儿消息。
- 数据目录默认落在 agent/data/，可用环境变量 AGENT_DATA_DIR 覆盖。
"""
import os
import logging
import aiosqlite

logger = logging.getLogger(__name__)

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
    # WAL：读写并发不互斥，避免多请求同时操作时互相锁库
    await db.execute("PRAGMA journal_mode = WAL")
    # busy_timeout：并发写发生锁竞争时等待而非立即报错/挂起
    await db.execute("PRAGMA busy_timeout = 5000")
    db.row_factory = aiosqlite.Row
    return db


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    model_id    TEXT NOT NULL,
    mode        TEXT NOT NULL DEFAULT 'chat',
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
    metadata         TEXT,
    created_at       INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conv
    ON messages(conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_conversations_updated
    ON conversations(updated_at DESC);
"""


async def _column_exists(db, table: str, column: str) -> bool:
    """用 PRAGMA table_info 真实判断列是否存在，替代「try ALTER 吞错」。"""
    async with db.execute(f"PRAGMA table_info({table})") as cur:
        rows = await cur.fetchall()
    return any(r[1] == column for r in rows)


async def _ensure_column(db, table: str, column: str, ddl: str) -> None:
    """幂等补列：先查存在性，缺失才 ALTER；真正失败时打日志，不再静默吞掉。"""
    if await _column_exists(db, table, column):
        return
    try:
        await db.execute(ddl)
        logger.info(f"[db] 迁移：已为 {table} 补列 {column}")
    except Exception as exc:  # noqa: BLE001
        # 列已存在是常见路径（并发/重入），其余失败必须可见
        if "duplicate column" not in str(exc).lower():
            logger.error(f"[db] 迁移 {table}.{column} 失败: {exc}")


async def init_db() -> None:
    """建库建表（幂等，可重复调用）。"""
    ensure_data_dir()
    db = await connect()
    try:
        await db.executescript(SCHEMA_SQL)
        # 轻量迁移：老库补列（显式检查，不再靠 try/except 静默吞错）
        await _ensure_column(db, "messages", "metadata",
                             "ALTER TABLE messages ADD COLUMN metadata TEXT")
        await _ensure_column(db, "conversations", "mode",
                             "ALTER TABLE conversations ADD COLUMN mode TEXT NOT NULL DEFAULT 'chat'")
        await _ensure_column(db, "conversations", "workspace_path",
                             "ALTER TABLE conversations ADD COLUMN workspace_path TEXT")
        await db.commit()
        logger.info(f"[db] 初始化完成: {DB_PATH}")
    finally:
        await db.close()

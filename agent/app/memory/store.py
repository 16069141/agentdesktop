"""长期记忆存储层（P1）。

表结构：
- memories：id(自增) / kind(preference|fact|conclusion) / content /
  source_conversation / created_at / updated_at / hit_count / last_seen_at

检索策略（无外部依赖、跨环境一致）：
- 把查询拆成关键词（中文 2/4 字滑窗 + 英文单词），对 content 做 LIKE 召回；
- 按「命中关键词数 → 偏好优先 → 历史命中 → 最近更新」打分排序取 top-k。
记忆表通常只有几十到几百条，全表 LIKE 足够快，不需要全文索引。
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

from ..storage import connect

logger = logging.getLogger(__name__)

KINDS = ("preference", "fact", "conclusion")
KIND_LABELS = {"preference": "偏好", "fact": "事实", "conclusion": "结论"}
_KIND_ORDER = {"preference": 0, "fact": 1, "conclusion": 2}

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS memories (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    kind                TEXT NOT NULL,
    content             TEXT NOT NULL,
    source_conversation TEXT NOT NULL DEFAULT '',
    created_at          INTEGER NOT NULL,
    updated_at          INTEGER NOT NULL,
    hit_count           INTEGER NOT NULL DEFAULT 0,
    last_seen_at        INTEGER
);
CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC);
"""

_initialized = False


async def _ensure_init() -> None:
    global _initialized
    if _initialized:
        return
    db = await connect()
    try:
        await db.executescript(_TABLE_SQL)
        await db.commit()
        _initialized = True
    finally:
        await db.close()


def _norm(content: str) -> str:
    """去空白/标点/大小写归一，用于近似去重。"""
    s = re.sub(r"[\s，。、；：！？,.!?;:（）()【】\[\]「」『』\"'“”‘’\-—…]+", "", content)
    return s.lower()


def _keyword_terms(query: str) -> List[str]:
    """把查询拆成检索关键词：英文单词(≥3) + 中文连续段（双字 bigram + 4 字滑窗）。

    长句会跨多个分句（标点/空白分隔），每个分句的词都要保留；
    4 字窗比 bigram 更精确，排序靠前；总量封顶 32 个，防止 LIKE 过长。
    """
    s = re.sub(r"[^\w\u4e00-\u9fff]+", " ", query.lower())
    parts = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", s)
    four_grams: List[str] = []
    bigrams: List[str] = []
    words: List[str] = []
    for p in parts:
        if re.fullmatch(r"[a-z0-9]+", p):
            if len(p) >= 3:
                words.append(p)
        elif re.fullmatch(r"[\u4e00-\u9fff]+", p):
            n = len(p)
            if n <= 2:
                bigrams.append(p)
            else:
                bigrams.extend(p[i : i + 2] for i in range(n - 1))
                four_grams.extend(p[i : i + 4] for i in range(n - 3 + 1))
    seen: set = set()
    out: List[str] = []
    # 英文词精确匹配价值最高；bigram 是中文召回主力；4 字窗仅作短语提示，放最后
    for t in [*words, *bigrams, *four_grams]:
        if t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= 32:
            break
    return out


async def add_memory(kind: str, content: str, source_conversation: str = "") -> Dict[str, Any]:
    """新增一条记忆；与已有记忆高度相似时更新原条（去重）并返回它。"""
    await _ensure_init()
    kind = kind if kind in KINDS else "fact"
    content = (content or "").strip()
    if not content:
        return {"id": None, "dup": True}
    now = int(time.time())
    n = _norm(content)

    db = await connect()
    try:
        async with db.execute(
            "SELECT id, content FROM memories WHERE kind=? ORDER BY updated_at DESC LIMIT 30",
            (kind,),
        ) as cur:
            rows = await cur.fetchall()
        for r in rows:
            existing_n = _norm(r["content"])
            if n and (n == existing_n or n in existing_n or existing_n in n):
                await db.execute(
                    "UPDATE memories SET content=?, updated_at=? WHERE id=?",
                    (content, now, r["id"]),
                )
                await db.commit()
                return {"id": r["id"], "dup": True}
        async with db.execute(
            "INSERT INTO memories (kind, content, source_conversation, created_at, updated_at)"
            " VALUES (?,?,?,?,?)",
            (kind, content, source_conversation, now, now),
        ) as cur:
            mid = cur.lastrowid
        await db.commit()
        return {"id": mid, "dup": False}
    finally:
        await db.close()


async def search_memories(query: str, limit: int = 6, kinds: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """关键词召回 + 打分排序检索。"""
    await _ensure_init()
    q = (query or "").strip()
    if not q:
        return []
    terms = _keyword_terms(q)
    if not terms:
        return []

    params: List[str] = [f"%{t}%" for t in terms]
    where = "(" + " OR ".join("content LIKE ?" for _ in terms) + ")"
    kinds_sql = ""
    if kinds:
        ks = [k for k in kinds if k in KINDS]
        if ks:
            kinds_sql = f" AND kind IN ({','.join('?' * len(ks))})"
            params = [*params, *ks]

    async with _conn() as db:
        async with db.execute(
            "SELECT * FROM memories WHERE " + where + kinds_sql + " LIMIT 80",
            params,
        ) as cur:
            rows = await cur.fetchall()

    scored: List[tuple] = []
    for r in rows:
        content = r["content"]
        matched = sum(1 for t in terms if t in content)
        if matched == 0:
            continue
        scored.append(
            (-matched, _KIND_ORDER.get(r["kind"], 2), -r["hit_count"], -r["updated_at"], r)
        )
    scored.sort(key=lambda x: x[:4])
    return [dict(x[4]) for x in scored[:limit]]


async def list_memories(limit: int = 200, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    await _ensure_init()
    sql = "SELECT * FROM memories"
    params: list = []
    if kind in KINDS:
        sql += " WHERE kind=?"
        params.append(kind)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(min(max(limit, 1), 500))
    async with _conn() as db:
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def delete_memory(mid: int) -> bool:
    await _ensure_init()
    db = await connect()
    try:
        async with db.execute("DELETE FROM memories WHERE id=?", (mid,)) as cur:
            deleted = cur.rowcount
        await db.commit()
        return deleted > 0
    finally:
        await db.close()


async def clear_memories() -> int:
    await _ensure_init()
    db = await connect()
    try:
        async with db.execute("SELECT COUNT(*) AS c FROM memories") as cur:
            row = await cur.fetchone()
        total = row["c"] if row else 0
        await db.execute("DELETE FROM memories")
        await db.commit()
        return total
    finally:
        await db.close()


async def bump_memory(mid: int) -> None:
    """检索命中后更新 hit_count / last_seen_at（后台 fire-and-forget）。"""
    await _ensure_init()
    try:
        async with _conn() as db:
            await db.execute(
                "UPDATE memories SET hit_count = hit_count + 1, last_seen_at=? WHERE id=?",
                (int(time.time()), mid),
            )
            await db.commit()
    except Exception:  # noqa: BLE001
        pass


async def count_memories() -> int:
    await _ensure_init()
    async with _conn() as db:
        async with db.execute("SELECT COUNT(*) AS c FROM memories") as cur:
            row = await cur.fetchone()
    return row["c"] if row else 0


from contextlib import asynccontextmanager


@asynccontextmanager
async def _conn():
    """只读/短事务用连接上下文。"""
    db = await connect()
    try:
        yield db
    finally:
        await db.close()

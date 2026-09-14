"""Phase 2 扩展建表（tool_registry + usage_log）。

由 main.py lifespan 调用，也可在迁移时单独执行。
Phase B（P0）在此基础上新增：
- tool_registry 加列：kind / permission_level / version / metadata（统一元数据中心）
- enterprise_users：企业账号与权限（统一认证）
- audit_logs：审计日志（追加只写，安全合规）
- skills：Skill 包注册表（Skill 集成技术方案）
"""

import json
import logging
from datetime import datetime

from .db import connect, SCHEMA_SQL

logger = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


EXT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tool_registry (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    description       TEXT NOT NULL,
    source            TEXT NOT NULL,
    enabled           INTEGER DEFAULT 1,
    requires_approval INTEGER DEFAULT 0,
    last_loaded_at    INTEGER,
    last_used_at      INTEGER,
    -- Phase B (P0)：统一元数据中心扩展字段
    kind              TEXT DEFAULT 'tool',
    permission_level  TEXT DEFAULT 'P2',
    version           TEXT DEFAULT '0.1.0',
    metadata          TEXT
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

-- 模型服务器连接（局域网/互联网大模型服务，纯云端架构的连接管理）
CREATE TABLE IF NOT EXISTS llm_servers (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    base_url       TEXT NOT NULL,
    protocol       TEXT NOT NULL DEFAULT 'ollama',
    api_key_ref    TEXT,
    enabled        INTEGER DEFAULT 1,
    timeout_sec    INTEGER DEFAULT 60,
    models_cache   TEXT,
    last_health_at INTEGER,
    last_health_ok INTEGER,
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL
);

-- 知识库连接（局域网/互联网 RAG 服务、LLM Wiki 等）
CREATE TABLE IF NOT EXISTS knowledge_servers (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    type           TEXT NOT NULL,
    platform       TEXT NOT NULL,
    base_url       TEXT NOT NULL,
    auth_type      TEXT DEFAULT 'api_key',
    api_key_ref    TEXT,
    extra_config   TEXT,
    enabled        INTEGER DEFAULT 1,
    timeout_sec    INTEGER DEFAULT 30,
    last_health_at INTEGER,
    last_health_ok INTEGER,
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL
);

-- ============ Phase B (P0)：企业级安全底座 ============

-- 企业账号（统一认证与权限，需求 §2.3）
CREATE TABLE IF NOT EXISTS enterprise_users (
    id           TEXT PRIMARY KEY,
    username     TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    role         TEXT NOT NULL DEFAULT 'member',      -- admin / manager / member
    data_scope   TEXT NOT NULL DEFAULT 'personal',    -- personal / department / global
    department   TEXT NOT NULL DEFAULT '',
    sso_provider TEXT,                                -- oidc / cas / ldap / none
    sso_subject  TEXT,                                -- SSO 侧主体标识
    status       INTEGER DEFAULT 1,                   -- 1 启用 / 0 停用
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL
);

-- 审计日志（追加只写，需求 §2.4：Who/When/What/Params/Result 完整链路）
CREATE TABLE IF NOT EXISTS audit_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL DEFAULT '',
    session_id      TEXT NOT NULL DEFAULT '',
    actor           TEXT NOT NULL DEFAULT 'local',    -- 用户 / 企业账号
    tool_name       TEXT NOT NULL,
    action          TEXT NOT NULL DEFAULT 'execute',  -- execute / approve / deny / breaker_open
    params_redacted TEXT NOT NULL DEFAULT '{}',       -- 脱敏后参数
    result_preview  TEXT NOT NULL DEFAULT '',         -- 截断结果
    risk_level      TEXT NOT NULL DEFAULT 'low',      -- low / medium / high
    approved        INTEGER,                          -- NULL 不适用 / 1 通过 / 0 拒绝
    latency_ms      INTEGER DEFAULT 0,
    created_at      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_conv   ON audit_logs(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_tool   ON audit_logs(tool_name, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_actor  ON audit_logs(actor, created_at);

-- Skill 包注册表（Skill 集成技术方案，需求 §2.7）
CREATE TABLE IF NOT EXISTS skills (
    id               TEXT PRIMARY KEY,               -- skill 标识（如 pdf-reader）
    name             TEXT NOT NULL,
    version          TEXT NOT NULL DEFAULT '0.1.0',
    description      TEXT NOT NULL DEFAULT '',
    kind             TEXT NOT NULL DEFAULT 'skill',  -- skill / mcp
    permission_level TEXT NOT NULL DEFAULT 'P2',     -- P1-P4 四级安全等级
    source           TEXT NOT NULL DEFAULT 'builtin',-- builtin / market / dialog / private
    source_url       TEXT NOT NULL DEFAULT '',
    manifest         TEXT,                           -- manifest.json 全文
    enabled          INTEGER DEFAULT 1,
    installed_at     INTEGER NOT NULL
);

-- ============ Phase B (P1)：核心连接能力 ============

-- 企业系统连接器实例（需求 §2.1：ERP/CRM/OA 首批，WMS/EAM 等 P4 补齐）
CREATE TABLE IF NOT EXISTS connector_configs (
    id             TEXT PRIMARY KEY,               -- 实例 id（如 conn_erp_prod）
    type           TEXT NOT NULL,                  -- erp / crm / oa
    name           TEXT NOT NULL,
    base_url       TEXT NOT NULL,
    auth_type      TEXT DEFAULT 'api_key',         -- api_key / basic / none
    api_key_ref    TEXT,
    operations     TEXT,                           -- JSON：操作端点模板覆盖（默认模板 + 覆盖）
    enabled        INTEGER DEFAULT 1,
    timeout_sec    INTEGER DEFAULT 30,
    last_health_at INTEGER,
    last_health_ok INTEGER,
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL
);

-- 数据库只读直连（需求 §2.2：适配无 API 的老旧系统，强制只读）
CREATE TABLE IF NOT EXISTS db_connectors (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    db_type     TEXT NOT NULL DEFAULT 'sqlite',    -- sqlite / postgres（已实现）；mysql / oracle / sqlserver（已配置，驱动待接入）
    dsn         TEXT NOT NULL,                     -- SQLite 文件路径 或 连接串
    enabled     INTEGER DEFAULT 1,
    max_rows    INTEGER DEFAULT 100,
    timeout_sec INTEGER DEFAULT 10,
    last_health_at INTEGER,                        -- P2：健康监控回写
    last_health_ok INTEGER,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

-- Webhook 接收事件（需求 §2.2：异步事件驱动，P3 工作流触发器使用）
CREATE TABLE IF NOT EXISTS webhook_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    hook_id    TEXT NOT NULL,                      -- webhook token 标识
    source     TEXT NOT NULL DEFAULT '',
    payload    TEXT NOT NULL,
    processed  INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_webhook_hook ON webhook_events(hook_id, created_at);

-- ============ Phase B (P3)：自动化编排 ============

-- 工作流定义（需求 §B.4.1：自动化工作流 DSL）
CREATE TABLE IF NOT EXISTS workflows (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    trigger_type   TEXT NOT NULL,          -- manual / schedule / event / webhook / db
    trigger_config TEXT,                   -- JSON：{cron} / {hook_id} / {sql,operator,threshold,interval} / {event}
    steps          TEXT NOT NULL,          -- JSON 数组（步骤 DSL）
    enabled        INTEGER DEFAULT 1,
    created_by     TEXT NOT NULL DEFAULT '',
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL
);

-- 工作流执行记录
CREATE TABLE IF NOT EXISTS workflow_runs (
    id           TEXT PRIMARY KEY,
    workflow_id  TEXT NOT NULL,
    trigger      TEXT NOT NULL,            -- manual / webhook / schedule / db / event
    status       TEXT NOT NULL,            -- running / success / failed / skipped
    error        TEXT,
    logs         TEXT,                     -- JSON：每步结果
    started_at   INTEGER NOT NULL,
    finished_at  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_wf_runs_wf ON workflow_runs(workflow_id, started_at);

-- ============ Phase B (P2)：协同与增强 ============

-- 项目空间（需求 §B.3.1：多人协同）
CREATE TABLE IF NOT EXISTS projects (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    owner         TEXT NOT NULL DEFAULT '',        -- 创建者（企业账号 / local）
    config_share  TEXT,                            -- JSON：{skills,mcp,agent} 配置共享开关
    status        INTEGER DEFAULT 1,               -- 1 活跃 / 0 归档
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);

-- 项目成员（admin / editor / viewer 三级）
CREATE TABLE IF NOT EXISTS project_members (
    project_id  TEXT NOT NULL,
    username    TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'viewer',   -- admin / editor / viewer
    joined_at   INTEGER NOT NULL,
    PRIMARY KEY (project_id, username)
);

-- 项目任务（独立 / 分享 / 协作三种模式）
CREATE TABLE IF NOT EXISTS project_tasks (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'todo',     -- todo / doing / done
    mode        TEXT NOT NULL DEFAULT 'shared',   -- independent / shared / collaborative
    assignee    TEXT NOT NULL DEFAULT '',
    created_by  TEXT NOT NULL DEFAULT '',
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_project ON project_tasks(project_id, created_at);

-- 项目资产库（需求 §B.3.1：服务端 RAG 检索，经 knowledge_servers 连接）
CREATE TABLE IF NOT EXISTS project_assets (
    id                 TEXT PRIMARY KEY,
    project_id         TEXT NOT NULL,
    name               TEXT NOT NULL,
    type               TEXT NOT NULL DEFAULT 'doc', -- doc / data / image / url
    content            TEXT NOT NULL DEFAULT '',    -- 文本 / JSON 引用 / URL
    knowledge_server   TEXT,                        -- 关联知识库连接 id（RAG 检索源）
    created_by         TEXT NOT NULL DEFAULT '',
    created_at         INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_asset_project ON project_assets(project_id, created_at);

-- 字段映射配置（需求 §B.3.3：跨系统字段映射，JSON 配置编辑，可视化拖拽留 P3）
CREATE TABLE IF NOT EXISTS field_mappings (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    connector_id  TEXT NOT NULL,                  -- 连接器实例 id（connector_configs）
    source_field  TEXT NOT NULL,                  -- 源字段（可含路径分隔如 order.items[].sku）
    target_field  TEXT NOT NULL,                  -- 目标字段
    transform     TEXT,                           -- JSON：{type: none|map|format|join, args}
    enabled       INTEGER DEFAULT 1,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);

-- 联网搜索服务（web_search 工具的后端 provider 配置）
CREATE TABLE IF NOT EXISTS web_search_servers (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    provider      TEXT NOT NULL,                  -- tavily|bing|brave|serpapi|duckduckgo
    base_url      TEXT,                           -- 可选：自定义网关/代理
    api_key_ref   TEXT,                           -- keychain 引用名
    enabled       INTEGER DEFAULT 1,
    max_results   INTEGER DEFAULT 5,
    timeout_sec   INTEGER DEFAULT 15,
    last_health_at INTEGER,
    last_health_ok INTEGER,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);

-- 外部 MCP Server（动态挂载为 Agent 工具）
CREATE TABLE IF NOT EXISTS mcp_servers (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    url           TEXT NOT NULL,                  -- MCP HTTP/SSE 端点
    headers       TEXT,                           -- JSON：附加请求头
    enabled       INTEGER DEFAULT 1,
    tools_cache   TEXT,                           -- JSON：tools/list 缓存（名称+描述+schema）
    last_health_at INTEGER,
    last_health_ok INTEGER,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);
"""


_TOOL_REGISTRY_UPGRADE_COLUMNS = [
    ("kind", "TEXT DEFAULT 'tool'"),
    ("permission_level", "TEXT DEFAULT 'P2'"),
    ("version", "TEXT DEFAULT '0.1.0'"),
    ("metadata", "TEXT"),
]

# P2：既有 db_connectors 表补健康字段
_DB_CONNECTORS_UPGRADE_COLUMNS = [
    ("last_health_at", "INTEGER"),
    ("last_health_ok", "INTEGER"),
]


async def _migrate_tool_registry(db) -> None:
    """对已存在的 tool_registry 表补列（SQLite ALTER TABLE 幂等迁移）。"""
    cursor = await db.execute("PRAGMA table_info(tool_registry)")
    existing = {row["name"] for row in await cursor.fetchall()}
    for col, ddl in _TOOL_REGISTRY_UPGRADE_COLUMNS:
        if col not in existing:
            await db.execute(f"ALTER TABLE tool_registry ADD COLUMN {col} {ddl}")
            logger.info("[schema] tool_registry 新增列: %s", col)


async def _migrate_db_connectors(db) -> None:
    """对已存在的 db_connectors 表补健康字段（幂等）。"""
    cursor = await db.execute("PRAGMA table_info(db_connectors)")
    existing = {row["name"] for row in await cursor.fetchall()}
    for col, ddl in _DB_CONNECTORS_UPGRADE_COLUMNS:
        if col not in existing:
            await db.execute(f"ALTER TABLE db_connectors ADD COLUMN {col} {ddl}")
            logger.info("[schema] db_connectors 新增列: %s", col)


async def init_extensions() -> None:
    db = await connect()
    try:
        await db.executescript(EXT_SCHEMA_SQL)
        await _migrate_tool_registry(db)
        await _migrate_db_connectors(db)
        await db.commit()
    finally:
        await db.close()


# ---------- 启动加速：单连接批量建表 + 播种 ----------

_TOOL_UPSERT_SQL = """\
INSERT INTO tool_registry
    (id, name, description, source, enabled, requires_approval,
     last_loaded_at, kind, permission_level, version, metadata)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    name=excluded.name,
    description=excluded.description,
    source=excluded.source,
    enabled=excluded.enabled,
    requires_approval=excluded.requires_approval,
    kind=excluded.kind,
    permission_level=excluded.permission_level,
    version=excluded.version,
    metadata=excluded.metadata,
    last_loaded_at=excluded.last_loaded_at"""

_SKILL_UPSERT_SQL = """\
INSERT INTO skills
    (id, name, version, description, kind, permission_level,
     source, source_url, manifest, enabled, installed_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    name=excluded.name,
    version=excluded.version,
    description=excluded.description,
    kind=excluded.kind,
    permission_level=excluded.permission_level,
    source=excluded.source,
    source_url=excluded.source_url,
    manifest=excluded.manifest,
    enabled=excluded.enabled"""


async def init_and_seed() -> None:
    """一次性完成建表 + 迁移 + 内置数据播种（单连接单事务）。

    将原先 init_db + init_extensions + seed_builtin_tools +
    seed_builtin_skills + seed_connector_tools 五步（59+ 次串行连接）
    合并为一次连接、一次提交。
    """
    from ..connectors import CONNECTOR_TYPES

    db = await connect()
    try:
        # 1. 基础表 + 扩展表
        await db.executescript(SCHEMA_SQL)
        await db.executescript(EXT_SCHEMA_SQL)
        # 1.5 幂等迁移：老库补 messages.metadata 列（交付文件持久化）
        try:
            await db.execute("ALTER TABLE messages ADD COLUMN metadata TEXT")
        except Exception:
            pass  # 列已存在
        # 1.6 幂等迁移：老库补 conversations.mode 列（对话/工作双分区）
        try:
            await db.execute(
                "ALTER TABLE conversations ADD COLUMN mode TEXT NOT NULL DEFAULT 'chat'"
            )
        except Exception:
            pass  # 列已存在
        # 2. 幂等迁移
        await _migrate_tool_registry(db)
        await _migrate_db_connectors(db)

        now = _now_ms()

        # 3. 内置工具
        for t in BUILTIN_TOOLS:
            await db.execute(
                _TOOL_UPSERT_SQL,
                (
                    t["id"],
                    t["name"],
                    t["description"],
                    t["source"],
                    int(t["enabled"]),
                    int(t.get("requires_approval", False)),
                    now,
                    t.get("kind", "tool"),
                    t.get("permission_level", "P2"),
                    t.get("version", "0.1.0"),
                    json.dumps(t.get("metadata"), ensure_ascii=False)
                    if t.get("metadata")
                    else None,
                ),
            )

        # 4. 内置技能（skills 表 + tool_registry 双写）
        for s in BUILTIN_SKILLS:
            await db.execute(
                _SKILL_UPSERT_SQL,
                (
                    s["id"],
                    s["name"],
                    s["version"],
                    s["description"],
                    "skill",
                    s["permission_level"],
                    s["source"],
                    "",
                    json.dumps(s["manifest"], ensure_ascii=False)
                    if s.get("manifest")
                    else None,
                    1,
                    now,
                ),
            )
            await db.execute(
                _TOOL_UPSERT_SQL,
                (
                    s["id"],
                    s["name"],
                    s["description"],
                    "builtin",
                    0,  # 基础 Skill 默认停用
                    int(s["permission_level"] == "P4"),
                    now,
                    "skill",
                    s["permission_level"],
                    s["version"],
                    json.dumps(
                        {"skill": True, "tools": s["manifest"].get("tools", [])},
                        ensure_ascii=False,
                    ),
                ),
            )

        # 5. 连接器类型工具条目
        for ctype, meta in CONNECTOR_TYPES.items():
            operations = list(meta["operations"].keys())
            high_risk = any(
                t.get("risk") == "high" for t in meta["operations"].values()
            )
            await db.execute(
                _TOOL_UPSERT_SQL,
                (
                    ctype,
                    meta["name"],
                    meta["description"] + "。可用操作: " + ", ".join(operations),
                    "connector",
                    0,
                    int(high_risk),
                    now,
                    "connector",
                    "P3",
                    "0.1.0",
                    json.dumps(
                        {"connector_type": ctype, "operations": operations},
                        ensure_ascii=False,
                    ),
                ),
            )

        await db.commit()
        logger.info(
            "[seed] 批量播种完成: %d 工具 + %d 技能 + %d 连接器",
            len(BUILTIN_TOOLS),
            len(BUILTIN_SKILLS),
            len(CONNECTOR_TYPES),
        )
    finally:
        await db.close()


# 预置内置工具集（幂等 upsert）
# Phase B (P0)：统一元数据中心 —— 每项带 kind / permission_level / version / metadata。
BUILTIN_TOOLS = [
    {
        "id": "filesystem",
        "name": "文件系统",
        "description": "文件读写、目录搜索（受限根目录，审计）",
        "source": "builtin",
        "enabled": True,
        "requires_approval": False,
        "kind": "tool",
        "permission_level": "P2",
        "version": "0.1.0",
        "metadata": {"operations": ["read", "write", "list", "search"]},
    },
    {
        "id": "shell",
        "name": "Shell 命令",
        "description": "命令执行（白名单 + UI 确认 + 超时 + 全量审计）",
        "source": "builtin",
        "enabled": True,
        "requires_approval": True,
        "kind": "tool",
        "permission_level": "P4",
        "version": "0.1.0",
        "metadata": {"risk": "high", "requires_approval": True},
    },
    {
        "id": "knowledge",
        "name": "知识库检索",
        "description": "远程知识库检索（RAG / Wiki 连接，带引用溯源）",
        "source": "builtin",
        "enabled": True,
        "requires_approval": False,
        "kind": "tool",
        "permission_level": "P3",
        "version": "0.1.0",
        "metadata": {"connector": "knowledge_servers"},
    },
    {
        "id": "browser",
        "name": "浏览器自动化",
        "description": "Playwright 自动化（来源白名单）",
        "source": "builtin",
        "enabled": False,
        "requires_approval": False,
        "kind": "tool",
        "permission_level": "P3",
        "version": "0.1.0",
        "metadata": {"status": "placeholder"},
    },
    {
        "id": "code",
        "name": "代码分析",
        "description": "Tree-sitter 静态分析（只读）",
        "source": "builtin",
        "enabled": True,
        "requires_approval": False,
        "kind": "tool",
        "permission_level": "P1",
        "version": "0.1.0",
        "metadata": {"readonly": True},
    },
    {
        "id": "db_query",
        "name": "数据库只读查询",
        "description": "对已配置的只读数据库连接执行 SQL 查询（强制只读 SELECT/WITH/EXPLAIN）",
        "source": "builtin",
        "enabled": True,
        "requires_approval": False,
        "kind": "tool",
        "permission_level": "P3",
        "version": "0.1.0",
        "metadata": {"readonly": True, "connector": "db_connectors"},
    },
    {
        "id": "rpa",
        "name": "RPA 模拟操作",
        "description": "遗留系统 UI 模拟操作（P1 骨架预留，Playwright 驱动 P2 接入）",
        "source": "builtin",
        "enabled": False,
        "requires_approval": True,
        "kind": "tool",
        "permission_level": "P4",
        "version": "0.1.0",
        "metadata": {"status": "placeholder"},
    },
]


# 内置 6 类基础 Skill（需求 §2.7：PDF/DOCX/XLSX/PPT/浏览器控制/安全审查）
# 以 manifest 声明形式预置，注册进 tool_registry（kind=skill）与 skills 表。
BUILTIN_SKILLS = [
    {
        "id": "skill-pdf",
        "name": "PDF 文档处理",
        "description": "读取、解析与生成 PDF 文档",
        "version": "0.1.0",
        "permission_level": "P2",
        "source": "builtin",
        "manifest": {
            "name": "skill-pdf",
            "version": "0.1.0",
            "security_level": "P2",
            "permissions": {
                "filesystem": "read+write(工作目录)",
                "network": "none",
                "system_calls": "none",
            },
            "dependencies": {"python": ["pypdf"]},
            "tools": [{"name": "pdf_extract_text", "description": "提取 PDF 文本"}],
        },
    },
    {
        "id": "skill-docx",
        "name": "DOCX 文档处理",
        "description": "读取、编辑与生成 Word 文档",
        "version": "0.1.0",
        "permission_level": "P2",
        "source": "builtin",
        "manifest": {
            "name": "skill-docx",
            "version": "0.1.0",
            "security_level": "P2",
            "permissions": {
                "filesystem": "read+write(工作目录)",
                "network": "none",
                "system_calls": "none",
            },
            "dependencies": {"python": ["python-docx"]},
            "tools": [{"name": "docx_edit", "description": "编辑 Word 文档"}],
        },
    },
    {
        "id": "skill-xlsx",
        "name": "XLSX 表格处理",
        "description": "读取、分析与生成 Excel 表格",
        "version": "0.1.0",
        "permission_level": "P2",
        "source": "builtin",
        "manifest": {
            "name": "skill-xlsx",
            "version": "0.1.0",
            "security_level": "P2",
            "permissions": {
                "filesystem": "read+write(工作目录)",
                "network": "none",
                "system_calls": "none",
            },
            "dependencies": {"python": ["openpyxl"]},
            "tools": [{"name": "xlsx_analyze", "description": "分析表格数据"}],
        },
    },
    {
        "id": "skill-ppt",
        "name": "PPT 演示文稿",
        "description": "创建与编辑 PowerPoint 演示文稿",
        "version": "0.1.0",
        "permission_level": "P2",
        "source": "builtin",
        "manifest": {
            "name": "skill-ppt",
            "version": "0.1.0",
            "security_level": "P2",
            "permissions": {
                "filesystem": "read+write(工作目录)",
                "network": "none",
                "system_calls": "none",
            },
            "dependencies": {"python": ["python-pptx"]},
            "tools": [{"name": "ppt_build", "description": "生成演示文稿"}],
        },
    },
    {
        "id": "skill-browser",
        "name": "浏览器控制",
        "description": "网页自动化操作（来源白名单 + 审计）",
        "version": "0.1.0",
        "permission_level": "P3",
        "source": "builtin",
        "manifest": {
            "name": "skill-browser",
            "version": "0.1.0",
            "security_level": "P3",
            "permissions": {
                "filesystem": "none",
                "network": "受限白名单",
                "system_calls": "none",
            },
            "dependencies": {"python": ["playwright"]},
            "tools": [{"name": "browser_navigate", "description": "打开网页"}],
        },
    },
    {
        "id": "skill-security-review",
        "name": "安全审查",
        "description": "对代码/配置/命令进行安全合规审查",
        "version": "0.1.0",
        "permission_level": "P1",
        "source": "builtin",
        "manifest": {
            "name": "skill-security-review",
            "version": "0.1.0",
            "security_level": "P1",
            "permissions": {
                "filesystem": "readonly",
                "network": "none",
                "system_calls": "none",
            },
            "dependencies": {"python": []},
            "tools": [{"name": "security_review", "description": "静态安全审查"}],
        },
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
            kind=t.get("kind", "tool"),
            permission_level=t.get("permission_level", "P2"),
            version=t.get("version", "0.1.0"),
            metadata=t.get("metadata"),
        )


async def seed_builtin_skills() -> None:
    """内置 Skill 预置：skills 表 + tool_registry（kind=skill）双写。"""
    from .extensions import tool_registry_repo, skills_repo

    for s in BUILTIN_SKILLS:
        await skills_repo.upsert(
            skill_id=s["id"],
            name=s["name"],
            version=s["version"],
            description=s["description"],
            kind="skill",
            permission_level=s["permission_level"],
            source=s["source"],
            manifest=s["manifest"],
        )
        # 注册为可调度工具条目（统一元数据中心）
        await tool_registry_repo.upsert(
            tool_id=s["id"],
            name=s["name"],
            description=s["description"],
            source="builtin",
            enabled=False,  # 基础 Skill 默认停用，需用户显式启用
            requires_approval=s["permission_level"] == "P4",
            kind="skill",
            permission_level=s["permission_level"],
            version=s["version"],
            metadata={"skill": True, "tools": s["manifest"].get("tools", [])},
        )


async def seed_connector_tools() -> None:
    """连接器类型工具条目同步（kind=connector，需求 §2.1）。

    由连接器框架声明（CONNECTOR_TYPES），与用户配置的连接器实例解耦：
    条目始终存在（描述可用操作），未配置实例时工具自身返回明确提示。
    实例启停/配置变更由 API 层同步 health 状态，条目本身保持稳定。
    """
    from ..connectors import CONNECTOR_TYPES
    from .extensions import tool_registry_repo

    for ctype, meta in CONNECTOR_TYPES.items():
        operations = list(meta["operations"].keys())
        high_risk = any(t.get("risk") == "high" for t in meta["operations"].values())
        await tool_registry_repo.upsert(
            tool_id=ctype,
            name=meta["name"],
            description=meta["description"] + "。可用操作: " + ", ".join(operations),
            source="connector",
            enabled=False,  # 未配置实例前默认停用；配置实例后由 sync 启用
            requires_approval=high_risk,
            kind="connector",
            permission_level="P3",
            version="0.1.0",
            metadata={"connector_type": ctype, "operations": operations},
        )


async def sync_connector_instance_tools() -> None:
    """按已配置的连接器实例同步 tool_registry 条目启用状态。

    - 存在启用实例的类型 → 条目 enabled=1
    - 无启用实例的类型 → 条目 enabled=0（工具仍在注册表中，返回未配置提示）
    """
    from ..connectors import CONNECTOR_TYPES
    from .connectors import connector_config_repo
    from .extensions import tool_registry_repo

    configs = await connector_config_repo.list_all()
    enabled_types = {c["type"] for c in configs if c.get("enabled")}
    for ctype, meta in CONNECTOR_TYPES.items():
        operations = list(meta["operations"].keys())
        high_risk = any(t.get("risk") == "high" for t in meta["operations"].values())
        await tool_registry_repo.upsert(
            tool_id=ctype,
            name=meta["name"],
            description=meta["description"] + "。可用操作: " + ", ".join(operations),
            source="connector",
            enabled=ctype in enabled_types,
            requires_approval=high_risk,
            kind="connector",
            permission_level="P3",
            version="0.1.0",
            metadata={"connector_type": ctype, "operations": operations},
        )

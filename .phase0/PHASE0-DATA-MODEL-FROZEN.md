# Phase 0 — 数据模型冻结文档

> **状态**: 已冻结（Phase 0 完成）
> **依据**: `DEVELOPMENT-GUIDE.md` §6
> **冻结日期**: 2026-09-03

---

## 1. SQLite 表结构

### 1.1 conversations（会话表）

```sql
CREATE TABLE conversations (
    id            TEXT PRIMARY KEY,          -- uuid
    title         TEXT NOT NULL,             -- 会话标题（首条消息生成）
    model_id      TEXT NOT NULL,             -- 当前模型
    created_at    INTEGER NOT NULL,          -- unix ms
    updated_at    INTEGER NOT NULL
);
```

### 1.2 messages（消息表）

```sql
CREATE TABLE messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,           -- user / assistant / tool / system
    content         TEXT NOT NULL,
    model_id        TEXT,                    -- 生成该消息的模型
    created_at      INTEGER NOT NULL
);
CREATE INDEX idx_messages_conv ON messages(conversation_id, created_at);
```

### 1.3 tool_calls（工具调用表）

```sql
CREATE TABLE tool_calls (
    id              TEXT PRIMARY KEY,
    message_id      TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    call_id         TEXT NOT NULL,           -- 与 LLM tool_call_id 对应
    name            TEXT NOT NULL,
    arguments       TEXT NOT NULL,           -- JSON
    result          TEXT,                    -- 结果内容（可截断）
    is_error        INTEGER DEFAULT 0,
    approved        INTEGER,                 -- NULL=无需确认, 1=批准, 0=拒绝
    created_at      INTEGER NOT NULL
);
```

### 1.4 tool_registry（MCP 工具注册表）

```sql
CREATE TABLE tool_registry (
    id            TEXT PRIMARY KEY,          -- filesystem/shell/browser/code/knowledge
    name          TEXT NOT NULL,
    description   TEXT NOT NULL,
    source        TEXT NOT NULL,             -- 来源白名单标记
    enabled       INTEGER DEFAULT 1,
    requires_approval INTEGER DEFAULT 0,     -- shell=1
    last_loaded_at INTEGER,
    last_used_at  INTEGER
);
```

### 1.5 usage_log（用量统计）

```sql
CREATE TABLE usage_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    model_id        TEXT NOT NULL,
    prompt_tokens   INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    tool_calls      INTEGER DEFAULT 0,
    cost            REAL DEFAULT 0,          -- 本地模型为 0
    created_at      INTEGER NOT NULL
);
```

---

## 2. ChromaDB 设计

### 2.1 集合策略

- MVP 单库，按文档组织（不按知识库分集合）
- 每个文档通过 `doc_id` 标识

### 2.2 元数据字段

```python
{
    "source": str,          # 文件名
    "page": int,            # 页码
    "title_path": str,      # 标题路径
    "content_hash": str,    # 内容 hash（去重用）
    "doc_id": str,          # 文档 ID
    "chunk_index": int      # 块索引
}
```

### 2.3 去重策略

- 导入前计算 `content_hash`
- 已存在则跳过
- 支持按 `doc_id` 删除/重建索引

---

## 3. 配置文件格式

### 3.1 providers.json

```json
{
  "providers": [
    {
      "id": "ollama-local",
      "type": "ollama",
      "baseUrl": "http://localhost:11434/v1",
      "models": ["qwen2.5:7b", "deepseek-coder:6.7b"]
    },
    {
      "id": "private-api",
      "type": "openai-compatible",
      "baseUrl": "https://api.your-company.com/v1",
      "apiKeyRef": "PRIVATE_API_KEY",
      "models": ["enterprise-v3"]
    },
    {
      "id": "cloud-deepseek",
      "type": "openai-compatible",
      "baseUrl": "https://api.deepseek.com/v1",
      "apiKeyRef": "CLOUD_DEEPSEEK_KEY",
      "models": ["deepseek-chat", "deepseek-reasoner"],
      "isPublic": true
    }
  ]
}
```

> **注意**: `apiKeyRef` 指向系统钥匙串，配置文件中不得出现密钥明文。

### 3.2 rag-config.json

```json
{
  "embed_model": "bge-m3",
  "chunk_size": 500,
  "chunk_overlap": 50,
  "retrieve_top_k": 20,
  "rerank_top_n": 5,
  "use_rerank": true,
  "bm25_enabled": true,
  "rrf_k": 60
}
```

### 3.3 mcp-servers.json

```json
{
  "servers": [
    {
      "id": "filesystem",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/allowed/path"],
      "enabled": true
    }
  ]
}
```

---

## 4. 冻结确认

- [x] SQLite 表结构已定义
- [x] ChromaDB 元数据已明确
- [x] 配置文件格式已确定
- [x] 密钥存储策略已确认（钥匙串，非明文）

**后续阶段约定**: 所有阶段只引用本文件路径，不重述数据模型。如需变更，必须重新进入 Phase 0。

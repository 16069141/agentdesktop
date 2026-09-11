# 私有域 AI 助手客户端 — 开发规格书（Development Guide）

> **版本**：v1.0（评审稿）
> **文档定位**：面向开发团队的详细实现规格书，是《PRIVATE-AI-CLIENT-SCHEME-REVISED.md》（方案 v2）与 `private-ai-client-prototype/index.html`（高保真原型）的落地执行版。评审通过后即作为开发唯一依据。
> **配套文档**：`DEV-COMMAND-PROMPT.md`（指挥开发的 Prompt，可直接投喂 AI 编码助手）；本文件是它的详细规格支撑。
> **评审背景**：`review_visual.html` 所列 16 项问题已在方案 v2 处理，本规格书确保这 16 项在执行层不再回退。

---

## 目录

1. [文档与范围说明](#1-文档与范围说明)
2. [产品定义与 MVP 边界](#2-产品定义与-mvp-边界)
3. [系统架构与职责矩阵](#3-系统架构与职责矩阵)
4. [技术栈与版本口径](#4-技术栈与版本口径)
5. [目录结构（落地版）](#5-目录结构落地版)
6. [数据模型设计](#6-数据模型设计)
7. [通信协议（SSE + HTTP API）](#7-通信协议sse--http-api)
8. [前端开发规格（对齐原型）](#8-前端开发规格对齐原型)
9. [后端开发规格](#9-后端开发规格)
10. [安全实现要求](#10-安全实现要求)
11. [测试与评测](#11-测试与评测)
12. [开发任务分解与里程碑](#12-开发任务分解与里程碑)
13. [风险与应对](#13-风险与应对)
14. [开发顺序与协作约定](#14-开发顺序与协作约定)

---

## 1. 文档与范围说明

### 1.1 引用基线（按优先级）

| 优先级 | 文档 | 说明 |
|--------|------|------|
| P0 | `PRIVATE-AI-CLIENT-SCHEME-REVISED.md` | 方案最终权威版本，含全部架构与设计决策 |
| P0 | `private-ai-client-prototype/index.html` | 高保真可交互原型，界面/交互/文案的唯一依据 |
| P1 | `DEV-COMMAND-PROMPT.md` | 指挥开发的 Master Prompt（与本文互为引用） |
| P2 | `PRIVATE-AI-CLIENT-SCHEME.md` | 原版 v1，仅作演进参考，不再作为开发依据 |

### 1.2 本规格书要解决的问题

方案 v2 已从「可行性方案」推进到「可开发原型方案」，本规格书进一步把方案落到**可执行**层面：数据结构、接口契约、组件清单、任务分解与验收方式，避免开发期对「到底做成什么样」产生歧义。

---

## 2. 产品定义与 MVP 边界

### 2.1 产品定位

类 WorkBuddy 的桌面 AI 助手，支持**双模式模型接入**：

- **私有化模式**：主打「数据不出内网」，自托管开源模型（Ollama / vLLM）或私有化商业模型 API
- **公网模式**：可对接互联网上的商业大模型 API（OpenAI 兼容，**全厂商支持**：OpenAI / DeepSeek / 通义千问等），由用户显式配置密钥后使用，**默认不隐藏**（模型列表直接可见可选）

四大核心能力：

1. 自托管开源模型（Ollama / vLLM）
2. 商业大模型 API（私有化或互联网，统一 OpenAI 兼容接口）
3. MCP 工具执行（文件 / Shell / 浏览器 / 代码 / 知识库）
4. 企业知识库 RAG（本地向量检索）

### 2.2 目标用户与场景

| 用户 | 典型场景 |
|------|----------|
| 企业员工 | 基于私有知识库问答（制度、产品资料、项目文档），不出内网 |
| 研发人员 | 编程助手：代码问答、重构建议、跨文件分析 |
| 数据敏感部门 | 对话与文档仅存本地；公网模型产品层默认可见可选，是否启用由**组织策略/管理员**决定（可在设置中禁用公网模型） |
| IT 管理员 | 统一管理模型接入、工具启停、知识库权限 |

### 2.3 MVP 范围清单

**必须包含**：
- 桌面端：对话 UI、会话管理、模型切换（含私有化与互联网商业大模型）、知识库管理、工具面板、设置页、用量统计页
- 后端：SSE 流式对话、LangGraph 单轮/多轮工具调用、MCP 启停、RAG 导入/检索、模型路由、上下文管理
- 安全基线：本地端口绑定、密钥钥匙串、Shell 白名单 + 确认 + 审计、**公网模型显式授权与知情提示**

**明确不做**（避免范围蔓延）：
- 公网多租户 SaaS 化
- 千万级文档 / 高并发知识库检索
- 语音实时交互、待办任务中心、多 Agent 协作
- 多用户私有部署（pgvector / ACL / 计费 / OIDC，MVP 后另行立项）

### 2.4 MVP 验收标准（硬指标）

| 编号 | 指标 | 阈值 |
|------|------|------|
| A1 | 干净环境一键启动 | README 流程可复现，无需人工修依赖 |
| A2 | 流式首字延迟 | < 3s（本地 7B 模型，20 token 首字） |
| A3 | RAG 检索质量 | 10 份混合格式文档导入，Top-5 命中率 ≥ 80%（内部评测集） |
| A4 | MCP 工具调用循环 | ≥ 3 轮不中断、上下文正确回灌 |
| A5 | 数据持久性 | 断电/崩溃重启后对话记录不丢失 |
| A6 | 安全基线 | Shell 白名单/端口绑定/密钥不落明文验证通过 |

---

## 3. 系统架构与职责矩阵

### 3.1 单一 Agent 权威原则（不可违反）

Agent 编排（LangGraph 循环、工具调度、上下文组装）**唯一归属后端 Python**；前端不得重复实现 Agent 循环、不得直连 LLM、不得维护 Provider 适配层。

```
┌──────────────────────────────────────────────────────────────┐
│                  Desktop Client (Electron)                     │
│  对话 / 知识库 / 工具 / 用量 / 设置 五个视图                    │
│  Zustand（UI 状态）← preload 白名单 IPC → Main 主进程           │
└──────────────────────────┬───────────────────────────────────┘
                           │ 本地 HTTP/SSE（仅 127.0.0.1 + token）
┌──────────────────────────▼───────────────────────────────────┐
│              Agent Process（Python，Electron 管理生命周期）     │
│  Provider 层(统一OpenAI兼容) → LangGraph Loop(唯一Agent权威)     │
│  MCP Client  │  RAG Pipeline  │  Context 管理  │  Security     │
│  SQLite（对话/注册表） + ChromaDB（向量） + 系统钥匙串（密钥）    │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────┐
│   LLM Providers：Ollama / vLLM / 私有 API / 互联网商业大模型   │
│            全部走 OpenAI 兼容 chat/completions                │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 职责矩阵

| 端 | 负责 | 明确不负责 |
|----|------|-----------|
| Electron 渲染进程 | 渲染与交互、SSE 会话客户端、UI 状态、主题、事件时间线展示 | 不持有 Agent 循环、不调 LLM、无 Provider 适配 |
| Electron 主进程 | IPC、系统能力（文件/通知/托盘）、Python 子进程生命周期管理、对外代理 | 不实现业务逻辑 |
| Python Agent 服务 | Agent 编排、MCP 客户端、RAG、模型路由、上下文组装、安全/审计 | 不做 UI、不存 UI 状态 |
| 数据层 | SQLite + ChromaDB + 钥匙串，仅被 Python 访问 | 客户端不直连 |

> 若未来需要「离线直连模式」（不启动 Python），应设计为**独立第二条调用路径**，而非在 Electron 内维护第二套 Agent。

---

## 4. 技术栈与版本口径

### 4.1 技术栈锁定

| 层 | 技术 | 备注 |
|----|------|------|
| 桌面框架 | Electron + React + TypeScript | 备选 Tauri v2（不采用：Rust FFI 成本高，Agent 在后端时前端框架收益差异不大） |
| UI | Ant Design / Shadcn UI | 可 Tailwind + Radix |
| 状态管理 | Zustand（仅 UI 状态） | 不用 Redux |
| SSE 客户端 | 自研（fetch + ReadableStream） | 处理粘包/半包；备选 eventsource-parser |
| 后端 | FastAPI + LangGraph + MCP Python SDK | Agent 编排权威 |
| 模型接入 | OpenAI 兼容 chat/completions | 统一 /v1/models；覆盖 Ollama / vLLM / 私有 API / 互联网商业大模型（OpenAI / DeepSeek / 通义千问等） |
| 向量库 | ChromaDB（内置持久化） | 多用户再上 pgvector |
| 数据库 | SQLite（aiosqlite） | 会话/消息/工具调用/注册表 |
| 打包 | electron-builder + PyInstaller（Agent） | 见 §12 Phase 4 |
| 密钥 | 系统钥匙串 | macOS Keychain / Windows Credential Manager |

### 4.2 依赖版本口径

| 依赖 | 建议口径 | 注意 |
|------|----------|------|
| Electron / electron-builder | 当前稳定 LTS（≥33） | 锁定 lockfile |
| React | 18.x 或 19.x | 跟随生态稳定版 |
| langgraph / langchain | 当前稳定大版本 | 0.0.30 过旧，API 差异大，禁止使用 |
| chromadb | 当前稳定版（0.5+ / 1.x） | 0.4 及以下 API 有变化 |
| ollama (python) | 当前稳定版 | 用 `ollama.embed()`，**不是**废弃的 `embeddings(prompt=)` |
| openai | 当前稳定版 | 兼容层基础 |
| fastapi / uvicorn / pydantic | 当前稳定版 | 入参出参 Pydantic 校验 |

---

## 5. 目录结构（落地版）

```
private-ai-client/
├── desktop/                          # Electron 客户端
│   ├── src/
│   │   ├── components/
│   │   │   ├── chat/                 # 对话页（ChatWindow/MessageList/MessageItem/InputBox/
│   │   │   │   │                     #   EventTimeline/InspectorPanel/QuickCommands/ContextStatus）
│   │   │   ├── knowledge/            # 知识库页（KnowledgeBase/DocumentList/SearchPanel/ImportModal）
│   │   │   ├── tools/                # 工具页（ToolPanel/ToolRegistry）
│   │   │   ├── usage/                # 用量页（UsageChart/UsageTable）
│   │   │   ├── settings/             # 设置页（ProviderSettings/MCPSettings/SecuritySettings）
│   │   │   └── common/               # 通用（ThemeToggle/Toast/Sidebar/Modal）
│   │   ├── sse/sseClient.ts          # SSE 客户端（粘包解析、事件分发、AbortController）
│   │   ├── store/                    # Zustand（useConversationStore/useSettingsStore/useUiStore）
│   │   ├── theme/                    # 主题 token 与切换（深色/浅色 × 强调色）
│   │   └── types/                    # SSE 事件、API DTO、模型/工具/知识库类型
│   ├── electron/
│   │   ├── main.ts                   # 主进程入口
│   │   ├── preload.ts                # 白名单 IPC 暴露
│   │   ├── ipc/                      # IPC handlers
│   │   └── agentProcess.ts           # Python 子进程生命周期管理
│   ├── package.json
│   └── electron-builder.yml
│
├── agent/                            # Python Agent 服务
│   ├── app/
│   │   ├── main.py                   # FastAPI 入口（127.0.0.1 + token 鉴权 + /healthz）
│   │   ├── api/                      # chat/knowledge/mcp/models/settings/usage 路由
│   │   ├── agents/                   # LangGraph（base/coding/general）
│   │   ├── rag/                      # ingest/chunker/embedder/retriever/rerank
│   │   ├── tools/                    # MCP 工具（filesystem/shell/browser/code/knowledge）
│   │   ├── providers/                # base/ollama/vllm/private_api（统一 OpenAI 兼容）
│   │   ├── context/                  # token 预算/滑动窗口/摘要压缩
│   │   ├── security/                 # 钥匙串读取/命令白名单/审计日志
│   │   └── storage/                  # SQLite schema/repository/ChromaDB 封装
│   ├── tests/                        # pytest 单元/集成测试
│   ├── eval/                         # golden set 与评测脚本
│   ├── requirements.txt
│   └── pyproject.toml
│
├── config/                           # 配置（不含密钥）
│   ├── providers.json
│   ├── mcp-servers.json
│   └── rag-config.json
│
├── data/                             # 本地数据（gitignore）
│   ├── conversations/
│   ├── knowledge-base/
│   └── mcp-sessions/
│
├── scripts/                          # install/build-desktop/build-agent/start-agent
└── README.md
```

> 关键约束：**不设** `desktop/src/providers/`（Provider 层仅后端）；**不设** `desktop/src/engine/ToolDispatcher`（工具调度收敛到后端）；新增 `sse/`、`theme/`、`security/`、`context/`、`storage/`、`tests/`、`eval/`。

---

## 6. 数据模型设计

### 6.1 SQLite 表结构

```sql
-- 会话表
CREATE TABLE conversations (
    id            TEXT PRIMARY KEY,          -- uuid
    title         TEXT NOT NULL,             -- 会话标题（首条消息生成）
    model_id      TEXT NOT NULL,             -- 当前模型（qwen/coder/private...）
    created_at    INTEGER NOT NULL,          -- unix ms
    updated_at    INTEGER NOT NULL
);

-- 消息表
CREATE TABLE messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,           -- user / assistant / tool / system
    content         TEXT NOT NULL,
    model_id        TEXT,                    -- 生成该消息的模型
    created_at      INTEGER NOT NULL
);
CREATE INDEX idx_messages_conv ON messages(conversation_id, created_at);

-- 工具调用表（审计 + 事件时间线回放）
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

-- MCP 工具注册表（启停状态 + 来源 + 审计）
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

-- 用量统计
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

### 6.2 ChromaDB

- 集合：每个知识库一个 collection（MVP 单库 `hr-manual` / `product` / `security` / `sla` 示例见原型，实际按导入组织）。
- 文档元数据：`source`（文件名）、`page`（页码）、`title_path`（标题路径）、`content_hash`（去重）、`doc_id`、`chunk_index`。
- 去重：导入前按 `content_hash` 判断，已存在则跳过；按 `doc_id` 支持删除/重建索引。

### 6.3 配置（config/，不含密钥）

```json
// providers.json —— 模型接入（密钥只存钥匙串，这里仅引用名）
{
  "providers": [
    { "id": "ollama-local", "type": "ollama",
      "baseUrl": "http://localhost:11434/v1",
      "models": ["qwen2.5:7b", "deepseek-coder:6.7b"] },
    { "id": "private-api", "type": "openai-compatible",
      "baseUrl": "https://api.your-company.com/v1",
      "apiKeyRef": "PRIVATE_API_KEY",
      "models": ["enterprise-v3"] },
    { "id": "cloud-deepseek", "type": "openai-compatible",
      "baseUrl": "https://api.deepseek.com/v1",
      "apiKeyRef": "CLOUD_DEEPSEEK_KEY",      // 互联网商业大模型
      "models": ["deepseek-chat", "deepseek-reasoner"],
      "isPublic": true }                      // 标记公网模型，用于知情提示
  ]
}
```

```json
// rag-config.json —— RAG 参数
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

---

## 7. 通信协议（SSE + HTTP API）

### 7.1 SSE 事件协议（`POST /api/chat`，`text/event-stream`）

事件格式：`data: {json}\n\n`；流结束哨兵 `data: [DONE]`。

| event | 字段 | 说明 |
|-------|------|------|
| `meta` | `message_id, model, conversation_id` | 会话/消息元信息 |
| `thinking` | `text` | 推理过程（前端可折叠展示） |
| `tool_call` | `call_id, name, arguments` | 声明要执行的工具（arguments 为 JSON 字符串） |
| `tool_result` | `call_id, content, is_error` | 工具执行结果 |
| `text` | `delta` | 增量文本 |
| `done` | `usage {prompt_tokens, completion_tokens}, finish_reason` | 正常结束 |
| `error` | `code, message` | 错误（code 见下） |

**错误码枚举**：`model_not_found` / `tool_timeout` / `tool_denied` / `context_overflow` / `rate_limited` / `internal`。

**前端解析要求**：按 `\n\n` 切分事件块；用缓冲区拼接跨 chunk 的行；兼容多 `data:` 行与 `[DONE]`。

### 7.2 HTTP 接口清单

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/healthz` | 健康检查（Electron 轮询） |
| POST | `/api/chat` | 对话（SSE 流式） |
| GET | `/api/models` | 模型列表（Provider 聚合） |
| GET/POST | `/api/conversations` | 会话列表 / 新建 |
| GET/DELETE | `/api/conversations/{id}` | 会话详情（含消息+工具调用）/ 删除 |
| POST | `/api/conversations/{id}/export` | 会话导出（MD/JSON） |
| POST | `/api/knowledge/ingest` | 文档导入 |
| POST | `/api/knowledge/search` | 知识库检索（带引用） |
| DELETE | `/api/knowledge/{doc_id}` | 删除文档索引 |
| GET | `/api/mcp/tools` | 工具集与状态 |
| POST | `/api/mcp/{toolset}/enable` `/disable` | 工具集启停 |
| POST | `/api/mcp/{toolset}/approve` | 工具执行确认（如 Shell） |
| GET/PUT | `/api/settings` | 配置读取/更新（不含密钥明文） |
| GET | `/api/usage` | 用量统计（今日/本周/近 7 次） |

**鉴权**：所有接口要求 `Authorization: Bearer <token>`，token 由 Electron 主进程启动 Agent 时生成并注入，仅本机有效。

---

## 8. 前端开发规格（对齐原型）

> 原型文件：`private-ai-client-prototype/index.html`。以下为必须还原的功能点与视觉规范。

### 8.1 视觉设计 Token（抽取自原型）

| Token | 值 | 说明 |
|-------|-----|------|
| `--bg` | `#0A0F1A` | 深色背景 |
| `--bg-panel` | `rgba(255,255,255,0.055)` | 面板半透明 |
| `--glass` | `blur(24px) saturate(170%)` | 磨砂玻璃 |
| `--text` | `#EEF3F9` / `--text-dim #A8B5C6` / `--text-faint #6E7B8F` | 文字层级 |
| `--accent` | `#3FD8BE`（青绿） | 强调色 |
| `--thinking` | `#E9C463` | 思考块 |
| `--tool` | `#6E9CF0` | 工具块 |
| `--danger` | `#F0675F` | 危险/错误 |
| `--ok` | `#57C98F` | 成功 |
| 圆角 | 14px / 9px | 卡片/小控件 |
| 字体 | 系统 + SF Mono/JetBrains Mono（代码） | 等宽用于代码块 |

主题：深色/浅色 × 多强调色，切换结果 `localStorage` 持久化（原型 `proto-theme`）。

### 8.2 页面与功能清单

**布局**：左侧边栏 + 主区域。顶部 5 个标签页：对话 / 知识库 / 工具 / 用量 / 设置。

**侧边栏**：
- 应用标识：PrivateAI 助手 +「私有域 · 本地模型 · 数据不出内网」
- 模型选择下拉：Qwen2.5 7B（Ollama 本地）/ DeepSeek-Coder 6.7B（Ollama 本地）/ Enterprise-Model-V3（私有 API）/ DeepSeek-chat、通义千问 等互联网商业大模型（公网 API，带「公网」标识）
- 新建对话按钮、会话列表（可新建/删除/切换）
- 用户信息（名称/组织）、「已加密」标识、「仅本地 · 127.0.0.1」标识、主题切换

**对话页**：
- 快捷指令：查公司请假制度 / 运行 ls 命令 / 分析一段代码 / 总结项目进展（点击即触发对应场景）
- 消息流渲染：按「思考 → 工具调用 → 工具结果 → 引用 → 回答」顺序展示事件流
- 每条消息可展开「事件时间线」；右侧「消息分析」面板同步显示当前选中消息/步骤详情
- 输入框 + 发送 + 停止生成（AbortController）
- 上下文 token 状态：如「剩余 14,200 / 16,384 tokens」
- 模型可用切换；Shell 命令触发权限确认弹窗（批准/拒绝）

**知识库页**：
- 导入文档按钮（PDF/MD/Word/TXT，file input）
- 文档列表卡片：名称、大小、chunk 数、导入日期、命中次数
- Embedding 标识「bge-m3 · 本地」
- 搜索：Top-5 结果卡片（文档名、页码、相关度、片段、混合检索 RRF 融合说明），点击可「引用」

**工具页**：
- MCP 工具集列表：文件系统 / Shell 命令 / 浏览器 / 代码分析 / 知识库
- 每项：名称、描述、安全标识（如「白名单 · 需确认 · 审计」「受限根目录 + 审计」）、启用开关
- 关闭的工具体在对话中被拒（有提示）

**用量页**：
- 近 7 次对话 token 消耗图表
- 明细表：时间 / 会话 / 模型 / 输入 tokens / 输出 tokens / 工具调用 / 成本

**设置页**：
- 模型接入说明（OpenAI 兼容协议，含私有化与互联网商业大模型两类；公网模型显示「数据将发送至公网模型厂商」的知情提示）
- Provider 列表/卡片
- 安全设置：仅监听 127.0.0.1、Shell 命令白名单、危险命令拦截、工具调用审计日志开关、钥匙串 API Key 状态（如「3 个 API Key 已安全存储于系统钥匙串」）

### 8.3 前端技术要点

- SSE 客户端封装在 `sse/sseClient.ts`，事件类型 → React 状态更新；`stop()` 中止流。
- Zustand 仅管理 UI 状态（当前会话、选中消息、路由、主题、模型选择）；业务状态（Agent 循环）不在前端。
- 所有 API 调用统一封装 `api/` 层，携带 Bearer token（由主进程提供）。
- IPC：preload 仅暴露白名单方法（如 `getAgentToken`、`openFileDialog`、`shellApprove` 桥接等）。

---

## 9. 后端开发规格

### 9.1 Provider 层（统一，仅一份）

```python
# providers/base.py
class BaseProvider(ABC):
    @abstractmethod
    async def chat(self, messages, model, **kwargs) -> AsyncIterator[dict]: ...
    @abstractmethod
    async def list_models(self) -> list[ModelInfo]: ...

# 唯一实现（Ollama/vLLM/私有 API/互联网商业大模型只差 base_url 与鉴权）
class OpenAICompatibleProvider(BaseProvider):
    def __init__(self, base_url, api_key, model_map): ...
    async def chat(self, messages, model, **kwargs):
        # AsyncOpenAI(base_url=..., api_key=...) stream=True
        # yield {"type":"text","delta":...} / {"type":"done","finish_reason":...}
    async def list_models(self):
        # GET /v1/models
```

### 9.2 LangGraph Agent（唯一 Agent 权威）

```python
class AgentState(TypedDict):
    messages: list            # OpenAI 风格，含 role="tool" 回灌
    conversation_id: str

# chat_node: 组装预算上下文 → 路由模型 → LLM 生成（结构化 tool_calls）
# route_after_chat: 依据 getattr(last,"tool_calls",None) 决定 tool / final
# tool_node: 逐个执行 tool_calls，以 role="tool"+tool_call_id 回灌，
#            超长结果按 §上下文裁剪后入上下文

graph = StateGraph(AgentState)
graph.add_node("chat", chat_node); graph.add_node("tool", tool_node)
graph.add_conditional_edges("chat", route_after_chat, {"tool":"tool","final":END})
graph.add_edge("tool","chat")
graph.set_entry_point("chat")
```

- 工具调用采用 OpenAI 风格结构化字段；RAG 作为 `knowledge_search` 工具进入 Agent（不割裂）。
- 长会话自动触发摘要压缩节点。

### 9.3 MCP 工具管理器

```
用户启用工具集 → 校验来源白名单 → 启动 MCP Server 子进程 → 注册工具到 Agent
    → 执行（记录审计 + 按需权限确认）→ 用户停用/会话结束 → 清理进程
```

内置工具集（与原型一致）：

| 工具 | 描述 | 安全标识 |
|------|------|----------|
| filesystem | 文件读写、目录搜索 | 受限根目录 + 审计 |
| shell | 命令执行 | 白名单 · 需确认 · 审计 |
| browser | Playwright 自动化 | 来源白名单 |
| code | Tree-sitter 静态分析 | 只读 |
| knowledge | 企业 RAG 检索 | ACL 鉴权 |

### 9.4 RAG Pipeline

```
文档导入 → 解析(PDF/MD/TXT/Word, 扫描件OCR可选) → 分块(500/50, 保留元数据)
    → 逐块向量化(bge-m3, Ollama) → ChromaDB 入库(去重: content_hash)
    → 检索: 查询向量化 → 向量检索(Top20) + BM25(Top20) → RRF融合
    → Cross-Encoder rerank(Top5) → 注入 Prompt（带引用溯源）
```

- Embedding：**bge-m3**（中文主推，本地 Ollama）；`nomic-embed-text` 仅英文语料备选。
- 引用溯源：检索结果携带文档 ID、页码、原文片段，前端展示引用来源。
- 增量更新：content_hash 去重；按 doc_id 删除/重建索引。

### 9.5 上下文管理（token 预算）

| 策略 | 说明 |
|------|------|
| 滑动窗口 | 仅保留最近 N 轮（可配，如 20 轮） |
| token 预算 | 系统提示 15% + 历史 40% + RAG 20% + 生成 25%（可调） |
| 摘要压缩 | 超预算对旧消息做 LLM 摘要，压缩进上下文 |
| 工具结果裁剪 | 超长结果截断/摘要后再入上下文 |

### 9.6 模型路由与降级

| 任务类型 | 路由模型 | 降级链 |
|----------|----------|--------|
| 通用对话 | qwen2.5:7b | → 私有 API / 互联网商业大模型 → 报错提示 |
| 编程问答 | deepseek-coder:6.7b | → 通用模型 |
| 向量化 | bge-m3 | → nomic-embed-text |
| Embedding / 聊天分离 | 可分别指定 | 健康检查失败自动切换 |
| 公网模型 | 用户显式选择（带「公网」标识） | 默认可见可选（不隐藏）；用户选择即使用，无需额外授权门槛 |

### 9.7 进程生命周期（Electron ↔ Python）

- Electron 主进程 `agentProcess.ts`：spawn/stop Agent 子进程；轮询 `/healthz`；崩溃自动重启（限次 + 提示）；app 退出时优雅关闭。
- Python 打包：PyInstaller / embeddable Python，随安装包分发，不依赖用户机器装 Python。
- 版本联动：Agent 与 Electron 配套升级，避免协议不匹配。

---

## 10. 安全实现要求

| 风险面 | 落地实现（必须） |
|--------|------------------|
| 密钥泄露 | API Key 存系统钥匙串；配置文件仅 `apiKeyRef` 引用名；运行时读取；**代码/配置/日志均不得出现明文** |
| Shell 滥用 | 命令白名单（允许集 ls/cat/pwd 等）+ 路径前缀校验 + 危险命令黑名单（rm -rf、网络扫描等）+ 执行超时 + **每次执行前 UI 确认** + 全量审计日志 |
| MCP 不可信 | 仅白名单来源；启用前展示声明的工具与权限，用户确认；记录加载来源 |
| 端口暴露 | Agent 仅绑定 127.0.0.1；Electron↔Agent 间一次性随机 token 鉴权头 |
| 知识库权限 | 单用户模式无共享；多用户模式按知识库 ACL（MVP 后） |
| 数据泄露兜底 | 对话与文档默认仅本地；导出需显式操作；提供「本地删除」入口 |
| 公网模型出网 | 互联网商业大模型属公网访问：需用户配置密钥后即可选用（默认不隐藏）；选择时 UI 提示「对话将发送至公网模型厂商」；数据敏感会话建议仅用本地/私有模型 |
| 供应链 | 依赖锁文件 + 关键依赖版本固定 + npm audit / pip-audit |

---

## 11. 测试与评测

| 层次 | 内容 | 工具 |
|------|------|------|
| 单元测试 | Provider、分块、上下文预算、SSE 解析 | pytest / vitest |
| 集成测试 | 端到端对话、MCP 工具调用、RAG 导入检索 | pytest + TestClient |
| Agent 评测 | Golden set：输入 → 期望工具调用序列/答案；指标：任务完成率、工具调用准确率、RAG Top-5 命中率、平均轮次 | `agent/eval/` + 评测报告 |
| 回归 | 每次改动跑全量测试 + eval 对比基线 | CI（GitHub Actions 可选） |

**规则**：每次调整 Prompt / 模型 / 分块参数，必须先跑评测集再合入，防止「修好一个、回归一片」。

---

## 12. 开发任务分解与里程碑

### Phase 0：需求冻结与 PoC（1 周）
- [ ] 冻结 §7 通信协议（SSE 事件 + HTTP 接口 + 错误码）
- [ ] PoC：Ollama 本地模型 + OpenAI 兼容接口跑通最小流式对话
- DoD：curl 可流式拿到 `meta/text/done` 事件；协议表评审通过

### Phase 1：基础框架（2–3 周）
- [ ] Electron + React 骨架（目录对齐 §5）
- [ ] 基础对话 UI（消息列表、输入框、停止生成）+ 主题切换
- [ ] FastAPI 骨架：127.0.0.1 绑定 + token 鉴权 + /healthz
- [ ] SSE 客户端（粘包/半包处理）+ 流式对话端到端打通
- [ ] 会话持久化（SQLite）+ 会话列表 UI
- [ ] 模型配置与切换界面（OpenAI 兼容接入，含私有化与互联网商业大模型；公网模型带「公网」标识 + 知情提示）
- DoD：新建/删除会话、发消息收流式回复、重启会话不丢（验收 A1/A5 部分）

### Phase 2：核心能力（3–4 周）
- [ ] MCP Manager：工具集启停、来源白名单、审计日志
- [ ] 内置工具：filesystem / shell（白名单 + 确认 + 超时）/ knowledge
- [ ] RAG：文档导入 + 混合检索 + 引用展示（验收 A3）
- [ ] 上下文管理：token 预算 + 滑动窗口
- [ ] 密钥存储接入系统钥匙串
- [ ] 安全基线验证（Shell 白名单、端口、密钥）（验收 A6）
- DoD：对话触发 knowledge_search 并展示引用；shell 触发权限确认；审计有记录

### Phase 3：Agent 化与编程助手（3–4 周）
- [ ] LangGraph 多轮工具调用闭环（tool_calls 回灌 ≥3 轮）（验收 A4）
- [ ] 模型路由与降级（含公网模型非默认、仅显式授权可用）
- [ ] 编程助手 Agent（对齐原型 code 场景）
- [ ] 知识库管理界面（上传、删除、检索历史、引用查看）
- [ ] 上下文摘要压缩节点
- [ ] Agent 评测集搭建（golden set 基线）
- DoD：多工具连续调用任务（如「总结项目进展」= knowledge_search + filesystem_read）端到端正确

### Phase 4：打磨发布（2–3 周）
- [ ] Electron ↔ Python 生命周期管理 + PyInstaller 打包
- [ ] 打包分发（macOS/Windows/Linux）+ 自动更新
- [ ] 用户文档 + 内测反馈迭代
- [ ] 发布前验收：按 §2.4 六项硬指标逐项核对，跑全量测试与 eval
- DoD：干净环境安装包一键运行；README 流程可复现（验收 A1–A6 全项）

> 预期：约 **10–14 周完成 MVP/内测版**；「商业化条件」不在本阶段承诺，需另行评估安全合规、运维、客服与扩容。

---

## 13. 风险与应对

| 风险 | 影响 | 应对 |
|------|------|------|
| 上下文溢出（长对话/长工具结果） | 对话中断、回答劣化 | §9.5 预算 + 滑动窗口 + 摘要压缩 |
| 模型幻觉 / 引用不可信 | 错误结论 | 工具结果优先、知识库引用溯源、允许查证 |
| Ollama 本地性能不足 | 首字慢 | vLLM 备选、模型量化、异步流式 |
| 向量检索延迟/准确率低 | 体验差 | 混合检索 + rerank + 缓存 + 索引调优 |
| MCP/Shell 工具安全 | 数据泄露、误删 | §10 白名单 + 确认 + 审计 + 沙箱 |
| 密钥泄露 | 商业模型被盗用 | 系统钥匙串 + 最小权限 |
| Python 打包复杂 | 本地部署失败率高 | PyInstaller/embed python + CI 打包 + 生命周期管理 |
| Electron 体积大、内存高 | 下载慢、卡顿 | asar、按需加载、流式渲染、内存监控 |
| 打包平台差异 | 三平台交付延期 | CI 矩阵构建 + 每平台冒烟 |
| 多用户并发/扩容 | 商业化受阻 | 明确定位 MVP，多用户单独立项 |
| 数据合规 | 隐私风险 | 本地优先、隐私政策、删除入口、审计日志 |
| 公网模型数据出网 | 敏感信息外泄、合规风险 | 默认本地/私有模型；公网模型需显式授权 + 强提示 + 审计；敏感场景禁用公网模型 |

---

## 14. 开发顺序与协作约定

### 14.1 建议协作模式（2–3 人）

| 角色 | 负责 | 说明 |
|------|------|------|
| 前端（1 人） | desktop/ 全部 + SSE 客户端 + IPC | 按 §8 规格与原型实现 |
| 后端（1–2 人） | agent/ 全部 + 打包 + 评测 | 按 §9 规格实现 |
| 共用 | 协议冻结（§7）、测试与评测（§11）、安全基线（§10） | 前后端按协议并行开发 |

### 14.2 关键前置动作（开工前）

1. 冻结 §7 通信协议与 §6 数据模型（评审通过后冻结，改动需走变更流程）。
2. 首周完成 Phase 0 PoC，验证 OpenAI 兼容链路与 SSE 流式。
3. 建立 golden set 评测基线（可先用原型中 4 个场景：rag / shell / code / multi 作为首批用例）。

### 14.3 Definition of Done（每个任务通用）

- [ ] 代码通过类型检查与 lint（TS strict / Python mypy 可选）
- [ ] 相关单元/集成测试通过
- [ ] 涉及 Agent 行为/模型的改动跑过 eval 且不劣化基线
- [ ] 安全基线无回退（密钥/白名单/端口/审计）
- [ ] 与原型交互一致（可对照原型逐项核验）

---

## 附：待评审确认项

以下事项建议在评审会上拍板，避免开发期反复：

1. **UI 组件库**：Ant Design 还是 Shadcn UI？（本规格两可，需定一个）
2. **首版语言/主题**：是否默认深色磨砂 + 青绿（原型默认），浅色主题首版是否必须？
3. **知识库多集合**：MVP 是按「每文档一个集合」还是「单库多文档」？（本规格按单库多文档 + doc_id 管理）
4. **Shell 确认粒度**：每次执行都确认（原型行为）还是可「本次会话记住」？建议 MVP 保持每次确认。
5. **打包目标平台**：MVP 首发平台（macOS only？还是三平台同步）？影响 Phase 4 工作量。
6. **评测集规模**：首批 golden set 至少覆盖原型 4 场景 + 10 份文档 RAG 用例，确认是否够。
7. **公网模型接入范围（已确认）**：互联网商业大模型纳入 MVP，**全厂商支持**（凡 OpenAI 兼容的公网大模型均可接入，不限定厂商）；**默认不隐藏**，模型列表直接可见可选。

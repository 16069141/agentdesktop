# 私有域 AI 助手客户端开发方案（修订版 v2）

> **修订说明（v2）**：本版在原方案基础上完成评审修正，主要变更——
> 1. **架构收敛**：明确 Agent 编排的单一权威（后端 Python），消除前端 TS 与后端 Python「双 Agent 循环」的职责重叠；删除前端重复的 Provider 适配层。
> 2. **代码修正**：修复 RAG 向量化、ChromaDB 调用、SSE 流式解析、LangGraph 工具调用等多处示例 bug。
> 3. **补齐缺失**：新增产品定位与范围、SSE/API 通信协议、安全设计、上下文管理、模型路由、测试与评测、可观测性与用量统计、Electron 与 Python 进程生命周期管理等章节。
> 4. **依赖与时序**：更新依赖版本口径，修正「10–14 周即可商业化」的乐观预期为「MVP / 内测」目标。
>
> 修订时保留原方案的骨架与技术主线（Electron + React + Python FastAPI + LangGraph + MCP + RAG），未更改已确认的技术选型，仅纠正表述与补全细节。

---

## 一、产品定位与范围

### 1.1 产品定位

目标产品：**类 WorkBuddy 的桌面 AI 助手**，主打「数据不出内网」的私有化部署形态。面向需要**本地/私有化 LLM** 的企业或个人，核心能力四件套：

- 自托管开源模型（Ollama / vLLM）
- 私有化商业模型 API（OpenAI 兼容接口）
- MCP 工具执行（文件、Shell、浏览器、代码、知识库）
- 企业知识库 RAG（本地向量检索）

### 1.2 目标用户与核心场景

| 用户 | 典型场景 |
|------|----------|
| 企业员工 | 基于私有知识库问答（制度、产品资料、项目文档），要求不出内网 |
| 研发人员 | 编程助手：代码问答、重构建议、跨文件分析（本地代码库） |
| 数据敏感部门 | 对话与文档仅存本地，禁止上传公网模型 |
| IT 管理员 | 统一管理模型接入、工具启停、知识库权限 |

### 1.3 MVP 范围与验收标准

**MVP 必须包含**：

- 桌面端：对话 UI、会话管理、模型切换、知识库管理、工具面板、设置页
- 后端：SSE 流式对话、LangGraph 单轮/多轮工具调用、MCP 启停、RAG 导入/检索
- 安全基线：本地端口绑定、密钥加密存储、Shell 工具白名单与确认

**MVP 验收标准（每一里程碑「完成」均以此判定）**：

- 在干净环境按 README 一键启动，无需人工修复依赖
- 流式对话延迟 < 3s（本地 7B 模型，20 token 首字）
- 10 份混合格式文档（PDF/MD/Word/TXT）可导入并检索，Top-5 命中率 ≥ 80%（内部评测集）
- MCP 工具调用循环 ≥ 3 轮不中断、上下文正确回灌
- 断电/崩溃后重启，对话记录不丢失

### 1.4 非目标（明确边界，避免过度承诺）

本方案**不覆盖**以下场景，如有需要应另行立项：

- 公网多租户 SaaS 化（需完整租户隔离、计费、SLA）
- 千万级文档 / 高并发的知识库检索（需分布式向量库与检索集群）
- 与 WorkBuddy 对标的完整能力全集（语音实时交互、待办任务中心、多 Agent 协作等首版不做，按 2.3 需求优先级后续迭代）

---

## 二、总体架构与职责划分

### 2.1 单一 Agent 权威原则（本版核心修正）

原方案同时在前端 TS（Agent Engine「工具调用调度」）与后端 Python（LangGraph Agent 编排）各保留一套 Agent 逻辑，职责重叠，会导致「上下文在哪维护、工具结果如何回灌、谁发起 tool-call loop」三者失控。**本版收敛为：Agent 编排唯一权威在后端 Python 服务。**

| 端 | 职责（只做这些） | 明确不做什么 |
|----|------------------|--------------|
| Electron 客户端 | 渲染与交互、系统能力（文件、通知、托盘）、IPC、SSE 会话客户端、本地 UI 状态 | 不持有 Agent 循环、不直接调 LLM、不维护 Provider 适配 |
| Python Agent 服务 | Agent 编排（LangGraph）、MCP 客户端、RAG、模型路由、上下文组装 | 不做 UI，不存储 UI 状态 |
| 数据层 | SQLite + ChromaDB + 加密配置，仅被 Python 服务访问 | 客户端不直连数据库 |

> 若团队后续确需「离线直连模式」（不启动 Python），应将其设计为**独立的第二条调用路径**，而非在 Electron 内维护第二套 Agent 逻辑。

### 2.2 系统架构（修订版）

```
┌──────────────────────────────────────────────────────────────────┐
│                        Desktop Client (Electron)                  │
│  ┌───────────┐  ┌───────────┐  ┌──────────────┐  ┌────────────┐  │
│  │ Chat UI   │  │Knowledge  │  │ Tool Panel   │  │ Settings   │  │
│  │ (React)   │  │ Base UI   │  │ (MCP 列表)   │  │ (模型/密钥) │  │
│  └─────┬─────┘  └─────┬─────┘  └──────┬───────┘  └─────┬──────┘  │
│        │   Zustand（UI 状态，非业务状态）       │              │       │
│  ┌─────▼──────────────▼────────────────▼───────────────▼──────┐ │
│  │          Renderer → Main IPC（preload 暴露白名单 API）        │ │
│  └──────────────────────────┬───────────────────────────────────┘ │
│                             │ 本地 HTTP/SSE（仅绑定 127.0.0.1）    │
│  ┌──────────────────────────▼───────────────────────────────────┐ │
│  │         Agent Process（Python，Electron 管理其生命周期）        │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────┐  │ │
│  │  │ LLM Router   │  │ MCP Client   │  │ RAG Pipeline        │  │ │
│  │  │ 模型路由/降级 │  │ 启停/鉴权/审计 │  │ 解析/分块/向量/检索   │  │ │
│  │  └──────┬───────┘  └──────┬───────┘  └──────────┬──────────┘  │ │
│  │         └────────┬───────┴──────────────────────┘             │ │
│  │         LangGraph Agent Loop（唯一 Agent 权威）                │ │
│  │  ┌────────────────────────────────────────────────────────┐  │ │
│  │  │ Storage: SQLite（对话/注册表）+ ChromaDB（向量）          │  │ │
│  │  │          + 加密配置（钥匙串）                            │  │ │
│  │  └────────────────────────────────────────────────────────┘  │ │
│  └──────────────────────────┬───────────────────────────────────┘ │
└─────────────────────────────┼───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                        LLM Providers                             │
│   ┌──────────┐   ┌──────────┐   ┌────────────────────────────┐   │
│   │ Ollama   │   │  vLLM    │   │ Private API（OpenAI 兼容）   │   │
│   │ (本地)    │   │ (本地)    │   │ 统一走 chat.completions      │   │
│   └──────────┘   └──────────┘   └────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

### 2.3 关键设计决策（先行拍板，避免开发期反复）

| 决策点 | 结论 | 说明 |
|--------|------|------|
| Agent 编排归属 | 后端 Python（LangGraph） | 单一权威，前端不重复实现 |
| LLM 调用入口 | 仅后端 Provider 层 | 前端一律不直连模型 |
| 前后端通信 | 本地 HTTP + SSE | 单向流式足够，见 §5 协议 |
| 模型接入协议 | OpenAI 兼容 chat/completions | Ollama / vLLM / 私有 API 统一 |
| 向量库 | ChromaDB（单机默认）+ pgvector（多用户可选） | 见 §3.4 |
| 密钥存储 | 系统钥匙串（非明文 JSON） | 见 §6 |

---

## 三、技术栈选型

### 3.1 客户端（Desktop App）

| 维度 | 推荐 | 备选 |
|------|------|------|
| 框架 | **Electron + React + TypeScript** | Tauri v2（更轻量但生态弱、MCP 支持成本高） |
| UI 组件 | Ant Design / Shadcn UI | Tailwind + Radix |
| 状态管理 | Zustand（仅 UI 状态） | Redux Toolkit |
| SSE 会话客户端 | 自研（fetch + ReadableStream） | `eventsource-parser` |
| 打包分发 | electron-builder | Tauri bundle |

> **选 Electron 的理由（修正）**：2–3 人小团队需要深度系统能力（文件系统、MCP 进程管理、原生通知）且对前端生态熟悉；Electron 的 MCP SDK 与 Node 侧工具链成熟。**Tauri 更适合性能敏感型应用，但 Rust FFI 成本高，且 Agent 编排在后端时前端框架的收益差异不大。**（原方案「SSE 支持 HTTP/2」作为选型理由不成立，已在 §11 修正表述。）

### 3.2 后端服务（Python Agent 层）

```
FastAPI + LangGraph + MCP Python SDK
```

- **FastAPI**：异步 HTTP/SSE 接口
- **LangGraph**：Agent 编排（工具调用循环、RAG 路由、上下文压缩节点）
- **MCP Python SDK**：MCP Client 实现
- **向量检索**：ChromaDB（本地）+ pgvector（PostgreSQL 可选）

### 3.3 大模型适配层

所有 Provider 统一走 **OpenAI 兼容 chat/completions** 协议：

- Ollama 提供 OpenAI 兼容端点（`/v1/chat/completions`）
- vLLM 原生 OpenAI 兼容
- 私有商业模型：需确认其兼容 OpenAI 协议；不兼容者由适配器转换

> **修正**：原方案 4.4 用 Ollama 原生 `/api/chat`、`/api/tags` 示例与「统一 OpenAI SDK」矛盾。本版统一走 `chat.completions` 与 `/v1/models`，Provider 层只在后端存在一份。

### 3.4 数据存储

| 用途 | 方案 | 备注 |
|------|------|------|
| 对话记录 | SQLite（本地）/ PostgreSQL（多用户） | 会话表 + 消息表 + 工具调用表 |
| 向量检索 | ChromaDB（内置持久化）/ pgvector | 单机默认 ChromaDB |
| 用户配置 | 系统钥匙串 + 本地 JSON（不含密钥） | 密钥绝不明文入库 |
| MCP 工具注册表 | SQLite | 含启停状态、来源、审计 |

### 3.5 依赖版本（更新口径）

> 原方案给出的 Electron ^28、langgraph 0.0.30、langchain 0.1.0、chromadb 0.4.0 等版本已明显过时。**以下为选型口径，实际以开发当日锁定版本为准，并写入 lockfile 固定。**

| 依赖 | 建议口径 | 说明 |
|------|----------|------|
| Electron / electron-builder | 当前稳定 LTS（≥33） | 升级以跟进安全与 API |
| React | 18.x 或 19.x | 跟随生态稳定版 |
| langgraph / langchain | 当前稳定大版本 | 0.0.30 过旧，API 差异大 |
| chromadb | 当前稳定版（0.5+/1.x） | 0.4 及以下 API 有变化 |
| ollama python | 当前稳定版 | 使用 `ollama.embed()`（非废弃的 `embeddings(prompt=)`） |
| openai | 当前稳定版 | 统一兼容层基础 |

---

## 四、核心模块设计

### 4.1 对话引擎（Chat Engine）

位置：**Electron 渲染进程**，职责仅为 SSE 客户端与会话 UI 状态；不包含任何工具调用调度逻辑。

```
User Input → 组装请求 → POST /api/chat（SSE）→ 按事件分发渲染
                                              ├─ meta / thinking / tool_call / tool_result
                                              ├─ text（增量渲染）
                                              └─ done / error
```

**关键设计**：

- 使用 **SSE** 流式接收；解析层必须处理**粘包/半包**（见 §8.1）
- 每轮维护 `ConversationContext`：历史消息 + 工具结果 + 知识库引用（token 预算见 §4.6）
- 事件类型：`meta / thinking / tool_call / tool_result / text / done / error`（协议见 §5）
- 前端 `AbortController` 支持「停止生成」；断线自动提示并允许重连

### 4.2 Agent 编排（LangGraph，唯一权威）

```
User Input → Context Builder → LLM Router → LangGraph Loop
                                             ├─ chat_node（LLM 生成）
                                             ├─ tool_check_node（检查 tool_calls）
                                             ├─ tool_node（MCP 执行，结果回灌）
                                             └─ 循环直至无工具调用 → final_answer
```

- 工具调用采用 **OpenAI 风格结构化字段**（`tool_calls` + `tool_call_id`），多轮回灌正确关联
- RAG 作为工具之一（`knowledge_search`）进入 Agent，避免与工具系统割裂
- 长会话自动触发**摘要压缩节点**（见 §4.6）

### 4.3 MCP 工具管理器（MCP Manager）

```
用户启用工具集 → 校验来源（白名单）→ 启动 MCP Server 子进程 → 注册工具到 Agent
    → 执行（记录审计）→ 用户停用/会话结束 → 清理进程
```

**内置工具集**：

- `filesystem`：文件读写、搜索（受限根目录）
- `shell`：命令执行（白名单 + 确认 + 审计，见 §6）
- `browser`：浏览器自动化（Playwright，headless 优先）
- `code`：代码分析（Tree-sitter）
- `knowledge`：企业知识库检索（RAG）

### 4.4 企业知识库（RAG Pipeline）

```
文档导入 → 解析（PDF/MD/TXT/Word/扫描件OCR可选）→ 分块
    → 向量化（本地 Embedding）→ 存储（ChromaDB）
    → 检索：查询改写 → 混合检索（BM25 + 向量）→ 重排序 → 注入 Prompt
```

**关键设计（相对原版增强）**：

- **Embedding 模型**：中文为主的私有知识库**首选 `bge-m3`（多语言）**；`nomic-embed-text` 偏英文，仅英文语料时用。两者均经 Ollama 本地提供，数据不出内网。
- **分块策略**：按 Markdown 标题/段落语义分块，块长与 overlap 可配（默认 500/50），保留文档元数据（来源、页码、标题路径）。
- **混合检索**：BM25 关键词 + 向量检索融合（RRF 融合），提升专业术语命中。
- **重排序**：可选 Cross-Encoder rerank，Top-K 检索后重排再取 Top-N。
- **引用溯源**：检索结果携带文档 ID 与原文片段，前端展示「引用来源」。
- **增量更新**：文档去重（内容 hash）、按文档删除/重建索引，避免重复导入。
- **权限**：多用户模式下按知识库粒度做访问控制（见 §6）。

### 4.5 Provider 适配层（后端统一，仅一份）

```python
# provider/base.py —— 统一抽象
class BaseProvider(ABC):
    @abstractmethod
    async def chat(self, messages, model, **kwargs) -> AsyncIterator[dict]: ...
    @abstractmethod
    async def list_models(self) -> list[ModelInfo]: ...

# 实现（均基于 openai SDK 兼容协议）
class OllamaProvider(BaseProvider): ...   # base_url=http://localhost:11434/v1
class VLLMProvider(BaseProvider): ...
class PrivateAPIProvider(BaseProvider): ...
```

配置示例（密钥不在此文件，见 §6）：

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
      "models": ["your-model-v3"]
    }
  ]
}
```

> `apiKeyRef` 指向系统钥匙串中的条目名，而非内嵌密钥明文。

### 4.6 上下文管理（token 预算）

本地 7B 模型上下文小，长对话必须治理，否则上下文溢出是必然故障：

| 策略 | 说明 |
|------|------|
| 滑动窗口 | 仅保留最近 N 轮（可配，如 20 轮） |
| token 预算 | 系统提示 15% + 历史 40% + RAG 20% + 生成 25%（示例，可调） |
| 摘要压缩 | 超预算时对旧消息做 LLM 摘要，压缩进上下文中部节点 |
| 工具结果裁剪 | 工具返回超长时截断/摘要后再入上下文 |

### 4.7 模型路由与降级

| 任务类型 | 路由模型 | 降级链 |
|----------|----------|--------|
| 通用对话 | 通用模型（qwen2.5:7b） | → 私有 API 模型 → 报错提示 |
| 编程问答 | 代码模型（deepseek-coder:6.7b） | → 通用模型 |
| 向量化 | bge-m3 | → nomic-embed-text |
| Embedding/聊天 分离 | 可分别指定模型 | 健康检查失败自动切换 |

---

## 五、通信协议定义（本版新增）

### 5.1 SSE 事件协议

`POST /api/chat` 返回 `text/event-stream`，事件格式 `data: {json}\n\n`。事件类型如下：

| event | 字段 | 说明 |
|-------|------|------|
| `meta` | `message_id, model, conversation_id` | 会话/消息元信息 |
| `thinking` | `text` | 推理过程（可折叠展示） |
| `tool_call` | `call_id, name, arguments` | 声明要执行的工具 |
| `tool_result` | `call_id, content, is_error` | 工具执行结果 |
| `text` | `delta` | 增量文本 |
| `done` | `usage {prompt_tokens, completion_tokens}, finish_reason` | 正常结束 |
| `error` | `code, message` | 错误（code 见下） |

错误码约定：`model_not_found` / `tool_timeout` / `tool_denied` / `context_overflow` / `rate_limited` / `internal`。

流式结束哨兵：`data: [DONE]`（部分代理/网关约定，客户端应兼容）。

### 5.2 HTTP 接口清单

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/chat` | 对话（SSE 流式） |
| GET | `/api/models` | 模型列表 |
| GET/POST | `/api/conversations` | 会话列表 / 新建 |
| GET/DELETE | `/api/conversations/{id}` | 会话详情 / 删除 |
| POST | `/api/conversations/{id}/export` | 会话导出（MD/JSON） |
| POST | `/api/knowledge/ingest` | 文档导入 |
| POST | `/api/knowledge/search` | 知识库检索（带引用） |
| DELETE | `/api/knowledge/{doc_id}` | 删除文档索引 |
| GET | `/api/mcp/tools` | 工具集与状态 |
| POST | `/api/mcp/{toolset}/enable` `/disable` | 工具集启停 |
| GET/PUT | `/api/settings` | 配置读取/更新（不含密钥明文） |
| GET | `/api/usage` | 用量统计（见 §12） |

> 所有接口仅监听 `127.0.0.1`，由 Electron 主进程代理对外；生产环境建议加随机 token 鉴权（见 §6）。

---

## 六、安全设计（本版新增，MVP 必备）

| 风险面 | 对策 |
|--------|------|
| **密钥泄露** | API Key 存入系统钥匙串（macOS Keychain / Windows Credential Manager / 加密文件 + 主密码），运行时读取；配置文件中仅存引用名。**禁止明文 JSON 存密钥**。 |
| **Shell 工具滥用** | 命令**白名单**（允许集）+ 路径前缀校验（受限根目录）+ 危险命令黑名单（rm -rf / 网络扫描等）+ 执行超时 + **每次执行前 UI 确认** + 全量审计日志；可选 Docker/沙箱隔离模式 |
| **MCP Server 不可信** | 仅允许白名单来源的 MCP Server；启用前展示其声明的工具与权限，用户确认；记录加载来源 |
| **本地端口暴露** | Agent 服务仅绑定 `127.0.0.1`；Electron 与 Agent 间加一次性随机 token 鉴权头 |
| **知识库权限** | 单用户模式无共享；多用户模式按知识库粒度 ACL，检索前鉴权 |
| **数据泄露兜底** | 对话与文档默认仅本地；导出需用户显式操作；提供「本地删除」入口 |
| **供应链** | 依赖锁文件（lockfile）+ 关键依赖版本固定 + 定期安全扫描（npm audit / pip-audit） |

---

## 七、项目目录结构（修订版）

```
private-ai-client/
├── desktop/                  # Electron 客户端
│   ├── src/
│   │   ├── components/       # React 组件
│   │   │   ├── chat/         # 对话界面（ChatWindow/MessageList/MessageItem/InputBox）
│   │   │   ├── knowledge/    # 知识库界面（KnowledgeBase/DocumentList/SearchPanel）
│   │   │   ├── tools/        # 工具面板（ToolPanel/ToolRegistry）
│   │   │   └── settings/     # 设置（ProviderSettings/MCPSettings/SecuritySettings）
│   │   ├── sse/              # SSE 客户端（含粘包解析、事件分发）
│   │   │   └── sseClient.ts
│   │   └── store/            # Zustand UI 状态
│   │       ├── useConversationStore.ts
│   │       └── useSettingsStore.ts
│   ├── electron/             # Electron 主进程
│   │   ├── main.ts           # 主进程入口
│   │   ├── preload.ts        # 白名单 IPC 暴露
│   │   ├── ipc/              # IPC 处理
│   │   └── agentProcess.ts   # Python 子进程生命周期管理（见 §10.1）
│   ├── package.json
│   └── electron-builder.yml
│
├── agent/                    # Python Agent 服务
│   ├── app/
│   │   ├── main.py           # FastAPI 入口（127.0.0.1 + token 鉴权）
│   │   ├── api/              # HTTP 路由（chat/knowledge/mcp/models/settings/usage）
│   │   ├── agents/           # LangGraph（base/coding/general）
│   │   ├── rag/              # RAG（ingest/chunker/embedder/retriever/rerank）
│   │   ├── tools/            # MCP 工具实现（filesystem/shell/browser/code/knowledge）
│   │   ├── providers/        # 统一 Provider 层（base/ollama/vllm/private_api）
│   │   ├── context/          # 上下文管理（token 预算/压缩）
│   │   └── security/         # 钥匙串读取、命令白名单、审计日志
│   ├── tests/                # pytest 单元/集成测试
│   ├── eval/                 # Agent 评测集（golden set）与脚本
│   ├── requirements.txt
│   └── pyproject.toml
│
├── config/                   # 配置文件（不含密钥）
│   ├── providers.json
│   ├── mcp-servers.json
│   └── rag-config.json
│
├── data/                     # 本地数据（gitignore）
│   ├── conversations/
│   ├── knowledge-base/
│   └── mcp-sessions/
│
├── scripts/
│   ├── install.sh
│   ├── build-desktop.sh
│   ├── build-agent.sh        # PyInstaller/embed python 打包 Agent
│   └── start-agent.sh
│
└── README.md
```

> **修正**：删除原「desktop/src/providers/」——Provider 层只在后端一份；删除原 desktop/src/engine/ToolDispatcher 的「工具调度」职责（收敛到后端）；新增 sse/、security/、context/、tests/、eval/。

---

## 八、关键技术实现（修正后的代码示例）

### 8.1 流式对话（SSE 客户端，修正粘包/事件处理）

**前端 sseClient.ts：**

```typescript
export class SSEClient {
  private controller: AbortController | null = null;

  async chat(
    baseUrl: string,
    payload: { message: string; conversationId: string },
    handlers: {
      onEvent: (ev: SSEEvent) => void;
      onDone: (usage?: Usage) => void;
      onError: (code: string, message: string) => void;
    }
  ): Promise<void> {
    this.controller = new AbortController();
    const resp = await fetch(`${baseUrl}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: this.controller.signal,
    });
    if (!resp.ok || !resp.body) {
      handlers.onError('internal', `HTTP ${resp.status}`);
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = ''; // 处理粘包/半包

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // 按 SSE 双换行切分，解析可能跨 chunk 的行
      let sepIdx: number;
      while ((sepIdx = buffer.indexOf('\n\n')) !== -1) {
        const raw = buffer.slice(0, sepIdx);
        buffer = buffer.slice(sepIdx + 2);
        const lines = raw.split('\n').filter((l) => l.startsWith('data: '));
        for (const line of lines) {
          const data = line.slice(6).trim();
          if (data === '[DONE]') { handlers.onDone(); continue; }
          try {
            handlers.onEvent(JSON.parse(data) as SSEEvent);
          } catch {
            handlers.onError('internal', 'invalid SSE payload');
          }
        }
      }
    }
  }

  stop() { this.controller?.abort(); }
}
```

**后端 chat.py（FastAPI，SSE 流式 + 协议封装）：**

```python
@app.post("/api/chat")
async def chat(req: ChatRequest):
    async def event_stream():
        # 1. meta 事件
        yield _sse({"event": "meta", "message_id": msg_id, "model": model})
        # 2. Agent 循环（LangGraph）
        async for step in agent.astream(req.message, req.conversation_id):
            if step.type == "thinking":
                yield _sse({"event": "thinking", "text": step.text})
            elif step.type == "tool_call":
                yield _sse({"event": "tool_call", "call_id": step.call_id,
                            "name": step.name, "arguments": step.arguments})
            elif step.type == "tool_result":
                yield _sse({"event": "tool_result", "call_id": step.call_id,
                            "content": step.content, "is_error": step.is_error})
            elif step.type == "text":
                yield _sse({"event": "text", "delta": step.delta})
        # 3. 结束事件
        yield _sse({"event": "done", "usage": usage, "finish_reason": "stop"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

### 8.2 LangGraph Agent 编排（修正工具调用结构化字段）

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict

class AgentState(TypedDict):
    messages: list  # OpenAI 风格消息（含 role="tool" 回灌）
    conversation_id: str

def chat_node(state: AgentState) -> AgentState:
    """调用 LLM 生成（绑定模型路由与上下文预算）"""
    messages = build_prompt_with_budget(state["messages"])  # §4.6
    resp = llm.chat(messages, model=route_model(state))
    return {"messages": state["messages"] + [resp]}

def route_after_chat(state: AgentState) -> str:
    """检查结构化 tool_calls（而非伪属性 needs_tool）"""
    last = state["messages"][-1]
    return "tool" if getattr(last, "tool_calls", None) else "final"

def tool_node(state: AgentState) -> AgentState:
    """执行工具，并以 role='tool' + tool_call_id 回灌，保证多轮关联"""
    tool_results = []
    for call in state["messages"][-1].tool_calls:
        result = mcp_manager.execute(call["name"], call["arguments"])
        tool_results.append({
            "role": "tool",
            "tool_call_id": call["id"],
            "content": truncate_for_budget(result.content),  # §4.6 裁剪
        })
    return {"messages": state["messages"] + tool_results}

graph = StateGraph(AgentState)
graph.add_node("chat", chat_node)
graph.add_node("tool", tool_node)
graph.add_conditional_edges("chat", route_after_chat, {"tool": "tool", "final": END})
graph.add_edge("tool", "chat")  # 工具结果回灌后再进 LLM
graph.set_entry_point("chat")
agent = graph.compile()
```

### 8.3 RAG 导入与检索（修正 Embedding 与 ChromaDB API）

```python
# ingest.py —— 正确做法：逐块/分批向量化，不拼接全文
async def ingest_document(file_path: str, collection: Collection):
    text = await parse_document(file_path)            # PDF/MD/TXT/Word（扫描件走 OCR）
    chunks = chunker.split(text, chunk_size=500, overlap=50)  # 保留元数据

    # 分批 embed（ollama.embed 支持批量 input；不一次性拼接全篇）
    embeds = await ollama.embed(
        model="bge-m3",
        input=[c.text for c in chunks],               # 每块独立向量
    )

    collection.add(
        ids=[c.id for c in chunks],
        documents=[c.text for c in chunks],
        metadatas=[c.meta for c in chunks],           # 来源/页码/标题路径
        embeddings=embeds["embeddings"],              # 与 documents 一一对应
    )
    return {"chunks": len(chunks), "status": "success"}
```

```python
# retriever.py —— 混合检索 + 重排 + 引用
async def retrieve(query: str, collection: Collection, top_k: int = 20, final_k: int = 5):
    query_emb = (await ollama.embed(model="bge-m3", input=[query]))["embeddings"][0]

    vector_hits = collection.query(query_embeddings=[query_emb], n_results=top_k)
    bm25_hits = bm25_index.search(query, top_k)        # BM25 关键词检索
    fused = rrf_fusion(vector_hits, bm25_hits)         # RRF 融合

    reranked = cross_encoder.rerank(query, fused, top_n=final_k)  # 可选
    return [{
        "text": r.text,
        "source": r.meta["source"],
        "page": r.meta.get("page"),
        "score": r.score,
    } for r in reranked]   # 前端据此展示「引用来源」
```

### 8.4 Provider 适配（后端统一，OpenAI SDK）

```python
# providers/base.py
class OpenAICompatibleProvider(BaseProvider):
    def __init__(self, base_url: str, api_key: str | None, model_map: dict):
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key or "none")

    async def chat(self, messages, model, **kwargs):
        stream = await self.client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs)
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield {"type": "text", "delta": delta.content}
            if chunk.choices[0].finish_reason:
                yield {"type": "done", "finish_reason": chunk.choices[0].finish_reason}

    async def list_models(self):
        resp = await self.client.models.list()
        return [m.id for m in resp.data]
```

> Ollama / vLLM / 私有 API 均通过 `OpenAICompatibleProvider` 接入，仅 `base_url` 与鉴权方式不同；不再为每个厂商重复实现一份 `chat()`。

---

## 九、开发路线图（修正：去重 + 补安全/测试/验收）

### Phase 0：需求冻结与 PoC（1 周）
- [ ] 确定 MVP 范围、验收标准（见 §1.3）
- [ ] 完成「单一 Agent 权威」架构评审，冻结 SSE/API 协议（§5）
- [ ] PoC：Ollama 本地模型 + OpenAI 兼容接口跑通最小流式对话

### Phase 1：基础框架（2–3 周）
- [ ] Electron + React 项目骨架、目录结构（§7）
- [ ] 基础对话 UI（消息列表、输入框、停止生成）
- [ ] 后端 FastAPI 骨架 + 127.0.0.1 绑定 + token 鉴权
- [ ] SSE 客户端（粘包处理）与流式对话端到端打通
- [ ] 会话持久化（SQLite）与会话列表 UI
- [ ] 模型配置与切换界面（**合并原 Phase1/2 重复项**）

### Phase 2：核心能力（3–4 周）
- [ ] MCP Manager：工具集启停、来源白名单、审计日志
- [ ] 内置工具：filesystem、shell（白名单 + 确认 + 超时）、knowledge
- [ ] RAG：文档导入 + 混合检索 + 引用展示
- [ ] 上下文管理：token 预算 + 滑动窗口（§4.6）
- [ ] 密钥存储接入系统钥匙串（§6）
- [ ] **安全基线验证**（Shell 白名单、端口、密钥）

### Phase 3：Agent 化与编程助手（3–4 周）
- [ ] LangGraph 多轮工具调用闭环（含 tool_calls 回灌）
- [ ] 模型路由与降级（§4.7）
- [ ] 编程助手 Agent（代码问答、重构建议）
- [ ] 知识库管理界面（上传、删除、检索历史、引用查看）
- [ ] 上下文摘要压缩节点（§4.6）
- [ ] **Agent 评测集搭建**（§11，golden set 基线）

### Phase 4：打磨发布（2–3 周）
- [ ] Electron ↔ Python 进程生命周期管理（§10.1）
- [ ] 打包分发（macOS/Windows/Linux）+ 自动更新
- [ ] 用户文档 + 内测反馈迭代
- [ ] 性能优化（启动速度、内存、首字延迟）
- [ ] **发布前验收**：按 §1.3 逐项核对，跑全量测试与 eval

> **预期**：按以上节奏约 **10–14 周完成 MVP/内测版**；「具备商业化条件」不在本阶段承诺，需另行评估安全合规、运维、客服与扩容（见 §1.4、§14）。

---

## 十、部署方案

### 10.1 单用户本地部署（含 Electron↔Python 生命周期管理）

```
┌──────────────────────────────────────────────┐
│  Desktop App（Electron）                      │
│  ├─ 渲染进程：UI                              │
│  ├─ 主进程：IPC + Agent 子进程管理             │
│  └─ Python Agent 进程（随 app 启动/退出）      │
│       ├─ 健康检查（/healthz）与崩溃自动重启     │
│       ├─ 数据：SQLite + ChromaDB（本地）       │
│       └─ 模型：Ollama / vLLM（本地推理）       │
└──────────────────────────────────────────────┘
数据全部本地，无需联网（除模型下载）。
```

**关键点（原版缺失，本版补齐）**：

- **Python 运行时打包**：PyInstaller 或 embeddable Python 打包 Agent，随安装包分发；避免依赖用户机器装 Python。
- **生命周期管理**：Electron 主进程负责 spawn/stop Agent 子进程；轮询 `/healthz`；崩溃自动重启（限次+提示）；app 退出时优雅关闭 Agent。
- **启动流程**：首启检查 Ollama 是否安装并引导安装；模型缺失时引导 `ollama pull`。
- **升级联动**：Agent 与 Electron 版本配套升级，避免协议不匹配。

### 10.2 多用户私有部署（可选，MVP 后）

```
┌───────────────────────────────────────────┐
│  Web App / Desktop App                     │
└──────────────────┬────────────────────────┘
                   │ HTTPS + 认证（OIDC/JWT）
┌──────────────────▼────────────────────────┐
│  API Gateway（Nginx）                      │
└──────────────────┬────────────────────────┘
                   │
┌──────────────────▼────────────────────────┐
│  Python Agent Service（Docker）            │
│  + PostgreSQL + pgvector + 对象存储        │
└──────────────────┬────────────────────────┘
                   │
┌──────────────────▼────────────────────────┐
│  LLM Server（vLLM / Ollama 集群 + GPU 调度）│
└───────────────────────────────────────────┘
```

多用户模式需补充：认证与授权（OIDC/JWT）、知识库 ACL、多租户隔离、用量计费、GPU 队列调度。**本版默认单机，多用户作为可选项并明确其额外工作量。**

---

## 十一、测试与评测（本版新增）

AI 应用不能只靠「能跑」，必须有可回归的评测基线：

| 层次 | 内容 | 工具 |
|------|------|------|
| 单元测试 | Provider、分块、上下文预算、SSE 解析 | pytest / vitest |
| 集成测试 | 端到端对话、MCP 工具调用、RAG 导入检索 | pytest + TestClient |
| **Agent 评测** | Golden set：输入 → 期望工具调用序列/答案；指标：任务完成率、工具调用准确率、RAG Top-5 命中率、平均轮次 | 自建 `agent/eval/` 脚本 + 评测报告 |
| 回归 | 每次改动跑全量测试 + eval 对比基线 | CI（GitHub Actions 可选） |

> 每次调整 Prompt / 模型 / 分块参数，必须先跑评测集再合入，防止「修好一个、回归一片」。

---

## 十二、可观测性与用量统计（本版新增）

- **结构化日志**：后端 logging 落盘（request_id 贯穿）；Electron 主进程单独日志。
- **用量统计**：每会话/每模型记录 prompt/completion tokens 与耗时，`GET /api/usage` 提供统计，UI 展示「本日/本周用量」。
- **成本核算**：按模型单价换算成本（私有 API 场景必需），本地模型可只统计 token。
- **错误上报**：本地日志 + 可选脱敏后上报；生产排障依赖 `error` 事件与 request_id 关联。

---

## 十三、关键设计决策（修正版）

| 决策点 | 选择 | 理由（修正） |
|--------|------|--------------|
| 桌面框架 | Electron | 生态成熟、系统能力完整、MCP/Node 工具链好 |
| Agent 编排 | 后端 Python（LangGraph） | 单一权威，避免前后端双循环 |
| LLM 调用 | 后端统一 OpenAI 兼容层 | 一处适配，Ollama/vLLM/私有 API 同构 |
| 流式传输 | SSE | 单向流式足够；自带断线重连，实现简单（**非**「支持 HTTP/2」） |
| 向量库 | ChromaDB | 本地持久化、零配置；多用户再上 pgvector |
| Embedding | bge-m3（中文主推） | 多语言质量高；nomic-embed-text 偏英文仅备选 |
| Agent 框架 | LangGraph | 细粒度控制、可视化调试、便于加压缩节点 |
| 密钥 | 系统钥匙串 | 不落明文 JSON |
| 通信协议 | 结构化 SSE 事件（§5） | 前后端按协议并行开发 |

---

## 十四、风险与应对（扩充版）

| 风险 | 影响 | 应对策略 |
|------|------|----------|
| 上下文溢出（长对话/长工具结果） | 对话中断、回答劣化 | §4.6 token 预算 + 滑动窗口 + 摘要压缩 |
| 模型幻觉 / 引用不可信 | 错误结论 | 工具结果优先、知识库引用溯源、允许查证 |
| Ollama 本地性能不足 | 首字慢、响应卡 | vLLM 备选、模型量化、异步流式 |
| 向量检索延迟/准确率低 | 检索体验差 | 混合检索 + rerank + 缓存 + 索引调优（§4.4） |
| MCP/Shell 工具安全 | 数据泄露、误删 | §6 白名单 + 确认 + 审计 + 沙箱 |
| 密钥泄露 | 商业模型被盗用 | 系统钥匙串 + 最小权限 |
| Python 打包复杂 | 本地部署失败率高 | PyInstaller/embed python + CI 打包 + 生命周期管理（§10.1） |
| Electron 体积大、内存高 | 下载慢、卡顿 | asar、按需加载、流式渲染、内存监控 |
| 打包平台差异 | 三平台交付延期 | CI 矩阵构建 + 每平台冒烟 |
| 多用户并发/扩容 | 商业化受阻 | 明确定位 MVP，多用户单独立项（§1.4、§10.2） |
| 数据合规 | 隐私风险 | 本地优先、隐私政策、删除入口、审计日志 |

---

## 十五、总结

本版为**可进入开发的原型方案**，核心结论：

1. **架构收敛**：Agent 编排唯一归属后端 Python，前端只做 UI 与系统能力，消除双 Agent 循环的架构风险。
2. **统一协议**：LLM 统一走 OpenAI 兼容层、前后端通信走结构化 SSE 协议，保证并行开发与可替换性。
3. **安全前置**：密钥钥匙串、Shell 白名单+确认+审计、端口绑定，作为 MVP 硬基线而非「后期补」。
4. **质量闭环**：上下文治理、模型路由、混合检索 RAG、Agent 评测集、用量统计，构成可迭代闭环。
5. **务实节奏**：2–3 人团队约 **10–14 周交付 MVP/内测版**；商业化另评估。

> 待团队确认后，下一步建议：① 冻结 §5 通信协议；② 按 §1.3 验收标准细化任务拆分；③ 启动 Phase 0 PoC。

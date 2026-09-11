# 私有域 AI 助手客户端开发方案

## 一、系统概述

目标产品：类 WorkBuddy 的桌面 AI 助手，支持**自托管开源模型**（Ollama/vLLM）和**私有化商业模型 API**，内置 MCP 工具执行、企业知识库 RAG、编程助手四大核心能力。

---

## 二、技术栈选型

### 2.1 客户端（Desktop App）

| 维度 | 推荐 | 备选 |
|------|------|------|
| 框架 | **Electron + React + TypeScript** | Tauri v2（更轻量但生态弱） |
| UI 组件 | Ant Design / Shadcn UI | Tailwind + Radix |
| 状态管理 | Zustand | Redux Toolkit |
| 对话引擎 | 自研（SSE + 本地消息持久化） | |
| 打包分发 | electron-builder | Tauri bundle |

> **选 Electron 的理由**：2-3 人小团队、需要深度系统能力（文件系统、MCP 进程管理、原生通知）、有现成的 MCP SDK 生态。Tauri 更适合性能敏感型应用，但 Rust FFI 成本高。

### 2.2 后端服务（Python Agent 层）

```
FastAPI + LangGraph / CrewAI
```

- **FastAPI**：异步 HTTP/SSE 接口，对接 LLM
- **LangGraph**：Agent 编排（工具调用循环、RAG 检索路由）
- **MCP SDK**：Python 版 MCP Server 实现
- **向量检索**：ChromaDB（本地）+ pgvector（PostgreSQL 可选）

### 2.3 大模型适配层

```
┌─────────────────────────────────────┐
│         Provider 抽象接口            │
├──────────┬──────────┬───────────────┤
│ Ollama   │  vLLM    │  OpenAI兼容   │
│ (本地)   │ (本地)   │  (私有API)    │
└──────────┴──────────┴───────────────┘
```

使用 **OpenAI SDK 兼容协议**统一调用，Ollama 和私有化商业模型都走同一套 chat.completions 接口。

### 2.4 数据存储

| 用途 | 方案 |
|------|------|
| 对话记录 | SQLite（本地）/ PostgreSQL（多用户） |
| 向量检索 | ChromaDB（内置持久化）/ pgvector |
| 用户配置 | JSON 文件（~/.config/app/） |
| MCP 工具注册表 | SQLite |

---

## 三、系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        Desktop Client                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │   Chat UI   │  │  Knowledge  │  │     Tool Panel          │ │
│  │  (React)    │  │   Base UI   │  │   (MCP 工具列表)        │ │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────────┘ │
│         │                │                     │                │
│  ┌──────▼────────────────▼─────────────────────▼──────────────┐ │
│  │              Agent Engine (TypeScript)                      │ │
│  │  • 对话状态管理  • SSE 流式接收  • 工具调用调度              │ │
│  └──────────────────────────┬──────────────────────────────────┘ │
└─────────────────────────────┼───────────────────────────────────┘
                              │ HTTP/SSE
┌─────────────────────────────▼───────────────────────────────────┐
│                      Python Agent Service                       │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────┐   │
│  │  LLM Router  │  │   MCP Manager│  │      RAG Pipeline   │   │
│  │  • 模型选择   │  │  • Server启停│  │  • 文档解析         │   │
│  │  • 上下文组装 │  │  • 工具注册   │  │  • 向量检索         │   │
│  │  • 流式返回   │  │  • 参数校验   │  │  • 摘要生成         │   │
│  └──────────────┘  └──────────────┘  └─────────────────────┘   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                  Storage Layer                          │    │
│  │  SQLite + ChromaDB + JSON Config                       │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                      LLM Providers                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────────┐          │
│  │  Ollama  │  │   vLLM   │  │  Private API (OpenAI │          │
│  │  (本地)  │  │ (本地)   │  │   兼容接口)           │          │
│  └──────────┘  └──────────┘  └──────────────────────┘          │
└─────────────────────────────────────────────────────────────────┘
```

---

## 四、核心模块设计

### 4.1 对话引擎（Chat Engine）

```
User Input → Context Builder → LLM Router → Stream Response
                                      ↓
                              Tool Call Detected?
                                      ↓ Yes
                              MCP Manager → Execute Tool → Update Context → Loop
                                      ↓ No
                              Return Final Response
```

**关键设计**：
- 使用 **SSE（Server-Sent Events）** 实现流式响应
- 每轮对话维护 `ConversationContext`（历史消息 + 工具结果 + 知识库引用）
- 支持 **thinking / tool_call / text** 三种流式事件类型

### 4.2 MCP 工具管理器（MCP Manager）

```python
# MCP Server 生命周期
1. 用户启用某个工具集 → 启动对应 MCP Server 子进程
2. 注册工具到 LangGraph 节点
3. Agent 执行时动态调用
4. 会话结束 → 清理进程

# 内置工具集示例
- filesystem: 文件读写、搜索
- shell: 命令执行（沙箱）
- browser: 浏览器自动化（Playwright）
- code: 代码分析（Tree-sitter）
- knowledge: 企业知识库检索
```

### 4.3 企业知识库（RAG Pipeline）

```
文档导入 → 解析（PDF/MD/TXT/Word）→ 分块（Chunking）
    → 向量化（本地 Embedding 模型）→ 存储（ChromaDB）
    → 检索时：查询改写 → 向量检索 → 上下文注入 Prompt
```

**关键技术点**：
- 本地 Embedding：使用 `nomic-embed-text` 或 `bge-m3`（Ollama 支持）
- 分块策略：按语义分块（Markdown 标题分割）
- 检索增强：Top-K + 重排序（Cross-Encoder 可选）

### 4.4 Provider 适配器（统一 LLM 接口）

```typescript
interface LLMProvider {
  chat(messages: Message[], options: ChatOptions): AsyncIterable<Chunk>
  listModels(): Promise<ModelInfo[]>
}

// 实现
class OllamaProvider implements LLMProvider { /* ... */ }
class VLLMProvider implements LLMProvider { /* ... */ }
class PrivateAPIProvider implements LLMProvider { /* ... */ }
```

配置示例（JSON）：
```json
{
  "providers": [
    {
      "id": "ollama-local",
      "type": "ollama",
      "baseUrl": "http://localhost:11434",
      "models": ["qwen2.5:7b", "deepseek-coder:6.7b"]
    },
    {
      "id": "private-api",
      "type": "openai-compatible",
      "baseUrl": "https://api.your-company.com/v1",
      "apiKey": "${PRIVATE_API_KEY}",
      "models": ["your-model-v3"]
    }
  ]
}
```
## 五、项目目录结构

```
private-ai-client/
├── desktop/                  # Electron 客户端
│   ├── src/
│   │   ├── components/       # React 组件
│   │   │   ├── chat/         # 对话界面
│   │   │   │   ├── ChatWindow.tsx
│   │   │   │   ├── MessageList.tsx
│   │   │   │   ├── MessageItem.tsx
│   │   │   │   └── InputBox.tsx
│   │   │   ├── knowledge/    # 知识库界面
│   │   │   │   ├── KnowledgeBase.tsx
│   │   │   │   ├── DocumentList.tsx
│   │   │   │   └── SearchPanel.tsx
│   │   │   ├── tools/        # 工具面板
│   │   │   │   ├── ToolPanel.tsx
│   │   │   │   └── ToolRegistry.tsx
│   │   │   └── settings/     # 设置界面
│   │   │       ├── ProviderSettings.tsx
│   │   │       └── MCPSettings.tsx
│   │   ├── engine/           # 对话引擎
│   │   │   ├── ChatEngine.ts     # 核心对话逻辑
│   │   │   ├── ContextBuilder.ts # 上下文构建
│   │   │   └── ToolDispatcher.ts # 工具调度
│   │   ├── providers/        # LLM Provider 适配器
│   │   │   ├── BaseProvider.ts
│   │   │   ├── OllamaProvider.ts
│   │   │   ├── VLLMProvider.ts
│   │   │   └── PrivateAPIProvider.ts
│   │   ├── mcp/              # MCP 管理器
│   │   │   ├── MCPManager.ts
│   │   │   └── tools/        # 内置工具定义
│   │   └── store/            # Zustand 状态管理
│   │       ├── useConversationStore.ts
│   │       ├── useProviderStore.ts
│   │       └── useKnowledgeStore.ts
│   ├── electron/             # Electron 主进程
│   │   ├── main.ts           # 主进程入口
│   │   ├── preload.ts        # 预加载脚本
│   │   └── ipc/              # IPC 处理
│   ├── package.json
│   └── electron-builder.yml
│
├── agent/                    # Python Agent 服务
│   ├── app/
│   │   ├── main.py           # FastAPI 应用入口
│   │   ├── api/              # HTTP 路由
│   │   │   ├── chat.py       # 对话接口（SSE）
│   │   │   ├── knowledge.py  # 知识库接口
│   │   │   ├── mcp.py        # MCP 管理接口
│   │   │   └── models.py     # 模型列表接口
│   │   ├── agents/           # LangGraph Agent
│   │   │   ├── base.py       # 基础 Agent
│   │   │   ├── coding.py     # 编程助手 Agent
│   │   │   └── general.py    # 通用 Agent
│   │   ├── rag/              # RAG Pipeline
│   │   │   ├── ingest.py     # 文档导入
│   │   │   ├── chunker.py    # 文本分块
│   │   │   ├── embedder.py   # 向量化
│   │   │   └── retriever.py  # 检索
│   │   ├── tools/            # MCP 工具实现
│   │   │   ├── filesystem.py
│   │   │   ├── shell.py
│   │   │   └── knowledge.py
│   │   └── models/           # 数据模型
│   │       ├── conversation.py
│   │       └── message.py
│   ├── requirements.txt
│   └── pyproject.toml
│
├── config/                   # 配置文件
│   ├── providers.json        # LLM Provider 配置
│   ├── mcp-servers.json      # MCP Server 配置
│   └── rag-config.json       # RAG 参数配置
│
├── data/                     # 本地数据（gitignore）
│   ├── conversations/        # 对话记录
│   ├── knowledge-base/       # 知识库向量数据
│   └── mcp-sessions/         # MCP 会话
│
├── scripts/                  # 构建/部署脚本
│   ├── install.sh
│   ├── build-desktop.sh
│   └── start-agent.sh
│
└── README.md
```

---

## 六、关键技术实现

### 6.1 流式对话（SSE）

**前端 ChatEngine.ts：**
```typescript
export class ChatEngine {
  private baseUrl: string;
  private conversations: Map<string, Conversation> = new Map();

  async sendMessage(
    conversationId: string,
    message: string,
    onChunk: (chunk: ChatChunk) => void
  ): Promise<void> {
    const response = await fetch(`${this.baseUrl}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, conversationId }),
    });

    const reader = response.body!.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const text = decoder.decode(value);
      const lines = text.split('\n').filter(Boolean);

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = JSON.parse(line.slice(6)) as ChatChunk;
          onChunk(data);
        }
      }
    }
  }
}
```

**后端 chat.py（FastAPI）：**
```python
@app.post("/api/chat")
async def chat(req: ChatRequest, background_tasks: BackgroundTasks):
    async def event_stream():
        async for chunk in agent.run(req.message, req.context):
            yield f"data: {json.dumps(chunk)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream"
    )
```

### 6.2 LangGraph Agent 编排

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict, Literal

class AgentState(TypedDict):
    messages: list[Message]
    tools: list[Tool]
    knowledge_context: str

def chat_node(state: AgentState) -> AgentState:
    """调用 LLM 生成响应"""
    response = llm.chat(state["messages"])
    return {"messages": state["messages"] + [response]}

def tool_check_node(state: AgentState) -> AgentState:
    """检查是否需要工具调用"""
    if state["messages"][-1].needs_tool:
        return "tool_call"
    return "final_answer"

def tool_node(state: AgentState) -> AgentState:
    """执行工具并更新上下文"""
    tool_call = state["messages"][-1].tool_call
    result = execute_tool(tool_call)
    return {"messages": state["messages"] + [result]}

graph = StateGraph(AgentState)
graph.add_node("chat", chat_node)
graph.add_node("tool", tool_node)
graph.add_conditional_edges("chat", tool_check_node, {
    "tool_call": "tool",
    "final_answer": END
})
graph.set_entry_point("chat")
agent = graph.compile()
```

### 6.3 知识库导入与检索

```python
# ingest.py - 文档导入
async def ingest_document(file_path: str, collection_id: str):
    # 1. 解析（支持 PDF/MD/TXT/Word）
    text = await parse_document(file_path)

    # 2. 分块（按语义分块）
    chunks = chunker.split(text, chunk_size=500, overlap=50)

    # 3. 向量化（使用 Ollama 本地 Embedding 模型）
    embeddings = await ollama.embeddings(
        model="nomic-embed-text",
        prompt="\n".join(chunks)
    )

    # 4. 存储到 ChromaDB
    db.add(collection_id, chunks, embeddings)
    return {"chunks": len(chunks), "status": "success"}
```

```python
# retriever.py - 检索
async def retrieve(query: str, collection_id: str, top_k: int = 5):
    # 1. 查询向量化
    query_embedding = await ollama.embeddings(
        model="nomic-embed-text",
        prompt=query
    )

    # 2. 向量检索
    results = await db.search(
        collection_id,
        query_embedding,
        top_k=top_k
    )

    # 3. 组装上下文
    context = "\n\n".join([r["text"] for r in results])
    return context
```

### 6.4 Provider 适配器模式

```typescript
// BaseProvider.ts
export interface LLMProvider {
  chat(messages: Message[], options: ChatOptions): AsyncIterable<ChatChunk>
  listModels(): Promise<ModelInfo[]>
}

// OllamaProvider.ts
export class OllamaProvider implements LLMProvider {
  constructor(private baseUrl: string) {}

  async *chat(messages: Message[], options: ChatOptions) {
    const response = await fetch(`${this.baseUrl}/api/chat`, {
      method: 'POST',
      body: JSON.stringify({ model: options.model, messages }),
    });
    const reader = response.body!.getReader();
    // ... 流式处理
  }

  async listModels() {
    const response = await fetch(`${this.baseUrl}/api/tags`);
    return response.json();
  }
}
```

---

## 七、开发路线图

### Phase 1: 基础框架（2-3周）
- [ ] 搭建 Electron + React 项目骨架
- [ ] 实现基础对话 UI（消息列表、输入框）
- [ ] 实现 Ollama Provider 适配器
- [ ] 实现 SSE 流式响应
- [ ] 本地对话持久化（SQLite）
- [ ] 模型切换配置界面

### Phase 2: 核心能力（3-4周）
- [ ] MCP 工具管理器（启动/停止子进程）
- [ ] 内置工具：文件读写、Shell 命令
- [ ] 企业知识库：文档导入 + 向量检索
- [ ] Provider 切换配置界面
- [ ] 多轮对话上下文管理

### Phase 3: 高级功能（3-4周）
- [ ] LangGraph Agent 编排（多轮工具调用）
- [ ] 编程助手专用 Agent（代码分析、重构建议）
- [ ] 知识库管理界面（上传、删除、检索历史）
- [ ] 主题定制、快捷键配置
- [ ] 用户权限管理（可选）

### Phase 4: 打磨发布（2-3周）
- [ ] 打包分发（macOS/Windows/Linux）
- [ ] 自动更新机制
- [ ] 用户文档
- [ ] 内测反馈迭代
- [ ] 性能优化

---

## 八、部署方案

### 8.1 单用户本地部署

```
┌─────────────────────────────────────┐
│  Desktop App (Electron)             │
│  + Python Agent (本地进程)          │
│  + Ollama/vLLM (本地推理)           │
│  + ChromaDB (本地向量库)            │
└─────────────────────────────────────┘
```

数据全部本地，无需联网（除模型下载）。

### 8.2 多用户私有部署（可选）

```
┌─────────────────────────────────────┐
│  Web App / Desktop App              │
└──────────────┬──────────────────────┘
               │ HTTPS
┌──────────────▼──────────────────────┐
│  API Gateway (Nginx)                │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│  Python Agent Service (Docker)      │
│  + PostgreSQL + ChromaDB            │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│  LLM Server (vLLM / Ollama Cluster) │
└─────────────────────────────────────┘
```

---

## 九、依赖清单

### 前端（desktop/package.json）
```json
{
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "typescript": "^5.3.0",
    "zustand": "^4.4.0",
    "antd": "^5.0.0",
    "monaco-editor": "^0.44.0"
  },
  "devDependencies": {
    "electron": "^28.0.0",
    "electron-builder": "^24.0.0",
    "@types/react": "^18.2.0",
    "tailwindcss": "^3.4.0"
  }
}
```

### 后端（agent/requirements.txt）
```txt
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
langgraph>=0.0.30
langchain>=0.1.0
langchain-community>=0.0.10
ollama>=0.1.0
openai>=1.3.0
chromadb>=0.4.0
python-multipart>=0.0.6
pydantic>=2.5.0
sqlalchemy>=2.0.0
aiosqlite>=0.19.0
```

---

## 十、启动步骤

### 10.1 本地开发

```bash
# 1. 安装依赖
cd agent && pip install -r requirements.txt
cd ../desktop && npm install

# 2. 启动后端（端口 8000）
cd agent && uvicorn app.main:app --reload

# 3. 启动前端开发服务器
cd desktop && npm run dev

# 4. 安装 Ollama 并拉取模型
curl -fsSL https://ollama.ai/install.sh | sh
ollama pull qwen2.5:7b
ollama pull nomic-embed-text
```

### 10.2 打包发布

```bash
# 构建桌面应用
./scripts/build-desktop.sh

# 输出目录：desktop/dist/
# macOS: .dmg / .zip
# Windows: .exe / .msi
# Linux: .AppImage / .deb
```

---

## 十一、关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 桌面框架 | Electron | 生态成熟、MCP SDK 支持好 |
| 语言 | TS + Python | TS 适合 UI，Python 适合 AI/Agent |
| 流式传输 | SSE | 比 WebSocket 简单，支持 HTTP/2 |
| 向量库 | ChromaDB | 本地持久化、零配置 |
| Embedding 模型 | nomic-embed-text | 轻量、质量高、Ollama 支持 |
| Agent 框架 | LangGraph | 细粒度控制、可视化调试 |

---

## 十二、风险与应对

| 风险 | 影响 | 应对策略 |
|------|------|----------|
| Ollama 模型性能不足 | 用户体验差 | 支持 vLLM 作为高性能备选 |
| 向量检索延迟 | 检索体验差 | 本地缓存 + 异步预检索 |
| MCP 工具安全问题 | 数据泄露 | 沙箱隔离 + 权限确认 |
| 内存占用过高 | 设备卡顿 | 流式处理 + 内存监控 |
| 打包体积过大 | 下载慢 | 按需加载 + 代码分割 |

---

## 总结

这是一个完整的企业级 AI 助手客户端方案，核心特点：

1. **双模型支持**：Ollama（开源本地）+ 私有 API（商业模型）
2. **MCP 工具扩展**：标准化工具调用协议
3. **RAG 知识库**：本地向量检索，隐私安全
4. **LangGraph Agent**：多轮工具调用、复杂任务编排
5. **Electron + React**：跨平台桌面应用

适合 2-3 人团队，预计 **10-14 周**完成 MVP 并具备商业化条件。

# 各阶段可投喂指令（P0–P4）

> **用途**：每个阶段开新会话时，直接复制对应指令块投喂给 AI 编码助手。按「最省 Token」原则编写——只给当前阶段任务 + 规格切片引用 + DoD，不重复全量文档。
>
> **使用方式**：
> 1. 每个阶段开**独立新会话**（避免上下文累积）。
> 2. 复制对应阶段的指令块，粘贴为会话首条消息。
> 3. AI 完成后按 DoD 验收，通过则进入下一阶段（开新会话）。
>
> **配套文件**（项目内需存在，AI 按需 Read）：
> - `DEV-COMMAND-PROMPT.md` — 指挥开发主 Prompt（约束与禁止事项）
> - `DEVELOPMENT-GUIDE.md` — 开发规格书（详细实现规格）
> - `DEVELOPMENT-PROCESS-TOKEN.md` — 开发流程与省 Token 策略

---

## 通用输出约定（每个阶段都生效）

以下约定写在每个指令末尾，避免重复：

```
【输出要求】
1. 完成后列出：产出文件清单 + 每项自检结果（通过/未通过）。
2. 不要贴大段代码；关键实现用文件路径 + 简短说明。
3. 遇到规格书未覆盖的歧义，先列出假设再继续，不要自行扩张范围。
4. 严格遵守 DEV-COMMAND-PROMPT.md §十一 禁止事项。
```

---

## Phase 0 — 需求冻结 + PoC（1 周）

```text
你是本项目的高级全栈工程师。当前阶段：Phase 0 — 需求冻结 + PoC。

【阶段目标】
把「要做什么、接口长什么样」一次性定死，并用最小 PoC 验证链路可行。不搭完整工程。

【任务】
1. 冻结通信协议：SSE 事件类型（meta/thinking/tool_call/tool_result/text/done/error）、错误码枚举、HTTP 接口清单。
   参考：DEVELOPMENT-GUIDE.md §7（通信协议）。
2. 冻结数据模型：SQLite 表结构（conversations/messages/tool_calls/tool_registry/usage_log）、ChromaDB 集合与元数据、配置文件格式。
   参考：DEVELOPMENT-GUIDE.md §6（数据模型）。
3. PoC：用 Ollama 本地模型 + OpenAI 兼容接口跑通最小流式对话（一个独立脚本，不进正式工程）。
   参考：DEVELOPMENT-GUIDE.md §9.1（Provider 层）。

【DoD（完成标准）】
- 协议冻结文档产出，评审通过，后续阶段只引用不重述。
- curl 能流式拿到 meta / text / done 事件。
- PoC 脚本可独立运行，不依赖正式工程。

【省 Token 要点】
- PoC 用极简脚本，不引入 Electron/FastAPI 等完整框架。
- 协议/数据模型只写一次，后续阶段引用文件路径。

【输出要求】（见通用输出约定）
```

---

## Phase 1 — 基础框架（2–3 周）

```text
你是本项目的高级全栈工程师。当前阶段：Phase 1 — 基础框架。

【阶段目标】
把「能对话」的骨架立起来。前端后端可并行，但本会话聚焦【前端 + 后端联调】。

【任务】
1. Electron + React + TypeScript 项目骨架，目录对齐规格书 §5。
   参考：DEVELOPMENT-GUIDE.md §5（目录结构）、§8（前端规格）。
2. 基础对话 UI：消息列表、输入框、停止生成（AbortController）、主题切换。
   参考：DEVELOPMENT-GUIDE.md §8.1（视觉 Token）、§8.2（页面功能清单）。
3. FastAPI 骨架：仅绑定 127.0.0.1 + 一次性随机 token 鉴权 + /healthz。
   参考：DEVELOPMENT-GUIDE.md §7.2（HTTP 接口）、§10（安全）。
4. SSE 客户端：必须处理粘包/半包（按 \n\n 切分、缓冲跨 chunk 行），兼容 data: [DONE]。
   参考：DEVELOPMENT-GUIDE.md §7.1（SSE 协议）、DEV-COMMAND-PROMPT.md §八.2。
5. 会话持久化：SQLite（conversations/messages 表）+ 会话列表 UI（新建/删除/切换）。
   参考：DEVELOPMENT-GUIDE.md §6.1（SQLite 表结构）。
6. 模型配置与切换界面：含私有化与互联网商业大模型，公网模型带「公网」标识 + 知情提示。
   参考：DEVELOPMENT-GUIDE.md §9.1（Provider 层）、§9.6（模型路由）。

【DoD（完成标准）】
- 能新建/删除会话、发消息收到流式回复。
- 重启应用后会话记录不丢失（验收 A1/A5 部分）。
- 停止生成按钮可中断流式响应。
- 后端仅监听 127.0.0.1，无 token 请求被拒绝。

【省 Token 要点】
- 前端只投喂 §8 + §5 + §7 切片，不投喂后端实现细节。
- 后端只投喂 §9.1/§9.2 + §6 + §7 切片，不投喂前端 UI 细节。
- 若前后端分两个会话，各自独立，互不污染上下文。

【输出要求】（见通用输出约定）
```

---

## Phase 2 — 核心能力（3–4 周）

```text
你是本项目的高级全栈工程师。当前阶段：Phase 2 — 核心能力。

【阶段目标】
把「工具与知识库」能力补上，这是 MVP 的灵魂。

【任务】
1. MCP Manager：工具集启停、来源白名单、审计日志。
   参考：DEVELOPMENT-GUIDE.md §9.3（MCP 工具管理器）。
2. 内置工具：
   - filesystem：文件读写、目录搜索（受限根目录）
   - shell：命令白名单 + 每次执行前 UI 确认 + 执行超时 + 全量审计
   - knowledge：企业 RAG 检索（带引用溯源）
   参考：DEVELOPMENT-GUIDE.md §9.3、§10（安全实现）。
3. RAG Pipeline：
   - 文档导入：解析（PDF/MD/TXT/Word）→ 分块（默认 500/50，保留元数据）→ 逐块向量化（bge-m3，Ollama 本地）→ ChromaDB 入库（content_hash 去重）
   - 检索：查询向量化 → 向量检索(Top20) + BM25(Top20) → RRF 融合 → Cross-Encoder rerank(Top5) → 注入 Prompt（带引用溯源）
   参考：DEVELOPMENT-GUIDE.md §9.4（RAG Pipeline）、§6.2（ChromaDB）。
4. 上下文管理：token 预算（系统提示 15% + 历史 40% + RAG 20% + 生成 25%，可配）+ 滑动窗口（默认 20 轮）+ 超长工具结果截断。
   参考：DEVELOPMENT-GUIDE.md §9.5（上下文管理）。
5. 密钥存储：接入系统钥匙串（macOS Keychain / Windows Credential Manager），配置文件仅存 apiKeyRef 引用名。
   参考：DEVELOPMENT-GUIDE.md §10（安全实现）、§6.3（配置）。
6. 安全基线验证：Shell 白名单、端口绑定、密钥不落明文，逐项验证。
   参考：DEVELOPMENT-GUIDE.md §10。

【DoD（完成标准）】
- 对话中触发 knowledge_search 并展示引用（文档名 + 页码 + 相关度）。
- shell 命令触发权限确认弹窗，拒绝后给出明确提示；批准后执行并记录审计。
- 10 份混合格式文档可导入并检索，Top-5 命中率 ≥ 80%（验收 A3）。
- 安全基线验证通过（验收 A6）。

【省 Token 要点】
- RAG 与 MCP 是相对独立的子模块，可拆成两个会话分别推进。
- 安全基线一次性做完并写进 DoD，避免上线前返工。
- bge-m3 向量化用 ollama.embed() 批量 input，不拼接全文（规格书 §8.3 已修正）。

【输出要求】（见通用输出约定）
```

---

## Phase 3 — Agent 化 + 编程助手（3–4 周）

```text
你是本项目的高级全栈工程师。当前阶段：Phase 3 — Agent 化与编程助手。

【阶段目标】
把「工具调用」串成「Agent 闭环」，并建立评测基线防回归。

【任务】
1. LangGraph 多轮工具调用闭环：
   - 用 OpenAI 风格结构化字段 tool_calls + tool_call_id
   - 工具结果以 role='tool' + tool_call_id 回灌，保证多轮关联
   - 禁止用 needs_tool 伪属性判断
   参考：DEVELOPMENT-GUIDE.md §9.2（LangGraph Agent）、DEV-COMMAND-PROMPT.md §三.5。
2. 模型路由与降级：
   - 通用对话 → qwen2.5:7b → 私有 API / 互联网商业大模型 → 报错
   - 编程问答 → deepseek-coder:6.7b → 通用模型
   - 向量化 → bge-m3 → nomic-embed-text
   - 公网模型：默认可见可选（不隐藏），带「公网」标识，用户选择即授权
   参考：DEVELOPMENT-GUIDE.md §9.6（模型路由与降级）。
3. 编程助手 Agent：代码问答、重构建议，对齐原型 code 场景（Tree-sitter 静态分析）。
   参考：原型 private-ai-client-prototype/index.html 的 code 场景。
4. 知识库管理界面：上传、删除、检索历史、引用查看。
   参考：DEVELOPMENT-GUIDE.md §8.2（知识库页）。
5. 上下文摘要压缩节点：超预算时对旧消息做 LLM 摘要，压缩进上下文。
   参考：DEVELOPMENT-GUIDE.md §9.5。
6. Agent 评测集搭建（golden set 基线）：
   - 首批覆盖原型 4 场景（rag / shell / code / multi）+ 10 份文档 RAG 用例
   - 指标：任务完成率、工具调用准确率、RAG Top-5 命中率、平均轮次
   参考：DEVELOPMENT-GUIDE.md §11（测试与评测）。

【DoD（完成标准）】
- 多工具连续调用任务（如「总结项目进展」= knowledge_search + filesystem_read）端到端正确，≥3 轮不中断（验收 A4）。
- 工具调用回灌正确，多轮上下文不串。
- golden set 基线跑通，后续改动先跑评测再合入。

【省 Token 要点】
- 评测集先行：先建 golden set，再改 Agent——用「跑评测」代替「人工反复试错」。
- Agent 循环逻辑集中在 agents/ 单目录，改动范围小，可精确投喂该目录上下文。
- 编程助手与通用 Agent 可拆会话。

【输出要求】（见通用输出约定）
```

---

## Phase 4 — 打磨发布（2–3 周）

```text
你是本项目的高级全栈工程师。当前阶段：Phase 4 — 打磨发布。

【阶段目标】
让产物可安装、可交付，通过全部验收。

【任务】
1. Electron ↔ Python 进程生命周期管理：
   - 主进程 spawn/stop Agent 子进程
   - 轮询 /healthz，崩溃自动重启（限次 + 提示）
   - app 退出时优雅关闭 Agent
   参考：DEVELOPMENT-GUIDE.md §9.7（进程生命周期）。
2. Python 运行时打包：PyInstaller / embeddable Python，随安装包分发，不依赖用户机器装 Python。
   参考：DEVELOPMENT-GUIDE.md §9.7、§12（Phase 4 任务）。
3. 打包分发：electron-builder 打包 macOS/Windows/Linux + 自动更新机制。
   参考：DEV-COMMAND-PROMPT.md §七 Phase 4。
4. 用户文档：README 含一键安装启动步骤、模型安装引导、配置说明、常见问题。
5. 发布前验收：按六项硬指标逐项核对，跑全量测试 + eval。
   参考：DEVELOPMENT-GUIDE.md §2.4（MVP 验收标准）。

【DoD（完成标准）】
- 干净环境安装包可一键运行，README 流程可复现（验收 A1）。
- 流式延迟 < 3s（验收 A2）。
- RAG Top-5 ≥ 80%（验收 A3）。
- MCP 工具调用 ≥3 轮（验收 A4）。
- 断电/崩溃重启对话不丢（验收 A5）。
- 安全基线通过（验收 A6）。

【省 Token 要点】
- 打包/自动更新是成熟方案复用场景，明确要求 AI「引用官方模板」而非从零设计。
- 验收用清单逐项打勾，产出「一次过」结论，不做无谓重试。
- 三平台打包可并行会话，但共享同一份打包配置。

【输出要求】（见通用输出约定）
```

---

## 附：阶段切换检查清单

每进入下一阶段前，确认：

- [ ] 上一阶段 DoD 全部通过
- [ ] 上一阶段产物已落盘（代码/文档），新会话可靠「读产物 + 读规格」续接
- [ ] 本阶段所需规格切片已确认（见各指令的「参考」行）
- [ ] 新开独立会话，不延续上一阶段上下文
- [ ] 协议/数据模型无变更（若有变更，回到 P0 重新冻结）

# 指挥开发 Prompt — 私有域 AI 助手客户端（MVP）

> **文档用途**：本文件是一份「指挥开发」的主提示词（Master Prompt），可直接整段粘贴给 AI 编码助手（Cursor / Claude Code / GitHub Copilot / 豆包编程助手等）作为任务初始化指令，也可作为人工开发团队统一遵循的「项目军规」。
>
> **配套材料**（开发前必须由 AI 读取）：
> - 方案文档：`PRIVATE-AI-CLIENT-SCHEME-REVISED.md`（修订版 v2，最终权威版本）
> - 交互原型：`private-ai-client-prototype/index.html`（高保真可交互原型，界面与交互的最终依据）
> - 评审结论：`review_visual.html`（16 项评审问题，本 Prompt 已全部内置处理）
>
> 使用方式：将下方【主提示词】整段复制给 AI 编码助手；如需分阶段开发，可让 AI 按「开发阶段」逐段执行并以每阶段的 Definition of Done 验收。

---

## 【主提示词】开始

你是本项目的高级全栈工程师兼技术负责人。请严格依据你读取到的《PRIVATE-AI-CLIENT-SCHEME-REVISED.md》方案与 `private-ai-client-prototype/index.html` 高保真原型，独立完成「私有域 AI 助手客户端」MVP 的端到端开发。你的代码必须可运行、可验证、可交付，并严格遵守以下全部指令。

### 一、项目一句话定义

构建一个类 WorkBuddy 的**桌面 AI 助手**，支持两种模型接入模式：**私有化部署**（自托管开源模型 Ollama / vLLM、私有化商业模型 API，主打「数据不出内网」）；**对接互联网上的商业大模型 API**（OpenAI 兼容，**全厂商支持**：OpenAI / DeepSeek / 通义千问等，由用户显式配置密钥后使用，默认不隐藏）。并内置 MCP 工具执行、企业知识库 RAG 核心能力。

### 二、技术栈锁定（不得擅自更换）

| 层 | 必须使用 | 说明 |
|----|----------|------|
| 桌面端 | Electron（当前稳定 LTS ≥33）+ React（18/19）+ TypeScript | UI 组件 Ant Design 或 Shadcn；状态管理 Zustand（仅 UI 状态） |
| 后端 Agent | Python + FastAPI + LangGraph + MCP Python SDK | Agent 编排的唯一权威 |
| 模型接入 | OpenAI 兼容 `chat/completions` + `/v1/models` | Ollama / vLLM / 私有 API / 互联网商业大模型（全厂商支持）全部走同一协议 |
| 向量库 | ChromaDB（本地持久化） | 多用户模式才考虑 pgvector，MVP 不做 |
| Embedding | bge-m3（中文主推，Ollama 本地提供） | nomic-embed-text 仅英文语料备选 |
| 密钥存储 | 系统钥匙串（macOS Keychain / Windows Credential Manager / 加密文件+主密码） | **禁止**明文 JSON 存密钥 |
| 前后端通信 | 本地 HTTP + SSE，仅绑定 127.0.0.1 | 加一次性随机 token 鉴权头 |
| 对话记录 | SQLite | 会话表 + 消息表 + 工具调用表 |

依赖版本以开发当日稳定版为准，必须写入 lockfile 固定（锁定 Electron ≥33、LangGraph 当前稳定大版本等；禁止使用 0.0.30 这类过旧版本）。

### 三、架构纪律（最高优先级约束）

1. **单一 Agent 权威**：Agent 编排（LangGraph 循环、工具调用调度、上下文组装）**只能在后端 Python**；Electron 前端只负责渲染交互、系统能力（文件/通知/托盘）、IPC、SSE 会话客户端、本地 UI 状态。**前端禁止**实现 Agent 循环、禁止直连 LLM、禁止维护 Provider 适配层。
2. **Provider 层仅后端一份**：统一 `OpenAICompatibleProvider`，Ollama / vLLM / 私有 API 只是 `base_url` 与鉴权不同，不得为每个厂商重复实现。
3. **数据层隔离**：SQLite / ChromaDB / 加密配置只被 Python 服务访问，客户端不直连数据库。
4. **端口与鉴权**：后端仅监听 `127.0.0.1`；Electron 主进程作为对外代理，与 Agent 间携带一次性随机 token 鉴权头。
5. **工具调用用结构化字段**：LangGraph 工具调用采用 OpenAI 风格 `tool_calls` + `tool_call_id`，以 `role='tool'` 正确回灌，保证多轮关联；禁止用 `needs_tool` 这类伪属性判断。

### 四、MVP 范围（必须做 / 明确不做）

**必须实现**：
- 桌面端：对话 UI、会话管理、模型切换、知识库管理、工具面板、设置页、用量统计页（对齐原型 5 个标签页）
- 后端：SSE 流式对话、LangGraph 单轮/多轮工具调用闭环、MCP 启停、RAG 导入/混合检索/引用溯源、模型路由与降级、上下文管理（token 预算 + 滑动窗口 + 摘要压缩）
- 安全基线：仅本地端口绑定、密钥钥匙串存储、Shell 工具白名单 + 每次执行前 UI 确认 + 全量审计、MCP 来源白名单
- 可观测性：结构化日志（request_id 贯穿）、`GET /api/usage` 用量统计、错误事件与 request_id 关联

**明确不做**（不要为实现它们花费时间）：公网多租户 SaaS、千万级文档/高并发检索、语音实时交互、待办任务中心、多 Agent 协作、多用户私有部署（pgvector/ACL/计费/OIDC 均属 MVP 后）。

### 五、MVP 验收标准（每个里程碑「完成」以此判定）

- 干净环境按 README 一键启动，无需人工修复依赖
- 流式对话延迟 < 3s（本地 7B 模型，20 token 首字）
- 10 份混合格式文档（PDF/MD/Word/TXT）可导入并检索，Top-5 命中率 ≥ 80%（内部评测集）
- MCP 工具调用循环 ≥ 3 轮不中断、上下文正确回灌
- 断电/崩溃后重启，对话记录不丢失
- 安全基线验证通过（Shell 白名单、端口绑定、密钥不落明文）

### 六、UI 必须对齐原型（重点：还原度）

以 `private-ai-client-prototype/index.html` 为唯一视觉与交互依据，还原以下内容：

1. **整体布局**：左侧边栏（应用标识「私有域 · 本地模型 · 数据不出内网」、模型选择下拉（含本地/私有/互联网商业大模型，公网模型带「公网」标识且默认可见可选）、新建对话、会话列表、用户信息、加密/本地标识、主题切换）+ 右侧主区域，顶部 5 个标签页：**对话 / 知识库 / 工具 / 用量 / 设置**。
2. **视觉规范**：深色磨砂玻璃风格（背景 `#0A0F1A`、面板半透明、模糊 24px 饱和 170%），强调色青绿（`#3FD8BE`），支持深色/浅色 × 多强调色主题切换并持久化。
3. **对话页**：快捷指令（查公司请假制度 / 运行 ls 命令 / 分析一段代码 / 总结项目进展）、消息流按「思考 → 工具调用 → 工具结果 → 引用 → 回答」展示事件流、每条消息可展开事件时间线、右侧「消息分析」面板同步展示、输入框 + 停止生成、上下文 token 剩余状态（如「剩余 14,200 / 16,384 tokens」）。
4. **Shell 权限确认**：运行 shell 命令必须弹权限确认，拒绝后给出明确提示——这是安全设计的核心交互，必须实现。
5. **知识库页**：导入文档（PDF/MD/Word/TXT）、文档列表卡片（名称/大小/chunk 数/日期/命中数）、Embedding 标识「bge-m3 · 本地」、搜索展示 Top-5 结果（文档名/页码/相关度/混合检索 RRF 融合说明）、点击可引用。
6. **工具页**：MCP 工具集列表（文件系统 / Shell 命令 / 浏览器 / 代码分析 / 知识库），每项含描述、安全标识、启用开关；关闭的工具体在对话中被拒。
7. **用量页**：近 7 次对话 token 消耗图表、明细表（时间/会话/模型/输入 tokens/输出 tokens/工具调用/成本）。
8. **设置页**：模型接入说明（OpenAI 兼容协议）、安全设置（仅监听 127.0.0.1、Shell 白名单、危险命令拦截、工具审计日志、钥匙串中 API Key 状态展示）。

### 七、分阶段开发任务（按顺序执行，每阶段以 Definition of Done 验收）

**Phase 0 — 需求冻结与 PoC（1 周）**
- [ ] 冻结 SSE/API 通信协议（事件类型、错误码，见方案 §5）
- [ ] PoC：Ollama 本地模型 + OpenAI 兼容接口跑通最小流式对话
- DoD：本地起一个模型，能通过 curl 流式拿到 `meta/text/done` 事件。

**Phase 1 — 基础框架（2–3 周）**
- [ ] Electron + React 项目骨架（目录对齐方案 §7）
- [ ] 基础对话 UI（消息列表、输入框、停止生成）+ 主题切换
- [ ] 后端 FastAPI 骨架：127.0.0.1 绑定 + token 鉴权 + `/healthz`
- [ ] SSE 客户端（必须处理粘包/半包）+ 流式对话端到端打通
- [ ] 会话持久化（SQLite）与会话列表 UI
- [ ] 模型配置与切换界面（OpenAI 兼容 Provider 接入）
- DoD：能新建/删除会话、发消息收到流式回复、重启后会话还在。

**Phase 2 — 核心能力（3–4 周）**
- [ ] MCP Manager：工具集启停、来源白名单、审计日志
- [ ] 内置工具：filesystem（受限根目录）、shell（白名单 + UI 确认 + 超时）、knowledge
- [ ] RAG：文档导入（解析/分块/向量化/入库）+ 混合检索（BM25 + 向量 RRF 融合）+ 引用溯源展示
- [ ] 上下文管理：token 预算 + 滑动窗口
- [ ] 密钥存储接入系统钥匙串
- [ ] 安全基线验证（Shell 白名单、端口、密钥）
- DoD：能在对话中触发 knowledge_search 并展示引用；shell 命令触发权限确认；审计日志有记录。

**Phase 3 — Agent 化与编程助手（3–4 周）**
- [ ] LangGraph 多轮工具调用闭环（`tool_calls` 回灌，≥3 轮不中断）
- [ ] 模型路由与降级（通用/代码/Embedding 分离 + fallback 链）
- [ ] 编程助手 Agent（代码问答、重构建议，对齐原型 code 场景）
- [ ] 知识库管理界面（上传、删除、检索历史、引用查看）
- [ ] 上下文摘要压缩节点
- [ ] Agent 评测集搭建（golden set 基线）
- DoD：多轮工具任务（如「总结项目进展」需 knowledge_search + filesystem_read 连续调用）端到端正确。

**Phase 4 — 打磨发布（2–3 周）**
- [ ] Electron ↔ Python 进程生命周期管理（spawn/stop、/healthz 轮询、崩溃自动重启限次、退出优雅关闭）
- [ ] Python 运行时打包（PyInstaller / embeddable Python），随安装包分发
- [ ] 打包分发（macOS/Windows/Linux）+ 自动更新
- [ ] 用户文档 + 内测反馈迭代
- [ ] 发布前验收：按 MVP 验收标准逐项核对，跑全量测试与 eval
- DoD：干净环境安装包可一键运行，README 一键启动流程真实可复现。

### 八、编码规范与质量要求

1. **TypeScript / Python 严格类型**：前端开启 `strict`；后端用 Pydantic 模型校验所有入参出参。
2. **SSE 解析**：必须处理粘包/半包（按 `\n\n` 切分、缓冲跨 chunk 的行），兼容 `data: [DONE]` 哨兵。
3. **错误处理**：所有错误以 SSE `error` 事件携带 `code`（`model_not_found` / `tool_timeout` / `tool_denied` / `context_overflow` / `rate_limited` / `internal`）返回；前端 `AbortController` 支持停止生成。
4. **上下文治理**：实现方案 §4.6 的 token 预算分配（系统提示 15% + 历史 40% + RAG 20% + 生成 25% 可配）、滑动窗口、超长工具结果截断。
5. **日志**：后端结构化日志落盘，`request_id` 贯穿整条链路。
6. **测试**：单元测试（Provider/分块/上下文预算/SSE 解析）+ 集成测试（端到端对话/MCP 调用/RAG 导入检索）+ Agent 评测（golden set）。每次改 Prompt/模型/分块参数必须先跑评测再合入。
7. **不要重复造轮子**：能复用官方 SDK（openai、chromadb、mcp、langgraph）就复用，只写必要的业务胶水代码。

### 九、安全基线（MVP 硬性要求，不是「后期补」）

- 密钥：系统钥匙串存储，配置文件中仅存引用名（如 `apiKeyRef`），运行时读取；**任何代码不得出现密钥明文**。
- Shell：命令白名单（允许集）+ 路径前缀校验 + 危险命令黑名单（`rm -rf`、网络扫描等）+ 执行超时 + **每次执行前 UI 确认** + 全量审计日志。
- MCP：仅允许白名单来源；启用前展示其声明的工具与权限，用户确认；记录加载来源。
- 端口：Agent 服务仅绑定 `127.0.0.1`；Electron 与 Agent 间加一次性随机 token 鉴权头。
- 数据：对话与文档默认仅本地；导出需用户显式操作；提供「本地删除」入口。
- 供应链：依赖锁文件 + 关键依赖版本固定 + 定期安全扫描（npm audit / pip-audit）。

### 十、交付物要求

1. 完整可运行的项目（desktop/ + agent/ + config/ + scripts/ + README.md，目录对齐方案 §7）。
2. README 必须包含：一键安装启动步骤、模型安装引导、配置说明、常见问题。
3. 交付前自检：跑通全部测试 + eval；在干净环境按 README 验证一键启动。
4. 记录每个阶段的完成情况与未完成项，不得静默降级。

### 十一、禁止事项

- 禁止擅自更换已锁定的技术栈（Electron / FastAPI / LangGraph / ChromaDB / OpenAI 兼容协议）。
- 禁止在前端实现 Agent 循环或 Provider 适配层（违反单一 Agent 权威）。
- 禁止把 API Key 写进配置文件或代码，禁止明文落盘。
- 禁止跳过安全基线（Shell 白名单/确认/审计、端口绑定、钥匙串）直接上线。
- 禁止对「明确不做」的范围投入开发。
- 禁止臆造方案中不存在的接口、事件或字段——一切以方案 §5 协议和原型为准。

### 十二、开工前请先做

1. 读取 `PRIVATE-AI-CLIENT-SCHEME-REVISED.md` 全文与 `private-ai-client-prototype/index.html` 原型。
2. 向我复述你对以下 5 点的理解，确认无误后再开始编码：
   - 单一 Agent 权威的含义与前后端职责边界；
   - SSE 事件协议（`meta/thinking/tool_call/tool_result/text/done/error`）与错误码；
   - MCP 工具调用闭环与 `tool_call_id` 回灌方式；
   - RAG 混合检索（BM25 + 向量 RRF 融合）与引用溯源；
   - MVP 验收标准的 6 条硬指标（§5）。

## 【主提示词】结束

---

## 附：给「评审人」的使用说明

- **如何用**：整段复制【主提示词】给 AI 编码助手；若 AI 是渐进式（如 Cursor），可先粘贴全文，再按第七节 Phase 顺序下达「开始 Phase N」。
- **如何评审本 Prompt**：重点检查①架构纪律是否与方案 v2 一致；②MVP 边界是否清晰可执行；③验收标准是否可量化；④UI 还原点是否覆盖原型全部页面；⑤安全基线是否作为硬约束而非建议；⑥是否有与方案/原型冲突的表述。
- **可裁剪**：如果只用于单一 Phase，可删除第七节其他 Phase 任务，保留对应 DoD。

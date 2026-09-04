# Phase 0 — 通信协议冻结文档

> **状态**：已冻结（Phase 0 完成）
> **依据**：`DEVELOPMENT-GUIDE.md` §7、`PRIVATE-AI-CLIENT-SCHEME-REVISED.md` §5
> **冻结日期**：2026-09-03
> **配套**：`PHASE0-DATA-MODEL-FROZEN.md`（数据模型）

---

## 0. 使用约定

- 本文是**唯一权威**的通信契约。Phase 1–4 **只引用本文件路径，不重述协议内容**。
- 任何字段增删、事件类型变更、错误码扩展，**必须回到 Phase 0 重新冻结**，禁止在后续阶段私自扩张。
- 禁止臆造本文未定义的接口、事件或字段（DEV-COMMAND-PROMPT.md §十一）。

### 通用约定

| 项 | 约定 |
|----|------|
| 传输 | HTTP/1.1，仅绑定 `127.0.0.1` |
| 流式响应 | `Content-Type: text/event-stream; charset=utf-8` |
| 帧格式 | `data: {json}\n\n`（单个事件块以双换行结束） |
| 结束哨兵 | `data: [DONE]`（客户端**必须**兼容） |
| 字符编码 | UTF-8 |
| 时间字段 | Unix 毫秒整数（与 SQLite `*_at` 字段口径一致） |
| 鉴权 | `Authorization: Bearer <token>`，token 由 Electron 主进程启动时随机生成并注入 |

---

## 1. SSE 事件协议（`POST /api/chat`）

### 1.1 事件总表

| event | 必填字段 | 说明 |
|-------|----------|------|
| `meta` | `message_id`, `model`, `conversation_id` | 首个事件，会话/消息元信息 |
| `thinking` | `text` | 推理过程，前端可折叠展示 |
| `tool_call` | `call_id`, `name`, `arguments` | 声明待执行工具；`arguments` 为 **JSON 字符串** |
| `tool_result` | `call_id`, `content`, `is_error` | 工具执行结果，`call_id` 与 `tool_call` 对应 |
| `text` | `delta` | 增量文本，前端追加渲染 |
| `done` | `usage{prompt_tokens, completion_tokens}`, `finish_reason` | 正常结束 |
| `error` | `code`, `message` | 错误终止，`code` 见 §2 |

### 1.2 事件顺序约束

```
meta → [thinking]* → ( tool_call → tool_result )* → [text]* → done | error
```

- `meta` **必须**是第一个事件，且仅出现一次。
- `tool_result.call_id` 必须与前序某个 `tool_call.call_id` 严格对应（前端据此串联事件时间线）。
- `done` 与 `error` 互斥，二者之一出现即代表本轮流结束；其后仍应发送 `data: [DONE]` 哨兵。
- 一轮对话中 `tool_call/tool_result` 可出现多组（Phase 3 要求 ≥3 轮不中断）。

### 1.3 帧示例

```
data: {"event":"meta","message_id":"msg-a1b2c3","model":"qwen2.5:7b","conversation_id":"conv-9f8e"}

data: {"event":"text","delta":"你好"}

data: {"event":"tool_call","call_id":"call_01","name":"shell","arguments":"{\"command\":\"ls -la\"}"}

data: {"event":"tool_result","call_id":"call_01","content":"total 12\n...","is_error":false}

data: {"event":"done","usage":{"prompt_tokens":128,"completion_tokens":64},"finish_reason":"stop"}

data: [DONE]
```

---

## 2. 错误码枚举（封闭集合）

| code | 含义 | 前端处理 |
|------|------|----------|
| `model_not_found` | 模型不存在 / 未拉取 / Provider 不可达 | 提示「模型不可用」，引导设置页切换或 `ollama pull` |
| `tool_timeout` | 工具执行超时 | 展示超时提示，允许重试 |
| `tool_denied` | 工具被拒绝（未启用、非白名单、用户拒绝确认） | 展示拒绝原因，不重试 |
| `context_overflow` | 上下文超出 token 预算 | 提示新建会话或触发摘要压缩 |
| `rate_limited` | 请求频率超限 | 提示稍后重试，展示退避倒计时 |
| `internal` | 服务内部错误 | 展示 `message` 与 `request_id`，引导查日志 |

**约束**：
- 错误码集合**封闭**，新增需重走冻结流程。
- 所有 `error` 事件应同时携带 `request_id`（见 §5），便于与后端结构化日志关联。
- 禁止用 HTTP 状态码替代 `error` 事件：非 2xx 响应仅用于「鉴权失败 / 路由不存在 / 请求体非法」等协议层问题。

---

## 3. HTTP 接口清单

> 全部接口要求 `Authorization: Bearer <token>`；服务仅监听 `127.0.0.1`。

| 方法 | 路径 | 说明 | 阶段 |
|------|------|------|------|
| GET | `/healthz` | 健康检查，Electron 主进程轮询（返回 `{status, version}`） | P1 |
| POST | `/api/chat` | 对话，SSE 流式（§1） | P1 |
| GET | `/api/models` | 模型列表（Provider 聚合，含 `isPublic` 标识） | P1 |
| GET | `/api/conversations` | 会话列表 | P1 |
| POST | `/api/conversations` | 新建会话 | P1 |
| GET | `/api/conversations/{id}` | 会话详情（含消息 + 工具调用） | P1 |
| DELETE | `/api/conversations/{id}` | 删除会话（级联删除消息） | P1 |
| POST | `/api/conversations/{id}/export` | 会话导出（MD / JSON） | P3 |
| POST | `/api/knowledge/ingest` | 文档导入（multipart） | P2 |
| POST | `/api/knowledge/search` | 知识库检索，返回 Top-5 + 引用溯源 | P2 |
| DELETE | `/api/knowledge/{doc_id}` | 删除文档索引 | P2 |
| GET | `/api/mcp/tools` | 工具集列表与启停状态 | P2 |
| POST | `/api/mcp/{toolset}/enable` | 启用工具集 | P2 |
| POST | `/api/mcp/{toolset}/disable` | 停用工具集 | P2 |
| POST | `/api/mcp/{toolset}/approve` | 工具执行确认（Shell 等需审批，`{call_id, approved}`） | P2 |
| GET | `/api/settings` | 配置读取（**不含密钥明文**，仅 `apiKeyRef`） | P1 |
| PUT | `/api/settings` | 配置更新 | P1 |
| GET | `/api/usage` | 用量统计（今日 / 本周 / 近 7 次） | P2 |

### 3.1 请求/响应约定

- 入参出参统一用 **Pydantic 模型**校验（后端）；前端用 TypeScript 类型镜像。
- 分页参数（若后续需要）统一 `?limit=&offset=`，MVP 阶段列表接口可不分页。
- 所有写操作成功返回 2xx + JSON；失败返回错误 JSON `{code, message, request_id}`。

---

## 4. 前端 SSE 解析要求（强制）

1. **按 `\n\n` 切分事件块**，用 `buffer` 拼接跨 chunk 的半包行——禁止按单 chunk 直接 `JSON.parse`。
2. 兼容**多 `data:` 行**的事件块（逐行解析，不可只取第一行）。
3. 兼容 `data: [DONE]` 哨兵：遇到即视为流结束，**不得**尝试 JSON 解析。
4. 忽略 `event:` / `id:` / `retry:` 等未使用字段行，保持前向兼容。
5. `AbortController` 支持「停止生成」；中止后前端自行收尾，不依赖服务端确认。
6. 单帧 JSON 解析失败时：`onError('internal', 'invalid SSE payload')`，并保留已有已渲染内容，不清空消息。

> 参考实现：`PRIVATE-AI-CLIENT-SCHEME-REVISED.md` §8.1（已修正粘包/半包处理）。

---

## 5. 可观测性约定

| 项 | 约定 |
|----|------|
| `request_id` | 每次 `/api/chat` 生成 UUID，贯穿后端结构化日志与 `error` 事件 |
| 结构化日志 | 后端 logging 落盘，字段含 `ts / level / request_id / conversation_id / event` |
| 用量记录 | 每轮 `done` 的 `usage` 写入 `usage_log`（见数据模型 §1.5） |
| 首字延迟 | PoC 阶段用 `first_token_ms` 观测，对应验收 A2（< 3s） |

---

## 6. 安全约束（协议层）

- 服务**仅绑定 `127.0.0.1`**，禁止 `0.0.0.0`。
- token 为**一次性随机值**，由 Electron 主进程生成并注入 Agent 进程，不落盘、不写日志。
- `/api/settings` 与 `/api/models` 响应中**禁止出现密钥明文**，仅返回 `apiKeyRef` 引用名。
- `/api/mcp/{toolset}/approve` 是「每次执行前 UI 确认」的协议落点：`requires_approval=1` 的工具（如 shell）在未收到批准前**不得执行**，超时/拒绝返回 `tool_denied`。

---

## 7. 版本与变更

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-09-03 | Phase 0 首次冻结 |

**变更流程**：任何协议变更 → 回到 Phase 0 修订本文 → 升版本号 → 通知前后端同步 → 更新 `PHASE-PROMPTS.md` 的阶段引用。禁止在 Phase 1–4 会话内就地改协议。

---

## 8. 冻结确认

- [x] SSE 事件类型（7 类）与字段已定义
- [x] 事件顺序与 `call_id` 串联约束已明确
- [x] 错误码枚举（6 个）已封闭
- [x] HTTP 接口清单（18 个）已列出
- [x] 鉴权与端口约束已明确
- [x] 前端解析（粘包/半包/`[DONE]`）要求已强制
- [x] `curl` 可验证（见 `poc/poc_streaming.py --serve`）

**后续阶段约定**：所有阶段只引用本文件路径，不重述协议。

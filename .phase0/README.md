# Phase 0 — 需求冻结 + PoC

> **阶段**：Phase 0（1 周）
> **状态**：协议/数据模型已冻结，PoC 协议层已验证通过
> **日期**：2026-09-03

---

## 1. 产出文件清单

| 文件 | 内容 | 状态 |
|------|------|------|
| `PHASE0-PROTOCOL-FROZEN.md` | SSE 事件协议 + 错误码 + HTTP 接口清单 + 安全约束 | 已冻结 |
| `PHASE0-DATA-MODEL-FROZEN.md` | SQLite 5 表 + ChromaDB 元数据 + 配置格式 | 已冻结 |
| `poc/poc_streaming.py` | 最小流式对话 PoC（零依赖，独立可运行） | 已验证 |
| `README.md` | 本文件（阶段产出与验收记录） | — |

---

## 2. 协议冻结要点

**SSE 事件（7 类）**：`meta` / `thinking` / `tool_call` / `tool_result` / `text` / `done` / `error`
- 顺序约束：`meta → [thinking]* → (tool_call → tool_result)* → [text]* → done | error`
- `tool_result.call_id` 必须与前序 `tool_call.call_id` 严格对应
- 结束哨兵 `data: [DONE]`，客户端必须兼容

**错误码（封闭集合，6 个）**：`model_not_found` / `tool_timeout` / `tool_denied` / `context_overflow` / `rate_limited` / `internal`

**HTTP 接口（18 个）**：`/healthz`、`/api/chat`、会话 CRUD + 导出、知识库 ingest/search/delete、MCP tools 启停 + approve、settings、usage

**安全约束**：仅绑定 `127.0.0.1` + 一次性随机 token；响应禁止出现密钥明文；`requires_approval=1` 的工具（shell）未获批准不得执行

> 完整定义见 `PHASE0-PROTOCOL-FROZEN.md`。**后续阶段只引用本文件，不重述协议。**

## 3. 数据模型冻结要点

- **SQLite 5 表**：`conversations` / `messages` / `tool_calls` / `tool_registry` / `usage_log`
- **ChromaDB**：单库多文档，元数据含 `source` / `page` / `title_path` / `content_hash` / `doc_id` / `chunk_index`，按 `content_hash` 去重
- **配置**：`providers.json`（密钥仅存 `apiKeyRef`）/ `rag-config.json` / `mcp-servers.json`

> 完整定义见 `PHASE0-DATA-MODEL-FROZEN.md`。

---

## 4. PoC 验证结果

### 4.1 PoC 设计

`poc/poc_streaming.py` 采用**零依赖**实现（纯 Python 标准库），三个模式：

| 模式 | 命令 | 用途 |
|------|------|------|
| A 直连流式 | `python3 poc_streaming.py --prompt "..."` | 验证 OpenAI 兼容端点流式链路 |
| B SSE 服务 | `python3 poc_streaming.py --serve` | 启动 SSE 服务供 curl 验收协议 |
| C 替身流 | 追加 `--mock` | 无模型环境下验证帧格式本身 |

> 说明：默认用标准库直连 `/v1/chat/completions`（验证裸协议，比 SDK 更具说服力）；`--sdk` 可切到官方 `openai` 包，与 Phase 1 provider 实现保持一致。

### 4.2 验收记录（已执行）

**验收 1 — 正常流式** ✅
```
curl -N -X POST http://127.0.0.1:8766/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"你好","conversation_id":"poc-1"}'

→ HTTP 200，耗时 0.77s
data: {"message_id":"msg-...","model":"mock","conversation_id":"poc-1","event":"meta"}
data: {"delta":"...","event":"text"}          × 12 块
data: {"usage":{"prompt_tokens":-1,"completion_tokens":12},"finish_reason":"stop","first_token_ms":63,"event":"done"}
data: [DONE]
```

**验收 2 — 空 message** ✅ `HTTP 400` + `error{message is required}`

**验收 3 — 非法 JSON** ✅ `HTTP 400` + `error{invalid JSON body}`

**验收 4 — 未知路径** ✅ `HTTP 404` + `error{not found}`

**验收 5 — 直连模式** ✅ mock 流正常输出（首字 65ms / 12 块）；无 Ollama 时报明确的连接错误，不崩溃

### 4.3 过程中修复的问题

| 问题 | 现象 | 修复 |
|------|------|------|
| SSE 连接不关闭 | curl 收完 `[DONE]` 后仍空等到超时（exit 28） | 响应头 `Connection: keep-alive` → `close`；`/api/chat` 本就是一次性流 |

---

## 5. DoD 验收状态

| DoD 项 | 状态 | 证据 |
|--------|------|------|
| 协议冻结文档产出，后续只引用不重述 | ✅ | `PHASE0-PROTOCOL-FROZEN.md` |
| 数据模型冻结 | ✅ | `PHASE0-DATA-MODEL-FROZEN.md` |
| curl 能流式拿到 `meta` / `text` / `done` 事件 | ✅ | 见 §4.2 验收 1（mock 模式，验证帧格式与事件序列） |
| PoC 脚本可独立运行，不依赖正式工程 | ✅ | 零第三方依赖，仅标准库 |
| 真机模型跑通最小流式对话 | ⚠️ **顺延** | 环境 `ollama.com` 不可达，详见 §6.1；不阻塞 Phase 1 |

---

## 6. 遗留项

| 项 | 说明 | 影响 |
|----|------|------|
| 真机模型验证 | **未通过** | 不阻塞 Phase 1 |

### 6.1 真机验证受阻记录（如实记录，不静默降级）

已尝试 `brew install ollama`，**失败**，原因与证据：

| 环节 | 结果 |
|------|------|
| `brew info ollama` | 公式可见（0.33.0），走 USTC 镜像，依赖 `mlx` / `mlx-c`，并会升级 `python@3.14` |
| `brew install ollama`（NONINTERACTIVE） | 运行 16 分钟无进展后中止；`Cellar/ollama`、`mlx`、`mlx-c` 均未创建 |
| `curl https://ollama.com` | HTTP 000 —— **不可达** |

**结论**：即使 Ollama 装成功，`ollama pull qwen2.5:7b`（约 4.4GB）仍依赖 `ollama.com` 注册表，当前网络不可达，**真机验证在本环境无法完成**。

**PoC 已覆盖与未覆盖的边界**：

- ✅ 已验证：SSE 帧格式、事件顺序、`meta/text/done` + `[DONE]` 哨兵、错误分支（400/404）——即**协议层**全部 DoD 项。
- ❌ 未验证：真实模型的流式生成与首字延迟（验收 A2 < 3s）——**顺延至 Phase 1**，届时搭起 FastAPI 骨架后可在本机或联网环境实测。

**解锁条件**（任一即可真机验证）：
1. 网络可访问 `ollama.com`（或配置代理/镜像）后执行：
   ```bash
   brew install ollama && ollama serve &
   ollama pull qwen2.5:7b
   python3 .phase0/poc/poc_streaming.py --serve   # 去掉 --mock
   ```
2. 提供一个可用的 OpenAI 兼容端点，用 `--base-url` / `--model` 指向它验证。

---

## 7. 进入 Phase 1 的前置确认

按 `PHASE-PROMPTS.md` 附：阶段切换检查清单

- [x] 上一阶段 DoD 全部通过（协议层已验证；真机验证列为遗留项，不阻塞）
- [x] 上一阶段产物已落盘，新会话可靠「读产物 + 读规格」续接
- [x] 本阶段所需规格切片已确认（Phase 1 引用 §5 / §6 / §7 / §8）
- [ ] 新开独立会话，不延续本阶段上下文 ← **进入 P1 时执行**
- [x] 协议/数据模型无变更

### Phase 1 投喂时将引用的文件

```
- .phase0/PHASE0-PROTOCOL-FROZEN.md      （协议，只引用不重述）
- .phase0/PHASE0-DATA-MODEL-FROZEN.md    （数据模型，只引用不重述）
- DEVELOPMENT-GUIDE.md §5 / §8 / §7      （前端会话）
- DEVELOPMENT-GUIDE.md §9.1 / §9.2 / §6 / §7（后端会话）
- DEV-COMMAND-PROMPT.md §十一            （禁止事项）
```

# AGENTS.md — 颤翎子客户端（agent + desktop 双层架构）

本文件供后续开发/迭代的 Agent 阅读。要点：**如何扩展 Agent 工具能力**、关键文件位置、构建启动命令、不可违反的约束。

## 架构总览

```
v2/
├── agent/        FastAPI 后端（Python，端口 8765）
│   └── app/
│       ├── api/          路由层（chat.py 会话流式 / files.py 上传解析 / models.py 模型探测）
│       ├── agents/       orchestrator.py：Agent 多轮工具调用闭环（核心引擎）
│       ├── tools/        __init__.py：工具基类 + 全部内置工具 + create_tools 注册表
│       ├── providers/    base.py：OpenAI 兼容流式客户端（tool_calls 聚合、thinking 解析）
│       ├── auth/         identity.py 身份解析 / permissions.py 操作分级授权
│       ├── security/     shell_security.py shell 白名单校验
│       └── data/uploads/ 上传文件与 Agent 生成文件的落盘目录
├── desktop/      Electron + React + Vite 客户端
│   ├── electron/ main.ts（IPC + shell 打开文件）/ preload.ts / agentProcess.ts（拉起后端）
│   └── src/
│       ├── components/chat/  ChatView.tsx（SSE 事件处理）/ MessageItem.tsx（执行过程+交付卡片）/ InputBox.tsx
│       ├── sse/             sseClient.ts SSE 解码
│       ├── store/           useUiStore.ts 对话状态
│       └── types/index.ts   共享类型
```

## 如何新增一个工具（30 行 + 1 行注册，框架零改动）

1. **定义工具类**（`agent/app/tools/__init__.py`）：继承 `BaseTool`，实现：
   - `name` / `description`（**模型靠 description 决策，写清"何时用、怎么用、别做什么"**）
   - `parameters`（OpenAI JSON Schema）
   - `async def execute(arguments) -> dict`（真实执行，返回 `{"success": bool, ...}`）
   - 生成文件的工具必须返回 `"path"` 绝对路径字段 → 自动进入前端交付卡片
2. **注册**：`create_tools()` 的 `tools` dict 加一行。
3. **装依赖**：`cd v2/agent && .venv/bin/python -m pip install <pkg> -i https://pypi.tuna.tsinghua.edu.cn/simple`
   （venv 的 pip shebang 指向旧路径，**禁止直接调 `.venv/bin/pip`**，必须 `python -m pip`）
4. **重启后端生效**：杀 8765 占用 → Electron 自动重启后端。

已有工具：`filesystem`（read/write/list/search）、`shell`、`knowledge`、`code`、`browser`、`db_query`、`rpa`、`doc_to_html`（mammoth）、`generate_ppt`（python-pptx）。

## 关键协议（改动时必须保持兼容）

- **SSE 事件类型**：`meta / thinking / tool_call / tool_result / citation / text / done / error`
- **tool_result 附加 `saved_files: string[]`**（从工具结果 JSON 的 `path` 提取）→ 前端渲染「打开文件/浏览文件夹」（Electron `shell.openPath` / `showItemInFolder`）
- **Agent 循环**：`orchestrator.run_stream` 把 `tools` schema 随请求下发 → 收集 `tool_calls` → `ToolNode` 执行 → `role="tool"` 消息回填 → 继续循环。上限 10 轮、同参去重、同工具连续 3 次强制终止。
- **模型能力**：`model_supports_tools` 默认 True，运行时收到 "does not support tools" 400 自动降级关闭工具并重试。
- **附件路径注入**：上传文件落盘 `data/uploads/<hash>/`，chat 请求 `attachments[].saved_path` → orchestrator 注入消息，模型可用 `filesystem` 读取原始文件。

## 权限与安全（勿绕过）

- **本地身份 = admin**（`auth/identity.py`：无 X-User-* 头时 role=admin 可写）；企业身份透传才按角色限制。
- **shell 白名单**在 `security/shell_security.py`（已含 ls/cat/python/pip/mkdir 等），危险模式正则拦截 `rm -rf`、`curl|sh` 等。
- `filesystem` 路径校验在 `allowed_root_dirs` 内（默认主目录 + uploads）。
- 工具审计：`ToolNode.execute` 统一做熔断检查 + 操作级权限 + 审计落库。

## 构建 / 启动

```bash
# 前端构建（改 desktop/src 后必跑）
cd v2/desktop && npx tsc --noEmit && npx vite build

# 启动客户端（Electron 自动拉起 v2/agent 后端）
cd v2/desktop && npx electron .          # 日志 /tmp/a4_client.log

# 标准重启序列（防打包 App 旧后端抢占 8765）
pkill -f "desktop/node_modules/electron"; pkill -f "app.main"; pkill -f "颤翎子AI助手"
for p in $(lsof -nP -iTCP:8765 -sTCP:LISTEN -t); do kill -9 $p; done
cd v2/desktop && npx electron .
```

## 同步到已安装的 App（/Applications/颤翎子AI助手.app）

**关键：后端 Python 不在 asar 内**，位于 `Resources/agent/`，可直接复制，无需重打包。

- **只改了后端（Python / config）** → 跑同步脚本即可（自动关 App）：
  ```bash
  bash v2/scripts/sync_agent_to_app.sh     # 需 dangerouslyDisableSandbox（写 /Applications 被沙箱拦）
  open -a "颤翎子AI助手"
  ```
  同步内容：`web_tools.py`、`settings.py`、`shell_security.py`、`config/settings.json`

- **改了前端（desktop/src）** → 先 `vite build`，再 `bash v2/update_app.sh`（重打包 asar）

- **settings.json 在 App 内的解析路径** = `Resources/config/settings.json`
  （`settings.py` 的 `CONFIG_PATH` 从 `app/api/` 上溯三级）。App 内若无此文件则用 `DEFAULT_SETTINGS`。

- **验证 App 内实际生效配置**（不依赖 token，`ps` 在沙箱下取不到 AGENT_TOKEN）：
  ```bash
  cd "/Applications/颤翎子AI助手.app/Contents/Resources/agent" && \
  .venv/bin/python -c "import sys; sys.path.insert(0,'.'); \
  from app.api.settings import _load_settings; print(_load_settings())"
  ```

## 常用验证

```bash
# 后端健康
curl -s http://127.0.0.1:8765/healthz

# AGENT_TOKEN（后端进程环境变量）
PID=$(lsof -nP -iTCP:8765 -sTCP:LISTEN -t | head -1)
TOKEN=$(ps eww $PID | tr ' ' '\n' | grep '^AGENT_TOKEN=' | cut -d= -f2)

# 文件上传解析（支持 txt/md/pdf/docx/xlsx/pptx，返回 saved_path）
curl -s -F "file=@<file>" -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8765/api/files/upload
```

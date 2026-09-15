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

已有工具：`filesystem`（read/write/list/search）、`shell`、`knowledge`、`code`、`browser`、`db_query`、`rpa`、`doc_to_html`（mammoth）、`generate_ppt`（python-pptx）、`create_plan`/`update_plan`（P0 计划-执行-验证，状态挂请求级 PlanBus）、`subagent`（P3 多智能体委派）。

## 模型服务器连接（llm_servers，端点型 base 处理）

- `agent/app/api/llm_servers.py`：`normalize_v1_url` 识别**端点型** base（含 `/chat/completions`、`/v2/`、`/api/paas/` 等标记或 `/v2`/`/v4` 结尾）→ 原样保留不补 `/v1`；裸域名/服务根才补 `/v1`（Ollama）。`_chat_endpoint_base` 把 `/chat/completions` 结尾剥成服务根（OpenAI SDK 会拼回）。
- `providers/base.py` build_providers 对端点型 base 同样剥根，否则推理请求打到 `/chat/completions/chat/completions` 404。
- 无 `/models` 列表接口的服务（讯飞星火等）：`/api/llm-servers/{id}/test` 404/405 时回退 `_probe_chat_endpoint`（POST 推理端点，<500 即视为可用），健康标记正常 + notice 提示填白名单；`sync-models` 同样处理。**改 normalize 逻辑后注意存量坏值**（如 `/v2/v1`）需用 PUT 重新写 base_url。

## P0.9 图片视觉输入（上传图片解析）

- `agent/app/vision.py`：data URL 落盘（`agent/data/uploads/images/chat-*.png`）+ OCR 提取。macOS 用 **ocrmac**（Apple Vision，离线中文准，pip 装进 dev venv 与 App mac runtime），Windows 用 PowerShell **Windows.Media.Ocr**（Win10 1803+ 系统自带，无 Python 依赖，模式同 speech.py SAPI 分支）。
- `orchestrator.run_stream` images 处理在 **resolve_model 之后**（需判断网关本机/远程）：
  - 本机网关（base_url 127.0.0.1/localhost）→ 注入 OCR 文本 + `image_url`（`http://127.0.0.1:{AGENT_PORT}/api/files/images/{name}`）
  - 远程网关 → 只注入 OCR 文本（网关访问不到本机 URL），文本带「用户上传截图 OCR 识别结果」说明
- 前端已具备：InputBox 图片选择（FileReader → base64 data URL）→ ChatView → sseClient images 字段；MessageItem 渲染图片缩略图。
- 验证：真实 chat 请求带 data URL 图 → 模型能复述截图内容（曾因滑动窗口裁掉 user 消息导致 400，见 context_manager 修复）。

## P0.5 三种工作模式（craft / plan / ask，对齐 WorkBuddy）

- `orchestrator.run_stream(mode=, plan_confirmed=)`：craft=直接执行（默认）；ask=关闭工具 schema（tools_enabled=False），纯问答；plan=未确认前工具门控只放行 `create_plan`/`update_plan`（`_PLAN_GATE_TOOLS`），其余调用返回「等待确认」占位结果不执行；计划建立后产出 `plan_awaiting_confirm` 事件（载荷同 plan 事件：goal+steps）并 break 本轮。
- `chat.py` ChatRequest 新增 `work_mode` / `plan_confirmed` 字段透传；Ask 模式跳过 P1 记忆提取写入。
- 前端：useUiStore.workMode（持久化）、ChatView 顶栏三模式切换器 + plan_awaiting_confirm 确认条（点「开始执行」以 plan_confirmed=true 重发续跑）。后台任务路径 tasks.py 暂不带模式（恒 craft）。

## P0.8 视频生成工具（generate_video，异步任务式）

- `agent/app/tools/video_tools.py`：GenerateVideoTool，**异步任务**：提交后轮询（上限 `_MAX_POLL_SECONDS`=480s，间隔 5s），超时返回「仍在生成中（含 task id）」而非假失败。自动探测两类端点：OpenAI 风格 `POST/GET /videos/generations{/id}`（提交 404/405 或轮询 404 时回退）→ 火山方舟 Seedance `POST/GET /contents/generations/tasks{/id}`；同步返回 `data[0].url` 的服务一步完成。产物落盘 `data/uploads/videos/vid-*.mp4`，返回 `path` + `url`（`/api/files/videos/{name}`，前端 `<video>` 播放）。
- 配置：settings.json `video_api.{base_url,model}` + Key 钥匙串 ref=`video-api:key`（同 P0.7 模式）。
- 前端：`VideoGenManager.tsx` 设置卡片 + MessageItem 视频交付预览。
- 测试注意：工具内函数级 `import httpx`，mock 用 `patch.object(httpx, 'AsyncClient', ...)`（patch 模块属性无效）。

## P0.7 图像生成工具（generate_image，OpenAI 兼容端点）

- `agent/app/tools/image_tools.py`：GenerateImageTool，POST `{base_url}/images/generations`（Bearer key），支持火山方舟 Seedream / OpenRouter 等任何兼容服务；产物落盘 `data/uploads/images/img-*.png`，返回 `path`（进交付卡片）+ `url`（`/api/files/images/{name}`，前端 `<img>` 预览）。
- 配置：settings.json `image_api.{base_url,model}`（PUT /api/settings 白名单字段 `image_api`/`image_api_key`）；Key 存钥匙串 ref=`image-api:key`（macOS keyring 无 list API，`keychain.list_keys()` 恒空，验证用 `keyring.get_password('private-ai-agent', ref)` 直接读）。
- 未配置 → 工具返回 success=false + 设置引导（不抛错，模型知道能力存在）。
- 前端：`ImageGenManager.tsx`（设置页卡片）+ MessageItem 对 `uploads/images/` 路径的交付文件渲染图片预览。
- 尺寸白名单 `_ALLOWED_SIZES`（1024 系/720p/1536 系），非法尺寸回落 1024。

## P0.6 语音交互（speech.py：TTS + ASR，全本地）

- `agent/app/api/speech.py`（已在 main.py 显式 import + include_router，新增路由两处都要改）：
  - `POST /api/speech/synthesize`：macOS `say`（中文声线优先 Tingting/Meijia/Sinji，**别用 Eddy——多语言声线说中文会静音**）→ `afconvert` 转 WAV → `data/uploads/speech/`，返回 {path, url}。
  - `POST /api/speech/transcribe`：ffmpeg 转 16k mono WAV → faster-whisper base（CPU int8，语言 zh）→ {text}。
  - `GET /api/speech/audio/{name}`：FileResponse 供 `<audio>` 播放（文件名安全化防穿越）。
- whisper 模型本地捆绑在 `agent/data/models/whisper-base/`（**gitignore，不提交**；从 modelscope 下载 Systran/faster-whisper-base，HF 官方在本机不可达）。模型目录不存在时回退联网拉 "base"。
- **App 依赖陷阱**：打包 App 的后端用 `Resources/runtime/python/macos-arm64/bin/python3`（不是 agent/.venv，也不是 agent/runtime）——新增 python 依赖必须装进这个 runtime 的 site-packages。
- 前端：InputBox 麦克风按钮（getUserMedia + MediaRecorder webm → 转写填入输入框）；MessageItem 朗读按钮（synthesize + Audio 播放）。

## P3 多智能体（subagents/）

- `agent/app/subagents/__init__.py`：6 角色（researcher/writer/reviewer/coder/analyst/assistant）各带独立 system prompt + 受限工具集；`run_subagent()` 惰性导入 `AgentOrchestrator`（**类名不是 Orchestrator**，orchestrator.py:261），新建受限工具注册表后复用 `run_stream` 跑子智能体；嵌套深度上限 2（`_depth_var`）。
- `agent/app/tools/subagent_tools.py`：SubagentTool，从 orchestrator 暴露的 contextvar（`get_current_model/get_current_conversation`）读取模型/会话，结果截断 6000 字。
- 子智能体消息只在内存，不写会话历史；多个 subagent 调用由 ToolNode 的 asyncio.gather 并行执行（总耗时 ≈ 最慢一个）。

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

## Windows 打包（NSIS 安装包）

- 一键脚本：`bash v2/scripts/build_windows.sh`（stage-release win-x64 → electron-builder --win nsis）。
- electron-builder.yml：`win.signAndEditExecutable=false`（macOS 无 wine 时跳过 rcedit，exe 用默认图标/版本）。
- **NSIS 工具链从 GitHub Releases 下载常超时**：预置缓存 `~/Library/Caches/electron-builder/nsis/nsis-3.0.4.1.7z`（+ nsis-resources-3.4.1.7z），镜像源 `https://registry.npmmirror.com/-/binary/electron-builder-binaries/nsis-3.0.4.1/nsis-3.0.4.1.7z`。
- **exe 图标修复（无 wine 也能用）**：`win.signAndEditExecutable=false` 会跳过 rcedit → exe 保持 Electron 默认图标。已在 `electron-builder.yml` 挂 `afterPack: scripts/after-pack-win.js`（resedit 纯 JS 改 PE 资源，macOS 无需 wine），pack 后自动把 `build/icon.ico` 的 6 个尺寸写入 exe。resedit API 注意：`NtExecutableResource.from(exe)` 只收 exe 实例（新版），替换用 `Resource.IconGroupEntry.replaceIconsForResource(res.entries, groupId, lang, icons)`。验证：`wrestool -l`（brew icoutils，只读）+ `icotool -x` 提取对比哈希。
- stage-release.sh 现在会捆绑 whisper 模型（agent/data/models → staging，electron-builder.yml agent filter 放行 `!**/data/uploads/**` 而非 `!**/data/**`）。
- Windows 语音：speech.py `os.name=='nt'` → TTS 用 PowerShell System.Speech（SAPI，base64 传文本防转义），ASR 的 ffmpeg 找不到时 PyAV 兜底解码（faster-whisper 自带 av）。
- agentProcess.ts 已跨平台（win32 → runtime/python/win-x64/python.exe，userData 存数据/配置）；electron-builder 的 win-unpacked 在 release/。
- **交叉安装 win 依赖**：`pip install --platform win_amd64 --python-version 3.13 --implementation cp --only-binary=:all: --target <dir> -i https://pypi.tuna.tsinghua.edu.cn/simple <pkg>` 后 tar 进 `runtime/python/win-x64/Lib/site-packages/`（faster-whisper 全家桶已装，勿重装）。

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

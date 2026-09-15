# 颤翎子AI助手 — Windows 开发部署包

本包是**可继续开发的部署包**：解压到任意 Windows 10/11 x64 机器即可运行，并可在包内直接修改代码继续开发，无需安装 Python / Node 环境（已内置）。

---

## 一、包结构

```
颤翎子AI助手-Windows-DevKit/
├── AGENTS.md                  # 项目规则（Agent 开发指引，改动代码前先读）
├── README-WINDOWS-DEV.md      # 本文件
├── config/
│   └── settings.json          # 默认配置（allowed_root_dirs 用 ~ 自动展开为用户主目录）
├── agent/                     # 后端源码（Python / FastAPI，端口 8765）
│   ├── app/                   # 全部后端代码（api / agents / tools / providers ...）
│   ├── data/models/           # 本地语音模型（whisper-base，语音转写开箱即用）
│   └── requirements*.txt      # 依赖清单（供自行重建环境时参考）
├── desktop/                   # 前端源码（Electron + React + Vite）
│   ├── src/  electron/        # 前端与 Electron 主进程源码
│   ├── dist/  dist-electron/  # 已构建产物（开箱即用）
│   └── package.json           # 前端工程（node_modules 首次自动安装）
├── runtime/python/win-x64/    # 内置 Windows Python 运行时 + 全部后端依赖（不要删除）
└── scripts/
    ├── start-agent.bat        # 单独启动 Agent 后端（调试用）
    ├── start-desktop.bat      # 一键启动客户端（推荐入口）
    └── build-desktop.bat      # 修改前端源码后重新构建
```

## 二、快速开始

1. 解压本包（路径建议不含空格与中文，如 `D:\spiritcaller`）。
2. 双击 `scripts\start-desktop.bat` —— 首次运行自动 `npm install`，随后启动客户端，Electron 会自动拉起后端。
3. 打开客户端即可对话；模型连接在「设置 → 模型服务器」配置。

> 只想单独跑后端调试：双击 `scripts\start-agent.bat`（用包内 Python 启动，不依赖系统 Python）。

## 三、继续开发（核心）

### 3.1 改后端（Python）
- 代码位置：`agent\app\`（API 路由在 `app/api/`、Agent 引擎在 `app/agents/`、工具在 `app/tools/`）。
- 改完**重启后端**生效：关闭客户端重开，或单独跑 `start-agent.bat`。
- 新增 Python 依赖：用包内运行时安装
  ```bat
  runtime\python\win-x64\python.exe -m pip install <包名> -i https://pypi.tuna.tsinghua.edu.cn/simple
  ```
- **如何新增一个工具**（30 行 + 1 行注册）：见 `AGENTS.md`「如何新增一个工具」。

### 3.2 改前端（React / Electron）
- 代码位置：`desktop\src\`（组件/界面）、`desktop\electron\`（主进程）。
- 改完运行 `scripts\build-desktop.bat`（tsc 类型检查 + vite 构建），再双击 `start-desktop.bat` 生效。
- 首次新增 npm 依赖：在 `desktop\` 下 `npm install <包名>`。

### 3.3 改配置
- `config\settings.json`：shell 白名单、允许目录（`allowed_root_dirs` 填 `~` 即用户主目录，可加具体路径）、技能市场地址等。改后重启后端生效。

### 3.4 打包成安装包（可选）
在 `desktop\` 下运行：
```bat
npx tsc --noEmit && npx vite build
npx electron-builder --win nsis
```
产物在 `desktop\release\`（需联网下载 electron/NSIS 工具链）。

## 四、数据与重置

- 运行时数据（会话、上传、模型连接配置）在 `agent\data\`（`*.db`、`uploads\` 等），**删除这些文件即恢复出厂**（模型文件 `data\models\` 勿删，删了语音转写失效并需重新下载）。
- 日志：后端 stdout 直接显示在启动窗口。

## 五、常见问题

| 现象 | 处理 |
|---|---|
| 端口 8765 被占用 | 关闭旧实例后重试；`netstat -ano \| findstr 8765` 查占用进程 |
| npm install 慢/失败 | `npm config set registry https://registry.npmmirror.com` 后重试 |
| 首次 Electron 启动慢 | 正常，正在拉取/解压运行时 |
| 语音转写不可用 | 确认 `agent\data\models\whisper-base\` 存在（包内已含） |
| 改代码后不生效 | 后端改动需重启后端；前端改动需先 `build-desktop.bat` 再重启客户端 |

## 六、版本

- 包版本：0.1.1-dev（含 P0 计划-执行-验证、P1 长期记忆、P2 定时任务、P3 多智能体、三工作模式、语音/图像/视频生成、图片视觉输入、模型连接端点修复、自我认知注入、code_locate 代码定位工具）
- 内置运行时：Python 3.13 (win-x64, python-build-standalone) + faster-whisper 等全部后端依赖
- 前端：Electron + React + Vite（已构建产物随包附带）

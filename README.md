# 私有域 AI 助手客户端

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.1.0-brightgreen.svg)](https://github.com/your-org/private-ai-client/releases)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgray.svg)](#)

一个类 WorkBuddy 的桌面 AI 助手，支持私有化部署和互联网商业大模型，主打「数据不出内网」。

## 快速开始

### 1. 下载安装包

从 [Releases](https://github.com/your-org/private-ai-client/releases) 下载对应平台版本：
- **macOS**：`私有域AI助手-0.1.0.dmg`
- **Windows**：`私有域AI助手 Setup 0.1.0.exe`
- **Linux**：`私有域AI助手-0.1.0.AppImage`

### 2. 安装 Ollama（本地模型）

```bash
# macOS
brew install ollama

# Windows
# 访问 https://ollama.ai/download/windows

# Linux
curl -fsSL https://ollama.ai/install.sh | sh
```

### 3. 下载模型

```bash
ollama pull qwen2.5:7b         # 通用对话
ollama pull deepseek-coder:6.7b  # 编程助手（可选）
ollama pull bge-m3             # Embedding（知识库必需）
```

### 4. 启动应用

双击应用图标即可。首次启动会自动连接本地 Ollama 服务。

---

## 核心特性

- **双模式模型接入**：自托管开源模型（Ollama/vLLM）+ 互联网商业大模型（OpenAI/DeepSeek/通义千问等）
- **MCP 工具执行**：文件操作、Shell 命令、浏览器自动化、代码分析、知识库检索
- **企业知识库 RAG**：本地向量检索，支持 PDF/MD/Word/TXT 格式，带引用溯源
- **安全基线**：端口绑定 127.0.0.1、密钥钥匙串存储、Shell 白名单 + UI 确认 + 审计日志
- **深色磨砂玻璃 UI**：支持深色/浅色主题切换

## 系统要求

- **macOS**：12.0+ (Apple Silicon + Intel)
- **Windows**：10+ (64-bit)
- **Linux**：Ubuntu 20.04+ / Debian 11+ / Fedora 36+ (x64)
- **内存**：8GB+（运行 7B 模型建议 16GB）
- **磁盘**：10GB+（含模型文件）

## 一键安装

### 方式一：下载安装包（推荐）

1. 从 [Releases](https://github.com/your-org/private-ai-client/releases) 下载对应平台的安装包
2. 运行安装程序，按提示完成安装
3. 启动应用

### 方式二：源码构建

```bash
# 1. 克隆仓库
git clone https://github.com/your-org/private-ai-client.git
cd private-ai-client

# 2. 安装依赖
cd desktop
npm install
cd ..

# 3. 安装 Python 依赖
cd agent
pip install -r requirements.txt
cd ..

# 4. 开发模式启动
cd desktop
npm run electron:dev
```

## 模型配置

### 本地模型（Ollama）

应用需要先安装 Ollama 和本地模型：

```bash
# 1. 安装 Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# 2. 下载模型
ollama pull qwen2.5:7b        # 通用对话
ollama pull deepseek-coder:6.7b  # 编程助手
ollama pull bge-m3            # Embedding
```

启动后在设置页选择「本地模型」，应用会自动连接 `http://127.0.0.1:11434`。

### 互联网商业大模型

在设置页配置 API Key：
- OpenAI：`sk-...`
- DeepSeek：`sk-...`
- 通义千问：`sk-...`

配置后在模型选择下拉中可见（带「公网」标识），选择即使用，无需额外授权。

## 功能说明

### 对话页

- 快捷指令：查公司请假制度 / 运行 ls 命令 / 分析一段代码 / 总结项目进展
- 消息流：支持思考 → 工具调用 → 工具结果 → 引用 → 回答的完整事件流
- 停止生成：点击按钮中断流式响应

### 知识库

- 导入文档：支持 PDF/MD/Word/TXT，自动解析、分块、向量化（bge-m3）
- 检索展示：Top-5 结果，带文档名/页码/相关度/混合检索说明
- 引用溯源：点击可跳转到原文位置

### 工具

- 文件系统：受限根目录读写，全量审计
- Shell 命令：白名单 + 路径校验 + **每次执行前 UI 确认**
- 知识库：RAG 检索，带 ACL 鉴权

### 设置

- 模型接入：OpenAI 兼容协议配置
- 安全设置：端口绑定、Shell 白名单、危险命令拦截
- 钥匙串：API Key 状态展示（不落明文）

## 安全设计

| 风险面 | 落地实现 |
|--------|----------|
| 密钥泄露 | API Key 存系统钥匙串；配置文件仅存引用名 |
| Shell 滥用 | 命令白名单 + 路径校验 + 危险命令黑名单 + UI 确认 + 审计 |
| 端口暴露 | Agent 仅绑定 127.0.0.1；一次性随机 token 鉴权 |
| 数据泄露 | 对话与文档默认仅本地；导出需显式操作 |
| 公网模型 | 用户显式配置密钥后可选用；选择时提示「发送至公网」 |

## MVP 验收标准

- [x] A1：干净环境一键启动（README 流程可复现）
- [x] A2：流式首字延迟 < 3s（本地 7B 模型）
- [x] A3：RAG Top-5 命中率 ≥ 80%（10 份混合格式文档）
- [x] A4：MCP 工具调用 ≥ 3 轮不中断（Golden Set 6/6 PASS）
- [x] A5：断电/崩溃重启对话不丢（SQLite 持久化）
- [x] A6：安全基线通过（Shell 白名单、端口绑定、密钥不落明文）

## 常见问题

**Q: 启动后后端连接失败？**
A: 检查 Ollama 是否运行（`ollama list`），端口 11434 是否可访问。

**Q: Shell 命令被拒绝？**
A: 命令不在白名单内。可在设置页查看/修改白名单配置。

**Q: 知识库检索不到内容？**
A: 检查文档是否已成功导入（知识库页查看 chunk 数），确认 embedding 模型正常。

**Q: 如何导出数据？**
A: 对话记录在 `~/Library/Application Support/PrivateAI/`（macOS）或 `%APPDATA%`（Windows），可直接复制。

## 开发

### 项目结构

```
private-ai-client/
├── desktop/          # Electron + React 前端
│   ├── electron/     # 主进程代码
│   ├── src/          # 渲染进程代码
│   └── package.json
├── agent/            # Python FastAPI 后端
│   ├── app/          # 应用代码
│   ├── eval/         # 评测框架
│   └── requirements.txt
├── scripts/          # 构建脚本
└── release/          # 打包输出
```

### 运行测试

```bash
# 后端单元测试
cd agent
pytest tests/

# Golden Set 评测
cd agent
python eval/golden_set.py
```

## 许可证

MIT License

## 联系方式

- GitHub Issues: https://github.com/your-org/private-ai-client/issues
- 邮箱：your-email@example.com

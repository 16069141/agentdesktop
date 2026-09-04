# Release Notes - v0.1.0 (MVP)

## 概述

私有域 AI 助手客户端 MVP 正式发布，支持私有化部署和互联网商业大模型，主打「数据不出内网」。

## 核心功能

### 双模式模型接入
- **本地模型**：Ollama（qwen2.5:7b、deepseek-coder:6.7b、bge-m3）
- **互联网模型**：OpenAI、DeepSeek、通义千问等（OpenAI 兼容接口）

### MCP 工具执行
- **文件系统**：受限根目录读写，全量审计
- **Shell 命令**：白名单 + UI 确认 + 超时控制
- **知识库**：RAG 检索，带引用溯源
- **代码分析**：Tree-sitter 静态分析

### 企业知识库 RAG
- 支持格式：PDF、MD、Word、TXT
- 混合检索：BM25 + 向量 RRF 融合
- 引用展示：文档名/页码/相关度

### 安全基线
- Agent 仅绑定 127.0.0.1
- API Key 存系统钥匙串，不落明文
- Shell 命令白名单 + 执行确认 + 审计日志
- 公网模型显式授权提示

## 技术栈

| 层 | 技术 |
|---|------|
| 桌面端 | Electron 33 + React 18 + TypeScript |
| 后端 | Python FastAPI + LangGraph |
| 向量库 | ChromaDB（本地持久化） |
| Embedding | bge-m3（Ollama） |
| 数据库 | SQLite |
| 通信 | SSE 流式 + HTTP API |

## 安装方式

### macOS
下载 `.dmg` 安装包，拖拽到 Applications 即可。

### Windows
下载 `.exe` 安装包，按向导完成安装。

### Linux
下载 `.AppImage`，赋予执行权限后运行。

### 源码构建
```bash
git clone <repo>
cd desktop && npm install
npm run electron:build
```

## 系统要求

- macOS 12.0+ / Windows 10+ / Linux Ubuntu 20.04+
- 8GB+ 内存（运行 7B 模型建议 16GB）
- 10GB+ 磁盘空间（含模型文件）

## 已知限制

- ChromaDB 未安装时 RAG 降级为文本匹配
- 首次使用需手动下载 Ollama 模型
- 多用户共享功能暂不支持（MVP 后立项）

## 下一步计划

- [ ] 自动更新机制（electron-updater）
- [ ] 更多模型支持（vLLM、本地 LLaMA）
- [ ] 国际化 i18n
- [ ] 多用户 ACL 权限
- [ ] 企业部署方案

## 反馈与支持

- GitHub Issues: https://github.com/your-org/private-ai-client/issues
- 邮箱: your-email@example.com

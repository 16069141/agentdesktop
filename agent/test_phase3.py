#!/usr/bin/env python3
"""Phase 3 验证脚本 - 测试 Agent 核心功能"""

import sys
import os
import asyncio

# 设置路径
sys.path.insert(0, '/Users/caojian/Desktop/agent/workdesktop/agent')
os.environ['AGENT_TOKEN'] = 'test123'
os.environ['AGENT_PORT'] = '9999'

from app.agents.orchestrator import AgentOrchestrator
from app.providers.base import OpenAICompatibleProvider
from app.tools import FilesystemTool, KnowledgeTool
from app.security.shell_security import ShellSecurity

print("=" * 60)
print("Phase 3 验证：Agent 核心功能")
print("=" * 60)

# 1. 测试工具实例
print("\n[1] 测试工具实例...")
security = ShellSecurity(allowed_commands=["ls", "cat", "pwd", "echo"])
tools = {
    "filesystem": FilesystemTool(),
    "shell": ShellTool(security=security),
    "knowledge": KnowledgeTool(),
}
print(f"  ✓ 创建了 {len(tools)} 个工具实例:")
for name, tool in tools.items():
    print(f"    - {name}: {tool.description[:50]}...")

# 2. 测试 Provider
print("\n[2] 测试 Ollama Provider...")
try:
    provider = OpenAICompatibleProvider(
        base_url="http://127.0.0.1:11434/v1",
        api_key="ollama",
        model="qwen2.5-1m-q4"
    )
    print(f"  ✓ Provider 初始化成功")
    print(f"    模型: {provider.model}")
    print(f"    端点: {provider.base_url}")
except Exception as e:
    print(f"  ✗ Provider 初始化失败: {e}")
    sys.exit(1)

# 3. 测试 Agent Orchestrator
print("\n[3] 测试 Agent Orchestrator...")
try:
    orchestrator = AgentOrchestrator(
        provider=provider,
        tools=tools,
        max_turns=5
    )
    print(f"  ✓ Agent Orchestrator 初始化成功")
    print(f"    最大轮次: {orchestrator.max_turns}")
except Exception as e:
    print(f"  ✗ Agent Orchestrator 初始化失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# 4. 测试简单对话
print("\n[4] 测试简单对话...")
try:
    result = asyncio.run(orchestrator.run("你好，请简单介绍一下自己"))
    print(f"  ✓ 对话执行成功")
    print(f"    轮次: {result.get('turns', 0)}")
    reply = result.get('reply', '')
    print(f"    回复: {reply[:150]}{'...' if len(reply) > 150 else ''}")
except Exception as e:
    print(f"  ⚠ 对话执行失败: {e}")
    import traceback
    traceback.print_exc()

# 5. 测试文件系统工具
print("\n[5] 测试文件系统工具...")
try:
    result = asyncio.run(orchestrator.run("列出 /Users/caojian/Desktop/agent/workdesktop/agent 目录的文件"))
    print(f"  ✓ 文件列表测试完成")
    print(f"    轮次: {result.get('turns', 0)}")
    reply = result.get('reply', '')
    print(f"    回复片段: {reply[:200]}{'...' if len(reply) > 200 else ''}")
except Exception as e:
    print(f"  ⚠ 文件列表测试失败: {e}")

print("\n" + "=" * 60)
print("验证完成")
print("=" * 60)

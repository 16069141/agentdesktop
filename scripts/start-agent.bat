@echo off
:: Phase 3 - Agent 编排启动脚本 (Windows)
:: 启动命令: scripts\start-agent.bat

echo ========================================
echo   PrivateAI Agent - Phase 3 启动
echo ========================================
echo.

:: 设置环境变量
set AGENT_TOKEN=%AGENT_TOKEN%
set AGENT_HOST=127.0.0.1
set AGENT_PORT=8765

:: 生成一次性 Token（如果未设置）
if "%AGENT_TOKEN%"=="" (
    set /p AGENT_TOKEN=请输入 Agent Token（或直接运行脚本生成随机Token）:
    if "%AGENT_TOKEN%"=="" (
        for /f "tokens=*" %%i in ('python -c "import secrets; print(secrets.token_hex(32))"') do set AGENT_TOKEN=%%i
    )
)

:: 启动 Agent
echo [agent] Token: %AGENT_TOKEN:~0,8%...
echo [agent] 监听地址: %AGENT_HOST%:%AGENT_PORT%
echo [agent] 启动中...
echo.

:: 切换到 agent 目录并启动
cd /d "%~dp0..\agent"
python -m app.main --host %AGENT_HOST% --port %AGENT_PORT%

pause

@echo off
setlocal
:: ============================================
::  颤翎子AI助手 - Agent 后端启动 (Windows)
::  使用包内自带 Python 运行时，无需安装 Python
:: ============================================
cd /d "%~dp0.."

set "PYTHON=%~dp0..\runtime\python\win-x64\python.exe"
if not exist "%PYTHON%" (
    echo [错误] 未找到内置 Python 运行时: %PYTHON%
    echo        请确认本包结构完整（runtime\python\win-x64\ 存在）。
    pause
    exit /b 1
)

:: 一次性 Token：首次自动生成并保存到 agent\.agent_token
set "TOKEN_FILE=agent\.agent_token"
if not exist "%TOKEN_FILE%" (
    for /f "delims=" %%i in ('"%PYTHON%" -c "import secrets; print(secrets.token_hex(32))"') do echo %%i> "%TOKEN_FILE%"
)
set /p AGENT_TOKEN=< "%TOKEN_FILE%"

set AGENT_HOST=127.0.0.1
set AGENT_PORT=8765

echo ============================================
echo   颤翎子AI助手 Agent 后端
echo   Token: %AGENT_TOKEN:~0,8%...
echo   监听:  %AGENT_HOST%:%AGENT_PORT%
echo ============================================
echo.

cd agent
"%PYTHON%" -m app.main --host %AGENT_HOST% --port %AGENT_PORT%
if errorlevel 1 (
    echo.
    echo [错误] Agent 启动失败，请检查端口 8765 是否被占用。
)
pause

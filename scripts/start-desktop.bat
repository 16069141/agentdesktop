@echo off
setlocal
:: ============================================
::  颤翎子AI助手 - 桌面客户端启动 (Windows)
::  首次运行会自动 npm install；Electron 会自动拉起 Agent 后端 (:8765)
:: ============================================
cd /d "%~dp0..\desktop"

if not exist node_modules (
    echo [首次运行] 安装前端依赖（npm install）...
    call npm install
    if errorlevel 1 (
        echo [错误] npm install 失败。若网络慢可换镜像：
        echo        npm config set registry https://registry.npmmirror.com
        pause
        exit /b 1
    )
)

echo [desktop] 启动 Electron（将自动拉起 Agent 后端 :8765）...
echo [desktop] 关闭本窗口即退出客户端。
npx electron .
pause

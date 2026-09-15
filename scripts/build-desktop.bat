@echo off
setlocal
:: ============================================
::  颤翎子AI助手 - 前端构建 (Windows)
::  修改 desktop/src 下的前端代码后运行本脚本重新构建
:: ============================================
cd /d "%~dp0..\desktop"

if not exist node_modules (
    echo [首次运行] 安装前端依赖...
    call npm install
    if errorlevel 1 exit /b 1
)

echo [build] tsc 类型检查 + vite 构建...
call npx tsc --noEmit
if errorlevel 1 (
    echo [错误] TypeScript 类型检查未通过，请先修复。
    pause
    exit /b 1
)
call npx vite build
if errorlevel 1 (
    echo [错误] vite 构建失败。
    pause
    exit /b 1
)
echo.
echo [build] 完成。产物在 desktop\dist\，重新运行 start-desktop.bat 生效。
pause

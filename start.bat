@echo off
chcp 65001 >nul
title 闲鱼自动化管理系统

echo ==============================================
echo   闲鱼自动上架 + 自动发货系统 v1.0
echo ==============================================
echo.

cd /d "%~dp0"

:: 检查 Python 是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.9+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: 检查并安装依赖
echo [1/3] 检查依赖...
python -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo [安装] 正在安装 Flask...
    pip install flask flask-cors -q
)

python -c "import playwright" >nul 2>&1
if errorlevel 1 (
    echo [安装] 正在安装 Playwright...
    pip install playwright -q
    echo [安装] 正在安装 Chromium 浏览器...
    python -m playwright install chromium
)

:: 确保目录存在
echo [2/3] 初始化目录...
if not exist "data" mkdir data
if not exist "products" mkdir products

:: 启动服务
echo [3/3] 启动服务...
echo.
echo ==============================================
echo   管理后台: http://127.0.0.1:5000
echo   按 Ctrl+C 停止服务
echo ==============================================
echo.

cd backend
python app.py

pause

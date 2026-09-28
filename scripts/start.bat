@echo off
chcp 65001 >nul
REM 启动拼豆图纸生成器（Windows）
REM 用法：scripts\start.bat [--reload]
cd /d "%~dp0\.."

where uv >nul 2>nul
if errorlevel 1 (
  echo 未找到 uv，请先安装：https://docs.astral.sh/uv/getting-started/installation/
  exit /b 1
)

uv sync --quiet || exit /b 1
uv run python run.py %*

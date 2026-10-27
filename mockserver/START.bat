@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
echo ================================================
echo   BSide 本地假服务
echo   保持本窗口开启，关闭即停止服务
echo   人设文件：persona.md （改完不用重启）
echo ================================================
py -3.12 server.py --port 8787
pause

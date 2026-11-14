@echo off
REM pathCAM 启动器：双击运行，自动开浏览器
cd /d "%~dp0"
echo.
echo   pathCAM - 3D 路径规划上位机
echo   ================================
echo.
echo   服务地址: http://127.0.0.1:8765
echo   按 Ctrl+C 停止
echo.
start "" http://127.0.0.1:8765
python -m uvicorn app.server:app --host 127.0.0.1 --port 8765
pause

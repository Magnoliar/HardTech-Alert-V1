@echo off
chcp 65001 >nul
echo ================================================
echo   HardTech Insight - 每日自动运行
echo ================================================
cd /d %~dp0

:: 优先使用 uv，回退到 python
where uv >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [UV] 使用 uv run 启动...
    uv run python main.py
) else (
    echo [Python] uv 未安装，使用系统 Python...
    if exist .venv\Scripts\python.exe (
        .venv\Scripts\python.exe main.py
    ) else (
        python main.py
    )
)

echo.
echo 运行完毕，按任意键退出...
pause >nul

@echo off
chcp 65001 >nul
echo ================================================
echo   📝 独立深度文章写作工具
echo ================================================
echo.
cd /d %~dp0

:: 如果有命令行参数，直接传递
if not "%~1"=="" (
    echo [输入] %*
    echo.
    where uv >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        uv run python write_article.py %*
    ) else (
        if exist .venv\Scripts\python.exe (
            .venv\Scripts\python.exe write_article.py %*
        ) else (
            python write_article.py %*
        )
    )
) else (
    :: 无参数，进入交互模式
    where uv >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        uv run python write_article.py
    ) else (
        if exist .venv\Scripts\python.exe (
            .venv\Scripts\python.exe write_article.py
        ) else (
            python write_article.py
        )
    )
)

echo.
echo 运行完毕，按任意键退出...
pause >nul

@echo off
chcp 65001 >nul
setlocal

REM ============================================================
REM  星际猎人 · 自动助手 测试仪 —— 一键打包（图形界面版）
REM  产物：dist\星际猎人测试仪.exe
REM  需要：C:\Program Files\Python314 下的 Python（已装 PyInstaller）
REM ============================================================

set PY="C:\Program Files\Python314\python.exe"
cd /d "%~dp0"

echo.
echo [1/4] 清理旧的构建产物...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [2/4] 开始打包（约 3-8 分钟，请勿关闭窗口）...
%PY% -m PyInstaller tester.spec --noconfirm
if errorlevel 1 (
    echo.
    echo [失败] 打包出错，请看上面的日志。
    pause
    exit /b 1
)

echo [3/4] 复制运行所需的配置文件与模板...
if exist "dist\config.json" del /q "dist\config.json"
if exist config.json copy /y "config.json" "dist\" >nul
if exist refs (
    if not exist "dist\refs" mkdir "dist\refs"
    xcopy /e /i /y "refs\*" "dist\refs\" >nul
)
if exist README.md copy /y "README.md" "dist\" >nul

echo [4/4] 完成。
echo.
echo   ^>^>^>  dist\星际猎人测试仪.exe   双击即可运行
echo.
dir /b dist
echo.
pause

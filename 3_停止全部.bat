@echo off
chcp 65001 >nul
title 停止星际猎人自动脚本及关联进程
echo.
echo 正在停止：星际猎人自动脚本 + 关联进程 + adb（彻底释放设备信道）...
echo.

:: 1) 关闭图形测试仪
taskkill /F /IM "星际猎人测试仪.exe" >nul 2>&1
if errorlevel 1 (echo   - 测试仪未运行) else (echo   - 已结束 星际猎人测试仪.exe)

:: 2) 关闭持有实例锁的 python 进程（.run_lock 里记录的就是 autoup/daily/all 的 pid）
if exist .run_lock (
    for /f "usebackq delims=" %%p in (".run_lock") do (
        taskkill /F /PID %%p >nul 2>&1
        if errorlevel 1 (echo   - 锁进程 pid=%%p 已结束或不存在) else (echo   - 已结束锁进程 pid=%%p)
    )
    del /Q .run_lock >nul 2>&1
) else (
    echo   - 无实例锁（未运行自动脚本）
)

:: 3) 关闭 adb（彻底释放设备信道）
taskkill /F /IM adb.exe >nul 2>&1
if errorlevel 1 (echo   - adb 未运行) else (echo   - 已结束 adb.exe)

:: 4) 兜底：结束任何仍在跑 lagrange_upgrade.py 的 python 进程
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter ""CommandLine LIKE '%%lagrange_upgrade%%'"" | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
echo   - 已兜底清理 lagrange_upgrade.py 残留 python 进程

echo.
echo 完成。如仍有残留，请打开任务管理器手动结束 python.exe / adb.exe。
echo.
pause

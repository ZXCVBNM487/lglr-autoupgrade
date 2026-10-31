@echo off
chcp 65001 >nul
cd /d %~dp0
python -m pip install -r requirements.txt
echo.
echo ====== 开始自动升级循环，关闭本窗口或按 Ctrl+C 停止 ======
python lagrange_upgrade.py run
pause

@echo off
chcp 65001 >nul
cd /d %~dp0
echo 第一次运行会自动安装依赖（OCR 库约 100MB），请稍等...
python -m pip install -r requirements.txt
echo.
echo ====== 开始全屏识别，请确保游戏停在建筑详情界面 ======
python lagrange_upgrade.py scan
echo.
pause

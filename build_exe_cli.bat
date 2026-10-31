@echo off
chcp 65001 >nul
cd /d %~dp0
echo ====== 打包命令行版（无界面，给高级用户/计划任务用） ======
echo 产物: dist\LagrangeUpgrade\LagrangeUpgrade.exe
echo.
"C:\Program Files\Python314\python.exe" -m PyInstaller --noconfirm --clean --console --name LagrangeUpgrade ^
  --collect-all rapidocr_onnxruntime ^
  --collect-all onnxruntime ^
  lagrange_upgrade.py
echo.
if exist "dist\LagrangeUpgrade\LagrangeUpgrade.exe" (
  echo 打包成功：dist\LagrangeUpgrade\ 整个文件夹拷走即可用
  copy /y config.json "dist\LagrangeUpgrade\config.json" >nul 2>&1
) else (
  echo 打包失败，请把上面的报错发我
)
pause

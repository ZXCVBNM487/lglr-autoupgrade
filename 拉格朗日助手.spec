# -*- mode: python ; coding: utf-8 -*-
"""
拉格朗日助手 单文件合一打包 spec
================================
一个 exe 内含三部分，按首参数自分发：
  无参数        → Tkinter 图形界面（自动隐藏控制台黑框）
  --core ...    → OCR 值守核心（lagrange_upgrade，scan/shot/tap/run/check）
  --launcher .. → 游戏一键启动器（game_launcher，OCR+模拟鼠标）
外置文件（放 exe 同目录，不打入包内）：config.json / qq_token_cache.json
内置只读资源：refs\（桌面图标模板等）
"""
import os

block_cipher = None
here = os.path.abspath(".")
refs_dir = os.path.join(here, "refs")

datas = []
if os.path.isdir(refs_dir):
    datas.append((refs_dir, "refs"))

a = Analysis(
    ["lagrange_gui.py"],
    pathex=[here],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "game_launcher",
        "lagrange_upgrade",
        "adb_diag",
        "adb_locator",
        "qq_official",
        "websocket",
        "numpy",
        "cv2",
        "PIL",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "scipy", "pandas", "tkinter.test", "unittest", "pydoc"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# 收集 OCR / 图像 相关全部资源（模型文件、onnxruntime dll 等）
from PyInstaller.utils.hooks import collect_all
for pkg in ("rapidocr_onnxruntime", "onnxruntime", "cv2", "PIL"):
    tmp_datas, tmp_binaries, tmp_hidden = collect_all(pkg)
    a.datas += tmp_datas
    a.binaries += tmp_binaries
    a.hiddenimports += tmp_hidden

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="拉格朗日助手",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,          # GUI 模式启动后自行隐藏控制台；子进程保留控制台
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

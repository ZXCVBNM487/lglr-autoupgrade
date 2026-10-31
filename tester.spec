# -*- mode: python ; coding: utf-8 -*-
"""星际猎人测试仪 打包配置

用法：
    pyinstaller tester.spec --noconfirm

产物：dist/星际猎人测试仪.exe（单文件）
config.json 与 refs/ 不打包进 exe，由 build_exe.bat 复制到 dist 目录，
因为主脚本按「exe 所在目录」读取它们，用户可以直接编辑。
"""
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = [
    "lagrange_upgrade",
    "adb_locator",
    "qq_official",
    "adb_diag",
]

# OCR / 图像依赖：这些包带数据文件与二进制，必须整包收集
for pkg in ("rapidocr_onnxruntime", "onnxruntime", "cv2", "PIL"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

a = Analysis(
    ["tester.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib", "scipy", "pandas", "IPython", "jupyter",
        "notebook", "tkinter.test", "test", "unittest.test",
        "PySide6", "PyQt5", "PyQt6",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="星际猎人测试仪",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,              # GUI 不弹黑窗；CLI 模式靠管道传输出
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

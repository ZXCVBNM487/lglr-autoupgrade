# -*- coding: utf-8 -*-
"""在同一个进程生命周期内：启动测试仪 exe → 等窗口 → 截图 → 关闭。
用于验证 GUI 布局，避免跨工具调用导致进程被回收。"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time

from PIL import ImageGrab

EXE = r"E:\拉格朗日自动升级\dist\星际猎人测试仪.exe"
OUT = r"E:\拉格朗日自动升级\gui_probe.png"

if len(sys.argv) > 1 and sys.argv[1] == "src":
    CMD = [r"C:\Program Files\Python314\python.exe", r"E:\拉格朗日自动升级\tester.py"]
    OUT = r"E:\拉格朗日自动升级\gui_src.png"
elif len(sys.argv) > 1 and sys.argv[1] == "auto":
    _c = sys.argv[2] if len(sys.argv) > 2 else "check"
    CMD = [r"C:\Program Files\Python314\python.exe", r"E:\拉格朗日自动升级\tester.py",
           "--autorun", _c]
    OUT = r"E:\拉格朗日自动升级\gui_auto.png"
elif len(sys.argv) > 1 and sys.argv[1] == "exe":
    _c = sys.argv[2] if len(sys.argv) > 2 else "check"
    CMD = [EXE, "--autorun", _c]
    OUT = r"E:\拉格朗日自动升级\gui_exe.png"
else:
    CMD = [EXE]

u = ctypes.windll.user32
u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.IsWindowVisible.argtypes = [wt.HWND]
u.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
u.SetForegroundWindow.argtypes = [wt.HWND]
u.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]


def find_window():
    hits = []
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb(h, l):
        if u.IsWindowVisible(h):
            n = u.GetWindowTextLengthW(h)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(h, buf, n + 1)
                if "测试仪" in buf.value:
                    hits.append((h, buf.value))
        return True

    u.EnumWindows(CB(cb), 0)
    return hits


print("[1] 启动:", CMD)
p = subprocess.Popen(CMD)
print("    pid =", p.pid)

hits = []
for i in range(40):
    time.sleep(1)
    hits = find_window()
    if hits:
        print(f"[2] {i + 1}s 找到窗口: {hits}")
        break
    if p.poll() is not None:
        print(f"[!] {i + 1}s 进程已退出, 退出码 = {p.returncode}")
        sys.exit(2)

if not hits:
    print("[!] 40 秒内没找到窗口")
    p.terminate()
    sys.exit(3)

h = hits[0][0]
u.ShowWindow(h, 9)
u.SetForegroundWindow(h)
_mode = sys.argv[1] if len(sys.argv) > 1 else ""
time.sleep(50 if _mode in ("auto", "exe") else 2)

r = wt.RECT()
u.GetWindowRect(h, ctypes.byref(r))
print(f"[3] 窗口矩形: {r.left},{r.top} - {r.right},{r.bottom}")

im = ImageGrab.grab()
im.save(OUT)
print("[4] 整屏截图:", im.size, "->", OUT)

if r.right - r.left > 200:
    crop = im.crop((r.left, r.top, r.right, r.bottom))
    crop.save(OUT.replace(".png", "_win.png"))
    print("[5] 窗口截图:", crop.size)

time.sleep(1)

# ---- 可选：模拟点击第一个按钮，验证 GUI → 子进程 → 日志 的链路 ----
if len(sys.argv) > 2 and sys.argv[2] == "click":
    u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    u.mouse_event.argtypes = [wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_void_p]
    u.SetForegroundWindow(h)
    time.sleep(0.6)
    # 按钮「① 环境自检」相对窗口左上角约 (137, 160)
    px, py = r.left + 137, r.top + 160
    print(f"[6] 点击按钮 -> ({px}, {py})")
    u.SetCursorPos(px, py)
    time.sleep(0.4)
    u.mouse_event(0x0002, 0, 0, 0, None)
    time.sleep(0.08)
    u.mouse_event(0x0004, 0, 0, 0, None)
    time.sleep(18)
    im2 = ImageGrab.grab()
    im2.save(OUT.replace(".png", "_click.png"))
    crop2 = im2.crop((r.left, r.top, r.right, r.bottom))
    crop2.save(OUT.replace(".png", "_click_win.png"))
    print("[7] 点击后截图完成")

p.terminate()
print("[8] 已关闭")

# -*- coding: utf-8 -*-
"""
adb_locator.py —— adb.exe 智能定位模块
========================================
查找顺序（由快到慢）：
  1. config.json 的 adb_path / 环境变量 ADB_PATH
  2. 已知模拟器安装路径静态表（覆盖雷电/MuMu/夜神/逍遥/蓝叠/腾讯/MuMuFever 等）
  3. where adb（系统 PATH）
  4. 进程探测：模拟器正在运行 → 在其 exe 附近找 adb.exe
  5. 关键词目录扫描：全盘固定硬盘符，按模拟器关键词剪枝，深度≤4

返回：adb.exe 绝对路径；找不到返回 None。
find_adb_ex() 返回 (path, how_found)，便于 GUI 显示来源。
"""

import os
import re
import glob
import time
import subprocess

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

ADB_NAMES = ("adb.exe", "HD-Adb.exe")
ADB_SUBDIRS = ("", "shell", "bin", "adb", "platform-tools", "tools", "sdk")

# 静态候选路径（常见的模拟器默认安装位置）
_STATIC = [
    # 独立官方 platform-tools（真机优先，版本最新，避免与模拟器 adb 冲突）
    r"E:\AndroidBuild\platform-tools-latest-windows\platform-tools\adb.exe",
    # 网易 MuMu（含 FeverApps 渠道版 / nx_device 结构）
    r"D:\FeverApps\MuMuFever\nx_main\adb.exe",
    r"D:\FeverApps\MuMuFever\nx_device\12.0\shell\adb.exe",
    r"C:\FeverApps\MuMuFever\nx_main\adb.exe",
    r"E:\FeverApps\MuMuFever\nx_main\adb.exe",
    r"C:\Program Files\Netease\MuMu Player 12\shell\adb.exe",
    r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
    r"D:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
    r"E:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
    r"C:\Program Files (x86)\Netease\MuMuPlayer-12.0\shell\adb.exe",
    r"C:\Program Files\Netease\MuMuPlayer-13\shell\adb.exe",
    r"C:\Program Files\Netease\MuMu\emulator\nemu\EmulatorShell\adb.exe",
    # 雷电
    r"C:\leidian\LDPlayer9\adb.exe", r"D:\leidian\LDPlayer9\adb.exe",
    r"E:\leidian\LDPlayer9\adb.exe", r"F:\leidian\LDPlayer9\adb.exe",
    r"C:\leidian\LDPlayer4\adb.exe", r"D:\leidian\LDPlayer4\adb.exe",
    r"C:\ChangZhi\dnplayer2\adb.exe",
    # 夜神
    r"C:\Program Files\Nox\bin\adb.exe", r"D:\Program Files\Nox\bin\adb.exe",
    r"E:\Program Files\Nox\bin\adb.exe", r"C:\Program Files (x86)\Nox\bin\adb.exe",
    # 逍遥
    r"C:\Program Files\Microvirt\MEmu\adb.exe", r"D:\Program Files\Microvirt\MEmu\adb.exe",
    r"E:\Program Files\Microvirt\MEmu\adb.exe",
    # 蓝叠
    r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe",
    r"C:\Program Files\BlueStacks\HD-Adb.exe",
    r"D:\Program Files\BlueStacks_nxt\HD-Adb.exe",
    # 腾讯手游助手 / Gameloop
    r"C:\Program Files\Android\Tencent\MobileGamePC\adb.exe",
    r"C:\Program Files (x86)\Tencent\MobileGamePC\adb.exe",
    r"C:\Program Files (x86)\Gameloop\adb.exe",
    # Android SDK
    r"C:\AndroidBuild\sdk\platform-tools\adb.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
]

# 关键词剪枝目录扫描用
_KEYWORDS = ("mumu", "netease", "nemu", "leidian", "ldplayer", "changzhi", "dnplayer",
             "nox", "bignox", "memu", "microvirt", "bluestacks", "tencent", "gameloop",
             "txgameassistant", "mobilegamepc", "android", "feverapps", "sameday",
             "sdk", "emulator", "网易", "雷电", "夜神", "逍遥", "手游助手")
_SKIP_DIRS = {"windows", "programdata", "$recycle.bin", "system volume information",
              "recovery", "perflogs", "node_modules", "__pycache__", "conda",
              "miniconda3", "anaconda3", ".git", ".trae-cn", "AppData"}


def _valid(path):
    return bool(path) and os.path.isfile(path)


def _from_static():
    for p in _STATIC:
        if _valid(p):
            return p
    # Program Files 通配
    for base in (r"C:\Program Files", r"C:\Program Files (x86)",
                 r"D:\Program Files", r"E:\Program Files"):
        for pat in (os.path.join(base, "Netease", "*", "shell", "adb.exe"),
                    os.path.join(base, "Netease", "*", "emulator", "*", "adb.exe"),
                    os.path.join(base, "leidian", "*", "adb.exe"),
                    os.path.join(base, "Microvirt", "MEmu", "adb.exe"),
                    os.path.join(base, "Nox", "bin", "adb.exe"),
                    os.path.join(base, "BlueStacks_nxt", "HD-Adb.exe")):
            for hit in glob.glob(pat):
                if _valid(hit):
                    return hit
    return None


def _from_where():
    try:
        out = subprocess.run(["where", "adb"], capture_output=True, timeout=10,
                             creationflags=CREATE_NO_WINDOW).stdout.decode("utf-8", "replace")
        for line in out.splitlines():
            line = line.strip()
            if line.lower().endswith(".exe") and _valid(line):
                return line
    except Exception:
        pass
    return None


def _probe_dir(d):
    """在某目录及其常见子目录中找 adb"""
    for sub in ADB_SUBDIRS:
        for name in ADB_NAMES:
            p = os.path.join(d, sub, name) if sub else os.path.join(d, name)
            if _valid(p):
                return p
    return None


_EMU_PROC_RE = re.compile(
    r"MuMu|Nemu|dnplayer|LdBox|LdVBox|Ld9Box|Nox|MEmu|HD-Player|aow_exe|MetalBox|Player",
    re.I)


def _from_process():
    """模拟器进程在运行 → 到它 exe 附近找 adb"""
    if os.name != "nt":
        return None
    ps = ("Get-Process | Where-Object {$_.Path} | "
          "Where-Object {$_.ProcessName -match "
          "'MuMu|Nemu|dnplayer|LdBox|LdVBox|Ld9Box|Nox|MEmu|HD-Player|aow_exe|MetalBox'} | "
          "Select-Object -ExpandProperty Path -Unique")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, timeout=15,
                             creationflags=CREATE_NO_WINDOW).stdout.decode("utf-8", "replace")
    except Exception:
        return None
    for line in out.splitlines():
        exe = line.strip()
        if not exe or not exe.lower().endswith(".exe"):
            continue
        d = os.path.dirname(exe)
        parent = os.path.dirname(d)
        # 同目录 / 常见子目录 / 父目录 / 兄弟目录
        for cand in (d, parent):
            hit = _probe_dir(cand)
            if hit:
                return hit
        for pat in (os.path.join(parent, "*", "adb.exe"),
                    os.path.join(parent, "*", "shell", "adb.exe"),
                    os.path.join(parent, "*", "bin", "adb.exe"),
                    os.path.join(d, "*", "adb.exe")):
            for h in glob.glob(pat):
                if _valid(h):
                    return h
    return None


def _walk_find(root, deadline, max_depth=4, state=None):
    """关键词剪枝的深度优先扫描；deadline 为 time.time() 截止时间"""
    if state is None:
        state = {"n": 0}
    if state["n"] > 6000 or time.time() > deadline:
        return None
    try:
        entries = list(os.scandir(root))
    except OSError:
        return None
    # 先看当前目录
    hit = _probe_dir(root)
    if hit:
        return hit
    for e in entries:
        if not e.is_dir(follow_symlinks=False):
            continue
        name = e.name
        if name.lower() in _SKIP_DIRS or name.startswith((".", "$")):
            continue
        depth_ok = state["n"] < 6000 and time.time() < deadline
        if not depth_ok:
            return None
        state["n"] += 1
        # 深度 0-1 全放行；更深只进关键词目录
        rel_depth = root.count(os.sep)
        if rel_depth >= 2 and not any(k in name.lower() for k in _KEYWORDS):
            continue
        got = _walk_find(e.path, deadline, max_depth, state)
        if got:
            return got
    return None


def _from_drives(timeout=15):
    deadline = time.time() + timeout
    for letter in "CDEF":
        root = f"{letter}:\\"
        if not os.path.isdir(root):
            continue
        hit = _walk_find(root, deadline)
        if hit:
            return hit
    return None


def find_adb(config=None):
    p, _ = find_adb_ex(config)
    return p


def find_adb_ex(config=None):
    """返回 (adb路径, 来源说明)。找不到返回 (None, '')"""
    # 1. 配置覆盖
    if config:
        p = (config.get("adb_path") or "").strip()
        if _valid(p):
            return p, "配置文件指定"
    env = os.environ.get("ADB_PATH", "")
    if _valid(env):
        return env, "环境变量 ADB_PATH"
    # 2. 静态表
    p = _from_static()
    if p:
        return p, "已知模拟器路径"
    # 3. PATH
    p = _from_where()
    if p:
        return p, "系统 PATH"
    # 4. 运行中的模拟器进程
    p = _from_process()
    if p:
        return p, "检测到模拟器进程"
    # 5. 全盘关键词扫描
    p = _from_drives()
    if p:
        return p, "全盘扫描"
    return None, ""


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    t0 = time.time()
    path, how = find_adb_ex()
    if path:
        print(f"找到 adb: {path}\n来源: {how}\n耗时: {time.time()-t0:.1f}s")
    else:
        print(f"未找到 adb（耗时 {time.time()-t0:.1f}s）")

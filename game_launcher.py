# -*- coding: utf-8 -*-
"""
game_launcher.py —— 游戏自动启动器（OCR 文字识别 + 模拟鼠标点击）
=================================================================
针对「网易发烧游戏」PC 平台（Qt 窗口）→ 星际猎人（无尽的拉格朗日）。

【核心原则】全程禁止代码启动（不调用 MuMuManager / adb am start / shell monkey），
一切启动动作均通过「OCR 定位 + 模拟真实鼠标点击/双击」完成，模拟真人操作。

流程（对应需求 8 步）：
  S0 平台窗口定位（未开时：桌面图标 OCR 定位 → 模拟双击启动平台）
  S1 ADB 连接有效性验证（设备在位时：设备端截图文件 → 拉回 → PNG 完整性校验）
  S2 OCR 定位并点击「我的游戏」标签 → 验证切换成功
  S3 截图验证游戏列表页正常渲染 + OCR 确认目标游戏（星际猎人）在列
  S4 精准定位游戏图标区域（避开文字）→ 模拟双击
  S5 等待 10~15 秒界面加载
  S6 再次 OCR 定位并点击「我的游戏」
  S7 点击「开始游戏」按钮
  S8 等待游戏启动 → ADB 设备端截图验证游戏主界面正常渲染（补做 S1）
  S9 启动游戏自动化值守脚本（LagrangeUpgrade，可用 --no-run-script 跳过）

用法：
  python game_launcher.py                 完整流程
  python game_launcher.py --dump          只输出窗口内所有 OCR 文字及坐标（调试）
  python game_launcher.py --shot          只保存一张平台窗口截图（调试）
  python game_launcher.py --click-text 我的游戏   只点某个文字（调试）
  python game_launcher.py --step 4        只执行第 4 步（调试）
  python game_launcher.py --dry-run       全流程演练：只识别定位，不真点击
  python game_launcher.py --no-run-script 启动游戏后不接值守脚本

依赖：rapidocr_onnxruntime、opencv-python、pillow、numpy（与主工程一致）
"""

import sys
import os
import re
import time
import random
import ctypes
import ctypes.wintypes as wt
import subprocess
import difflib

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np
from PIL import Image

import cv2

# ==================== 控制台配色（与主工程一致的三月七深色风） ====================
class C:
    R = "\033[0m"; B = "\033[1m"; D = "\033[2m"
    ACCENT = "\033[38;5;45m"; OK = "\033[38;5;82m"; WARN = "\033[38;5;220m"
    ERR = "\033[38;5;203m"; DIM = "\033[38;5;244m"; HL = "\033[38;5;231m"

if sys.platform == "win32":
    try:
        _k32 = ctypes.windll.kernel32
        _k32.SetConsoleMode(_k32.GetStdHandle(-11), 7)
    except Exception:
        pass


def log(msg, level="info"):
    ts = f"{C.DIM}[{time.strftime('%H:%M:%S')}]{C.R}"
    col = {"info": C.ACCENT, "ok": C.OK, "warn": C.WARN, "err": C.ERR, "step": C.HL}.get(level, "")
    tag = {"step": "▶ "}.get(level, "")
    print(f"{ts} {col}{tag}{msg}{C.R}")


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = app_dir()
# refs 是只读参考图：单 exe 模式下打包在 _MEIPASS 解包目录，源码模式在脚本目录
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    REF_DIR = os.path.join(sys._MEIPASS, "refs")
else:
    REF_DIR = os.path.join(BASE_DIR, "refs")
SHOT_DIR = os.path.join(BASE_DIR, "_launcher_shots")

# 平台窗口匹配关键词（标题或类名）
APP_TITLE_KEYS = ("网易发烧游戏", "发烧游戏", "MuMuFever", "EGFever")
APP_CLASS_HINTS = ("Qt5152QWindowIcon",)

# ADB
ADB_CANDIDATES = [
    r"D:\FeverApps\MuMuFever\nx_main\adb.exe",
    r"D:\FeverApps\MuMuFever\nx_device\12.0\shell\adb.exe",
]
ADB_SERIAL = "127.0.0.1:16384"

# 目标游戏关键词
GAME_NAME = "星际猎人"
GAME_NAME_ALT = "无尽的拉格朗日"


# ==================== DPI 感知 ====================
def _enable_dpi_aware():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# ==================== 窗口抓取 ====================
class WindowCapture:
    """定位并抓取指定窗口画面（BitBlt 屏幕区域法，带 PrintWindow 兜底）"""

    def __init__(self):
        _enable_dpi_aware()
        self.user32 = ctypes.windll.user32
        self.gdi32 = ctypes.windll.gdi32
        self.hwnd = None

    # ---- 窗口枚举 ----
    def find_window(self, fresh=True):
        """返回匹配的平台窗口句柄（可见+有尺寸+标题/类名匹配）"""
        EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        found = []

        def _cb(hwnd, _):
            if not self.user32.IsWindowVisible(hwnd):
                return True
            ln = self.user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(ln + 1)
            self.user32.GetWindowTextW(hwnd, buf, ln + 1)
            title = buf.value or ""
            cls = ctypes.create_unicode_buffer(256)
            self.user32.GetClassNameW(hwnd, cls, 256)
            r = wt.RECT()
            self.user32.GetWindowRect(hwnd, ctypes.byref(r))
            w, h = r.right - r.left, r.bottom - r.top
            if w < 400 or h < 300:          # 排除托盘气泡等小窗
                return True
            hit_title = any(k in title for k in APP_TITLE_KEYS)
            hit_cls = any(k == cls.value for k in APP_CLASS_HINTS) and any(
                k in title for k in ("游戏", "MuMu", "Fever", "猎人"))
            if hit_title or hit_cls:
                found.append((hwnd, title, w, h))
            return True

        self.user32.EnumWindows(EnumProc(_cb), 0)
        self.hwnd = found[0][0] if found else None
        return found

    def _force_foreground(self):
        """把平台窗口拉到前台（AttachThreadInput 兼容前台锁）"""
        if not self.hwnd:
            return
        self.user32.ShowWindow(self.hwnd, 9)      # SW_RESTORE
        fg = self.user32.GetForegroundWindow()
        t1 = self.user32.GetWindowThreadProcessId(fg, None)
        t2 = ctypes.windll.kernel32.GetCurrentThreadId()
        if t1 != t2:
            self.user32.AttachThreadInput(t1, t2, True)
        self.user32.SetForegroundWindow(self.hwnd)
        self.user32.BringWindowToTop(self.hwnd)
        if t1 != t2:
            self.user32.AttachThreadInput(t1, t2, False)
        time.sleep(0.45)

    def rect(self):
        r = wt.RECT()
        self.user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        return r.left, r.top, r.right, r.bottom

    def capture(self, to_front=False):
        """抓窗口画面。优先 PrintWindow(可穿透遮挡)，失败回退 BitBlt 屏幕区域
        返回 (PIL.Image, left, top)"""
        # 窗口可能被关闭/重开，自动重新定位
        if not self.hwnd or not self.user32.IsWindow(self.hwnd):
            found = self.find_window()
            if not found:
                raise RuntimeError("平台窗口不存在（可能已关闭）")
        L, T, R, B = self.rect()
        W, H = R - L, B - T

        class BMIH(ctypes.Structure):
            _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                        ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                        ("biSizeImage", ctypes.c_uint32), ("biXPPM", ctypes.c_int32),
                        ("biYPPM", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                        ("biClrImp", ctypes.c_uint32)]

        hdr = BMIH(ctypes.sizeof(BMIH), W, -H, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(W * H * 4)

        # --- 首选：PrintWindow 穿透遮挡 ---
        wdc = self.user32.GetWindowDC(self.hwnd)
        mdc = self.gdi32.CreateCompatibleDC(wdc)
        hbm = self.gdi32.CreateCompatibleBitmap(wdc, W, H)
        old = self.gdi32.SelectObject(mdc, hbm)
        # PW_RENDERFULLCONTENT = 0x2（Win8.1+，支持 Qt/DWM 硬件加速窗口）
        ok = self.user32.PrintWindow(self.hwnd, mdc, 2)
        img = None
        if ok:
            self.gdi32.GetDIBits(mdc, hbm, 0, H, buf, ctypes.byref(hdr), 0)
            img = Image.frombuffer("RGBA", (W, H), buf.raw, "raw", "BGRA", 0, 1).convert("RGB")
            arr = np.asarray(img.convert("L"))
            if float(arr.std()) < 1.0:      # 全黑说明该窗口 PrintWindow 失效
                img = None
        # --- 兜底：BitBlt 屏幕区域（需在前台） ---
        if img is None:
            if to_front:
                self._force_foreground()
            L, T, R, B = self.rect()
            W, H = R - L, B - T
            hdr = BMIH(ctypes.sizeof(BMIH), W, -H, 1, 32, 0, 0, 0, 0, 0, 0)
            buf = ctypes.create_string_buffer(W * H * 4)
            self.gdi32.SelectObject(mdc, old)
            self.gdi32.DeleteObject(hbm)
            self.gdi32.DeleteDC(mdc)
            self.user32.ReleaseDC(0, wdc)
            hdc = self.user32.GetDC(0)
            mdc = self.gdi32.CreateCompatibleDC(hdc)
            hbm = self.gdi32.CreateCompatibleBitmap(hdc, W, H)
            old = self.gdi32.SelectObject(mdc, hbm)
            self.gdi32.BitBlt(mdc, 0, 0, W, H, hdc, L, T, 0x00CC0020)
            self.gdi32.GetDIBits(mdc, hbm, 0, H, buf, ctypes.byref(hdr), 0)
            img = Image.frombuffer("RGBA", (W, H), buf.raw, "raw", "BGRA", 0, 1).convert("RGB")

        self.gdi32.SelectObject(mdc, old)
        self.gdi32.DeleteObject(hbm)
        self.gdi32.DeleteDC(mdc)
        self.user32.ReleaseDC(0, wdc)
        return img, L, T

    def save_shot(self, tag="shot"):
        img, L, T = self.capture()
        os.makedirs(SHOT_DIR, exist_ok=True)
        p = os.path.join(SHOT_DIR, f"{tag}_{time.strftime('%H%M%S')}.png")
        img.save(p)
        return p, img, (L, T)


# ==================== 模拟鼠标 ====================
class MouseSim:
    """真实鼠标事件模拟（SetCursorPos + mouse_event），坐标=屏幕物理像素"""

    MOVE_STEPS = 8          # 移动插值步数（拟人轨迹）

    def __init__(self):
        _enable_dpi_aware()
        self.user32 = ctypes.windll.user32

    def _move_to(self, x, y):
        p = wt.POINT()
        self.user32.GetCursorPos(ctypes.byref(p))
        cx, cy = p.x, p.y
        for i in range(1, self.MOVE_STEPS + 1):
            t = i / self.MOVE_STEPS
            nx = int(cx + (x - cx) * t)
            ny = int(cy + (y - cy) * t)
            self.user32.SetCursorPos(nx, ny)
            time.sleep(0.012)
        self.user32.SetCursorPos(int(x), int(y))
        time.sleep(0.06)

    def _press(self):
        self.user32.mouse_event(0x0002, 0, 0, 0, 0)   # LEFTDOWN
        time.sleep(0.045)
        self.user32.mouse_event(0x0004, 0, 0, 0, 0)   # LEFTUP
        time.sleep(0.05)

    def click(self, x, y):
        self._move_to(x, y)
        self._press()

    def double_click(self, x, y):
        self._move_to(x, y)
        self._press()
        time.sleep(0.10)          # 小于系统双击间隔(默认500ms)
        self._press()

    def move_away(self):
        """点击后把鼠标挪到角落，避免遮挡后续截图"""
        self.user32.SetCursorPos(5, 5)

    def show_desktop(self):
        """模拟 Win+D 显示桌面（仅最小化窗口，不启动任何程序）"""
        VK_LWIN = 0x5B; VK_D = 0x44
        self.user32.keybd_event(VK_LWIN, 0, 0, 0)
        time.sleep(0.08)
        self.user32.keybd_event(VK_D, 0, 0, 0)
        time.sleep(0.08)
        self.user32.keybd_event(VK_D, 0, 0x0002, 0)   # KEYUP
        time.sleep(0.08)
        self.user32.keybd_event(VK_LWIN, 0, 0x0002, 0)
        time.sleep(0.5)


# ==================== OCR 定位 ====================
class Ocr:
    """RapidOCR 单例封装：返回 [{text, score, box, cx, cy}]"""

    _eng = None

    @classmethod
    def engine(cls):
        if cls._eng is None:
            from rapidocr_onnxruntime import RapidOCR
            cls._eng = RapidOCR()
        return cls._eng

    @classmethod
    def read(cls, pil_img):
        arr = np.array(pil_img)
        res, _ = cls.engine()(arr)
        out = []
        if res:
            for box, text, score in res:
                xs = [p[0] for p in box]; ys = [p[1] for p in box]
                out.append({
                    "text": str(text).strip(),
                    "score": float(score),
                    "box": box,
                    "cx": sum(xs) / 4.0,
                    "cy": sum(ys) / 4.0,
                    "w": max(xs) - min(xs),
                    "h": max(ys) - min(ys),
                })
        return out

    @staticmethod
    def find(items, keyword, fuzzy=True, min_score=0.5):
        """按关键词找文本块。fuzzy 时做包含/相似度双重匹配，返回最佳一条"""
        best, best_r = None, 0.0
        for it in items:
            t = it["text"].replace(" ", "")
            k = keyword.replace(" ", "")
            if not k:
                continue
            if k in t:
                r = 1.0
            elif fuzzy:
                r = difflib.SequenceMatcher(None, k, t).ratio()
            else:
                r = 0.0
            if it["score"] < min_score:
                r *= 0.5
            if r > best_r and r >= 0.55:
                best, best_r = it, r
        return best

    @staticmethod
    def any_text(items, keywords, fuzzy=False):
        """画面中是否出现任一关键词（验证用）"""
        for it in items:
            t = it["text"].replace(" ", "")
            for k in keywords:
                kk = k.replace(" ", "")
                if kk in t:
                    return it
                if fuzzy and difflib.SequenceMatcher(None, kk, t).ratio() >= 0.75:
                    return it
        return None


# ==================== 模板匹配（图标定位） ====================
def template_find(screen_img, ref_path, scales=None, thresh=0.70):
    """多尺度模板匹配。返回 (cx, cy, score) 或 None。
    兼容中文路径（用 np.fromfile 读取）"""
    if not os.path.isfile(ref_path):
        return None
    if scales is None:
        scales = np.arange(0.18, 1.61, 0.06)
    scr = cv2.cvtColor(np.array(screen_img), cv2.COLOR_RGB2GRAY)
    # 中文路径兼容：不用 cv2.imread
    try:
        ref0 = cv2.imdecode(np.fromfile(ref_path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    except Exception:
        return None
    if ref0 is None:
        return None
    best = None
    for s in scales:
        rw, rh = max(8, int(ref0.shape[1] * s)), max(8, int(ref0.shape[0] * s))
        if rw >= scr.shape[1] or rh >= scr.shape[0]:
            continue
        tpl = cv2.resize(ref0, (rw, rh), interpolation=cv2.INTER_AREA)
        res = cv2.matchTemplate(scr, tpl, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        if best is None or mx > best[2]:
            best = (loc[0] + rw / 2, loc[1] + rh / 2, float(mx))
    if best and best[2] >= thresh:
        return best
    return None


def icon_above_label(label, img_h, factor=0.9):
    """安卓/桌面布局：图标在文字标签正上方。返回图标中心 (cx, cy)"""
    return label["cx"], max(6, label["cy"] - label["h"] * factor)


def icon_left_of_label(label, gap=0.9):
    """横排 chip：图标在文字左侧。返回图标中心 (cx, cy)"""
    return max(6, label["cx"] - label["w"] / 2 - label["h"] * gap), label["cy"]


# ==================== ADB 截图验证（需求第 1 步） ====================
class AdbShot:
    """ADB 连接有效性验证：设备端生成截图文件 → 拉回 → PNG 完整性校验"""

    def __init__(self):
        self.adb = self._find_adb()

    @staticmethod
    def _find_adb():
        for p in ADB_CANDIDATES:
            if os.path.isfile(p):
                return p
        from adb_locator import find_adb_ex
        try:
            return find_adb_ex({})[0]
        except Exception:
            return None

    def _run(self, *args, timeout=20):
        r = subprocess.run([self.adb, *args], capture_output=True, timeout=timeout,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        return (r.returncode,
                (r.stdout or b"").decode("utf-8", "replace"),
                (r.stderr or b"").decode("utf-8", "replace"))

    def device_online(self):
        """设备是否在线。先主动 connect（只增不删，已连接则返回 already），再枚举"""
        try:
            self._run("connect", ADB_SERIAL, timeout=12)
            code, out, _ = self._run("devices", timeout=10)
            return re.search(rf"{re.escape(ADB_SERIAL)}\s+device\b", out) is not None
        except Exception:
            return False

    def verify(self, tag="v"):
        """完整验证链，返回 (ok, 消息, 本地文件路径或None)。
        用 exec-out screencap 直接读 bytes（不能走 _run，它会 decode 成文本）"""
        if not self.adb:
            return False, "未找到 adb.exe", None
        os.makedirs(SHOT_DIR, exist_ok=True)
        local = os.path.join(SHOT_DIR, f"adb_verify_{tag}.png")
        try:
            # 直接 subprocess 读 bytes，避免 _run 的 utf-8 解码破坏 PNG 数据
            r = subprocess.run([self.adb, "-s", ADB_SERIAL, "exec-out", "screencap", "-p"],
                               capture_output=True, timeout=30,
                               creationflags=0x08000000 if os.name == "nt" else 0)
            if r.returncode != 0:
                err = (r.stderr or b"").decode("utf-8", "replace").strip()
                return False, f"设备端截图失败: {err or r.returncode}", None
            png = r.stdout
            if not png or len(png) < 100:
                return False, "截图为空（可能未渲染完成）", None
            if png[:4] != b"\x89PNG":
                return False, "PNG 头校验失败", None
            with open(local, "wb") as f:
                f.write(png)
            im = Image.open(local)
            im.load()
            if im.width <= 0 or im.height <= 0:
                return False, "图像尺寸异常", None
            g = np.asarray(im.convert("L"))
            if float(g.std()) < 3.0:
                return False, "画面疑似黑屏（未渲染）", local
            return True, f"截图 {im.width}x{im.height}，{len(png)} 字节，内容完整", local
        except subprocess.TimeoutExpired:
            return False, "ADB 命令超时（设备未就绪）", None
        except Exception as e:
            return False, f"ADB 异常: {e}", None

    def boot_completed(self):
        """Android 系统是否开机完成"""
        try:
            code, out, _ = self._run("-s", ADB_SERIAL, "shell",
                                     "getprop sys.boot_completed", timeout=10)
            return out.strip() == "1"
        except Exception:
            return False

    def wait_ready(self, timeout=300, on_msg=None):
        """轮询等待：设备上线 → 系统开机完成 → 截图验证"""
        t0 = time.time()
        boot_logged = False
        while time.time() - t0 < timeout:
            if self.device_online():
                if not self.boot_completed():
                    if on_msg and not boot_logged:
                        on_msg("设备已连接，Android 系统开机中…")
                        boot_logged = True
                    time.sleep(6)
                    continue
                ok, msg, path = self.verify("ready")
                if ok:
                    return True, msg, path
                if on_msg:
                    on_msg(f"已开机但验证未过：{msg}")
            time.sleep(6)
        return False, f"等待超时（{timeout}s）", None


# ==================== 步骤结果 ====================
class StepResult:
    def __init__(self, no, name, ok, detail="", evidence=None):
        self.no, self.name, self.ok = no, name, ok
        self.detail, self.evidence = detail, evidence   # evidence: 截图路径

    def line(self):
        icon = "✅" if self.ok else "❌"
        return f"  {icon} 第{self.no:>2}步  {self.name}    {self.detail}"


# ==================== 主流程 ====================
class GameLauncher:
    def __init__(self, dry_run=False, run_script=True):
        self.win = WindowCapture()
        self.mouse = MouseSim()
        self.adb = AdbShot()
        self.dry = dry_run
        self.run_script = run_script
        self.results = []

    # ---------- 基础动作 ----------
    def _click(self, x, y, win_xy, double=False, what=""):
        """窗口坐标 → 屏幕坐标 → 模拟点击（dry_run 只记录）"""
        L, T = win_xy
        sx, sy = int(L + x), int(T + y)
        if self.dry:
            log(f"[演练] 将{'双击' if double else '单击'} {what} @ 窗口({x:.0f},{y:.0f})", "warn")
            return
        # 点击前必须把窗口拉到前台，否则鼠标事件发到遮挡的窗口上
        self.win._force_foreground()
        time.sleep(0.5)
        if double:
            self.mouse.double_click(sx, sy)
        else:
            self.mouse.click(sx, sy)
        self.mouse.move_away()
        log(f"已{'双击' if double else '单击'} {what} @ 屏幕({sx},{sy})", "ok")

    def _capture(self, tag):
        p, img, xy = self.win.save_shot(tag)
        return img, xy, p

    def _record(self, no, name, ok, detail="", evidence=None):
        self.results.append(StepResult(no, name, ok, detail, evidence))
        log(f"第{no}步 {'通过' if ok else '失败'}：{detail}", "ok" if ok else "err")

    # ---------- S0 平台窗口 ----------
    def step0_locate_app(self):
        log("S0 定位「网易发烧游戏」平台窗口", "step")
        found = self.win.find_window()
        if found:
            hwnd, title, w, h = found[0]
            log(f"窗口已开启：{title}（{w}x{h}）", "ok")
            self._record(0, "平台窗口定位", True, f"{title} {w}x{h}")
            return True
        # 窗口不存在 → 桌面图标模拟双击启动（OCR + 鼠标，非代码启动）
        log("平台未开启，尝试桌面图标模拟双击…", "warn")
        # 先显示桌面（Win+D 仅最小化窗口，不启动程序），否则桌面图标被遮挡
        self.mouse.show_desktop()
        # 抓整个桌面（Progman/WorkerW 所在屏幕）
        u = self.win.user32
        SW, SH = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
        hdc = u.GetDC(0)
        g = self.win.gdi32
        mdc = g.CreateCompatibleDC(hdc)
        hbm = g.CreateCompatibleBitmap(hdc, SW, SH)
        g.SelectObject(mdc, hbm)
        g.BitBlt(mdc, 0, 0, SW, SH, hdc, 0, 0, 0x00CC0020)
        class BMIH(ctypes.Structure):
            _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                        ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                        ("biSizeImage", ctypes.c_uint32), ("a", ctypes.c_int32),
                        ("b", ctypes.c_int32), ("c", ctypes.c_uint32), ("d", ctypes.c_uint32)]
        hdr = BMIH(ctypes.sizeof(BMIH), SW, -SH, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(SW * SH * 4)
        g.GetDIBits(mdc, hbm, 0, SH, buf, ctypes.byref(hdr), 0)
        desk = Image.frombuffer("RGBA", (SW, SH), buf.raw, "raw", "BGRA", 0, 1).convert("RGB")
        g.DeleteObject(hbm); g.DeleteDC(mdc); u.ReleaseDC(0, hdc)

        # 优先模板匹配参考图标，其次 OCR 找「网易发烧游戏」文字标签
        hit = template_find(desk, os.path.join(REF_DIR, "ref_fever_icon.png"))
        if hit:
            cx, cy, score = hit
            log(f"桌面图标模板命中（score={score:.2f}）", "ok")
        else:
            items = Ocr.read(desk)
            label = Ocr.find(items, "网易发烧游戏")
            if not label:
                self._record(0, "平台窗口定位", False, "桌面未找到图标（请手动双击后重试）")
                return False
            cx, cy = icon_above_label(label, SH)
            log(f"桌面图标文字标签定位：({label['cx']:.0f},{label['cy']:.0f})", "info")
        if self.dry:
            log(f"[演练] 将双击桌面图标 @ ({cx:.0f},{cy:.0f})", "warn")
            self._record(0, "平台窗口定位", True, "演练：定位到桌面图标")
            return True
        self.mouse.double_click(int(cx), int(cy))
        self.mouse.move_away()
        # 等窗口出现
        for _ in range(20):
            time.sleep(3)
            if self.win.find_window():
                self._record(0, "平台窗口定位", True, "桌面图标双击 → 平台窗口已开启")
                return True
        self._record(0, "平台窗口定位", False, "双击桌面图标后窗口未出现")
        return False

    # ---------- S1 ADB 验证（设备在位时） ----------
    def step1_adb_verify(self):
        log("S1 ADB 连接有效性验证（设备端截图→拉回→完整性校验）", "step")
        if not self.adb.device_online():
            self._record(1, "ADB有效性验证", True, "设备暂未在位（游戏启动后自动补验）")
            return True
        ok, msg, path = self.adb.verify("s1")
        self._record(1, "ADB有效性验证", ok, msg, path if ok else None)
        return ok

    # ---------- S2 我的游戏 ----------
    def _find_and_click_text(self, keyword, win_xy, img, double=False, what=None,
                             must=True):
        """OCR 找文字并点击；返回 (成功, 命中项)"""
        items = Ocr.read(img)
        hit = Ocr.find(items, keyword)
        if not hit:
            if must:
                log(f"未识别到「{keyword}」", "err")
            return False, None
        what = what or f"「{keyword}」"
        self._click(hit["cx"], hit["cy"], win_xy, double=double, what=what)
        return True, hit

    def step2_open_mygames(self):
        log("S2 OCR 定位并点击「我的游戏」", "step")
        for attempt in (1, 2, 3):
            img, xy, p = self._capture(f"s2_try{attempt}")
            ok, hit = self._find_and_click_text("我的游戏", xy, img)
            if not ok:
                time.sleep(2)
                continue
            time.sleep(2.5)                       # 等页面切换
            img2, xy2, _ = self._capture(f"s2_after{attempt}")
            items = Ocr.read(img2)
            # 验证：不再停留在商店页 / 出现游戏相关文字
            switched = Ocr.any_text(items, [GAME_NAME, GAME_NAME_ALT, "开始游戏",
                                            "更新", "全部", "我的"]) is not None
            still_store = Ocr.find(items, "商店", fuzzy=False) is not None and \
                Ocr.find(items, "我的游戏", fuzzy=False) is None
            if switched and not still_store:
                self._record(2, "点击「我的游戏」", True,
                             "页面已切换（命中验证文字）", p)
                return True
            log(f"第{attempt}次点击后未见切换，重试…", "warn")
        self._record(2, "点击「我的游戏」", False, "多次尝试未切换到我的游戏页")
        return False

    # ---------- S3 页面渲染 + 目标确认 ----------
    def step3_verify_page(self):
        log("S3 验证游戏列表页渲染 + OCR 确认目标游戏", "step")
        img, xy, p = self._capture("s3")
        items = Ocr.read(img)
        if len(items) < 3:
            self._record(3, "页面渲染验证", False, "OCR 文字过少，页面可能未渲染", p)
            return False
        hit = Ocr.any_text(items, [GAME_NAME, GAME_NAME_ALT], fuzzy=True)
        if not hit:
            self._record(3, "目标游戏确认", False, "列表中未找到「星际猎人/无尽的拉格朗日」", p)
            return False
        self._record(3, "页面渲染+目标确认", True,
                     f"页面正常，命中「{hit['text']}」", p)
        return True

    # ---------- S4 双击游戏图标（避开文字） ----------
    def step4_double_click_icon(self):
        log("S4 定位游戏图标（避开文字）→ 模拟双击", "step")
        img, xy, p = self._capture("s4")
        items = Ocr.read(img)
        # 1) 找游戏名标签（取最像“游戏入口”的小字号命中，避免命中大标题/公告）
        cands = []
        for it in items:
            t = it["text"].replace(" ", "")
            if GAME_NAME in t or GAME_NAME_ALT in t:
                cands.append(it)
        if not cands:
            self._record(4, "游戏图标定位", False, "未找到游戏名标签", p)
            return False
        # 入口一般是 chip/卡片，字号较小；按高度从小到大排
        cands.sort(key=lambda d: d["h"])
        label = cands[0]
        # 2) 图标位置：chip 横排→文字左侧；卡片→文字上方。取两者中“更靠画面可用区”的
        left = icon_left_of_label(label)
        above = icon_above_label(label, img.height)
        # 用像素判定：左侧点若落在另一文本块内则放弃，选上方
        def in_any_text(pt):
            for it in items:
                if it is label:
                    continue
                b = it["box"]
                xs = [q[0] for q in b]; ys = [q[1] for q in b]
                if min(xs) - 4 <= pt[0] <= max(xs) + 4 and min(ys) - 4 <= pt[1] <= max(ys) + 4:
                    return True
            return False
        target, where = (left, "左侧") if not in_any_text(left) and left[0] > 4 else (above, "上方")
        if in_any_text(target):
            target = above if where == "左侧" else left
            where = "上方" if where == "左侧" else "左侧"
        log(f"游戏名标签「{label['text']}」({label['cx']:.0f},{label['cy']:.0f})，"
            f"图标取{where}({target[0]:.0f},{target[1]:.0f})", "info")
        self._click(target[0], target[1], xy, double=True, what=f"游戏图标（{where}，避开文字）")
        self._record(4, "双击游戏图标", True,
                     f"图标位于文字{where}，已避开文字区", p)
        return True

    # ---------- S5 等待 ----------
    def step5_wait_load(self):
        wait = random.randint(10, 15)
        log(f"S5 等待界面加载 {wait} 秒", "step")
        for i in range(wait, 0, -5):
            log(f"  …{i}s", "dim")
            time.sleep(min(5, i))
        self._record(5, "等待加载", True, f"{wait} 秒")
        return True

    # ---------- S6 再次点我的游戏 ----------
    def step6_mygames_again(self):
        log("S6 再次点击「我的游戏」", "step")
        img, xy, p = self._capture("s6")
        ok, hit = self._find_and_click_text("我的游戏", xy, img, must=False)
        if not ok:
            # 游戏页里若标签不可见，尝试模板找标签栏；仍找不到则视为已满足条件
            log("「我的游戏」标签当前不可见（可能已在该页），跳过点击", "warn")
            self._record(6, "再次点击「我的游戏」", True, "标签不可见（已在目标页），按规格跳过")
            return True
        self._record(6, "再次点击「我的游戏」", True, "已点击", p)
        return True

    # ---------- S7 开始游戏 ----------
    @staticmethod
    def _orange_button_center(img, min_area=6000):
        """橙色大按钮（#E8952F 系）连通区域定位：取最大连通块的几何中心。
        比 OCR 文本中心更稳（OCR 会把 ▷/√ 图标框进去导致中心偏移）。"""
        arr = np.array(img)
        mask = ((arr[:, :, 0] > 190) & (arr[:, :, 1] > 110) &
                (arr[:, :, 1] < 190) & (arr[:, :, 2] < 90)).astype(np.uint8)
        n, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
        best, best_area = None, 0
        for i in range(1, n):
            area = stats[i, cv2.CC_STAT_AREA]
            w = stats[i, cv2.CC_STAT_WIDTH]
            h = stats[i, cv2.CC_STAT_HEIGHT]
            # 按钮特征：大面积、横条（宽明显大于高）
            if area > best_area and area >= min_area and w > h * 1.5:
                best, best_area = centroids[i], area
        if best is None:
            return None
        return float(best[0]), float(best[1]), int(best_area)

    def step7_click_start(self):
        log("S7 点击「开始游戏」", "step")
        for attempt in (1, 2, 3):
            # 点击前强制把窗口拉到前台，避免被遮挡导致点击失效
            self.win._force_foreground()
            time.sleep(0.8)
            img, xy, p = self._capture(f"s7_try{attempt}")
            items = Ocr.read(img)
            ocr_hit = Ocr.find(items, "开始游戏") or Ocr.find(items, "开启游戏")
            # 主定位：橙色按钮色块中心；OCR 文本做存在性验证
            center = self._orange_button_center(img)
            if center:
                cx, cy, area = center
                if ocr_hit:
                    # OCR 与色块互验：文字中心应落在色块水平范围内（允许文字偏左）
                    dx = ocr_hit["cx"] - cx
                    dy = abs(ocr_hit["cy"] - cy)
                    if dy > 40 or dx < -160 or dx > 200:
                        log(f"色块({cx:.0f},{cy:.0f})与OCR文字({ocr_hit['cx']:.0f},"
                            f"{ocr_hit['cy']:.0f})偏差大，取色块中心", "warn")
                self._click(cx, cy, xy, what=f"「开始游戏」按钮（橙色色块中心，{area}px²）")
                self._record(7, "点击「开始游戏」", True,
                             f"色块定位({cx:.0f},{cy:.0f})" +
                             (f"，OCR确认「{ocr_hit['text']}」" if ocr_hit else ""), p)
                return True
            # 色块没找到 → 退到 OCR 文本中心，并向左修正（文字块常含左侧图标）
            if ocr_hit:
                cx = ocr_hit["cx"] - ocr_hit["w"] * 0.08
                self._click(cx, ocr_hit["cy"], xy, what=f"「{ocr_hit['text']}」（OCR定位）")
                self._record(7, "点击「开始游戏」", True, "OCR定位（左修正）", p)
                return True
            time.sleep(2)
        self._record(7, "点击「开始游戏」", False, "未找到开始游戏按钮（OCR与色块均未命中）", p)
        return False

    # ---------- S8 等游戏起来 + ADB 验证 ----------
    def step8_verify_game(self):
        log("S8 等待游戏启动 + ADB 截图验证游戏主界面", "step")
        if self.dry:
            self._record(8, "游戏启动验证", True, "演练：将轮询 ADB 就绪并做设备端截图完整性验证")
            return True
        log("轮询 ADB（VM 由平台拉起，预计 30~180 秒）…", "info")
        ok, msg, path = self.adb.wait_ready(timeout=300,
                                            on_msg=lambda m: log(f"  {m}", "dim"))
        if not ok:
            self._record(8, "游戏启动验证", False, msg)
            return False
        log(f"ADB 就绪：{msg}", "ok")
        # OCR 看游戏画面内容，输出识别文本供确认
        try:
            items = Ocr.read(Image.open(path))
            texts = [it["text"] for it in items][:12]
            log(f"游戏画面 OCR（前{len(texts)}条）：{' | '.join(texts) if texts else '（无可识别文字）'}", "info")
        except Exception as e:
            log(f"游戏画面 OCR 失败：{e}", "warn")
        self._record(8, "游戏启动验证", True, msg, path)
        return True

    # ---------- S9 接值守脚本 ----------
    def step9_run_script(self):
        if not self.run_script:
            self._record(9, "启动值守脚本", True, "已按参数跳过")
            return True
        log("S9 启动游戏自动化值守脚本（LagrangeUpgrade）", "step")
        try:
            if getattr(sys, "frozen", False):
                # 单 exe 模式：自调用 --core run，新控制台窗口
                cmd = [sys.executable, "--core", "run"]
                detail = "已在新窗口启动值守核心（单exe --core run）"
            else:
                src = os.path.join(BASE_DIR, "lagrange_upgrade.py")
                if not os.path.isfile(src):
                    self._record(9, "启动值守脚本", False, "找不到 lagrange_upgrade.py")
                    return False
                cmd = [sys.executable, src, "run"]
                detail = "已在新窗口启动 lagrange_upgrade.py run"
            subprocess.Popen(cmd, cwd=BASE_DIR,
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
            time.sleep(2)
            self._record(9, "启动值守脚本", True, detail)
            return True
        except Exception as e:
            self._record(9, "启动值守脚本", False, f"启动失败: {e}")
            return False

    # ---------- 总控 ----------
    def run(self, only_step=None):
        steps = [
            (0, "平台窗口定位", self.step0_locate_app),
            (1, "ADB有效性验证", self.step1_adb_verify),
            (2, "点击我的游戏", self.step2_open_mygames),
            (3, "页面渲染+目标确认", self.step3_verify_page),
            (4, "双击游戏图标", self.step4_double_click_icon),
            (5, "等待加载", self.step5_wait_load),
            (6, "再次点击我的游戏", self.step6_mygames_again),
            (7, "点击开始游戏", self.step7_click_start),
            (8, "游戏启动验证", self.step8_verify_game),
            (9, "启动值守脚本", self.step9_run_script),
        ]
        try:
            for no, name, fn in steps:
                if only_step is not None and no != only_step:
                    continue
                if not fn():
                    if only_step is None:
                        log("流程中断，输出已完成的步骤报告", "err")
                        break
        except KeyboardInterrupt:
            print()
            log("收到手动停止（Ctrl+C）", "warn")
        except Exception as e:
            log(f"流程异常：{e}", "err")
            import traceback
            traceback.print_exc()
        finally:
            self.report()

    def report(self):
        print()
        print(f"{C.ACCENT}{C.B}{'=' * 62}{C.R}")
        print(f"{C.ACCENT}{C.B}  启动流程执行报告{C.R}")
        print(f"{C.ACCENT}{C.B}{'=' * 62}{C.R}")
        for r in self.results:
            print(r.line())
        okn = sum(1 for r in self.results if r.ok)
        print(f"{C.ACCENT}{'-' * 62}{C.R}")
        print(f"  合计 {okn}/{len(self.results)} 步通过"
              + (f"{C.WARN}（演练模式，未真实点击）{C.R}" if self.dry else ""))
        print()


# ==================== 调试子命令 ====================
def dbg_dump():
    w = WindowCapture()
    if not w.find_window():
        print("平台窗口未找到"); return 1
    p, img, xy = w.save_shot("dump")
    items = Ocr.read(img)
    print(f"窗口截图: {p}  (窗口原点 {xy})")
    print(f"{'---':>2}  {'中心':<12} {'尺寸':<10} 文本 (置信度)")
    for it in items:
        print(f"{it['score']:.2f}  ({it['cx']:>5.0f},{it['cy']:>5.0f})  {it['w']:>4.0f}x{it['h']:<4.0f}  {it['text']}")
    return 0


def dbg_click_text(keyword, double=False):
    w = WindowCapture()
    if not w.find_window():
        print("平台窗口未找到"); return 1
    p, img, xy = w.save_shot("clickdbg")
    items = Ocr.read(img)
    hit = Ocr.find(items, keyword)
    if not hit:
        print(f"未找到「{keyword}」；当前文字：{' | '.join(i['text'] for i in items)}")
        return 1
    m = MouseSim()
    m.click(int(xy[0] + hit["cx"]), int(xy[1] + hit["cy"]))
    m.move_away()
    print(f"已点击「{hit['text']}」 @ ({hit['cx']:.0f},{hit['cy']:.0f})+窗口{xy}")
    return 0


# ==================== 入口 ====================
def main():
    import argparse
    ap = argparse.ArgumentParser(description="游戏自动启动器（OCR+模拟鼠标）")
    ap.add_argument("--dump", action="store_true", help="输出窗口OCR文字与坐标")
    ap.add_argument("--shot", action="store_true", help="只保存窗口截图")
    ap.add_argument("--click-text", metavar="文字", help="调试：点击窗口内指定文字")
    ap.add_argument("--step", type=int, metavar="N", help="只执行第 N 步")
    ap.add_argument("--dry-run", action="store_true", help="演练：只识别不点击")
    ap.add_argument("--no-run-script", action="store_true", help="启动游戏后不接值守脚本")
    args = ap.parse_args()

    if args.dump:
        return dbg_dump()
    if args.shot:
        w = WindowCapture()
        if not w.find_window():
            print("平台窗口未找到"); return 1
        p, img, xy = w.save_shot("manual")
        print(f"已保存: {p}")
        return 0
    if args.click_text:
        return dbg_click_text(args.click_text)

    print(f"{C.ACCENT}{C.B}── 游戏自动启动器（OCR+模拟鼠标，全程不代码启动）──{C.R}")
    gl = GameLauncher(dry_run=args.dry_run, run_script=not args.no_run_script)
    gl.run(only_step=args.step)
    return 0


if __name__ == "__main__":
    sys.exit(main())

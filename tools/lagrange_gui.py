# -*- coding: utf-8 -*-
"""
拉格朗日助手 GUI（Tkinter 轻量版，零第三方 GUI 依赖）
=====================================================
- 自足式 ADB：内置 AdbHelper，不依赖 lagrange_upgrade.py 源码，打包后独立可用
- adb.exe 智能定位：复用 adb_locator（静态路径→PATH→进程探测→全盘扫描）
- 截图/点击：直接 adb exec-out screencap / input tap，即时预览
- 校准/值守：调用同目录 LagrangeUpgrade.exe（OCR 核心子进程）
"""

import sys
import os
import re
import json
import time
import queue
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk

from adb_locator import find_adb_ex
from adb_diag import AdbDiagnostic

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = app_dir()
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SHOT_PATH = os.path.join(BASE_DIR, "shot.png")

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def cli_cmd(*args):
    """构造调用 OCR 核心的命令：
    单 exe 模式 → 自调用 [本exe, --core, ...]；源码模式 → python lagrange_upgrade.py"""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--core", *args]
    src = os.path.join(BASE_DIR, "lagrange_upgrade.py")
    if os.path.isfile(src):
        return [sys.executable, src, *args]
    return [sys.executable, *args]


# ==================== 自足式 ADB 助手 ====================
class AdbHelper:
    """轻量 ADB 封装：定位 adb / 扫描设备 / 截图 / 点击"""

    KNOWN_PORTS = (16384, 62001, 7555, 5555, 21503, 5556, 5557, 5559,
                   5558, 5560, 5561, 5562, 5563, 5564, 5565,
                   7401, 7402, 7403, 7404, 7405)

    def __init__(self, adb_path=None):
        self.path = adb_path or find_adb_ex(load_config())[0]
        self.device = None
        self.w = self.h = 0

    @property
    def ok(self):
        return bool(self.path) and os.path.isfile(self.path)

    def _run(self, *args, timeout=20):
        return subprocess.run([self.path, *args], capture_output=True,
                              timeout=timeout, creationflags=CREATE_NO_WINDOW)

    def _run_dev(self, *args, timeout=20):
        return subprocess.run([self.path, "-s", self.device, *args],
                              capture_output=True, timeout=timeout,
                              creationflags=CREATE_NO_WINDOW)

    def detect(self):
        """返回 (adb路径, 来源)"""
        return find_adb_ex(load_config())

    def list_devices(self):
        """[(id, status), ...]"""
        try:
            out = self._run("devices").stdout.decode("utf-8", "replace")
        except Exception:
            return []
        result = []
        for line in out.splitlines()[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and not line.startswith("*"):
                result.append((parts[0], parts[1]))
        return result

    def connect(self, addr):
        try:
            msg = self._run("connect", addr, timeout=12).stdout.decode("utf-8", "replace").strip()
            ok = ("connected" in msg.lower() or "already" in msg.lower())
            return ok, msg
        except Exception as e:
            return False, str(e)

    def scan_and_connect(self):
        for port in self.KNOWN_PORTS:
            try:
                self._run("connect", f"127.0.0.1:{port}", timeout=6)
            except Exception:
                pass
        return self.list_devices()

    def reset_server(self):
        try:
            self._run("kill-server", timeout=10)
            time.sleep(1)
            self._run("start-server", timeout=15)
            time.sleep(1)
        except Exception:
            pass

    def ensure_device(self, prefer=None):
        """确保有可用设备。返回 (成功, 消息)"""
        devs = [d for d, s in self.list_devices() if s == "device"]
        if not devs:
            self.scan_and_connect()
            devs = [d for d, s in self.list_devices() if s == "device"]
        if not devs:
            return False, "没有可用设备：请先启动模拟器，并在模拟器设置里确认 ADB 调试已开启"
        self.device = prefer if prefer in devs else devs[0]
        # 读分辨率（失败则 server 冲突，自愈一次）；wm size 可能报竖屏姿态，归一化为横屏
        try:
            out = self._run_dev("shell", "wm", "size").stdout.decode("utf-8", "replace")
            m = re.search(r"(\d+)x(\d+)", out)
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                self.w, self.h = max(a, b), min(a, b)
            else:
                raise ValueError(out.strip() or "empty")
        except Exception:
            self.reset_server()
            self.scan_and_connect()
            devs = [d for d, s in self.list_devices() if s == "device"]
            if not devs:
                return False, "adb server 冲突已尝试自愈，但仍无设备，请重启模拟器后重试"
            self.device = devs[0]
            try:
                out = self._run_dev("shell", "wm", "size").stdout.decode("utf-8", "replace")
                m = re.search(r"(\d+)x(\d+)", out)
                if m:
                    a, b = int(m.group(1)), int(m.group(2))
                    self.w, self.h = max(a, b), min(a, b)
            except Exception:
                pass
        return True, f"{self.device}  {self.w}x{self.h}"

    def screenshot_bytes(self, timeout=25):
        """exec-out screencap -p，返回 png 字节；以实际画面校正分辨率"""
        r = self._run_dev("exec-out", "screencap", "-p", timeout=timeout)
        if r.returncode != 0 or not r.stdout:
            raise RuntimeError(r.stderr.decode("utf-8", "replace").strip() or "截图为空")
        try:
            from PIL import Image as PILImage
            import io as _io
            im = PILImage.open(_io.BytesIO(r.stdout))
            self.w, self.h = max(im.width, im.height), min(im.width, im.height)
        except Exception:
            pass
        return r.stdout

    def tap(self, x, y):
        self._run_dev("shell", "input", "tap", str(x), str(y), timeout=15)


# ==================== 配色（三月七深色风格） ====================
COLORS = {
    "bg":     "#1e1e1e", "card": "#2a2a2a", "card2": "#333333",
    "accent": "#1fb6c9", "text": "#e8e8e8", "dim": "#9aa0a6",
    "ok":     "#5fff5f", "warn": "#ffd75f", "err": "#ff8787",
}


class GuiApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("拉格朗日助手")
        self.geometry("1100x760")
        self.configure(bg=COLORS["bg"])
        self.minsize(980, 680)

        self.adb = None
        self.worker = None
        self.worker_kind = None
        self.log_queue = queue.Queue()
        self._shot_img = None

        self._build_style()
        self._build_ui()
        self.after(100, self._drain_log)
        # 启动即自动检测 adb
        self.after(300, self._auto_detect)

    # ---------- 样式 ----------
    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("TLabelframe", background=COLORS["bg"], foreground=COLORS["text"],
                        bordercolor=COLORS["card2"])
        style.configure("TLabelframe.Label", background=COLORS["bg"],
                        foreground=COLORS["accent"], font=("Microsoft YaHei", 9, "bold"))
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"],
                        font=("Microsoft YaHei", 9))
        style.configure("Dim.TLabel", background=COLORS["bg"], foreground=COLORS["dim"],
                        font=("Microsoft YaHei", 8))
        style.configure("Accent.TLabel", background=COLORS["bg"], foreground=COLORS["accent"],
                        font=("Microsoft YaHei", 12, "bold"))
        style.configure("TButton", font=("Microsoft YaHei", 9), padding=(12, 6))
        style.configure("Accent.TButton", font=("Microsoft YaHei", 9, "bold"),
                        foreground="#10151a", background=COLORS["accent"], padding=(14, 6))
        style.map("Accent.TButton", background=[("active", "#28d0e3")])
        style.configure("TEntry", fieldbackground=COLORS["card2"], foreground=COLORS["text"],
                        insertcolor=COLORS["text"])
        style.configure("TCombobox", fieldbackground=COLORS["card2"], foreground=COLORS["text"])
        style.configure("TCheckbutton", background=COLORS["bg"], foreground=COLORS["text"])
        style.map("TCheckbutton", background=[("active", COLORS["bg"])])

    # ---------- 界面 ----------
    def _build_ui(self):
        root = ttk.Frame(self, padding=16)
        root.pack(fill=tk.BOTH, expand=True)

        head = ttk.Frame(root)
        head.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(head, text="拉格朗日助手", style="Accent.TLabel").pack(side=tk.LEFT)
        ttk.Label(head, text="无尽的拉格朗日 · 建筑自动升级值守",
                  style="Dim.TLabel").pack(side=tk.LEFT, padx=(12, 0))
        self.adb_src = ttk.Label(head, text="", style="Dim.TLabel")
        self.adb_src.pack(side=tk.RIGHT)

        # 显眼的 ADB 一键诊断 + 一键启动游戏 入口
        diag_bar = tk.Frame(root, bg="#26323a", highlightthickness=1,
                            highlightbackground=COLORS["accent"])
        diag_bar.pack(fill=tk.X, pady=(0, 12))
        inner = tk.Frame(diag_bar, bg="#26323a")
        inner.pack(fill=tk.X, padx=14, pady=10)
        tk.Label(inner, text="连接有问题？先一键诊断；要玩游戏？一键启动（OCR+模拟鼠标，不代码启动）",
                 bg="#26323a", fg="#cfeef4", font=("Microsoft YaHei", 9)).pack(side=tk.LEFT)
        launch_btn = tk.Button(inner, text="🚀  一键启动游戏", bg="#ff9f43", fg="#1a1205",
                               activebackground="#ffb976", activeforeground="#1a1205",
                               font=("Microsoft YaHei", 11, "bold"), relief=tk.FLAT,
                               padx=22, pady=6, cursor="hand2", command=self._launch_game)
        launch_btn.pack(side=tk.RIGHT, padx=(0, 10))
        diag_btn = tk.Button(inner, text="🔌  ADB 连接测试", bg=COLORS["accent"], fg="#10151a",
                             activebackground="#28d0e3", activeforeground="#10151a",
                             font=("Microsoft YaHei", 11, "bold"), relief=tk.FLAT,
                             padx=22, pady=6, cursor="hand2", command=self._open_diag)
        diag_btn.pack(side=tk.RIGHT)

        body = ttk.Frame(root)
        body.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(body)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        right = ttk.Frame(body, width=340)
        right.pack(side=tk.RIGHT, fill=tk.BOTH)

        self._build_conn(left)
        self._build_tools(left)
        self._build_run(left)
        self._build_log(left)
        self._build_settings(right)

    def _section(self, parent, title):
        lf = ttk.LabelFrame(parent, text=title, padding=10)
        lf.pack(fill=tk.X, pady=(0, 10))
        return lf

    # —— 连接区 ——
    def _build_conn(self, parent):
        sec = self._section(parent, "① ADB 连接")
        r1 = ttk.Frame(sec); r1.pack(fill=tk.X, pady=2)
        ttk.Label(r1, text="adb 路径").pack(side=tk.LEFT)
        self.adb_path_var = tk.StringVar(value=load_config().get("adb_path", ""))
        ttk.Entry(r1, textvariable=self.adb_path_var).pack(side=tk.LEFT, fill=tk.X,
                                                           expand=True, padx=(8, 6))
        ttk.Button(r1, text="浏览", command=self._pick_adb).pack(side=tk.LEFT)
        ttk.Button(r1, text="自动检测", command=self._auto_detect).pack(side=tk.LEFT, padx=(4, 0))

        r2 = ttk.Frame(sec); r2.pack(fill=tk.X, pady=2)
        ttk.Button(r2, text="扫描设备", style="Accent.TButton",
                   command=self._scan_devices).pack(side=tk.LEFT)
        self.dev_var = tk.StringVar()
        self.dev_combo = ttk.Combobox(r2, textvariable=self.dev_var, state="readonly", width=24)
        self.dev_combo.pack(side=tk.LEFT, padx=(8, 6))
        self.dev_combo.bind("<<ComboboxSelected>>", lambda e: self._select_device())
        ttk.Button(r2, text="刷新", command=self._refresh_devices).pack(side=tk.LEFT)
        ttk.Button(r2, text="重启ADB服务", command=self._reset_adb_server).pack(side=tk.LEFT, padx=(6, 0))

        r3 = ttk.Frame(sec); r3.pack(fill=tk.X, pady=2)
        ttk.Label(r3, text="手动连接").pack(side=tk.LEFT)
        self.conn_var = tk.StringVar(value="127.0.0.1:")
        ttk.Entry(r3, textvariable=self.conn_var, width=18).pack(side=tk.LEFT, padx=(8, 6))
        ttk.Button(r3, text="连接", command=self._manual_connect).pack(side=tk.LEFT)
        self.conn_status = ttk.Label(r3, text="未连接", style="Dim.TLabel")
        self.conn_status.pack(side=tk.LEFT, padx=(12, 0))

    # —— 工具区 ——
    def _build_tools(self, parent):
        sec = self._section(parent, "② 工具")
        row = ttk.Frame(sec); row.pack(fill=tk.X, pady=2)
        ttk.Button(row, text="OCR校准 (scan)", command=lambda: self._run_tool("scan")).pack(side=tk.LEFT)
        ttk.Button(row, text="截图预览", command=self._shot_preview).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(row, text="点击 X").pack(side=tk.LEFT, padx=(12, 4))
        self.tap_x = tk.StringVar(value="0")
        self.tap_y = tk.StringVar(value="0")
        ttk.Entry(row, textvariable=self.tap_x, width=6).pack(side=tk.LEFT)
        ttk.Label(row, text="Y").pack(side=tk.LEFT, padx=(4, 4))
        ttk.Entry(row, textvariable=self.tap_y, width=6).pack(side=tk.LEFT)
        ttk.Button(row, text="点击", command=self._tap_test).pack(side=tk.LEFT, padx=(6, 0))

        pv = ttk.Frame(sec); pv.pack(fill=tk.X, pady=(8, 0))
        self.preview = tk.Label(pv, text="「截图预览」可直接看模拟器画面",
                                bg=COLORS["card2"], fg=COLORS["dim"],
                                height=9, font=("Microsoft YaHei", 9))
        self.preview.pack(fill=tk.X)

    # —— 运行区 ——
    def _build_run(self, parent):
        sec = self._section(parent, "③ 值守")
        g = ttk.Frame(sec); g.pack(fill=tk.X)
        self.lbl_dev = self._stat(g, "设备", "未连接", 0, 0)
        self.lbl_res = self._stat(g, "分辨率", "—", 0, 1)
        self.lbl_status = self._stat(g, "状态", "空闲", 1, 0)
        self.lbl_clicks = self._stat(g, "今日点击", "0", 1, 1)
        self.lbl_event = self._stat(g, "最近事件", "—", 2, 0, span=2)

        row = ttk.Frame(sec); row.pack(fill=tk.X, pady=(8, 0))
        self.start_btn = ttk.Button(row, text="开始值守", style="Accent.TButton",
                                    command=self._start_run)
        self.start_btn.pack(side=tk.LEFT)
        self.stop_btn = ttk.Button(row, text="停止值守", command=self._stop_run)
        self.stop_btn.pack(side=tk.LEFT, padx=(8, 0))
        self.stop_btn.config(state=tk.DISABLED)
        ttk.Label(row, text="先把游戏停在【建筑详情面板】再点开始",
                  style="Dim.TLabel").pack(side=tk.LEFT, padx=(12, 0))

    def _stat(self, parent, name, val, r, c, span=1):
        ttk.Label(parent, text=name, style="Dim.TLabel").grid(row=r*2, column=c, sticky="w",
                                                              padx=(0, 24))
        lbl = ttk.Label(parent, text=val)
        lbl.grid(row=r*2+1, column=c, columnspan=span, sticky="w", padx=(0, 24), pady=(0, 6))
        return lbl

    # —— 日志区 ——
    def _build_log(self, parent):
        sec = self._section(parent, "④ 日志")
        bar = ttk.Frame(sec); bar.pack(fill=tk.X)
        ttk.Button(bar, text="清空", command=lambda: self.log_text.delete("1.0", tk.END)).pack(side=tk.RIGHT)
        self.log_text = tk.Text(sec, height=10, bg=COLORS["card"], fg=COLORS["text"],
                                insertbackground=COLORS["text"], font=("Consolas", 9),
                                relief=tk.FLAT, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self.log_text.tag_configure("ok", foreground=COLORS["ok"])
        self.log_text.tag_configure("warn", foreground=COLORS["warn"])
        self.log_text.tag_configure("err", foreground=COLORS["err"])
        self.log_text.tag_configure("dim", foreground=COLORS["dim"])

    # —— 设置区 ——
    def _build_settings(self, parent):
        cfg = load_config()
        qo = cfg.get("qq_official", {})
        self.qq_listener = None

        nb = ttk.Notebook(parent)
        nb.pack(fill=tk.BOTH, expand=True)
        tab_qq = ttk.Frame(nb, padding=10)
        tab_mail = ttk.Frame(nb, padding=10)
        nb.add(tab_qq, text=" QQ机器人 ")
        nb.add(tab_mail, text=" 邮箱·其他 ")

        # ========== 标签页 1：QQ 官方机器人（三月七同款） ==========
        self.qo_en = tk.BooleanVar(value=qo.get("enabled", False))
        head = ttk.Frame(tab_qq); head.pack(fill=tk.X)
        ttk.Checkbutton(head, text="启用 QQ 官方机器人通知",
                        variable=self.qo_en).pack(side=tk.LEFT)
        ttk.Button(head, text="教程", width=5,
                   command=lambda: os.startfile("https://q.qq.com/")).pack(side=tk.RIGHT)
        ttk.Label(tab_qq, text="QQ开放平台官方机器人推送（支持图片）",
                  style="Dim.TLabel").pack(anchor="w", pady=(0, 6))

        ttk.Label(tab_qq, text="机器人 AppID", style="Dim.TLabel").pack(anchor="w")
        self.qo_appid = tk.StringVar(value=qo.get("app_id", ""))
        ttk.Entry(tab_qq, textvariable=self.qo_appid).pack(fill=tk.X)

        ttk.Label(tab_qq, text="机器人 AppSecret", style="Dim.TLabel").pack(anchor="w", pady=(6, 0))
        self.qo_secret = tk.StringVar(value=qo.get("client_secret", ""))
        ttk.Entry(tab_qq, textvariable=self.qo_secret, show="•").pack(fill=tk.X)

        tgt = ttk.Frame(tab_qq); tgt.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(tgt, text="推送目标：", style="Dim.TLabel").pack(side=tk.LEFT)
        self.qo_target = tk.StringVar(value=qo.get("target_type", "group"))
        ttk.Radiobutton(tgt, text="群聊", value="group",
                        variable=self.qo_target).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Radiobutton(tgt, text="单聊", value="c2c",
                        variable=self.qo_target).pack(side=tk.LEFT)

        ttk.Label(tab_qq, text="单聊用户 openid", style="Dim.TLabel").pack(anchor="w", pady=(8, 0))
        self.qo_uopenid = tk.StringVar(value=qo.get("user_openid", ""))
        ttk.Entry(tab_qq, textvariable=self.qo_uopenid).pack(fill=tk.X)

        ttk.Label(tab_qq, text="群聊 group_openid", style="Dim.TLabel").pack(anchor="w", pady=(6, 0))
        self.qo_gopenid = tk.StringVar(value=qo.get("group_openid", ""))
        ttk.Entry(tab_qq, textvariable=self.qo_gopenid).pack(fill=tk.X)

        bind = ttk.Frame(tab_qq); bind.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(bind, text="自动绑定收件标识", style="Dim.TLabel").pack(side=tk.LEFT)
        self.bind_c2c_btn = ttk.Button(bind, text="绑定单聊",
                                       command=lambda: self._start_bind("c2c"))
        self.bind_c2c_btn.pack(side=tk.RIGHT)
        self.bind_grp_btn = ttk.Button(bind, text="绑定群聊",
                                       command=lambda: self._start_bind("group"))
        self.bind_grp_btn.pack(side=tk.RIGHT, padx=(0, 6))
        self.qo_bind_status = tk.StringVar(value="未启动绑定监听")
        ttk.Label(tab_qq, textvariable=self.qo_bind_status, style="Dim.TLabel",
                  foreground=COLORS["dim"]).pack(anchor="w", pady=(6, 0))

        ttk.Button(tab_qq, text="发送测试消息", command=self._send_qq_test).pack(anchor="w", pady=(8, 0))

        # ========== 标签页 2：邮箱 / 其他 ==========
        self.popup_en = tk.BooleanVar(value=cfg.get("popup", {}).get("enabled", True))
        self.email_en = tk.BooleanVar(value=cfg.get("email", {}).get("enabled", False))
        ttk.Checkbutton(tab_mail, text="自动关闭礼包弹窗",
                        variable=self.popup_en).pack(anchor="w", pady=2)
        ttk.Checkbutton(tab_mail, text="邮箱推送", variable=self.email_en).pack(anchor="w", pady=2)

        ttk.Label(tab_mail, text="邮箱 SMTP 服务器", style="Dim.TLabel").pack(anchor="w", pady=(8, 0))
        self.mail_host = tk.StringVar(value=cfg.get("email", {}).get("host", "smtp.qq.com"))
        ttk.Entry(tab_mail, textvariable=self.mail_host).pack(fill=tk.X)

        ttk.Label(tab_mail, text="发件邮箱", style="Dim.TLabel").pack(anchor="w", pady=(6, 0))
        self.mail_user = tk.StringVar(value=cfg.get("email", {}).get("user", ""))
        ttk.Entry(tab_mail, textvariable=self.mail_user).pack(fill=tk.X)

        ttk.Label(tab_mail, text="授权码", style="Dim.TLabel").pack(anchor="w", pady=(6, 0))
        self.mail_pwd = tk.StringVar(value=cfg.get("email", {}).get("password", ""))
        ttk.Entry(tab_mail, textvariable=self.mail_pwd, show="•").pack(fill=tk.X)

        ttk.Label(tab_mail, text="收件邮箱", style="Dim.TLabel").pack(anchor="w", pady=(6, 0))
        self.mail_to = tk.StringVar(value=cfg.get("email", {}).get("to_addr", ""))
        ttk.Entry(tab_mail, textvariable=self.mail_to).pack(fill=tk.X)

        ttk.Separator(tab_mail).pack(fill=tk.X, pady=10)
        ttk.Label(tab_mail, text="使用 NapCat / OneBot v11（进阶）：\n手动编辑 config.json 的 qq 段",
                  style="Dim.TLabel", justify=tk.LEFT).pack(anchor="w")

        row = ttk.Frame(parent); row.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(row, text="保存设置", style="Accent.TButton",
                   command=self._save_settings).pack(side=tk.LEFT)
        ttk.Button(row, text="config.json",
                   command=lambda: os.startfile(CONFIG_PATH)).pack(side=tk.LEFT, padx=(6, 0))

        tip = ttk.Frame(parent, padding=(2, 6, 0, 0))
        tip.pack(fill=tk.X)
        ttk.Label(tip, text="值守：启动模拟器→扫描设备→截图预览→OCR校准→开始",
                  style="Dim.TLabel", justify=tk.LEFT, wraplength=320).pack(anchor="w")

    # ---------- QQ 官方机器人：绑定 / 测试 ----------
    def _start_bind(self, target):
        if not self.qo_appid.get().strip() or not self.qo_secret.get().strip():
            messagebox.showwarning("缺少凭证", "请先填写并保存机器人 AppID 和 AppSecret")
            return
        self._save_settings(silent=True)
        if self.qq_listener and self.qq_listener.is_alive():
            messagebox.showinfo("监听运行中", "绑定监听已在运行，看状态提示即可")
            return
        from qq_official import QQOfficialBot, QQBotListener
        bot = QQOfficialBot(load_config().get("qq_official", {}))
        self.qo_target.set(target)

        def on_event(kind, data):
            if kind == "status":
                self.after(0, lambda: self.qo_bind_status.set(data.get("text", "")))
            elif kind == "bind":
                k = data.get("kind")
                self.after(0, lambda: self._on_bound(k))

        self.qq_listener = QQBotListener(bot, on_event=on_event,
                                         log=lambda m: self._log(m, "dim"))
        self.qq_listener.start()
        if target == "c2c":
            self.qo_bind_status.set("监听中：请用 QQ 给机器人发一条任意消息")
        else:
            self.qo_bind_status.set("监听中：请在目标 QQ 群里 @机器人 发一条消息")

    def _on_bound(self, kind):
        cfg = load_config().get("qq_official", {})
        if kind == "c2c":
            self.qo_uopenid.set(cfg.get("user_openid", ""))
            self.qo_bind_status.set("✅ 单聊已绑定（openid 已保存，7天内可主动推送）")
        else:
            self.qo_gopenid.set(cfg.get("group_openid", ""))
            self.qo_bind_status.set("✅ 群聊已绑定（group_openid 已保存，7天内可主动推送）")

    def _send_qq_test(self):
        self._save_settings(silent=True)
        from qq_official import QQOfficialBot
        bot = QQOfficialBot(load_config().get("qq_official", {}))
        if not bot.enabled:
            messagebox.showwarning("缺少凭证", "请先填写 AppID 和 AppSecret")
            return
        self._log("正在发送 QQ 官方机器人测试消息…")

        def work():
            ok, msg = bot.send("【拉格朗日助手】测试消息：通知通道正常 ✅")
            self.after(0, lambda: self._log(f"QQ官方：{msg}", "ok" if ok else "err"))
        threading.Thread(target=work, daemon=True).start()


    # ==================== ADB ====================
    def _set_adb(self, path, how=""):
        self.adb = AdbHelper(path)
        if self.adb.ok:
            self.adb_path_var.set(self.adb.path)
            self.adb_src.config(text=f"adb 来源：{how}" if how else "")
            self._log(f"adb.exe：{self.adb.path}", "ok")
            if how:
                self._log(f"来源：{how}", "dim")
        else:
            self._log("找不到 adb.exe！点「浏览」手动选，或启动模拟器后点「自动检测」", "err")

    def _auto_detect(self):
        self._log("正在自动检测 adb.exe（静态路径 → PATH → 进程探测 → 全盘扫描）...")
        self.update()
        t0 = time.time()
        path, how = find_adb_ex(load_config())
        if path:
            self._set_adb(path, how)
            self._log(f"检测完成（{time.time()-t0:.1f}s）", "dim")
            self._refresh_devices()
        else:
            self._set_adb(None)
            self._log(f"检测失败（{time.time()-t0:.1f}s）：全盘未发现 adb.exe", "err")

    def _pick_adb(self):
        p = filedialog.askopenfilename(title="选择 adb.exe",
                                       filetypes=[("adb.exe", "adb.exe"), ("可执行文件", "*.exe")])
        if p:
            self.adb_path_var.set(p)
            self._set_adb(p, "手动选择")
            cfg = load_config(); cfg["adb_path"] = p; save_config(cfg)

    def _get_adb(self):
        if self.adb and self.adb.ok:
            return self.adb
        self._auto_detect()
        return self.adb if (self.adb and self.adb.ok) else None

    def _scan_devices(self):
        adb = self._get_adb()
        if not adb:
            return
        self._log("正在扫描所有模拟器端口...")
        self.conn_status.config(text="扫描中...", foreground=COLORS["warn"])
        self.update()
        try:
            devices = adb.scan_and_connect()
        except Exception as e:
            self._log(f"扫描失败：{e}", "err")
            self.conn_status.config(text="扫描失败", foreground=COLORS["err"])
            return
        self._fill_devices(devices)

    def _refresh_devices(self):
        adb = self._get_adb()
        if not adb:
            return
        self._fill_devices(adb.list_devices(), quiet=True)

    def _fill_devices(self, devices, quiet=False):
        if not devices:
            if not quiet:
                self._log("没找到任何设备。请确认：1)模拟器已启动 2)模拟器设置里 ADB 调试已打开", "warn")
            self.conn_status.config(text="未找到设备", foreground=COLORS["err"])
            self.dev_combo["values"] = []
            return
        names = [f"{d}  [{s}]" for d, s in devices]
        self.dev_combo["values"] = names
        cur = self.dev_var.get()
        if cur not in names:
            self.dev_combo.current(0)
            self._select_device()
        elif not quiet:
            self._log(f"共 {len(devices)} 个设备", "ok")

    def _select_device(self):
        sel = self.dev_var.get()
        if not sel:
            return
        dev = sel.split()[0]
        adb = self._get_adb()
        if not adb:
            return
        adb.device = dev
        ok, msg = adb.ensure_device(prefer=dev)
        if ok:
            self.conn_status.config(text=f"已连接 {adb.device}", foreground=COLORS["ok"])
            self.lbl_dev.config(text=adb.device)
            self.lbl_res.config(text=f"{adb.w}x{adb.h}" if adb.w else "—")
            self._log(f"已选设备 {msg}", "ok")
        else:
            self.conn_status.config(text="连接失败", foreground=COLORS["err"])
            self._log(msg, "err")

    def _manual_connect(self):
        addr = self.conn_var.get().strip()
        if not addr:
            return
        adb = self._get_adb()
        if not adb:
            return
        ok, msg = adb.connect(addr)
        self._log(f"连接 {addr}：{msg}", "ok" if ok else "err")
        if ok:
            self._refresh_devices()

    def _reset_adb_server(self):
        adb = self._get_adb()
        if not adb:
            return
        self._log("正在重启 adb server（解决多版本冲突）...")
        self.update()
        adb.reset_server()
        self._log("adb server 已重启", "ok")
        self._refresh_devices()

    # ==================== ADB 连接测试（独立诊断窗口） ====================
    def _open_diag(self):
        DiagWindow(self)

    # ==================== 一键启动游戏（OCR+模拟鼠标，不代码启动） ====================
    def _launch_game(self):
        """在新窗口后台跑 game_launcher 完整流程，实时输出到文本框"""
        # 单 exe 模式 → 自调用 [本exe, --launcher, ...]；源码模式 → python game_launcher.py
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--launcher"]
        else:
            gl_src = os.path.join(BASE_DIR, "game_launcher.py")
            if os.path.isfile(gl_src):
                cmd = [sys.executable, gl_src]
            else:
                messagebox.showerror("缺少模块", "找不到 game_launcher.py")
                return
        # 可选：启动成功后自动接值守脚本
        run_after = messagebox.askyesno(
            "启动游戏", "启动游戏后，是否自动接着运行「建筑升级值守」脚本？\n\n"
                        "（选「是」：游戏启动后自动开始值守；选「否」：只启动游戏）")
        if not run_after:
            cmd.append("--no-run-script")
        LaunchGameWindow(self, cmd)

    def adopt_connection(self, adb_path, serial):
        """诊断窗口一键采用：写配置 → 刷新主界面设备选择"""
        cfg = load_config()
        cfg["adb_path"] = adb_path
        save_config(cfg)
        self.adb_path_var.set(adb_path)
        self.adb = AdbHelper(adb_path)
        try:
            ok, msg = self.adb.ensure_device(prefer=serial)
            if ok:
                self.adb.device = serial
                self._fill_devices(self.adb.list_devices(), quiet=True)
                self.dev_var.set(next((f"{d}  [{s}]" for d, s in self.adb.list_devices()
                                       if d == serial), self.dev_var.get()))
                self.conn_status.config(text=f"已连接 {serial}", foreground=COLORS["ok"])
                self.lbl_dev.config(text=serial)
                self.lbl_res.config(text=f"{self.adb.w}x{self.adb.h}" if self.adb.w else "—")
                self._log(f"已采用诊断结果：{serial}", "ok")
        except Exception as e:
            self._log(f"采用连接失败：{e}", "err")

    # ==================== 工具 ====================
    def _need_device(self):
        adb = self._get_adb()
        if not adb:
            messagebox.showwarning("找不到 adb", "未检测到 adb.exe，请先「自动检测」或「浏览」选择")
            return None
        if not adb.device:
            ok, msg = adb.ensure_device()
            if not ok:
                messagebox.showwarning("未连接", msg)
                return None
            self._select_device()
        return adb

    def _run_tool(self, cmd):
        if self.worker:
            messagebox.showwarning("请稍候", "有任务正在运行中")
            return
        if not self._need_device():
            return
        self._start_worker(cli_cmd(cmd), "tool")

    def _shot_preview(self):
        adb = self._need_device()
        if not adb:
            return
        self._log("正在截图...")
        self.update()
        try:
            data = adb.screenshot_bytes()
            with open(SHOT_PATH, "wb") as f:
                f.write(data)
            img = Image.open(SHOT_PATH)
            img.thumbnail((660, 340))
            self._shot_img = ImageTk.PhotoImage(img)
            self.preview.config(image=self._shot_img, text="")
            self._log(f"截图成功：{img.width}x{img.height}（原始 {adb.w}x{adb.h}）", "ok")
        except Exception as e:
            self._log(f"截图失败：{e}", "err")

    def _tap_test(self):
        try:
            x = int(self.tap_x.get()); y = int(self.tap_y.get())
        except ValueError:
            messagebox.showwarning("无效坐标", "请输入数字")
            return
        adb = self._need_device()
        if not adb:
            return
        try:
            adb.tap(x, y)
            self._log(f"已点击 ({x},{y})", "ok")
        except Exception as e:
            self._log(f"点击失败：{e}", "err")

    # ==================== 运行 ====================
    def _start_run(self):
        if not self._need_device():
            return
        if self.worker:
            return
        self.lbl_status.config(text="启动中…")
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self._start_worker(cli_cmd("run"), "run")

    def _stop_run(self):
        if self.worker:
            self.worker.stop()
            self._log("[GUI] 已发送停止指令", "dim")

    # ==================== 设置 ====================
    def _save_settings(self, silent=False):
        cfg = load_config()
        cfg["adb_path"] = self.adb_path_var.get().strip()
        cfg.setdefault("popup", {})["enabled"] = self.popup_en.get()

        qo = cfg.setdefault("qq_official", {})
        qo["enabled"] = self.qo_en.get()
        qo["app_id"] = self.qo_appid.get().strip()
        qo["client_secret"] = self.qo_secret.get().strip()
        qo["target_type"] = self.qo_target.get()
        qo["user_openid"] = self.qo_uopenid.get().strip()
        qo["group_openid"] = self.qo_gopenid.get().strip()

        em = cfg.setdefault("email", {})
        em["enabled"] = self.email_en.get()
        em["host"] = self.mail_host.get().strip()
        em["user"] = self.mail_user.get().strip()
        em["password"] = self.mail_pwd.get()
        em["to_addr"] = self.mail_to.get().strip()
        try:
            save_config(cfg)
            if not silent:
                messagebox.showinfo("已保存", "设置已写入 config.json")
        except Exception as e:
            if not silent:
                messagebox.showerror("保存失败", str(e))
            else:
                self._log(f"设置保存失败：{e}", "err")

    # ==================== 子进程 ====================
    def _start_worker(self, args, kind):
        self.worker = _Worker(args, BASE_DIR, self.log_queue)
        self.worker_kind = kind
        self.worker.start()
        self._log(f"[GUI] 开始执行 {'值守' if kind == 'run' else '工具'}", "dim")

    def _drain_log(self):
        try:
            while True:
                tag, text = self.log_queue.get_nowait()
                if tag == "__done__":
                    self._on_worker_done(text)
                else:
                    self._log(text, tag)
        except queue.Empty:
            pass
        self.after(120, self._drain_log)

    def _on_worker_done(self, code):
        kind = self.worker_kind
        self.worker = None
        self.worker_kind = None
        self._log(f"[GUI] 进程结束，退出码 {code}", "dim")
        if kind == "run":
            self.lbl_status.config(text="已停止")
            self.start_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.DISABLED)
        elif kind == "tool" and code == 0 and os.path.isfile(SHOT_PATH):
            try:
                img = Image.open(SHOT_PATH)
                img.thumbnail((660, 340))
                self._shot_img = ImageTk.PhotoImage(img)
                self.preview.config(image=self._shot_img, text="")
            except Exception:
                pass

    # ==================== 日志 ====================
    def _log(self, text, level="info"):
        clean = ANSI_RE.sub("", str(text)).rstrip()
        if not clean:
            return
        ts = time.strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{ts}] {clean}\n", level)
        self.log_text.see(tk.END)
        self._parse_status(clean)

    def _parse_status(self, line):
        if self.worker_kind != "run":
            return
        m = re.search(r"已点击「(.+?)」.*今日第\s*(\d+)\s*次", line)
        if m:
            self.lbl_status.config(text="值守中")
            self.lbl_clicks.config(text=m.group(2))
            self.lbl_event.config(text=f"已点击{m.group(1)}")
            return
        m = re.search(r"升级中，剩余\s*([\d:]+)", line)
        if m:
            self.lbl_status.config(text=f"升级中 {m.group(1)}")
            self.lbl_event.config(text="等待升级完成")
            return
        if "材料不足" in line:
            self.lbl_status.config(text="材料不足")
            self.lbl_event.config(text=line[:40])
            return
        if "重新连上" in line or "重连成功" in line:
            self.lbl_event.config(text="模拟器重连成功")
            return
        if line.startswith("出错：") or "截图失败" in line:
            self.lbl_event.config(text=line[:40])

    def on_close(self):
        if self.worker and self.worker_kind == "run":
            self.worker.stop()
            self.worker.join(timeout=3)
        if getattr(self, "qq_listener", None):
            self.qq_listener.stop()
        self.destroy()


class DiagWindow(tk.Toplevel):
    """ADB 连接测试窗口：后台线程执行，可随时终止，结果分级着色"""

    def __init__(self, master):
        super().__init__(master)
        self.master_app = master
        self.title("ADB 连接测试")
        self.geometry("780x640")
        self.configure(bg=COLORS["bg"])
        self.minsize(680, 520)
        self.transient(master)

        self.diag = None
        self.report = None
        self.queue = queue.Queue()

        top = ttk.Frame(self, padding=(14, 12, 14, 0))
        top.pack(fill=tk.X)
        ttk.Label(top, text="ADB 连接一键诊断", style="Accent.TLabel").pack(side=tk.LEFT)
        self.status_lbl = tk.Label(top, text="准备就绪", bg=COLORS["bg"], fg=COLORS["dim"],
                                   font=("Microsoft YaHei", 9))
        self.status_lbl.pack(side=tk.LEFT, padx=(14, 0))

        self.run_btn = ttk.Button(top, text="开始测试", style="Accent.TButton",
                                  command=self._start)
        self.run_btn.pack(side=tk.RIGHT)
        self.stop_btn = ttk.Button(top, text="终止测试", command=self._stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.RIGHT, padx=(0, 8))

        mid = ttk.Frame(self, padding=(14, 8, 14, 0))
        mid.pack(fill=tk.BOTH, expand=True)
        self.text = tk.Text(mid, bg=COLORS["card"], fg=COLORS["text"], wrap=tk.WORD,
                            font=("Consolas", 9), relief=tk.FLAT, padx=8, pady=6)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(mid, command=self.text.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.config(yscrollcommand=sb.set)
        for tag, fg in (("ok", COLORS["ok"]), ("err", COLORS["err"]), ("warn", COLORS["warn"]),
                        ("dim", COLORS["dim"]), ("title", COLORS["accent"]),
                        ("tip", "#8fd3e0")):
            self.text.tag_configure(tag, foreground=fg)
        self.text.tag_configure("title", foreground=COLORS["accent"],
                                font=("Microsoft YaHei", 9, "bold"))
        self.text.config(state=tk.DISABLED)

        bottom = ttk.Frame(self, padding=14)
        bottom.pack(fill=tk.X)
        self.adopt_btn = ttk.Button(bottom, text="采用第一个可用连接",
                                    command=self._adopt, state=tk.DISABLED)
        self.adopt_btn.pack(side=tk.LEFT)
        ttk.Button(bottom, text="复制报告", command=self._copy).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(bottom, text="关闭", command=self.destroy).pack(side=tk.RIGHT)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(150, self._start)

    def _start(self):
        if self.diag:
            return
        self.run_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.adopt_btn.config(state=tk.DISABLED)
        self._clear()
        self._put("开始诊断：只读检测，不会重启 ADB 服务、不影响正在运行的功能\n", "dim")
        prefer = self.master_app.adb.path if self.master_app.adb else None
        self.diag = AdbDiagnostic(on_progress=lambda m: self.queue.put(("progress", m)))
        threading.Thread(target=self._worker, args=(prefer,), daemon=True).start()
        self.after(120, self._poll)

    def _worker(self, prefer):
        try:
            rep = self.diag.run(prefer_path=prefer)
            self.queue.put(("done", rep))
        except Exception as e:
            self.queue.put(("error", str(e)))

    def _stop(self):
        if self.diag:
            self.diag.cancel()
            self.status_lbl.config(text="正在终止…", fg=COLORS["warn"])

    def _poll(self):
        try:
            while True:
                kind, data = self.queue.get_nowait()
                if kind == "progress":
                    self.status_lbl.config(text=data, fg=COLORS["warn"])
                elif kind == "done":
                    self.report = data
                    self._render(data)
                    self._finish()
                elif kind == "error":
                    self._put(f"测试异常：{data}\n", "err")
                    self._finish()
        except queue.Empty:
            pass
        if self.diag:
            self.after(150, self._poll)

    def _finish(self):
        self.diag = None
        self.run_btn.config(state=tk.NORMAL, text="重新测试")
        self.stop_btn.config(state=tk.DISABLED)
        n = len(self.report.usable) if self.report else 0
        if n:
            self.status_lbl.config(text=f"完成：发现 {n} 个可用设备实例", fg=COLORS["ok"])
            self.adopt_btn.config(state=tk.NORMAL)
        else:
            self.status_lbl.config(text="完成：没有可用设备，请按报告中的建议排查", fg=COLORS["err"])

    def _render(self, rep):
        for title, items in rep.sections:
            self._put(f"\n【{title}】\n", "title")
            for it in items:
                tag = {True: "ok", False: "err", None: "dim"}.get(it.ok, "dim")
                self._put(f"  {it.icon()} {it.name}\n", tag)
                if it.detail:
                    for dl in str(it.detail).splitlines():
                        self._put(f"      {dl}\n", "dim" if it.ok is None else None)
                if it.ok is False and it.suggestion:
                    for sl in str(it.suggestion).splitlines():
                        self._put(f"      → {sl}\n", "tip")
        self._put("\n" + "─" * 60 + "\n", "dim")
        if rep.usable:
            groups = {}
            for path, serial, model, size in rep.usable:
                groups.setdefault((path, model, size), []).append(serial)
            self._put("✅ 可用连接（同一台设备的多个端口已合并）：\n", "ok")
            for (path, model, size), serials in groups.items():
                self._put(f"   adb  : {path}\n")
                self._put(f"   型号 : {model}    分辨率: {size}\n")
                self._put(f"   端口 : {', '.join(serials)}\n", "ok")
        else:
            self._put("❌ 没有任何可用的 ADB 设备实例\n", "err")
        self._put(f"\n耗时 {rep.elapsed:.1f}s\n", "dim")

    def _adopt(self):
        if not self.report or not self.report.usable:
            return
        path, serial, _, _ = self.report.usable[0]
        self.master_app.adopt_connection(path, serial)
        self.status_lbl.config(text=f"已采用 {serial}，主界面设备已切换", fg=COLORS["ok"])

    def _copy(self):
        if not self.report:
            return
        self.clipboard_clear()
        self.clipboard_append(self.report.render_text())
        self.status_lbl.config(text="报告已复制到剪贴板", fg=COLORS["ok"])

    def _on_close(self):
        if self.diag:
            self.diag.cancel()
        self.destroy()

    def _clear(self):
        self.text.config(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        self.text.config(state=tk.DISABLED)

    def _put(self, s, tag=None):
        self.text.config(state=tk.NORMAL)
        self.text.insert(tk.END, s, tag) if tag else self.text.insert(tk.END, s)
        self.text.see(tk.END)
        self.text.config(state=tk.DISABLED)


class LaunchGameWindow(tk.Toplevel):
    """一键启动游戏窗口：后台跑 game_launcher，实时输出彩色日志，可终止"""

    def __init__(self, master, cmd):
        super().__init__(master)
        self.title("🚀 一键启动游戏")
        self.geometry("820x560")
        self.configure(bg=COLORS["bg"])
        self.cmd = cmd
        self.proc = None
        self._done = threading.Event()

        head = tk.Frame(self, bg=COLORS["bg"])
        head.pack(fill=tk.X, padx=12, pady=(12, 8))
        tk.Label(head, text="游戏自动启动流程（OCR 文字识别 + 模拟鼠标点击，不代码启动）",
                 bg=COLORS["bg"], fg=COLORS["accent"],
                 font=("Microsoft YaHei", 11, "bold")).pack(side=tk.LEFT)
        self.stop_btn = tk.Button(head, text="终止", bg="#555", fg="#fff",
                                  relief=tk.FLAT, padx=16, pady=3, cursor="hand2",
                                  command=self._stop)
        self.stop_btn.pack(side=tk.RIGHT)

        self.text = tk.Text(self, bg="#15191c", fg="#e8e8e8",
                            font=("Consolas", 10), wrap=tk.WORD,
                            insertbackground=COLORS["text"], state=tk.DISABLED)
        self.text.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 10))
        for tag, col in [("ok", COLORS["ok"]), ("err", COLORS["err"]),
                         ("warn", COLORS["warn"]), ("dim", COLORS["dim"]),
                         ("accent", COLORS["accent"]), ("step", "#ffffff")]:
            self.text.tag_configure(tag, foreground=col)
        self.text.tag_configure("step", font=("Consolas", 10, "bold"))

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._start)

    def _start(self):
        self._put("▶ 启动 game_launcher 流程…\n", "accent")
        try:
            self.proc = subprocess.Popen(
                self.cmd, cwd=BASE_DIR,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=1, text=True, encoding="utf-8", errors="replace",
                creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            self._put(f"启动失败：{e}\n", "err")
            return
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        try:
            for line in self.proc.stdout:
                if self._done.is_set():
                    break
                self.after(0, lambda l=line: self._put_line(l))
            rc = self.proc.wait()
            self.after(0, lambda: self._put(
                f"\n━━ 流程结束（exit={rc}）━━\n",
                "ok" if rc == 0 else "err"))
        except Exception as e:
            msg = str(e)  # except 变量离开作用域即被删除，先固化供延迟 lambda 使用
            self.after(0, lambda: self._put(f"读取异常：{msg}\n", "err"))

    def _put_line(self, line):
        # 按行首颜色标记着色
        s = line.rstrip("\n")
        tag = None
        if "❌" in s or "失败" in s:
            tag = "err"
        elif "✅" in s or "通过" in s or "已双击" in s or "已单击" in s:
            tag = "ok"
        elif "▶" in s or "第" in s and "步" in s:
            tag = "step"
        elif "warn" in s.lower() or "重试" in s or "演练" in s:
            tag = "warn"
        self.text.config(state=tk.NORMAL)
        self.text.insert(tk.END, s + "\n", tag)
        self.text.see(tk.END)
        self.text.config(state=tk.DISABLED)

    def _stop(self):
        self._done.set()
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self._put("\n⚠ 用户终止流程\n", "warn")
            except Exception:
                pass
        self.stop_btn.config(state=tk.DISABLED)

    def _on_close(self):
        self._stop()
        self.destroy()


class _Worker(threading.Thread):
    def __init__(self, args, cwd, log_queue):
        super().__init__(daemon=True)
        self.args = list(args)
        self.cwd = cwd
        self.q = log_queue
        self.proc = None

    def run(self):
        try:
            self.proc = subprocess.Popen(
                self.args, cwd=self.cwd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            self.q.put(("err", f"[启动失败] {e}"))
            self.q.put(("__done__", -1))
            return
        for ln in self.proc.stdout:
            ln = ln.rstrip()
            if not ln:
                continue
            level = "info"
            if any(k in ln for k in ("失败", "错误", "报错", "找不到", "没有检测到", "没有可用")):
                level = "err"
            elif any(k in ln for k in ("警告", "不足", "重试", "卡住", "冲突")):
                level = "warn"
            elif any(k in ln for k in ("成功", "已点击", "已保存", "已选", "找到", "已关闭")):
                level = "ok"
            self.q.put((level, ln))
        self.proc.wait()
        self.q.put(("__done__", self.proc.returncode))

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


def main():
    app = GuiApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


def _fix_stdio_for_frozen():
    """windowed(无控制台) 单 exe 下 sys.stdout/stderr 可能为 None。
    优先级：继承句柄(PIPE 启动) → AttachConsole 附着父控制台(bat 双击启动)
    → 都没有则指向 nul，保证任何模式下 print 都不崩。"""
    if not getattr(sys, "frozen", False):
        return
    try:
        import ctypes
        import msvcrt
        k32 = ctypes.windll.kernel32
        STD_OUTPUT_HANDLE, STD_ERROR_HANDLE, STD_INPUT_HANDLE = -11, -12, -10

        def _handle_valid(h):
            return h and h not in (0xFFFFFFFFFFFFFFFF, 0xFFFFFFFF)

        # 没有被 PIPE 重定向时，尝试附着到父进程控制台（bat 启动场景）
        if not _handle_valid(k32.GetStdHandle(STD_OUTPUT_HANDLE)):
            try:
                k32.AttachConsole(-1)   # ATTACH_PARENT_PROCESS
            except Exception:
                pass

        def _rebuild(hid, mode, encoding):
            h = k32.GetStdHandle(hid)
            if _handle_valid(h):
                try:
                    fd = msvcrt.open_osfhandle(h, mode | os.O_TEXT)
                    return os.fdopen(fd, "r" if mode == os.O_RDONLY else "w",
                                     encoding=encoding, errors="replace", closefd=False)
                except Exception:
                    pass
            return None

        if sys.stdout is None:
            sys.stdout = _rebuild(STD_OUTPUT_HANDLE, os.O_WRONLY, "utf-8") or \
                open(os.devnull, "w", encoding="utf-8")
        if sys.stderr is None:
            sys.stderr = _rebuild(STD_ERROR_HANDLE, os.O_WRONLY, "utf-8") or \
                open(os.devnull, "w", encoding="utf-8")
        if sys.stdin is None:
            sys.stdin = _rebuild(STD_INPUT_HANDLE, os.O_RDONLY, "utf-8") or \
                open(os.devnull, "r", encoding="utf-8")
    except Exception:
        pass


def _free_console():
    """命令行模式结束时脱离附着的父控制台，让 cmd 正常回收提示符"""
    if not getattr(sys, "frozen", False):
        return
    try:
        import ctypes
        ctypes.windll.kernel32.FreeConsole()
    except Exception:
        pass


def _dispatch():
    """单 exe 入口分发：
      无参数/--gui   → 图形界面（无黑框）
      --core ...     → OCR 值守核心（lagrange_upgrade 命令行）
      --launcher ... → 游戏一键启动器（game_launcher 命令行）"""
    _fix_stdio_for_frozen()
    if len(sys.argv) >= 2 and sys.argv[1] == "--core":
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        import lagrange_upgrade
        try:
            return lagrange_upgrade.main()
        finally:
            _free_console()
    if len(sys.argv) >= 2 and sys.argv[1] == "--launcher":
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        import game_launcher
        try:
            return game_launcher.main()
        finally:
            _free_console()
    # GUI 模式
    main()
    return 0


if __name__ == "__main__":
    sys.exit(_dispatch())

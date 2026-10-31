# -*- coding: utf-8 -*-
"""
星际猎人 · 自动助手 测试仪
=============================
一个双击即可运行的图形化测试工具，把 lagrange_upgrade.py 的所有子命令做成按钮，
实时显示输出、耗时与退出码，并支持截图预览。

两种入口（同一个 exe）：
  - 无参数       → 启动本图形界面
  - --cli <cmd>  → 以命令行方式执行 lagrange_upgrade 的子命令（GUI 内部走这条）

打包见 build_exe.bat / tester.spec。
"""

import sys
import os
import re
import json
import time
import queue
import threading
import subprocess

APP_TITLE = "星际猎人 · 自动助手 测试仪"
VERSION = "1.0"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ==================== CLI 转发：--cli xxx → 走原脚本 ====================
def _hide_child_consoles():
    """打包成「无控制台 exe」后，子进程（adb 等）会自己弹出黑色命令行窗口挡住界面。
    这里给本进程内所有 subprocess 调用默认补上 CREATE_NO_WINDOW。
    只在 --cli 子进程中生效，不影响源码直接运行、也不影响 GUI 主进程。
    """
    if os.name != "nt":
        return
    orig_init = subprocess.Popen.__init__

    def patched(self, *a, **kw):
        kw["creationflags"] = int(kw.get("creationflags", 0)) | CREATE_NO_WINDOW
        orig_init(self, *a, **kw)

    subprocess.Popen.__init__ = patched


def _dispatch_cli():
    """把 --cli 之后的参数还原成 lagrange_upgrade 认识的 argv，然后执行。

    无控制台打包（console=False）时 stdout 句柄可能无效，因此 GUI 会额外传
    --out <文件>，我们直接把 stdout/stderr 重定向到该文件，GUI 再增量读取。
    这样无论有没有控制台，输出都不会丢。
    """
    argv = list(sys.argv[1:])

    # 取出 --out 目标文件
    out_path = None
    if "--out" in argv:
        i = argv.index("--out")
        if i + 1 < len(argv):
            out_path = argv[i + 1]
        del argv[i:i + 2]

    if "--cli" in argv:
        argv.remove("--cli")

    if out_path:
        try:
            f = open(out_path, "w", encoding="utf-8", buffering=1)
            sys.stdout = f
            sys.stderr = f
        except Exception:
            pass

    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    sys.argv = [sys.argv[0]] + argv
    try:
        from lagrange_upgrade import main
        main()
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        try:
            sys.stdout.flush()
        except Exception:
            pass
        raise
    finally:
        try:
            sys.stdout.flush()
        except Exception:
            pass
    sys.exit(0)


def _install_crash_log():
    """GUI 模式下把未捕获异常写到 logs/crash.log，便于排查。"""
    def hook(exc_type, exc, tb):
        import traceback
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            p = os.path.join(LOG_DIR, "crash.log")
            with open(p, "a", encoding="utf-8") as f:
                f.write(f"\n{'=' * 60}\n{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                traceback.print_exception(exc_type, exc, tb, file=f)
        except Exception:
            pass
        try:
            sys.__excepthook__(exc_type, exc, tb)
        except Exception:
            pass
    sys.excepthook = hook


if "--cli" in sys.argv:
    _hide_child_consoles()
    _dispatch_cli()


# ==================== 路径工具 ====================
def app_dir():
    """exe 或脚本所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = app_dir()
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_DIR = os.path.join(BASE_DIR, "logs")

ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def strip_ansi(s):
    return ANSI_RE.sub("", s)


def load_cfg():
    """只读 json，不 import 主脚本（保证 GUI 秒开）。"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cfg(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def cli_argv(extra, out_path=None):
    """构造调用自身的命令行。打包后即 exe 自身，未打包时是 python + 脚本。"""
    if getattr(sys, "frozen", False):
        base = [sys.executable]
    else:
        base = [sys.executable, os.path.abspath(__file__)]
    base.append("--cli")
    if out_path:
        base += ["--out", out_path]
    return base + list(extra)


# ==================== 动作定义 ====================
# (按钮名, 子命令, 说明, 分组, 是否长任务)
ACTIONS = [
    ("① 环境自检", "check", "adb / 设备 / 分辨率 / 游戏前台", "基础检测", False),
    ("② 读当前状态", "status", "资源与建造队列（勿与升级同时跑）", "基础检测", False),
    ("③ 截图", "shot", "截当前画面并在右侧预览", "基础检测", False),
    ("④ 整屏 OCR", "scan", "识别整屏文字并标注坐标", "基础检测", False),

    ("⑤ 日常领取", "daily", "任务 / 活动 / 邮件三段领取 ＋ 回执", "业务功能", False),
    ("⑥ 升级循环", "autoup", "队列空位自动补位，长期挂机", "业务功能", True),
    ("⑦ 一体化", "all", "先领取，再进入升级循环", "业务功能", True),
]


# ==================== GUI ====================
def run_gui():
    _install_crash_log()
    import tkinter as tk
    from tkinter import ttk, messagebox

    # ---------- 主题色 ----------
    BG = "#1e2228"
    BG_PANEL = "#262b33"
    BG_LOG = "#15181d"
    FG = "#dfe4ea"
    FG_DIM = "#8b96a5"
    ACCENT = "#3fb9d4"
    OK = "#4cd07d"
    WARN = "#e8b33c"
    ERR = "#e8615a"

    FONT = ("Microsoft YaHei UI", 10)
    FONT_SM = ("Microsoft YaHei UI", 9)
    FONT_LOG = ("Consolas", 10)
    FONT_TITLE = ("Microsoft YaHei UI", 13, "bold")

    root = tk.Tk()
    root.title(f"{APP_TITLE}  v{VERSION}")
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    w = min(1320, sw - 80)
    h = min(880, sh - 110)
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{max((sh - h) // 2 - 30, 0)}")
    root.minsize(1080, 720)
    root.configure(bg=BG)

    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("TCombobox", fieldbackground=BG_PANEL, background=BG_PANEL,
                    foreground=FG, arrowcolor=FG, borderwidth=0)
    style.map("TCombobox", fieldbackground=[("readonly", BG_PANEL)],
              foreground=[("readonly", FG)])
    style.configure("TCheckbutton", background=BG_PANEL, foreground=FG, font=FONT_SM)
    style.map("TCheckbutton", background=[("active", BG_PANEL)])

    # ---------- 状态变量 ----------
    proc_holder = {"proc": None}
    running = {"flag": False}
    start_ts = {"t": 0.0}
    log_q = queue.Queue()
    log_file = {"path": None}

    # ========== 顶部标题 ==========
    header = tk.Frame(root, bg=BG)
    header.pack(fill="x", padx=14, pady=(12, 6))
    tk.Label(header, text="星际猎人 · 自动助手", bg=BG, fg=ACCENT,
             font=FONT_TITLE).pack(side="left")
    tk.Label(header, text="测试仪", bg=BG, fg=FG_DIM, font=FONT).pack(side="left", padx=(8, 0))
    dev_label = tk.Label(header, text="设备：未检测", bg=BG, fg=FG_DIM, font=FONT_SM)
    dev_label.pack(side="right")

    # ========== 设备配置区 ==========
    devbox = tk.Frame(root, bg=BG_PANEL, highlightthickness=0)
    devbox.pack(fill="x", padx=14, pady=(0, 8))

    inner = tk.Frame(devbox, bg=BG_PANEL)
    inner.pack(fill="x", padx=12, pady=10)

    tk.Label(inner, text="设备模式", bg=BG_PANEL, fg=FG, font=FONT_SM).pack(side="left")
    mode_var = tk.StringVar(value="auto")
    mode_cb = ttk.Combobox(inner, textvariable=mode_var, width=11, state="readonly",
                           values=["auto", "phone", "emulator", "serial"], font=FONT_SM)
    mode_cb.pack(side="left", padx=(6, 14))

    tk.Label(inner, text="序列号", bg=BG_PANEL, fg=FG, font=FONT_SM).pack(side="left")
    serial_var = tk.StringVar(value="")
    tk.Entry(inner, textvariable=serial_var, width=24, bg=BG_LOG, fg=FG,
             insertbackground=FG, relief="flat", font=FONT_SM).pack(side="left", padx=(6, 14))

    def _save_device():
        cfg = load_cfg()
        if not cfg:
            messagebox.showwarning("提示", "没有找到 config.json，请先运行一次环境自检。")
            return
        cfg.setdefault("device", {})
        cfg["device"]["mode"] = mode_var.get()
        cfg["device"]["serial"] = serial_var.get().strip()
        if save_cfg(cfg):
            append_log(f"[配置] 设备模式已保存为 {mode_var.get()}"
                       f"{(' / ' + serial_var.get().strip()) if serial_var.get().strip() else ''}\n", "ok")
        else:
            messagebox.showerror("失败", "config.json 写入失败，可能被占用。")

    tk.Button(inner, text="保存设备设置", command=_save_device,
              bg=BG_LOG, fg=ACCENT, activebackground=BG_LOG, activeforeground=ACCENT,
              relief="flat", font=FONT_SM, padx=12, pady=3, cursor="hand2").pack(side="left")

    tk.Button(inner, text="打开目录", command=lambda: _open_dir(BASE_DIR),
              bg=BG_LOG, fg=FG_DIM, activebackground=BG_LOG, activeforeground=FG,
              relief="flat", font=FONT_SM, padx=10, pady=3, cursor="hand2").pack(side="right")
    tk.Button(inner, text="打开日志目录", command=lambda: _open_dir(LOG_DIR),
              bg=BG_LOG, fg=FG_DIM, activebackground=BG_LOG, activeforeground=FG,
              relief="flat", font=FONT_SM, padx=10, pady=3, cursor="hand2").pack(side="right", padx=(0, 6))

    # ========== 底部状态栏（先占位，避免被主体挤掉） ==========
    status = tk.Frame(root, bg=BG_PANEL)
    status.pack(fill="x", side="bottom")
    status_dot = tk.Label(status, text="●", bg=BG_PANEL, fg="#5a636f", font=("Consolas", 11))
    status_dot.pack(side="left", padx=(12, 4), pady=6)
    status_label = tk.Label(status, text="空闲", bg=BG_PANEL, fg=FG_DIM, font=FONT_SM)
    status_label.pack(side="left")
    elapsed_label = tk.Label(status, text="", bg=BG_PANEL, fg="#5a636f", font=FONT_SM)
    elapsed_label.pack(side="right", padx=12)

    # ========== 主体：左按钮 + 右预览 ==========
    body = tk.Frame(root, bg=BG)
    body.pack(fill="both", expand=True, padx=14, pady=(0, 6))

    left = tk.Frame(body, bg=BG, width=300)
    left.pack(side="left", fill="y")
    left.pack_propagate(False)
    right = tk.Frame(body, bg=BG)
    right.pack(side="right", fill="both", expand=True, padx=(10, 0))

    btns = {}

    def make_group(title, group_key):
        box = tk.Frame(left, bg=BG_PANEL)
        box.pack(fill="x", pady=(0, 8))
        tk.Label(box, text=title, bg=BG_PANEL, fg=FG_DIM, font=FONT_SM,
                 anchor="w").pack(fill="x", padx=12, pady=(8, 4))
        for name, cmd, desc, grp, is_long in ACTIONS:
            if grp != group_key:
                continue
            b = tk.Button(box, text=name, anchor="w", bg=BG_LOG, fg=FG,
                          activebackground="#2f3540", activeforeground=FG,
                          relief="flat", font=FONT, padx=14, pady=6, cursor="hand2",
                          disabledforeground="#5a636f",
                          command=lambda c=cmd, d=desc, l=is_long: start_cmd(c, d, l))
            b.pack(fill="x", padx=10, pady=2)
            btns[cmd] = b
            tk.Label(box, text="   " + desc, bg=BG_PANEL, fg="#6c7684", font=("Microsoft YaHei UI", 8),
                     anchor="w", justify="left", wraplength=258).pack(fill="x", padx=12, pady=(0, 3))
        return box

    make_group("基础检测", "基础检测")
    make_group("业务功能", "业务功能")

    # 控制区
    ctrl = tk.Frame(left, bg=BG_PANEL)
    ctrl.pack(fill="x", pady=(0, 8))
    tk.Label(ctrl, text="控制", bg=BG_PANEL, fg=FG_DIM, font=FONT_SM,
             anchor="w").pack(fill="x", padx=12, pady=(8, 4))

    stop_btn = tk.Button(ctrl, text="■ 停止当前任务", bg="#5a2d2d", fg="#ff9c94",
                         activebackground="#733838", activeforeground="#ffb4ad",
                         relief="flat", font=FONT, padx=14, pady=6, cursor="hand2",
                         state="disabled", command=lambda: stop_task())
    stop_btn.pack(fill="x", padx=10, pady=2)

    tk.Button(ctrl, text="一键全测（自检＋状态＋截图）", bg=BG_LOG, fg=ACCENT,
              activebackground="#2f3540", activeforeground=ACCENT,
              relief="flat", font=FONT, padx=14, pady=6, cursor="hand2",
              command=lambda: start_selftest()).pack(fill="x", padx=10, pady=(6, 10))

    # ---------- 右：截图预览（固定高度，避免挤压日志） ----------
    prev_frame = tk.Frame(right, bg=BG_PANEL, height=268)
    prev_frame.pack(fill="x")
    prev_frame.pack_propagate(False)
    tk.Label(prev_frame, text="画面预览", bg=BG_PANEL, fg=FG_DIM, font=FONT_SM,
             anchor="w").pack(fill="x", padx=12, pady=(8, 2))
    prev_label = tk.Label(prev_frame, text="（运行「③ 截图」或「④ 整屏 OCR」后自动显示）",
                          bg=BG_LOG, fg="#5a636f", font=FONT_SM)
    prev_label.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    # ---------- 右：日志 ----------
    log_frame = tk.Frame(right, bg=BG)
    log_frame.pack(fill="both", expand=True, pady=(8, 0))
    log_head = tk.Frame(log_frame, bg=BG)
    log_head.pack(fill="x")
    tk.Label(log_head, text="运行输出", bg=BG, fg=FG_DIM, font=FONT_SM).pack(side="left")
    log_path_label = tk.Label(log_head, text="", bg=BG, fg="#5a636f", font=("Microsoft YaHei UI", 8))
    log_path_label.pack(side="right")

    tk.Button(log_head, text="清空", bg=BG, fg=FG_DIM, activebackground=BG, activeforeground=FG,
              relief="flat", font=FONT_SM, padx=8, cursor="hand2",
              command=lambda: log_text.delete("1.0", "end")).pack(side="right", padx=(4, 10))

    auto_scroll = tk.BooleanVar(value=True)
    tk.Checkbutton(log_head, text="自动滚动", variable=auto_scroll, bg=BG, fg=FG_DIM,
                   activebackground=BG, activeforeground=FG, selectcolor=BG_PANEL,
                   relief="flat", font=FONT_SM, cursor="hand2").pack(side="right", padx=(0, 6))

    log_wrap = tk.Frame(log_frame, bg=BG_LOG)
    log_wrap.pack(fill="both", expand=True, pady=(4, 0))
    log_text = tk.Text(log_wrap, bg=BG_LOG, fg=FG, insertbackground=FG, relief="flat",
                       font=FONT_LOG, wrap="word", padx=10, pady=8, state="disabled")
    sb = tk.Scrollbar(log_wrap, command=log_text.yview, bg=BG_PANEL, troughcolor=BG_LOG,
                      relief="flat", width=10)
    log_text.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    log_text.pack(side="left", fill="both", expand=True)

    log_text.tag_config("ok", foreground=OK)
    log_text.tag_config("warn", foreground=WARN)
    log_text.tag_config("err", foreground=ERR)
    log_text.tag_config("dim", foreground=FG_DIM)
    log_text.tag_config("hl", foreground=ACCENT)
    log_text.tag_config("big", foreground=FG, font=("Consolas", 10, "bold"))

    # ==================== 通用工具 ====================
    def _open_dir(path):
        try:
            os.makedirs(path, exist_ok=True)
            os.startfile(path)
        except Exception as e:
            messagebox.showerror("打不开", f"{path}\n{e}")

    def append_log(text, tag=None):
        log_text.configure(state="normal")
        log_text.insert("end", text, tag or ())
        log_text.configure(state="disabled")
        if auto_scroll.get():
            log_text.see("end")
        # 同步落盘
        if log_file["path"]:
            try:
                with open(log_file["path"], "a", encoding="utf-8") as f:
                    f.write(strip_ansi(text))
            except Exception:
                pass

    def classify(line):
        s = line
        if re.search(r"错误|异常|失败|Error|Traceback|不可用|未检测到", s):
            return "err"
        if re.search(r"✅|成功|完成|已保存|已同步", s):
            return "ok"
        if re.search(r"警告|注意|未命中|跳过|提示", s):
            return "warn"
        if re.search(r"▸|===", s):
            return "hl"
        return None

    def set_running(flag):
        running["flag"] = flag
        state = "disabled" if flag else "normal"
        for cmd, b in btns.items():
            b.configure(state=state)
        stop_btn.configure(state="normal" if flag else "disabled")

    def set_status(text, color="#5a636f"):
        status_dot.configure(fg=color)
        status_label.configure(text=text)

    def refresh_device_from_cfg():
        cfg = load_cfg()
        d = cfg.get("device", {}) if isinstance(cfg, dict) else {}
        mode_var.set(d.get("mode", "auto"))
        serial_var.set(d.get("serial", ""))

    # ==================== 任务执行 ====================
    def stop_task():
        p = proc_holder.get("proc")
        if p and p.poll() is None:
            append_log("\n[测试仪] 正在停止…\n", "warn")
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                                   capture_output=True, creationflags=CREATE_NO_WINDOW)
                else:
                    p.terminate()
            except Exception as e:
                append_log(f"[测试仪] 停止失败: {e}\n", "err")

    def start_cmd(cmd, desc, is_long, on_done=None):
        if running["flag"]:
            return
        os.makedirs(LOG_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        log_file["path"] = os.path.join(LOG_DIR, f"{stamp}_{cmd}.log")
        log_path_label.configure(text=os.path.basename(log_file["path"]))

        # 子进程输出中转文件（无控制台打包也能拿到输出）
        out_file = os.path.join(LOG_DIR, "_run.tmp")
        try:
            if os.path.exists(out_file):
                os.remove(out_file)
        except Exception:
            pass

        set_running(True)
        set_status(f"运行中：{desc}", ACCENT)
        start_ts["t"] = time.time()
        elapsed_label.configure(text="0.0s")

        append_log(f"\n{'=' * 66}\n", "dim")
        append_log(f"[{time.strftime('%H:%M:%S')}] 开始执行  {cmd}   {desc}\n", "big")

        argv = cli_argv([cmd], out_path=out_file)
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"

        def worker():
            rc = -1
            pos = [0]

            def drain():
                try:
                    with open(out_file, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(pos[0])
                        chunk = f.read()
                        pos[0] = f.tell()
                except (FileNotFoundError, OSError):
                    return
                if chunk:
                    for ln in chunk.splitlines(True):
                        log_q.put(("line", ln))

            try:
                p = subprocess.Popen(
                    argv, cwd=BASE_DIR,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=env, creationflags=CREATE_NO_WINDOW,
                )
                proc_holder["proc"] = p
                while p.poll() is None:
                    drain()
                    time.sleep(0.12)
                drain()
                rc = p.returncode if p.returncode is not None else -1
            except Exception as e:
                log_q.put(("line", f"[测试仪] 启动失败: {e}\n"))
            finally:
                log_q.put(("done", rc, cmd, on_done))

        threading.Thread(target=worker, daemon=True).start()

    def start_selftest():
        """一键全测：check → status → shot，串行执行。"""
        if running["flag"]:
            return

        def step2():
            root.after(300, lambda: start_cmd("status", "读取资源与队列", False, step3))

        def step3():
            root.after(300, lambda: start_cmd("shot", "截图预览", False, None))

        start_cmd("check", "检查 adb / 设备 / 分辨率", False, step2)

    # ==================== 队列轮询 ====================
    def pump():
        try:
            while True:
                item = log_q.get_nowait()
                if item[0] == "line":
                    raw = item[1]
                    clean = strip_ansi(raw).rstrip("\n")
                    if clean.strip():
                        append_log(clean + "\n", classify(clean))
                elif item[0] == "done":
                    rc = item[1]
                    cmd = item[2]
                    on_done = item[3] if len(item) > 3 else None
                    dur = time.time() - start_ts["t"]
                    elapsed_label.configure(text=f"{dur:.1f}s")
                    if rc == 0:
                        append_log(f"[{time.strftime('%H:%M:%S')}] 结束 {cmd} · 退出码 0 · 耗时 {dur:.1f}s\n", "ok")
                        set_status(f"完成：{cmd}（{dur:.1f}s）", OK)
                    else:
                        append_log(f"[{time.strftime('%H:%M:%S')}] 结束 {cmd} · 退出码 {rc} · 耗时 {dur:.1f}s\n", "err")
                        set_status(f"异常退出：{cmd}（退出码 {rc}）", ERR)
                    proc_holder["proc"] = None
                    set_running(False)
                    if cmd in ("shot", "scan"):
                        root.after(150, load_preview)
                    if on_done:
                        on_done()
        except queue.Empty:
            pass
        # 运行中刷新计时
        if running["flag"]:
            elapsed_label.configure(text=f"{time.time() - start_ts['t']:.1f}s")
        root.after(120, pump)

    # ==================== 截图预览 ====================
    def load_preview():
        try:
            from PIL import Image, ImageTk
        except Exception:
            return
        cand = []
        for name in ("shot.png", "scan_标注.png"):
            p = os.path.join(BASE_DIR, name)
            if os.path.isfile(p):
                cand.append((os.path.getmtime(p), p))
        if not cand:
            return
        cand.sort(reverse=True)
        try:
            im = Image.open(cand[0][1])
            avail_w = max(prev_label.winfo_width(), 400) - 20
            avail_h = max(prev_label.winfo_height(), 200) - 20
            im.thumbnail((avail_w, avail_h), Image.LANCZOS)
            ph = ImageTk.PhotoImage(im)
            prev_label.configure(image=ph, text="")
            prev_label.image = ph
        except Exception:
            pass

    # ==================== 启动 ====================
    append_log(f"{APP_TITLE}  v{VERSION}\n", "big")
    append_log(f"工作目录：{BASE_DIR}\n", "dim")
    cfg = load_cfg()
    if cfg:
        d = cfg.get("device", {})
        append_log(f"已加载配置：设备模式 = {d.get('mode', 'auto')}"
                   f"{(' / ' + d.get('serial')) if d.get('serial') else ''}"
                   f" · 目标建筑 = {cfg.get('au_target_building', '未设置')}\n", "dim")
        refresh_device_from_cfg()
    else:
        append_log("未找到 config.json —— 请先点「① 环境自检」，脚本会自动生成默认配置。\n", "warn")
    append_log("提示：升级循环与读状态不要同时运行，两者会抢屏幕。\n", "dim")

    # 自测/演示模式：--autorun <子命令>，界面起来后自动执行一次
    if "--autorun" in sys.argv:
        i = sys.argv.index("--autorun")
        if i + 1 < len(sys.argv):
            _cmd = sys.argv[i + 1]
            append_log(f"（自测模式：启动后自动执行 {_cmd}）\n", "warn")
            root.after(1000, lambda: start_cmd(_cmd, "自动测试执行", False))

    root.after(120, pump)
    root.after(600, load_preview)   # 目录里有上次的截图就先显示出来
    root.mainloop()


if __name__ == "__main__":
    run_gui()

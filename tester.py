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


def _parent_pid_alive(pid):
    """判断 GUI 父进程是否还在（Windows 用 OpenProcess + GetExitCodeProcess）。"""
    if os.name != "nt":
        try:
            os.kill(int(pid), 0)
            return True
        except OSError:
            return False
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, int(pid))     # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
    k32.CloseHandle(h)
    return bool(ok) and code.value == 259            # STILL_ACTIVE


def _watch_parent_process(pid, interval=2.0):
    """GUI 父进程消失时立即结束本 CLI 子进程。

    之前只靠 GUI 的「停止」按钮，关掉窗口（或 GUI 被强杀）后挂机子进程会一直留在后台。
    """
    def loop():
        while True:
            time.sleep(interval)
            if not _parent_pid_alive(pid):
                try:
                    sys.stderr.write("[守护] 测试仪已关闭，自动结束后台挂机进程\n")
                    sys.stderr.flush()
                except Exception:
                    pass
                os._exit(0)
    threading.Thread(target=loop, daemon=True, name="parent-watch").start()


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

    # 父进程（GUI）消失时自动退出，避免关窗口 / GUI 被强杀后挂机子进程残留在后台
    parent_pid = os.environ.get("_TESTER_PARENT_PID", "")
    if parent_pid.isdigit():
        _watch_parent_process(int(parent_pid))

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
def eta_display(slot, now=None, updated_at=None):
    """纯函数：把 state.json 的一条倒计时换算成 (显示文字, 是否活跃)。

    供 GUI 倒计时条使用，也便于单测覆盖各种边界（过期 / 陈旧 / 坏数据）。
    slot: {"sec":秒, "at":"写入时刻", "name":建筑名}
    返回 ("--:--:--" | "HH:MM:SS" | "已完成 ✓" | "数据已过期", bool 活跃)
    """
    def _parse(s):
        try:
            return time.mktime(time.strptime(str(s), "%Y-%m-%d %H:%M:%S"))
        except Exception:
            return None
    slot = slot if isinstance(slot, dict) else {}
    now = time.time() if now is None else now
    try:
        sec = int(slot.get("sec"))
    except Exception:
        return "--:--:--", False
    base = _parse(slot.get("at"))
    if base is None:
        return "--:--:--", False
    # 关键（2026-11-23 bug 修复）：不能用「距上次写入时间」判陈旧！
    # 脚本静默等待 1 小时不写文件是正常行为，那时 upgrade_at 已过 10 分钟，
    # 若据此判「数据已过期」会把正常长倒计时误报掉。
    # 正确语义：at 时刻读到还剩 sec 秒 → 完成时刻 = at + sec。
    #   还没到完成时刻 → 倒计时有效（无论 at 多久以前）；
    #   刚过完成时刻 10 分钟内 → 已完成；超过 10 分钟 → 数据已过期。
    remain = sec - (now - base)
    if remain > 0:
        h, m, s = int(remain) // 3600, (int(remain) % 3600) // 60, int(remain) % 60
        return f"{h:02d}:{m:02d}:{s:02d}", True
    if remain > -600:
        return "已完成 ✓", False
    return "数据已过期", False


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


# ===== 运行日志：按天分文件 + 保留最近 7 天，避免 logs/ 无限堆积 =====
LOG_KEEP_DAYS = 7
LOG_NAME_RE = re.compile(r"^\d{8}_[A-Za-z0-9_]+\.log$")


def prune_old_logs(log_dir, keep_days=LOG_KEEP_DAYS):
    """删除本程序生成的超期日志。只匹配 YYYYMMDD_<命令>.log，绝不碰其它文件。
    返回被删除的文件名列表，便于核对。"""
    os.makedirs(log_dir, exist_ok=True)
    names = [n for n in os.listdir(log_dir) if LOG_NAME_RE.match(n)]
    days = sorted({n[:8] for n in names}, reverse=True)
    old = set(days[keep_days:])
    removed = []
    for n in names:
        if n[:8] not in old:
            continue
        try:
            os.remove(os.path.join(log_dir, n))
            removed.append(n)
        except OSError:
            pass
    return removed


def daily_log_path(log_dir, cmd, day=None):
    """按天分文件（同一天多次运行追加到同一文件），并顺带清理超期旧日志。"""
    day = day or time.strftime("%Y%m%d")
    prune_old_logs(log_dir)
    return os.path.join(log_dir, f"{day}_{cmd}.log")


# ===== ADB 设备扫描（GUI 直接调 adb，不 import 主脚本，保证秒开）=====
DEVICE_AUTO_LABEL = "（自动选择第一个在线设备）"
DEVICE_STATUS_CN = {
    "device": "在线", "offline": "离线", "unauthorized": "未授权",
    "recovery": "恢复模式", "no permissions": "无权限",
}


def parse_adb_devices(text):
    """从 `adb devices` 输出里取 status=device 的序列号（兼容旧调用/单测）。"""
    return [d["serial"] for d in parse_adb_device_rows(text) if d["online"]]


def parse_adb_device_rows(text):
    """解析 `adb devices -l` 的全部设备（含离线/未授权），返回 dict 列表。

    每项：serial / status / status_cn / model / resolution / online。
    不再猜测真机还是模拟器——序列号特征不可靠，界面统一合并展示所有在线设备。
    """
    rows = []
    for line in (text or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2 or parts[0].startswith("*"):
            continue
        serial, status = parts[0], parts[1].lower()
        model = ""
        for tok in parts[2:]:
            if tok.startswith("model:"):
                model = tok.split(":", 1)[1]
                break
        rows.append({
            "serial": serial,
            "status": status,
            "status_cn": DEVICE_STATUS_CN.get(status, status),
            "model": model,
            "resolution": "未知",
            "online": status == "device",
        })
    return rows


def format_device_info(d):
    """一行完整设备信息，用于截图后的反馈与「当前设备」标识。"""
    if not d:
        return "未选择设备"
    return (f"{d['serial']} ｜ {d['status_cn']} ｜ "
            f"{d.get('resolution') or '未知'} ｜ 型号 {d.get('model') or '未知'}")


def compact_online_text(rows):
    """把所有在线设备紧凑拼成一行文本（合并展示用）。"""
    online = [d for d in (rows or []) if d["online"]]
    if not online:
        return "在线设备（0）：无"
    parts = []
    for d in online:
        res = d.get("resolution") or "未知"
        model = f" · {d['model']}" if d.get("model") else ""
        parts.append(f"{d['serial']}（{res}{model}）")
    return f"在线设备（{len(online)}）：" + " ｜ ".join(parts)


def pick_device(rows, serial=None):
    """选择设备：指定 serial 优先（必须在线）；否则挑第一台在线。返回 (设备dict, 错误信息)。"""
    rows = rows or []
    if serial:
        hit = next((d for d in rows if d["serial"] == serial and d["online"]), None)
        if hit:
            return hit, ""
        return None, f"设备 {serial} 不在线或不存在"
    pool = [d for d in rows if d["online"]]
    if not pool:
        return None, "没有在线设备"
    return pool[0], ""


def _adb_run(adb_path, args, timeout=6):
    r = subprocess.run([adb_path] + list(args), capture_output=True,
                       timeout=timeout, creationflags=CREATE_NO_WINDOW)
    return r.stdout.decode("utf-8", "replace")


def _device_adb_path(config=None, adb_path=None):
    if adb_path:
        return adb_path
    cfg = config if isinstance(config, dict) else load_cfg()
    try:
        from adb_locator import find_adb
        return find_adb(cfg)
    except Exception:
        return None


def scan_adb_devices(config=None, adb_path=None, timeout=8):
    """扫描本机 ADB 连接的全部设备（含离线/未授权），补上在线设备的分辨率。
    返回 (设备dict列表, 错误信息)。"""
    path = _device_adb_path(config, adb_path)
    if not path or not os.path.isfile(path):
        return [], "找不到 adb.exe（可在 config.json 里填 adb_path）"
    try:
        out = _adb_run(path, ["devices", "-l"], timeout)
    except Exception as e:
        return [], f"adb devices 失败：{e}"
    rows = parse_adb_device_rows(out)
    for d in rows:
        if not d["online"]:
            continue
        try:
            size = _adb_run(path, ["-s", d["serial"], "shell", "wm", "size"], 5)
            m = re.search(r"(\d+)x(\d+)", size)
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                d["resolution"] = f"{max(a, b)}x{min(a, b)}"
        except Exception:
            pass
    return rows, ""


def capture_device_shot(serial, out_path=None, config=None, adb_path=None):
    """给指定设备截一张全屏 PNG，可保存到 out_path。
    返回 (是否成功, 说明文字, PNG字节数)。"""
    path = _device_adb_path(config, adb_path)
    if not path or not serial:
        return False, "没有可用 adb 或设备序列号", 0
    try:
        r = subprocess.run([path, "-s", serial, "exec-out", "screencap", "-p"],
                           capture_output=True, timeout=20, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return False, "截图超时（20 秒）", 0
    except Exception as e:
        return False, f"截图失败：{e}", 0
    if r.stdout[:4] != b"\x89PNG":
        return False, "未拿到 PNG 截图（设备可能锁屏或掉线）", 0
    n = len(r.stdout)
    if out_path:
        try:
            with open(out_path, "wb") as f:
                f.write(r.stdout)
        except Exception as e:
            return False, f"截图已取得但保存失败：{e}", n
    return True, f"截图 {n // 1024} KB", n


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


# ===== 性能档位（发热 / CPU 占用控制）=====
# GUI 不 import 主脚本（保证秒开），这里只放标签与轮询间隔的轻量映射；
# 真正的运行参数在 lagrange_upgrade.py 的 PERF_MODES 里，切换写回 config.json。
PERF_CHOICES = [("省电（推荐）", "eco"), ("均衡", "balanced"), ("原速（排障）", "turbo")]
PERF_LABEL_BY_MODE = {m: t for t, m in PERF_CHOICES}
PERF_DESC = {
    "eco": "OCR 2 线程 · 每轮间歇 10s · 卡住退避至 90s",
    "balanced": "OCR 4 线程 · 每轮间歇 5s · 退避至 45s",
    "turbo": "不限线程 · 不额外等待（发热最高）",
}
PERF_POLL_MS = {"eco": 500, "balanced": 300, "turbo": 120}


# ===== 速本方案：独立的 profiles.json（不 import 主脚本，保证 GUI 秒开）=====
PROFILES_PATH = os.path.join(BASE_DIR, "profiles.json")
PROFILE_OFF = "（不启用方案）"


def load_profiles():
    try:
        with open(PROFILES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("profiles"), dict):
            return data
    except Exception:
        pass
    return {"active": "", "profiles": {}}


def save_profiles(data):
    try:
        with open(PROFILES_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
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
    ("⑤ 图标校准", "icons", "纯图标入口检测，生成标注图与活动区放大图", "基础检测", False),

    ("⑥ 日常领取", "daily", "任务 / 活动 / 邮件三段领取 ＋ 回执", "业务功能", False),
    ("⑦ 升级循环", "autoup", "队列空位自动补位，长期挂机", "业务功能", True),
    ("⑧ 一体化", "all", "先领取，再进入升级循环", "业务功能", True),
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

    # 性能档位状态：启动时读一次 config.json，切换档位时同步更新（避免每轮轮询都读盘）
    def _perf_mode():
        p = (load_cfg() or {}).get("performance")
        m = str((p or {}).get("mode") or "eco").strip().lower()
        return m if m in PERF_LABEL_BY_MODE else "eco"

    perf_state = {"mode": _perf_mode(), "poll_ms": 500}
    perf_state["poll_ms"] = PERF_POLL_MS[perf_state["mode"]]

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

    # ---------- 设备：所有在线设备合并展示（不再区分真机/模拟器）----------
    row1 = tk.Frame(devbox, bg=BG_PANEL)
    row1.pack(fill="x", padx=12, pady=(10, 4))

    tk.Label(row1, text="设备", bg=BG_PANEL, fg=FG, font=FONT_SM).pack(side="left")
    dev_var = tk.StringVar(value=DEVICE_AUTO_LABEL)
    dev_cb = ttk.Combobox(row1, textvariable=dev_var, width=44, state="readonly",
                          values=[DEVICE_AUTO_LABEL], font=FONT_SM)
    dev_cb.pack(side="left", padx=(6, 10))
    dev_info = tk.Label(row1, text="", bg=BG_PANEL, fg=FG_DIM, font=FONT_SM)
    dev_info.pack(side="left")

    dev_rows = {"all": []}
    dev_map = {DEVICE_AUTO_LABEL: ""}
    cur_dev = {"row": None}

    def _set_current(d, note=""):
        cur_dev["row"] = d
        if d:
            cur_label.configure(text=f"当前设备：{format_device_info(d)}{note}", fg=OK)
        else:
            cur_label.configure(text="当前设备：自动选择第一个在线设备", fg=WARN)

    def _save_serial(serial, label=None):
        cfg = load_cfg()
        if not isinstance(cfg.get("device"), dict):
            cfg["device"] = {}
        cfg["device"].pop("mode", None)
        cfg["device"]["serial"] = serial
        if not save_cfg(cfg):
            messagebox.showerror("失败", "config.json 写入失败，可能被占用。")
            return False
        append_log(f"[配置] 设备已设为 {label or serial or '自动选择'}\n", "ok")
        return True

    def _refresh_devices(*_):
        rows, err = scan_adb_devices()
        if err:
            dev_info.configure(text=err)
            append_log(f"[设备] 扫描失败：{err}\n", "warn")
            return
        dev_rows["all"] = rows
        online = [d for d in rows if d["online"]]
        dev_map.clear()
        dev_map[DEVICE_AUTO_LABEL] = ""
        labels = [DEVICE_AUTO_LABEL]
        for d in online:
            label = f"{d['serial']} · {d.get('resolution') or '未知'}"
            if d.get("model"):
                label += f" · {d['model']}"
            dev_map[label] = d["serial"]
            labels.append(label)
        dev_cb["values"] = labels
        cur = str((load_cfg().get("device", {}) or {}).get("serial") or "").strip()
        dev_var.set(next((l for l, s in dev_map.items() if s == cur), DEVICE_AUTO_LABEL))
        _set_current(next((d for d in rows if d["serial"] == cur), None) if cur else None)
        online_label.configure(text=compact_online_text(rows))
        ignored = len(rows) - len(online)
        dev_info.configure(text=f"共 {len(rows)} 台 · 在线 {len(online)} 台"
                           + (f" · 忽略 {ignored} 台离线/未授权" if ignored else ""))
        append_log(f"[设备] 扫描完成：在线 {len(online)} 台，已合并展示\n", "info")

    def _on_device_change(*_):
        label = dev_var.get()
        serial = dev_map.get(label, "")
        row = next((d for d in dev_rows["all"] if d["serial"] == serial), None) if serial else None
        if _save_serial(serial, label):
            _set_current(row)
            dev_info.configure(text=f"已选：{label}")

    def _capture_selected():
        label = dev_var.get()
        serial = dev_map.get(label, "")
        rows = dev_rows["all"] or scan_adb_devices()[0]
        dev, err = (pick_device(rows, serial=serial) if serial else pick_device(rows))
        if not dev:
            messagebox.showwarning("提示", err or "没有在线设备，请先点「刷新扫描」。")
            return
        _save_serial(dev["serial"], dev["serial"])
        out = os.path.join(BASE_DIR, "shot.png")
        dev_info.configure(text=f"正在截图 {dev['serial']} …")
        root.update_idletasks()
        ok, msg, _ = capture_device_shot(dev["serial"], out)
        if not ok:
            dev_info.configure(text=f"截图失败：{msg}")
            append_log(f"[设备] 截图失败 → {format_device_info(dev)}：{msg}\n", "err")
            messagebox.showwarning("截图失败", f"{format_device_info(dev)}\n{msg}")
            return
        dev_var.set(next((l for l, s in dev_map.items() if s == dev["serial"]), DEVICE_AUTO_LABEL))
        _set_current(dev, " · 已截图")
        dev_info.configure(text=f"截图完成：{msg}")
        append_log(f"[设备] 截图完成 → {format_device_info(dev)} ｜ {msg} ｜ 文件 {out}\n", "ok")
        set_status(f"当前设备：{dev['serial']}｜{msg}", OK)
        root.after(150, load_preview)

    dev_cb.bind("<<ComboboxSelected>>", _on_device_change)
    tk.Button(row1, text="截图所选", command=_capture_selected,
              bg=BG_LOG, fg=ACCENT, activebackground=BG_LOG, activeforeground=ACCENT,
              relief="flat", font=FONT_SM, padx=10, pady=3, cursor="hand2").pack(side="right")
    tk.Button(row1, text="刷新扫描", command=_refresh_devices,
              bg=BG_LOG, fg=FG_DIM, activebackground=BG_LOG, activeforeground=FG,
              relief="flat", font=FONT_SM, padx=10, pady=3, cursor="hand2").pack(side="right", padx=(0, 6))
    tk.Button(row1, text="打开目录", command=lambda: _open_dir(BASE_DIR),
              bg=BG_LOG, fg=FG_DIM, activebackground=BG_LOG, activeforeground=FG,
              relief="flat", font=FONT_SM, padx=10, pady=3, cursor="hand2").pack(side="right")
    tk.Button(row1, text="打开日志目录", command=lambda: _open_dir(LOG_DIR),
              bg=BG_LOG, fg=FG_DIM, activebackground=BG_LOG, activeforeground=FG,
              relief="flat", font=FONT_SM, padx=10, pady=3, cursor="hand2").pack(side="right", padx=(0, 6))

    row2 = tk.Frame(devbox, bg=BG_PANEL)
    row2.pack(fill="x", padx=12, pady=(0, 4))
    cur_label = tk.Label(row2, text="当前设备：自动选择第一个在线设备",
                         bg=BG_PANEL, fg=WARN, font=FONT_SM)
    cur_label.pack(side="left")

    row3 = tk.Frame(devbox, bg=BG_PANEL)
    row3.pack(fill="x", padx=12, pady=(0, 10))
    online_label = tk.Label(row3, text=compact_online_text([]), bg=BG_PANEL, fg=FG_DIM,
                            font=FONT_SM, wraplength=w - 80, justify="left", anchor="w")
    online_label.pack(fill="x")

    prow = tk.Frame(devbox, bg=BG_PANEL)
    prow.pack(fill="x", padx=12, pady=(0, 10))

    tk.Label(prow, text="速本方案", bg=BG_PANEL, fg=FG, font=FONT_SM).pack(side="left")
    prof_state = {"data": load_profiles()}
    prof_names = [PROFILE_OFF] + list(prof_state["data"].get("profiles", {}).keys())
    prof_var = tk.StringVar(value=PROFILE_OFF)
    prof_cb = ttk.Combobox(prow, textvariable=prof_var, width=16, state="readonly",
                           values=prof_names, font=FONT_SM)
    prof_cb.pack(side="left", padx=(6, 10))
    prof_info = tk.Label(prow, text="", bg=BG_PANEL, fg=FG_DIM, font=FONT_SM)
    prof_info.pack(side="left")

    def _prof_summary(name):
        p = prof_state["data"].get("profiles", {}).get(name) or {}
        rows = p.get("build_order") or []
        if not rows:
            return "（空方案）"
        tops = [str(r.get("target_level") or "不限") if isinstance(r, dict) else "不限"
                for r in rows[:3]]
        return f"{len(rows)} 项 · 目标 " + "/".join(tops) + ("…" if len(rows) > 3 else "")

    def _refresh_prof(*_):
        name = prof_var.get()
        prof_info.configure(
            text="按原逻辑补位（未启用速本方案）"
            if name == PROFILE_OFF or not prof_state["data"].get("profiles", {}).get(name)
            else _prof_summary(name) + " 级 · 按清单顺序")

    def _reload_profiles():
        prof_state["data"] = load_profiles()
        names = [PROFILE_OFF] + list(prof_state["data"].get("profiles", {}).keys())
        prof_cb["values"] = names
        cur = str(prof_state["data"].get("active") or "").strip()
        prof_var.set(cur if cur in names else PROFILE_OFF)
        _refresh_prof()

    def _on_profile_change(*_):
        name = prof_var.get()
        data = load_profiles()          # 重读，避免覆盖用户刚在编辑器里改好的清单
        data["active"] = "" if name == PROFILE_OFF else name
        if save_profiles(data):
            append_log(f"[配置] 速本方案已切换为 {name}\n", "ok")
        else:
            messagebox.showerror("失败", "profiles.json 写入失败，可能被占用。")
        prof_state["data"] = data
        _refresh_prof()

    def _edit_profiles():
        if not os.path.isfile(PROFILES_PATH):
            tpl = os.path.join(BASE_DIR, "profiles.example.json")
            try:
                with open(tpl, "r", encoding="utf-8") as f:
                    save_profiles(json.load(f))
            except Exception:
                save_profiles({"active": "", "profiles": {}})
        try:
            os.startfile(PROFILES_PATH)
        except Exception as e:
            messagebox.showerror("打不开", f"{PROFILES_PATH}\n{e}")

    prof_cb.bind("<<ComboboxSelected>>", _on_profile_change)
    tk.Button(prow, text="编辑方案", command=_edit_profiles,
              bg=BG_LOG, fg=ACCENT, activebackground=BG_LOG, activeforeground=ACCENT,
              relief="flat", font=FONT_SM, padx=10, pady=3, cursor="hand2").pack(side="right")
    tk.Button(prow, text="刷新", command=_reload_profiles,
              bg=BG_LOG, fg=FG_DIM, activebackground=BG_LOG, activeforeground=FG,
              relief="flat", font=FONT_SM, padx=10, pady=3, cursor="hand2").pack(side="right", padx=(0, 6))
    _reload_profiles()

    # ---------- 性能档位：挂机发热 / CPU 占用控制 ----------
    perrow = tk.Frame(devbox, bg=BG_PANEL)
    perrow.pack(fill="x", padx=12, pady=(0, 10))

    tk.Label(perrow, text="性能档位", bg=BG_PANEL, fg=FG, font=FONT_SM).pack(side="left")
    perf_var = tk.StringVar(value=PERF_LABEL_BY_MODE[perf_state["mode"]])
    perf_cb = ttk.Combobox(perrow, textvariable=perf_var, width=15, state="readonly",
                           values=[t for t, _ in PERF_CHOICES], font=FONT_SM)
    perf_cb.pack(side="left", padx=(6, 10))
    perf_info = tk.Label(perrow, text=PERF_DESC[perf_state["mode"]],
                         bg=BG_PANEL, fg=FG_DIM, font=FONT_SM)
    perf_info.pack(side="left")

    def _on_perf_change(*_):
        label = perf_var.get()
        mode = next((m for t, m in PERF_CHOICES if t == label), "eco")
        cfg = load_cfg()
        # config.json 缺 performance 段或该段被写坏时都能安全补上；
        # 文件不存在也没关系，主脚本 load_config() 会用默认配置合并补全。
        if not isinstance(cfg.get("performance"), dict):
            cfg["performance"] = {}
        cfg["performance"]["mode"] = mode
        if not save_cfg(cfg):
            messagebox.showerror("失败", "config.json 写入失败，可能被占用。")
            return
        perf_state["mode"] = mode
        perf_state["poll_ms"] = PERF_POLL_MS[mode]
        perf_info.configure(text=PERF_DESC[mode])
        append_log(f"[配置] 性能档位已切换为 {label}；已启动的挂机需重启后生效\n", "ok")

    perf_cb.bind("<<ComboboxSelected>>", _on_perf_change)

    # ========== 底部实时升级倒计时条（两条并排，始终可见） ==========
    # 数据来自 state.json 的 upgrade_etas（升级循环每轮写入），GUI 定时读取并按
    # at+sec 本地逐秒递减，脚本静默等待期间倒计时也能实时走动。
    eta_bar = tk.Frame(root, bg=BG_PANEL, highlightthickness=1,
                       highlightbackground="#333b45")
    # 先 pack 的贴底、后 pack 的 status 会排在它下面 → 倒计时条位于状态栏上方。
    eta_bar.pack(fill="x", side="bottom")
    tk.Label(eta_bar, text="升级倒计时", bg=BG_PANEL, fg=FG_DIM,
             font=FONT_SM, width=10, anchor="w").pack(side="left", padx=(12, 4), pady=4)
    eta_slots = []
    for _ in range(2):
        _f = tk.Frame(eta_bar, bg=BG_LOG, padx=8, pady=3)
        _f.pack(side="left", fill="x", expand=True, padx=(4, 4), pady=4)
        _name = tk.Label(_f, text="—", bg=BG_LOG, fg=FG_DIM,
                         font=("Microsoft YaHei UI", 9), anchor="w")
        _name.pack(side="left")
        _time = tk.Label(_f, text="--:--:--", bg=BG_LOG, fg=FG_DIM,
                         font=("Consolas", 12, "bold"), anchor="e")
        _time.pack(side="right")
        eta_slots.append((_name, _time))
    eta_dot = tk.Label(eta_bar, text="●", bg=BG_PANEL, fg="#5a636f",
                       font=("Consolas", 9))
    eta_dot.pack(side="right", padx=(4, 12))

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

    # 左侧功能区可上下滚动：低分辨率/窄窗口下底部「控制」按钮不再被裁掉
    left_outer = tk.Frame(body, bg=BG, width=300)
    left_outer.pack(side="left", fill="y")
    left_outer.pack_propagate(False)
    left_canvas = tk.Canvas(left_outer, bg=BG, highlightthickness=0, width=282)
    left_scroll = tk.Scrollbar(left_outer, orient="vertical",
                               command=left_canvas.yview,
                               bg=BG_PANEL, troughcolor=BG, width=10)
    left_canvas.pack(side="left", fill="both", expand=True)
    left_scroll.pack(side="right", fill="y")
    left_canvas.configure(yscrollcommand=left_scroll.set)
    left = tk.Frame(left_canvas, bg=BG)
    left_win = left_canvas.create_window((0, 0), window=left, anchor="nw")
    left.bind("<Configure>",
              lambda e: left_canvas.configure(scrollregion=left_canvas.bbox("all")))
    left_canvas.bind("<Configure>",
                     lambda e: left_canvas.itemconfigure(left_win, width=e.width))

    def _left_wheel(event):
        left_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def _bind_wheel(widget):
        widget.bind("<MouseWheel>", _left_wheel)
        for child in widget.winfo_children():
            _bind_wheel(child)

    left.after(120, lambda: _bind_wheel(left))

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
        cur = str(d.get("serial") or "").strip()
        dev_var.set(next((l for l, s in dev_map.items() if s == cur), DEVICE_AUTO_LABEL))
        _set_current(next((x for x in dev_rows["all"] if x["serial"] == cur), None))

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
        log_file["path"] = daily_log_path(LOG_DIR, cmd)
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
        env["_TESTER_PARENT_PID"] = str(os.getpid())   # 子进程据此监测 GUI 是否还活着

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
                    time.sleep(max(0.05, perf_state["poll_ms"] / 1000.0))
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

    # ==================== 底部实时升级倒计时 ====================
    def _refresh_etas():
        """读 state.json 的 upgrade_etas，按写入时刻 + 秒数本地逐秒递减。
        脚本静默等待时不写文件，GUI 自己走时；脚本每轮更新会重置基准。"""
        try:
            with open(os.path.join(BASE_DIR, "state.json"), encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            d = {}
        slots = d.get("upgrade_etas") if isinstance(d, dict) else None
        if not isinstance(slots, list):
            slots = []
        updated_at = d.get("upgrade_at") if isinstance(d, dict) else None
        now = time.time()
        any_active = False
        for i, (_name, _time) in enumerate(eta_slots):
            if i >= len(slots):
                _name.configure(text="—")
                _time.configure(text="--:--:--", fg=FG_DIM)
                continue
            slot = slots[i] if isinstance(slots[i], dict) else {}
            name = str(slot.get("name") or "").strip()
            _name.configure(text=name[:12] if name else f"队列 {i + 1}")
            text, active = eta_display(slot, now=now, updated_at=updated_at)
            any_active = any_active or active
            _time.configure(text=text, fg=(OK if active else FG_DIM))
        eta_dot.configure(fg=OK if any_active else "#5a636f")
        root.after(1000, _refresh_etas)

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
                    if cmd in ("shot", "scan", "icons"):
                        root.after(150, load_preview)
                    if on_done:
                        on_done()
        except queue.Empty:
            pass
        # 运行中刷新计时
        if running["flag"]:
            elapsed_label.configure(text=f"{time.time() - start_ts['t']:.1f}s")
        root.after(perf_state["poll_ms"], pump)

    # ==================== 截图预览 ====================
    def load_preview():
        try:
            from PIL import Image, ImageTk
        except Exception:
            return
        cand = []
        for name in ("shot.png", "scan_标注.png", "icons_标注.png"):
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
        append_log(f"已加载配置：设备 = {d.get('serial') or '自动选择'}"
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

    def on_close():
        """关窗口时主动结束挂机子进程（以前只靠「停止」按钮，直接点 X 会残留后台进程）。"""
        p = proc_holder.get("proc")
        if p and p.poll() is None:
            append_log("\n[测试仪] 窗口关闭，正在结束后台挂机进程…\n", "warn")
            stop_task()
            try:
                p.wait(timeout=5)
            except Exception:
                pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(400, _refresh_devices)   # 窗口先显示，再查在线设备（避免拖慢启动）
    root.after(perf_state["poll_ms"], pump)
    root.after(600, load_preview)   # 目录里有上次的截图就先显示出来
    root.after(300, _refresh_etas)  # 底部两条实时升级倒计时（每秒本地走时）
    root.mainloop()


if __name__ == "__main__":
    run_gui()

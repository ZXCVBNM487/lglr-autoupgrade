# -*- coding: utf-8 -*-
"""
adb_diag.py —— ADB 连接诊断模块（只读、可取消、不影响现有连接）
================================================================
诊断项：
  1. 系统中所有 adb.exe 进程（多版本兼容），仅对 5037 端口的属主做设备枚举，
     其余 adb 只测本地 version，避免不同版本 adb 互相 kill-server
  2. ADB 服务状态：5037 端口监听属主 / 版本 / devices 响应
  3. 设备实例遍历：serial、状态(device/offline/unauthorized)、model、分辨率
  4. 模拟器 ADB 端口占用探测（纯 TCP connect，不发 adb connect，零副作用）
  5. 基础命令响应：echo、getprop、wm size（强制指定 -s serial）
输出结构化报告 AdbReport，可渲染成文本/界面。
"""

import os
import re
import time
import socket
import subprocess
import threading

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# 主流模拟器 ADB 端口（MuMuFever/MuMu12:16384, 老MuMu:7555, 雷电:5555/5557,
# 夜神:62001, 逍遥:21503, 蓝叠:5555, MuMu 多实例 16384+32*n）
EMU_PORTS = {
    16384: "MuMu12 / 网易发烧游戏(默认)",
    16416: "MuMu12 多开实例1",
    16448: "MuMu12 多开实例2",
    5555:  "雷电 / 蓝叠 / 通用模拟器",
    5557:  "雷电 实例2",
    5559:  "雷电 实例3",
    7555:  "MuMu6 / 老版MuMu",
    62001: "夜神 Nox(默认)",
    62025: "夜神 Nox 实例2",
    21503: "逍遥 MEmu(默认)",
    21513: "逍遥 MEmu 实例2",
    5037:  "ADB Server 端口",
}


def _run(args, timeout=8):
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout,
                           creationflags=CREATE_NO_WINDOW)
        return (r.returncode,
                (r.stdout or b"").decode("utf-8", "replace").strip(),
                (r.stderr or b"").decode("utf-8", "replace").strip())
    except subprocess.TimeoutExpired:
        return -1, "", "超时（>%ss）" % timeout
    except FileNotFoundError:
        return -2, "", "文件不存在"
    except Exception as e:
        return -3, "", str(e)


def _ps_adb_processes():
    """[(pid, exe_path, proc_name)] 所有运行中的 adb 相关进程"""
    ps = ("Get-CimInstance Win32_Process | Where-Object {$_.Name -match 'adb'} | "
          "Select-Object ProcessId, Name, ExecutablePath | ConvertTo-Json -Compress")
    code, out, err = _run(["powershell", "-NoProfile", "-Command", ps], timeout=15)
    if code != 0 or not out:
        return []
    try:
        import json
        data = json.loads(out)
        if isinstance(data, dict):
            data = [data]
        result = []
        for d in data:
            p = d.get("ExecutablePath") or ""
            if p and p.lower().endswith("adb.exe"):
                result.append((int(d.get("ProcessId", 0)), p, d.get("Name", "adb.exe")))
        return result
    except Exception:
        return []


def _port_owner(port):
    """端口监听属主 (pid, name)；未监听返回 (None, None)"""
    code, out, err = _run(["netstat", "-ano", "-p", "TCP"], timeout=10)
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0].upper() == "TCP" and parts[1].endswith(f":{port}"):
            state = parts[-2] if parts[-1].isdigit() else parts[-1]
            if state == "LISTENING":
                pid = int(parts[-1])
                return pid, _pid_name(pid)
    return None, None


def _pid_name(pid):
    code, out, err = _run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], timeout=8)
    m = re.match(r'"([^"]+)"', out)
    return m.group(1) if m else f"PID {pid}"


def _tcp_open(port, host="127.0.0.1", timeout=1.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        s.close()


class Item:
    def __init__(self, name, ok, detail, suggestion=""):
        self.name = name
        self.ok = ok          # True/False/None(信息项)
        self.detail = detail
        self.suggestion = suggestion

    def icon(self):
        return {True: "✅", False: "❌", None: "ℹ️"}.get(self.ok, "ℹ️")


class AdbReport:
    def __init__(self):
        self.sections = []     # [(标题, [Item...])]
        self.usable = []       # 可用设备路径信息 [(path, serial, model, size)]
        self.elapsed = 0.0

    def add(self, title, items):
        self.sections.append((title, items))

    def render_text(self):
        L = []
        L.append("=" * 56)
        L.append("  ADB 连接测试报告")
        L.append("=" * 56)
        for title, items in self.sections:
            L.append("")
            L.append("【%s】" % title)
            for it in items:
                L.append(f"  {it.icon()} {it.name}")
                if it.detail:
                    for dl in str(it.detail).splitlines():
                        L.append(f"      {dl}")
                if not it.ok and it.suggestion:
                    for sl in str(it.suggestion).splitlines():
                        L.append(f"      💡 {sl}")
        L.append("")
        L.append("-" * 56)
        if self.usable:
            L.append("✅ 可用连接（同一台设备的多个端口已合并）：")
            groups = {}
            for path, serial, model, size in self.usable:
                key = (path, model, size)
                groups.setdefault(key, []).append(serial)
            for (path, model, size), serials in groups.items():
                L.append(f"   adb : {path}")
                L.append(f"   型号 : {model}   分辨率: {size}")
                L.append(f"   端口 : {', '.join(serials)}（任选其一，推荐第一个）")
        else:
            L.append("❌ 没有任何可用的 ADB 设备实例")
        L.append("-" * 56)
        L.append(f"耗时 {self.elapsed:.1f}s")
        return "\n".join(L)


class AdbDiagnostic:
    """在后台线程运行 run()，cancel_event 可随时终止"""

    def __init__(self, on_progress=None):
        self.on_progress = on_progress or (lambda msg: None)
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()

    def _cancelled(self):
        if self.cancel_event.is_set():
            raise RuntimeError("用户终止")

    def run(self, prefer_path=None):
        t0 = time.time()
        rep = AdbReport()
        try:
            self._diag_processes(rep, prefer_path)
            self._cancelled()
            self._diag_server(rep)
            self._cancelled()
            canonical = self._diag_adbs(rep, prefer_path)
            self._cancelled()
            open_ports = self._diag_ports(rep)
            self._cancelled()
            self._diag_devices(rep, canonical, open_ports)
        except RuntimeError as e:
            rep.add("已终止", [Item("测试被用户终止", None, str(e))])
        rep.elapsed = time.time() - t0
        return rep

    # ---------- 1. 进程 ----------
    def _diag_processes(self, rep, prefer_path):
        self.on_progress("检查 adb.exe 进程…")
        procs = _ps_adb_processes()
        if not procs:
            rep.add("① 运行中的 adb.exe 进程", [Item(
                "未发现运行中的 adb.exe", False,
                "当前没有任何 adb 进程，说明模拟器 ADB 服务未启动或未连接过",
                "先启动模拟器（网易发烧游戏→开始游戏），等安卓完全开机后再测试")])
            return
        seen = {}
        for pid, path, name in procs:
            seen.setdefault(path, []).append(pid)
        items = []
        for path, pids in seen.items():
            tag = "（当前选用）" if prefer_path and os.path.normcase(path) == os.path.normcase(prefer_path) else ""
            items.append(Item(f"adb.exe {tag}".strip(), True,
                              f"{path}\n进程 PID: {', '.join(map(str, pids))}"))
        rep.add("① 运行中的 adb.exe 进程（共 %d 个路径）" % len(seen), items)

    # ---------- 2. 5037 服务 ----------
    def _diag_server(self, rep):
        self.on_progress("检查 5037 ADB 服务端口…")
        pid, name = _port_owner(5037)
        if pid:
            rep.add("② ADB 服务状态（端口 5037）", [Item(
                "ADB Server 运行中", True, f"监听进程：{name}（PID {pid}）")])
        else:
            rep.add("② ADB 服务状态（端口 5037）", [Item(
                "5037 端口无监听", False, "ADB Server 尚未启动（属正常，连接设备时会自动拉起）",
                "一般无需处理；若已开模拟器仍无监听，在助手里点「重启ADB服务」")])

    # ---------- 3. 各 adb.exe 版本（不碰设备，避免互杀 server） ----------
    def _diag_adbs(self, rep, prefer_path):
        self.on_progress("枚举 adb.exe 并检查版本…")
        procs = _ps_adb_processes()
        paths = []
        for pid, path, name in procs:
            if path not in paths:
                paths.append(path)
        # 首选路径优先
        if prefer_path and prefer_path not in paths:
            paths.insert(0, prefer_path)
        # adb_locator 兜底
        try:
            from adb_locator import find_adb_ex
            p, how = find_adb_ex()
            if p and p not in paths:
                paths.append(p)
        except Exception:
            pass

        if not paths:
            rep.add("③ adb.exe 实例", [Item("找不到任何 adb.exe", False, "",
                    "点助手里的「自动检测」，或手动选择模拟器目录 shell\\adb.exe")])
            return None

        versions = {}
        items = []
        for p in paths:
            self._cancelled()
            code, out, err = _run([p, "version"], timeout=8)
            if code == 0:
                ver = ""
                m = re.search(r"version\s+([\d.]+)", out)
                if m:
                    ver = m.group(1)
                versions[p] = ver
                items.append(Item(os.path.basename(os.path.dirname(p)) or p, True,
                                  f"{p}\n版本 {ver or '未知'}"))
            else:
                items.append(Item(p, False, f"无法执行：{err or out}",
                                  "文件损坏或权限不足，建议从模拟器安装目录取用 adb.exe"))
        rep.add("③ adb.exe 实例（%d 个）" % len(paths), items)

        # 选主 adb：首选路径优先，否则取第一个可执行的
        canonical = prefer_path if prefer_path in versions else None
        if not canonical:
            for pp in paths:
                if pp in versions:
                    canonical = pp
                    break
        return canonical

    # ---------- 4. 设备实例 ----------
    def _diag_devices(self, rep, adb_path, open_ports=()):
        self.on_progress("遍历 ADB 设备实例…")
        if not adb_path:
            rep.add("⑤ 设备实例", [Item("跳过", None, "没有可用的 adb.exe")])
            return

        # 对开放的模拟器端口做一次"只增不删"的 connect（固定同一个 adb.exe）
        bound = []
        if open_ports:
            self.on_progress("绑定开放的模拟器端口…")
            for port in open_ports:
                self._cancelled()
                code, out, err = _run([adb_path, "connect", f"127.0.0.1:{port}"], timeout=8)
                txt = (out + " " + err).lower()
                if "connected" in txt or "already" in txt:
                    bound.append(port)

        # 只读枚举设备
        code, out, err = _run([adb_path, "devices", "-l"], timeout=12)
        if code != 0:
            conflict = "version" in (err + out).lower() or "kill" in (err + out).lower()
            rep.add("⑤ 设备实例", [Item("adb devices 失败", False, err or out,
                    "检测到多版本 adb 冲突" if conflict else "确认模拟器已完全开机；或点「重启ADB服务」")])
            return

        lines = [ln for ln in out.splitlines() if ln.strip() and "List of" not in ln
                 and not ln.strip().startswith("*")]
        if not lines:
            extra = f"；已尝试绑定端口 {sorted(open_ports)}" if open_ports else ""
            rep.add("⑤ 设备实例", [Item("设备列表为空", False,
                    f"ADB 正常但没有已连接设备{extra}（模拟器 ADB 未就绪 / 调试开关未开）",
                    "1) 确认网易发烧游戏里安卓已完全开机  2) MuMu 设置中开启 ADB 调试  "
                    "3) 助手里点「扫描设备」  4) 仍失败则重启模拟器")])
            return

        items = []
        for ln in lines:
            self._cancelled()
            parts = ln.split()
            serial, status = parts[0], parts[1] if len(parts) > 1 else "unknown"
            meta = " ".join(parts[2:])
            model = ""
            m = re.search(r"model:(\S+)", meta)
            if m:
                model = m.group(1)
            if status == "device":
                # 基础命令响应测试（固定 -s serial）
                ok_echo, echo, _ = self._shell(adb_path, serial,
                                               ["echo", "lag_diag_ok"], timeout=8)
                ok_model, model_out, _ = self._shell(adb_path, serial,
                    ["getprop", "ro.product.model"], timeout=8)
                ok_size, size_out, _ = self._shell(adb_path, serial,
                    ["wm", "size"], timeout=8)
                size_m = re.search(r"(\d+)x(\d+)", size_out)
                if size_m:
                    a, b = int(size_m.group(1)), int(size_m.group(2))
                    size = f"{max(a,b)}x{min(a,b)}"
                else:
                    size = "未知"
                model = model_out or model or "未知"
                cmd_ok = ok_echo and "lag_diag_ok" in echo
                items.append(Item(f"{serial}", cmd_ok,
                    f"状态 device | 型号 {model} | 分辨率 {size}（已按横屏归一）\n"
                    f"基础命令响应：{'正常' if cmd_ok else '异常'}"))
                if cmd_ok:
                    rep.usable.append((adb_path, serial, model, size))
                if size_m:
                    a, b = int(size_m.group(1)), int(size_m.group(2))
                    if max(a, b) < 2000 or (max(a, b) / max(1, min(a, b))) < 2.0:
                        items[-1].ok = False
                        items[-1].suggestion = (
                            "分辨率不是游戏所需的 2310×1080 超宽屏，"
                            "请在 MuMu 设置→显示→自定义分辨率改为 2310×1080 并重启模拟器")
            elif status == "offline":
                items.append(Item(serial, False, "状态 offline（ADB 握手未完成）",
                    "多版本 adb 冲突或模拟器开机未完成：点「重启ADB服务」，或完全重启模拟器"))
            elif status == "unauthorized":
                items.append(Item(serial, False, "状态 unauthorized（未授权调试）",
                    "在模拟器/手机屏幕上点「允许 USB 调试」"))
            else:
                items.append(Item(serial, False, f"状态 {status}",
                    "模拟器开机中或 ADB 通道异常，请等待开机完成后重测"))
        rep.add("⑤ 设备实例（%d 个）" % len(lines), items)

    def _shell(self, adb, serial, args, timeout=8):
        code, out, err = _run([adb, "-s", serial, "shell", *args], timeout=timeout)
        return code == 0, out, err

    # ---------- 4. 端口占用 ----------
    def _diag_ports(self, rep):
        self.on_progress("探测模拟器 ADB 端口占用（只读 TCP 探测）…")
        from concurrent.futures import ThreadPoolExecutor
        ports = [p for p in EMU_PORTS if p != 5037]
        with ThreadPoolExecutor(max_workers=8) as ex:
            states = dict(zip(ports, ex.map(lambda p: _tcp_open(p, timeout=0.6), ports)))
        items = []
        opened_ports = []
        for port in ports:
            self._cancelled()
            if states[port]:
                pid, name = _port_owner(port)
                owner = f"{name}（PID {pid}）" if pid else "属主未知"
                items.append(Item(f"{port}  {EMU_PORTS[port]}", True, f"端口开放，占用：{owner}"))
                opened_ports.append(port)
            else:
                items.append(Item(f"{port}  {EMU_PORTS[port]}", None, "端口关闭"))
        closed = [it for it in items if it.ok is None]
        opened = [it for it in items if it.ok is True]
        show = opened + closed[:3]
        if not opened:
            rep.add("④ 模拟器端口占用检测", [Item("无开放端口", False,
                    "没有任何模拟器 ADB 端口开放 → 模拟器未启动或 ADB 未就绪",
                    "启动网易发烧游戏并进入安卓桌面后再测")])
        else:
            rep.add("④ 模拟器端口占用检测（开放 %d 个）" % len(opened), show)
        return opened_ports

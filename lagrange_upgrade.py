# -*- coding: utf-8 -*-
"""
星际猎人（无尽的拉格朗日）建筑自动升级脚本 · OCR + ADB

命令（打包后用 星际猎人测试仪.exe --cli <命令>）：
  check     只检测 ADB 连接与设备信息（GUI 启动自检用）
  status    一屏状态汇总（屏幕 / 前台 / 建造队列 / 资源），只读
  shot      截一张图 shot.png
  scan      全屏 OCR 文字坐标标注，生成 scan_标注.png
  icons     纯图标入口校准：检测注册表，生成 icons_标注.png / icons_活动区.png
  points    点位配置自检：读取 points.json，列出双入口与 17 个标注点
  times     识别屏幕上的倒计时
  mark      生成界面标注图
  tap X Y   测试点击某坐标
  daily     日常一键领取（任务 / 活动 / 邮件）
  autoup    一键升级自动循环：倒计时驱动、队列先到先补（挂机）
  all       一体化：日常领取 → 一键升级自动循环（挂机首选）
  genconfig 打印 config.json 路径

不带参数 = 打印本用法。所有配置在程序同目录的 config.json，首次运行自动生成。
"""

import sys
import os
import io
import re
import json
import time
import random
import base64
import difflib
import threading
import subprocess
import atexit

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np
try:
    import cv2
except Exception:
    cv2 = None
from PIL import Image, ImageDraw

# ==================== 控制台配色（借鉴三月七深色主题） ====================
# 深色背景 + 青色强调色 + 分级彩色日志。Windows 10+ 控制台支持 ANSI。
class C:
    """ANSI 颜色常量。用法：print(f"{C.OK}成功{C.R}")"""
    R  = "\033[0m"          # 重置
    B  = "\033[1m"          # 加粗
    D  = "\033[2m"          # 暗淡
    # 主色调（三月七风格：青蓝强调色）
    ACCENT = "\033[38;5;45m"      # 青蓝 - 标题/强调
    CYAN   = "\033[38;5;51m"      # 亮青
    INFO   = "\033[38;5;252m"     # 亮灰白 - 普通信息
    OK     = "\033[38;5;82m"      # 绿色 - 成功
    WARN   = "\033[38;5;220m"     # 琥珀黄 - 警告
    ERR    = "\033[38;5;203m"     # 珊瑚红 - 错误
    DIM    = "\033[38;5;244m"     # 暗灰 - 次要/时间戳
    HL     = "\033[38;5;231m"     # 纯白 - 高亮关键字
    PURPLE = "\033[38;5;183m"     # 淡紫 - 装饰

# 启用 Windows 控制台 ANSI 转义（Win10+）
if sys.platform == "win32":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass

BANNER = r"""{ACCENT}{B}
   __                    __                                   _     
  / /  ____ _____  ____ _/ /____  ____  ____ ___  ____ ____  (_)____
 / /  / __ `/ __ \/ __ `/ //_/ / / / / / __ `__ \/ __ `/ _ \/ / ___/
/ /__/ /_/ / / / / /_/ / ,< / /_/ / / / / / / / / /_/ /  __/ / /    
\____/\__,_/_/ /_/\__,_/_/|_|\__, / /_/ /_/ /_/ /_/\__, /\___/_/_/     
                            /____/                /____/             
{R}{DIM}无尽的拉格朗日（星际猎人）建筑自动升级脚本  ·  OCR + ADB{R}
"""


def log(msg, level="info"):
    """带颜色的分级日志：info/ok/warn/err"""
    ts = f"{C.DIM}[{time.strftime('%H:%M:%S')}]{C.R}"
    col = {"info": C.INFO, "ok": C.OK, "warn": C.WARN, "err": C.ERR}.get(level, C.INFO)
    print(f"{ts} {col}{msg}{C.R}")


def banner():
    print(BANNER.format(ACCENT=C.ACCENT, B=C.B, R=C.R, DIM=C.DIM))


# ==================== 默认配置（config.json 不存在时写入） ====================
# 坐标基于真机截图 2310x1080（超宽屏）。模拟器请设为相同/接近比例的自定义分辨率，
# 否则 UI 位置会错位；若必须用 1280x720，请重新跑 scan 校准。
DEFAULT_CONFIG = {
    "adb_path": "",                            # 留空自动寻找雷电/MuMu/夜神 adb

    # 设备：留空 = 自动挑第一个在线通道；填序列号（127.0.0.1:5555 / 真机序列号）
    # 则只连这一台。旧版 mode(auto/phone/emulator/serial) 已移除，
    # 改由测试仪「设备」下拉直接列出在线设备，选中即写回这里。
    "device": {
        "serial": ""
    },

    # 性能 / 发热控制：长时间挂机时降低 CPU 占用。
    #   eco      = 省电（默认）：OCR 限 2 线程 + 被动等待，每轮间歇 10 秒，卡住时指数退避到 90 秒
    #   balanced = 均衡：4 线程 / 5 秒 / 退避 45 秒
    #   turbo    = 原速：不限线程 / 不额外等待（排障时用，发热最高）
    # 也可在 performance 里单独覆盖 ocr_threads / loop_min_interval / backoff_max 等数值。
    "performance": {
        "mode": "eco"
    },

    # 一键升级自动循环：要自动升级的目标建筑名（OCR 模糊匹配，含该名的第一个设施即命中）。
    # 例如 "平台指挥中心" / "资源港口" / "战地指挥中心"。留空则队列有空位时升“当前可见第一个可升设施”。
    "au_target_building": "",                # 留空=升可见第一个可升设施；填建筑名则优先升它

    # 一键升级循环绝不自动升级的建筑名单（OCR 模糊匹配，含该名的设施一律跳过）。
    # 用于保护你想保级 / 有特殊用途的设施（如“居住区”“研究实验室”）。
    # 留空（默认）表示不锁定任何建筑。仅匹配「建筑名」，不影响资源/队列等其它逻辑。
    "au_locked_buildings": [],

    # 日常一键领取（活动面板签到/活跃挑战/各活动奖励 + 系统邮件附件）
    # 仅靠 OCR 识别“领取/领奖/签到”按钮点击，绝不会点“升级/购买/充值”，安全。
    "daily": {
        "enabled": True,                  # 是否允许 daily 命令
        "claim_tasks": True,              # 任务面板：主线/委托/支线/赏金/集结 逐页签「一键领取」
        "claim_activity": True,           # 活动面板：签到 + 活跃挑战领奖 + 各活动领取
        "claim_mail": False,              # 邮件附件领取（实测本游戏附件自动入库、无「领取」键，默认关）
        "mail_makeup": False,             # 是否自动“补签”（消耗补签卡，默认关，避免浪费）
        "mail_max": 8,                    # 邮件单分类最多处理多少封（防超长运行）
        "max_rounds": 10,                 # 单个面板内领取循环上限
        "auto_daily": True,               # 挂机时跨天自动补跑一次 daily（连续挂机不漏领）
        "auto_daily_hour": 4              # 每日几点之后视为「新的一天」（游戏 4 点重置日常）
    },

    # 礼包/购买类弹窗自动关闭（检测到关键词就点右上角 X）
    "popup": {
        "enabled": True,
        "close_box": [2080, 20, 2280, 110],  # 右上角 X 兜底区域（adb 实测 X≈(2183,62)；中心≈(2180,65)）
        "keywords": ["¥", "￥", "礼包", "特惠", "购买", "强化点"],
        "cooldown": 30                          # 两次关弹窗最小间隔（秒）
    },

    # QQ 机器人推送（OneBot v11 HTTP，适配 NapCat / Lagrange.OneBot / go-cqhttp）
    "qq": {
        "enabled": False,                      # 改成 true 开启推送
        "http_url": "http://127.0.0.1:3000",  # 机器人框架的 HTTP 服务地址
        "access_token": "",                    # 框架里配的 access_token，没有就留空
        "target_type": "group",                # group=发群里  private=发私聊
        "target_id": 0,                        # 群号 或 QQ号
        "at_qq": 0,                            # 群消息里要 @ 的QQ号，0=不@（重要事件才@）
        "screenshot": True,                    # 通知是否带现场截图（材料/出错等才有图）
        "cooldown": 300,                       # 同类普通通知最小间隔（秒），防刷屏
        # 通知级别：all=全发；error=只发【重要】类（材料不足/卡住/断连/报错）
        "level": "all",
        # 通知合并：true 时把多条普通通知攒起来，在升级点击/汇总等节点打包成一条
        "merge": False,
        # 通知类别（11 类），每类独立开关；重要类会 @ 你且不受节流限制
        "notify": {
            "start":        True,   # 🚀 脚本启动
            "upgrade_click": True,  # ✅ 每次点完「升级」（带截图）
            "long_upgrade": True,   # ⏳ 单次升级耗时很长（超过 long_wait_alert 秒）
            "insufficient": True,   # ⚠️【重】材料不足，已停止点击（带截图）
            "popup_closed": False,  # 🧹 自动关掉礼包弹窗（默认关，太琐碎）
            "lost":         True,   # ❓【重】连续多次找不到按钮（带截图）
            "disconnect":   True,   # 🔌【重】模拟器断开连接
            "exception":    True,   # ❗【重】脚本运行报错
            "heartbeat":    False,  # 💓 定时心跳播报（间隔 heartbeat_interval 秒）
            "daily_summary": True,  # 📊 跨天时发送前一天的运行汇总
            "stopped":      False   # 🛑 脚本被手动停止（窗口关闭可能收不到）
        },
        "heartbeat_interval": 3600,   # 心跳间隔（秒），3600=每小时一次
        "lost_threshold": 3,          # 连续多少次识别失败算“卡住”并报警
        "long_wait_alert": 14400      # 单次倒计时超过该秒数提醒（14400=4小时），0=关闭
    },

    # QQ 官方机器人（QQ开放平台，三月七同款；与 OneBot 二选一或同时开）
    # 申请：https://q.qq.com/ 创建机器人后获取 AppID / AppSecret
    # 绑定：在 GUI 设置里开启自动绑定，然后用 QQ 给机器人发一条消息（单聊）或在群里 @机器人
    "qq_official": {
        "enabled": False,
        "app_id": "",                 # 机器人 AppID
        "client_secret": "",          # 机器人 AppSecret
        "target_type": "group",       # group=群聊  c2c=单聊
        "user_openid": "",            # 单聊收件标识（自动绑定）
        "group_openid": "",           # 群聊收件标识（自动绑定）
        "member_openid": "",          # 最近发消息的群成员（自动记录）
        "c2c_event_id": "",           # 单聊主动推送凭证（自动更新，7天有效）
        "c2c_event_ts": 0,
        "group_event_id": "",         # 群聊主动推送凭证（自动更新，7天有效）
        "group_event_ts": 0
    },

    # 邮箱通知（SMTP，QQ/163/Gmail 等均可，图片作为附件）
    # 注意：password 填邮箱【授权码】不是登录密码。QQ 邮箱在 设置-账户-POP3/SMTP 里生成。
    "email": {
        "enabled": False,
        "host": "smtp.qq.com",          # QQ: smtp.qq.com  163: smtp.163.com  Gmail: smtp.gmail.com
        "port": 465,                    # QQ/163 SSL=465；Gmail=465；非 SSL 常用 25
        "user": "yourname@qq.com",      # 发件邮箱
        "password": "",                 # 邮箱授权码（不是登录密码）
        "from_addr": "",                # 发件人地址，留空=user
        "to_addr": "yourname@qq.com",   # 收件邮箱
        "ssl": True,                    # 是否 SSL（QQ/163/Gmail 都用 True）
        "starttls": False               # 是否 STARTTLS（一般 False）
    }
}

DESIGN_W, DESIGN_H = 2310, 1080

# 点击抖动（像素）：避免每次都点在同一像素上
CLICK_JITTER = 6


def app_dir():
    """exe 或脚本所在目录（config.json 放这里）"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def load_config():
    path = os.path.join(app_dir(), "config.json")
    if not os.path.isfile(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        print(f"已生成默认配置文件: {path}")
        print("请先用 scan 校准坐标，再修改此文件（QQ 推送也在这里开）")
        return dict(DEFAULT_CONFIG)
    try:
        with open(path, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
    except Exception as e:
        print(f"config.json 解析失败（{e}），使用默认配置")
        return dict(DEFAULT_CONFIG)
    # 合并：以默认配置为底，用户配置覆盖（嵌套字典递归合并）
    def deep_merge(base, user):
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                deep_merge(base[k], v)
            else:
                base[k] = v
        return base
    cfg = deep_merge(json.loads(json.dumps(DEFAULT_CONFIG)), user_cfg)
    return cfg


CONFIG = load_config()


# ==================== 性能 / 发热控制 ====================
# 实测：2310x1080 全屏 RapidOCR 一次约 6~9 秒，且 ONNXRuntime 默认吃满所有 CPU 核心，
# 推理间隙 OpenMP 工作线程还会自旋忙等。旧循环在界面读不懂时约 2.5 秒一轮不停
# 「截图 + 全屏 OCR」，长时间挂机 CPU 长期满载、温度飙升。
# 这里从三处降耗（都不改升级判定逻辑，只改「多久做一次」）：
#   ① 限制 OMP 线程并关闭自旋等待   ② 主循环最小间隔 + 无进展指数退避
#   ③ 亮屏/前台健康检查节流（WAKEUP 保活仍每轮发，避免手机自动息屏截到黑屏）
PERF_MODES = {
    "eco": {                       # 省电（默认）：长时间挂机推荐
        "ocr_threads": 2,
        "loop_min_interval": 10.0,
        "backoff_max": 90.0,
        "wake_every": 30.0,
        "health_every": 60.0,
        "gui_poll_ms": 500,
    },
    "balanced": {                 # 均衡：响应更快，温度适中
        "ocr_threads": 4,
        "loop_min_interval": 5.0,
        "backoff_max": 45.0,
        "wake_every": 15.0,
        "health_every": 30.0,
        "gui_poll_ms": 300,
    },
    "turbo": {                    # 原速：排障用，发热最高
        "ocr_threads": 0,        # 0 = 不设置，交给 onnxruntime 默认（吃满所有核心）
        "loop_min_interval": 0.0,
        "backoff_max": 0.0,
        "wake_every": 0.0,
        "health_every": 0.0,
        "gui_poll_ms": 120,
    },
}
PERF_LABELS = {"eco": "省电", "balanced": "均衡", "turbo": "原速"}


def perf_settings(config=None, mode=None):
    """解析性能档位，返回完整参数字典。非法档位/非法数值一律回退默认，绝不抛异常。"""
    cfg = config if isinstance(config, dict) else CONFIG
    p = cfg.get("performance") if isinstance(cfg, dict) else None
    if not isinstance(p, dict):
        p = {}
    m = str(mode if mode is not None else p.get("mode", "eco")).strip().lower()
    if m not in PERF_MODES:
        m = "eco"
    out = dict(PERF_MODES[m])
    out["mode"] = m
    # 允许配置里单独覆盖数值（只接受合法类型/范围）
    for key, lo, hi in (("ocr_threads", 0, 64), ("loop_min_interval", 0.0, 600.0),
                        ("backoff_max", 0.0, 3600.0), ("wake_every", 0.0, 3600.0),
                        ("health_every", 0.0, 3600.0), ("gui_poll_ms", 50, 5000)):
        v = p.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi:
            out[key] = int(v) if key in ("ocr_threads", "gui_poll_ms") else float(v)
    return out


def apply_ocr_env(threads):
    """必须在 import onnxruntime 之前调用：限制 OMP 线程数 + 关闭忙等自旋。"""
    if not threads or threads <= 0:
        return
    os.environ["OMP_NUM_THREADS"] = str(int(threads))
    os.environ["OMP_WAIT_POLICY"] = "PASSIVE"
    os.environ["OMP_DYNAMIC"] = "FALSE"


# 模块加载时立即设置，保证后续任何路径 import onnxruntime 都拿到省电参数
apply_ocr_env(perf_settings()["ocr_threads"])


def pace_interval(streak, base, cap, factor=1.7):
    """连续无进展轮次 → 本轮最小间隔（指数退避，上限 cap）。base<=0 表示不节流。"""
    if base <= 0:
        return 0.0
    n = max(0, int(streak))
    try:
        v = float(base) * (float(factor) ** n)
    except (OverflowError, ValueError):
        v = float(base)
    if cap and cap > 0:
        v = min(float(cap), v)
    return max(0.0, v)


def pace_sleep(elapsed, interval):
    """本轮已耗时 elapsed 秒时，还需补睡多少秒（不会为负数）。
    工具函数：主循环改用「每轮固定间歇」而非按总时长扣减——单次 OCR 就可能
    超过间隔，扣减后等于没有喘息、CPU 又会被占满。"""
    return max(0.0, float(interval) - float(elapsed))


def should_wake_check(now, last, every):
    """健康检查是否到点。every<=0 表示每轮都查；用单调时钟，不受系统改时间影响。"""
    if every <= 0:
        return True
    return (now - last) >= every


_AU_LAST_WAKE_CHECK = 0.0
_AU_LAST_HEALTH_CHECK = 0.0


def _au_wake_paced(adb, every):
    """亮屏保活：每轮只发一个无害的 WAKEUP（防止手机自动息屏截到黑屏），
    每隔 every 秒才做一次完整的『查屏状态 + 必要时上滑解锁』，省掉大量 dumpsys。"""
    global _AU_LAST_WAKE_CHECK
    now = time.monotonic()
    if should_wake_check(now, _AU_LAST_WAKE_CHECK, every):
        _AU_LAST_WAKE_CHECK = now
        _au_wake(adb)
    else:
        adb.run("shell", "input", "keyevent", "KEYCODE_WAKEUP")


def _au_health_paced(adb, qq, every):
    """前台/掉线自愈检查节流：返回值含义同 _au_ensure_game（True=本轮做过干预）。"""
    global _AU_LAST_HEALTH_CHECK
    now = time.monotonic()
    if not should_wake_check(now, _AU_LAST_HEALTH_CHECK, every):
        return False
    _AU_LAST_HEALTH_CHECK = now
    return _au_ensure_game(adb, qq)


# ==================== 速本方案：多套配置 / 有序建筑清单 / 目标等级 ====================
# 方案文件与 config.json 同目录（打包后为 exe 所在目录），独立保存，不含任何凭据。
# 结构：{"active": "方案名", "profiles": {"方案名": {
#          "note": "...", "strict": false, "locked_buildings": [],
#          "build_order": [{"name": "建筑名", "target_level": 8}, ...]}}}
PROFILES_FILE = "profiles.json"
PROFILE_OFF = "（不启用方案）"

DEFAULT_PROFILES = {
    "active": "速本",
    "profiles": {
        "速本": {
            "note": "示例：冲本路线，请按你们自己的清单改（build_order 越靠前越优先）",
            "strict": False,
            "locked_buildings": [],
            "build_order": [
                {"name": "平台指挥中心", "target_level": 8},
                {"name": "资源港口", "target_level": 10},
                {"name": "通用港口", "target_level": 10},
            ],
        },
        "补产能": {
            "note": "示例：资源优先阶段",
            "strict": False,
            "locked_buildings": [],
            "build_order": [
                {"name": "资源港口", "target_level": 12},
                {"name": "通用港口", "target_level": 12},
            ],
        },
    },
}


def _profiles_path(path=None):
    return path or os.path.join(app_dir(), PROFILES_FILE)


def load_profiles(path=None):
    """读取速本方案；文件不存在时自动生成示例模板，坏文件不拖垮主流程。"""
    p = _profiles_path(path)
    if not os.path.isfile(p):
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_PROFILES, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log(f"无法生成 {PROFILES_FILE}（{e}），使用内置示例", "warn")
        return json.loads(json.dumps(DEFAULT_PROFILES))
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("profiles"), dict):
            raise ValueError("profiles 字段缺失或格式不对")
        return data
    except Exception as e:
        log(f"{PROFILES_FILE} 解析失败（{e}），使用内置示例", "warn")
        return json.loads(json.dumps(DEFAULT_PROFILES))


def save_profiles(data, path=None):
    try:
        with open(_profiles_path(path), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def active_profile(path=None):
    """返回 (方案名, 方案内容)；未启用 / 方案不存在时返回 ('', {})。"""
    data = load_profiles(path)
    name = str(data.get("active") or "").strip()
    if not name or name == PROFILE_OFF:
        return "", {}
    prof = (data.get("profiles") or {}).get(name)
    return (name, prof) if isinstance(prof, dict) else ("", {})


def profile_targets(profile):
    """规整有序清单 -> [(建筑名, 目标等级)]；0 或留空 = 不限等级。"""
    out, seen = [], set()
    for row in (profile or {}).get("build_order", []) or []:
        if isinstance(row, str):
            name, lvl = row.strip(), 0
        elif isinstance(row, dict):
            name = str(row.get("name") or "").strip()
            try:
                lvl = int(row.get("target_level") or 0)
            except (TypeError, ValueError):
                lvl = 0
        else:
            continue
        if not name or name in seen:
            continue
        seen.add(name)
        out.append((name, max(0, lvl)))
    return out


def profile_locked(profile):
    """方案锁定名单 + config.json 里的 au_locked_buildings，合并去重保序。"""
    out = []
    for x in list((profile or {}).get("locked_buildings") or []) + list(CONFIG.get("au_locked_buildings") or []):
        s = str(x).strip()
        if s and s not in out:
            out.append(s)
    return out


_LEVEL_PATTERNS = (
    re.compile(r"(?:Lv|LV|lv|等级)\s*[.：:]?\s*(\d{1,3})"),
    re.compile(r"(\d{1,3})\s*级"),
)
_LEVEL_EXCLUDE = ("升至", "升到", "目标", "需要", "消耗", "下一级", "升级后", "满级")
_PROFILE_NON_NAME = ("建造队列", "资源", "全部", "队列", "金属", "原料", "核燃料",
                     "建造中", "升级中", "进行中", "使用中", "已暂停", "已满", "暂停")

_AU_REACHED = set()      # 进程内：已达目标等级的建筑，避免反复打开同一个面板


def parse_level_from_items(items):
    """从建筑详情面板 OCR 文本里读『当前等级』；读不到返回 None（保守：照常升级）。"""
    for _, _, t, _ in items or []:
        s = str(t).strip()
        if not s or any(k in s for k in _LEVEL_EXCLUDE):
            continue
        for pat in _LEVEL_PATTERNS:
            m = pat.search(s)
            if m:
                v = int(m.group(1))
                if 0 < v <= 200:
                    return v
    return None


def match_profile_building(items, profile):
    """当前详情面板属于清单里的哪个建筑；匹配不到返回 None（不启用等级判断）。"""
    texts = [str(t).strip() for _, _, t, _ in items or []]
    for name, lvl in profile_targets(profile):
        if any(name in t for t in texts):
            return name, lvl
    return None


def profile_pick_target(items, adb, profile, skipped=None, locked=None):
    """纯逻辑：在底栏可见建筑条目里，按速本清单顺序挑第一个命中的。
    返回 (cx, cy, 建筑名, 目标等级) 或 None；跳过锁定名单与已达标建筑。"""
    bottom = int(adb.h * _AU_BOTTOM_Y / DESIGN_H)
    _locked = [str(x).strip() for x in (locked or []) if str(x).strip()]
    _skip = set(skipped or [])
    names = []
    for cx, cy, t, sc in items or []:
        if cy <= bottom or sc < 0.4:
            continue
        ts = str(t).strip()
        if not ts or parse_seconds(ts):          # 倒计时不是建筑名
            continue
        if not (re.search(r"[\u4e00-\u9fff]", ts) and len(ts) >= 2):
            continue
        if ts in _PROFILE_NON_NAME or any(k in ts for k in _AU_NAV_WORDS) \
                or any(k in ts for k in _locked):
            continue
        names.append((cx, cy, ts))
    names.sort(key=lambda q: q[0])
    for name, lvl in profile_targets(profile):
        if name in _skip:
            continue
        for cx, cy, ts in names:
            if name in ts or ts in name:
                return (cx, cy, name, lvl)
    return None


# ==================== 通知系统：QQ + 邮箱 双渠道 ====================
# 更多渠道（Webhook/WinToast 等）见 notifier_extra_channels.py

class _BaseChannel:
    """通知渠道基类。子类实现 _do_send(title, content, img_b64)。"""
    name = "base"
    def __init__(self, cfg):
        self.cfg = cfg
        self.enabled = bool(cfg.get("enabled", False))

    def send(self, title, content, img_b64):
        if not self.enabled:
            return
        try:
            self._do_send(title, content, img_b64)
        except Exception as e:
            print(f"  [通知/{self.name}失败] {e}")

    def _do_send(self, title, content, img_b64):
        raise NotImplementedError


class QQChannel(_BaseChannel):
    """OneBot v11 HTTP（NapCat / Lagrange.OneBot / go-cqhttp），发群或私聊，支持 @ 和图片"""
    name = "qq"
    def __init__(self, cfg):
        super().__init__(cfg)
        self.url = cfg.get("http_url", "http://127.0.0.1:3000").rstrip("/")
        self.token = cfg.get("access_token", "")
        self.target_type = cfg.get("target_type", "group")
        self.target_id = int(cfg.get("target_id", 0) or 0)
        self.at_qq = int(cfg.get("at_qq", 0) or 0)

    def _post(self, endpoint, payload):
        import urllib.request
        url = f"{self.url}{endpoint}"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status

    def _do_send(self, title, content, img_b64, important=False):
        if not self.target_id:
            return
        message = []
        if important and self.at_qq and self.target_type == "group":
            message.append({"type": "at", "data": {"qq": str(self.at_qq)}})
        text = f"{title}\n{content}" if content else title
        message.append({"type": "text", "data": {"text": text}})
        if img_b64:
            message.append({"type": "image", "data": {"file": f"base64://{img_b64}"}})
        if self.target_type == "private":
            ep, idf = "/send_private_msg", "user_id"
        else:
            ep, idf = "/send_group_msg", "group_id"
        self._post(ep, {idf: self.target_id, "message": message})

    # 重写 send 传入 important 以决定是否 @
    def send(self, title, content, img_b64, important=False):
        if not self.enabled:
            return
        try:
            self._do_send(title, content, img_b64, important=important)
        except Exception as e:
            print(f"  [通知/qq失败] {e}")


class EmailChannel(_BaseChannel):
    """SMTP 邮箱通知（QQ/163/Gmail 等），图片作为附件。无需第三方依赖。"""
    name = "email"
    def __init__(self, cfg):
        super().__init__(cfg)
        self.host = cfg.get("host", "")
        self.port = int(cfg.get("port", 465))
        self.user = cfg.get("user", "")
        self.password = cfg.get("password", "")       # 邮箱授权码，不是登录密码
        self.sender = cfg.get("from_addr") or self.user
        self.receiver = cfg.get("to_addr", "")
        self.use_ssl = bool(cfg.get("ssl", True))
        self.use_starttls = bool(cfg.get("starttls", False))

    def _do_send(self, title, content, img_b64):
        if not (self.host and self.user and self.receiver):
            return
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from email.mime.base import MIMEBase
        from email import encoders

        msg = MIMEMultipart()
        msg["From"] = self.sender
        msg["To"] = self.receiver
        msg["Subject"] = title
        msg.attach(MIMEText(content or " ", "plain", "utf-8"))
        if img_b64:
            import base64 as _b64
            part = MIMEBase("image", "png")
            part.set_payload(_b64.b64decode(img_b64))
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename="screenshot.png")
            msg.attach(part)

        if self.use_ssl:
            server = smtplib.SMTP_SSL(self.host, self.port, timeout=15)
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=15)
            if self.use_starttls:
                server.starttls()
        try:
            server.login(self.user, self.password)
            server.sendmail(self.sender, [self.receiver], msg.as_string())
        finally:
            server.quit()


class QQOfficialChannel(_BaseChannel):
    """QQ 开放平台官方机器人（AppID/AppSecret，群/单聊，支持图片）—— 三月七同款"""
    name = "qq_official"
    def __init__(self, cfg):
        super().__init__(cfg)
        self._bot = None

    def _bot_get(self):
        if self._bot is None:
            from qq_official import QQOfficialBot
            self._bot = QQOfficialBot(self.cfg, log=lambda m: print(f"{C.DIM}  [QQ官方]{C.R} {m}"))
        return self._bot

    def send(self, title, content, img_b64, important=False):
        if not self.enabled:
            return
        try:
            bot = self._bot_get()
            tag = "⚠️【重要】\n" if important else ""
            text = f"{tag}{title}\n{content}" if content else f"{tag}{title}"
            ok, msg = bot.send(text, img_b64)
            if not ok:
                print(f"{C.DIM}  [通知/QQ官方失败]{C.R} {C.WARN}{msg}{C.R}")
        except Exception as e:
            print(f"{C.DIM}  [通知/QQ官方失败]{C.R} {C.WARN}{e}{C.R}")


class Notifier:
    """分发器：级别过滤 + 合并 + 多渠道并行发送"""
    IMPORTANT = {"insufficient", "lost", "disconnect", "exception"}
    ICONS = {
        "start": "🚀", "upgrade_click": "✅", "long_upgrade": "⏳",
        "insufficient": "⚠️", "popup_closed": "🧹", "lost": "❓",
        "disconnect": "🔌", "exception": "❗", "heartbeat": "💓",
        "daily_summary": "📊", "stopped": "🛑",
    }

    def __init__(self, cfg):
        qcfg = cfg.get("qq", {})
        self.level = qcfg.get("level", "all")   # all / error
        self.merge = bool(qcfg.get("merge", False))
        self.cooldown = int(qcfg.get("cooldown", 300))
        self.send_shot = bool(qcfg.get("screenshot", True))
        self.toggles = dict(qcfg.get("notify", {}))
        # 兼容旧字段
        legacy = {"notify_on_start": "start", "notify_on_upgrade": "upgrade_click",
                  "notify_on_insufficient": "insufficient", "notify_on_error": "lost"}
        for old, new in legacy.items():
            if old in qcfg and new not in self.toggles:
                self.toggles[new] = bool(qcfg[old])

        self.channels = [QQChannel(qcfg),
                         QQOfficialChannel(cfg.get("qq_official", {})),
                         EmailChannel(cfg.get("email", {}))]
        self._last_sent = {}
        self._buffer = []   # 合并模式下的待发消息

    def _title_of(self, category):
        return f"{self.ICONS.get(category, '🔔')} 【拉格朗日助手】"

    def push(self, category, body, img_bgr=None, force=False, with_shot=None):
        """按类别发通知。body 正文；img_bgr 现场截图；force 跳过节流。"""
        if not self.toggles.get(category, False):
            return
        important = category in self.IMPORTANT
        if self.level == "error" and not important:
            return
        now = time.time()
        if not force and not important:
            if now - self._last_sent.get(category, 0) < self.cooldown:
                return
        img_b64 = None
        want_shot = self.send_shot if with_shot is None else with_shot
        if want_shot and img_bgr is not None:
            buf = io.BytesIO()
            Image.fromarray(img_bgr).save(buf, format="PNG")
            img_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

        title = self._title_of(category)
        if self.merge and not important and not force:
            self._buffer.append(f"{title}\n{body}")
            return
        if self._buffer:
            merged = "\n\n".join(self._buffer) + f"\n\n{title}\n{body}"
            self._buffer.clear()
            self._fanout("📋 【拉格朗日助手】合并通知", merged, img_b64, important)
        else:
            self._fanout(title, body, img_b64, important)
        self._last_sent[category] = now

    def flush(self):
        if not self._buffer:
            return
        merged = "\n\n".join(self._buffer)
        self._buffer.clear()
        self._fanout("📋 【拉格朗日助手】合并通知", merged, None, False)

    def _fanout(self, title, content, img_b64, important):
        for ch in self.channels:
            if not ch.enabled:
                continue
            if ch.name in ("qq", "qq_official"):
                ch.send(title, content, img_b64, important=important)
            else:
                ch.send(title, content, img_b64)
        first = content.splitlines()[0] if content else ""
        col = C.WARN if important else C.CYAN
        print(f"{C.DIM}  [通知]{C.R} {col}{first[:40]}{C.R}")

    # 旧代码兼容
    def send(self, text, img_bgr=None, tag="default", force=False):
        self.push(tag, text, img_bgr=img_bgr, force=force)


# ==================== ADB ====================
def find_adb():
    """智能定位 adb.exe（详见 adb_locator.py）"""
    from adb_locator import find_adb as _locate
    return _locate(CONFIG)


# 局域网 / 网络 ADB 设备（IP:端口）。本机回环 127.0.0.1:* 由端口扫描负责，不在此列。
_NET_ADB_ADDR_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}:\d{2,5}$")


def is_network_adb_addr(addr):
    """是否是需要主动 `adb connect` 的网络设备地址（非本机回环）。"""
    a = str(addr or "").strip()
    return bool(_NET_ADB_ADDR_RE.match(a)) and not a.startswith("127.0.0.1:")


def adb_reconnect_addrs(cfg=None, state=None, open_ports=()):
    """构造掉线后要主动重连的地址，按优先级去重：

    ① config.device.serial 指定的网络设备 ② 上次成功连接的网络设备
    ③ 本机正在监听的模拟器端口（127.0.0.1:*）。

    纯函数，便于无设备单测。实机教训：MuMu / WiFi ADB 通道会掉线，
    旧 scan_and_connect 只会连 127.0.0.1 端口，局域网设备一掉就再也连不回来。
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    state = state if isinstance(state, dict) else {}
    out = []
    want = str((cfg.get("device", {}) or {}).get("serial") or "").strip()
    last = str(state.get("last_device") or "").strip()
    for a in (want, last):
        if is_network_adb_addr(a) and a not in out:
            out.append(a)
    for p in open_ports or ():
        try:
            a = f"127.0.0.1:{int(p)}"
        except (TypeError, ValueError):
            continue
        if a not in out:
            out.append(a)
    return out


class Adb:
    # 主流模拟器 ADB 端口（覆盖雷电/MuMu/夜神/逍遥/蓝叠/腾讯）
    KNOWN_PORTS = (16384, 5555, 7555, 62001, 21503, 5556, 5557, 5559,
                   5554, 5558, 5560, 5561, 5562, 5563, 5564, 5565,
                   21503, 21513, 21523, 21533, 21543, 21553,
                   7401, 7402, 7403, 7404, 7405,        # 夜神多开
                   0)                                    # 0 = 哨兵，跳过
    # 注意：5037 是 adb server 自己的控制端口，不是设备端口；
    # 以前误把它写进列表，会连出一个 offline 假设备并拖慢启动。

    def __init__(self, auto_connect=True):
        self.path = find_adb()
        if not self.path:
            print(f"{C.ERR}找不到 adb.exe！{C.R}")
            print(f"{C.DIM}已尝试：已知模拟器路径 → 系统 PATH → 进程探测 → 全盘扫描{C.R}")
            print(f"{C.DIM}请把模拟器的 adb.exe 完整路径填到 config.json 的 adb_path{C.R}")
            print(f"{C.DIM}  雷电: 安装目录\\adb.exe    MuMu12: 安装目录\\shell\\adb.exe{C.R}")
            sys.exit(1)
        self.device = None
        self.w, self.h = DESIGN_W, DESIGN_H
        self._server_reset_done = False   # kill-server 自愈只做一次
        # 掉线重连候选：配置里指定的网络设备 + 上次成功连上的网络设备。
        # 只放实例属性，bare Adb（单测用 object.__new__ 造的）不会有它，行为保持不变。
        self._net_addrs = list(dict.fromkeys(
            a for a in (
                str((CONFIG.get("device", {}) or {}).get("serial") or "").strip(),
                str((_load_state() or {}).get("last_device") or "").strip(),
            ) if is_network_adb_addr(a)))
        if auto_connect:
            self.device = self._pick_device()
            self.w, self.h = self._get_resolution()
            if is_network_adb_addr(self.device):
                st = _load_state()
                if st.get("last_device") != self.device:
                    st["last_device"] = self.device
                    _save_state(st)
            print(f"{C.ACCENT}▸ ADB{C.R}      {C.DIM}{self.path}{C.R}")
            print(f"{C.ACCENT}▸ 设备{C.R}     {C.HL}{self.device}{C.R}    {C.DIM}分辨率: {self.w}x{self.h}{C.R}")

    def _reset_server(self):
        """多版本 adb 冲突自愈：kill-server 后重连"""
        if self._server_reset_done:
            return False
        self._server_reset_done = True
        print(f"{C.WARN}[ADB] 检测到 adb server 冲突，正在重启 adb server...{C.R}")
        try:
            self.raw("kill-server", timeout=10)
            time.sleep(1)
            self.raw("start-server", timeout=15)
            time.sleep(1)
        except Exception:
            pass
        return True

    def run(self, *args, timeout=10):
        r = subprocess.run([self.path, "-s", self.device, *args],
                           capture_output=True, timeout=timeout)
        # protocol fault / device not found → server 冲突，重启后重试一次
        err = (r.stderr or b"").decode("utf-8", "replace")
        if ("protocol fault" in err or "not found" in err) and self._reset_server():
            self.device = self._pick_device(fatal=False) or self.device
            r = subprocess.run([self.path, "-s", self.device, *args],
                               capture_output=True, timeout=timeout)
        return r

    def raw(self, *args, timeout=10):
        """不指定 -s 设备的原始 adb 命令（用于 devices / connect）"""
        return subprocess.run([self.path, *args],
                              capture_output=True, timeout=timeout)

    def list_devices(self):
        """返回已连接的设备 ID 列表（含状态）"""
        try:
            out = self.raw("devices", timeout=8).stdout.decode("utf-8", "replace")
        except Exception:
            return []
        result = []
        for line in out.splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                result.append((parts[0], parts[1]))   # (device_id, status)
        return result

    def connect(self, addr):
        """连接指定 IP:端口，返回 (success, message)"""
        try:
            r = self.raw("connect", addr, timeout=3)
            msg = r.stdout.decode("utf-8", "replace").strip()
            return ("connected" in msg.lower() or "already" in msg.lower()), msg
        except Exception as e:
            return False, str(e)

    # 没设备时也必须在几秒内给出结论：先用本机 TCP 探测筛掉没在监听的端口，
    # 再只对存活端口发 adb connect，并给整轮扫描设总时间预算。
    # 旧实现盲连 29 个端口 × 每个最多等 8 秒，设备掉线时启动能卡好几分钟。
    _SCAN_BUDGET = 6.0

    @staticmethod
    def _port_open(port, timeout=0.2):
        """本机 TCP 端口是否在监听（比 adb connect 快得多，失败立刻返回）。"""
        import socket
        s = socket.socket()
        s.settimeout(timeout)
        try:
            return s.connect_ex(("127.0.0.1", int(port))) == 0
        except Exception:
            return False
        finally:
            s.close()

    def scan_and_connect(self, budget=None):
        """扫描已知端口并连接。返回连接上的设备列表，整轮不超过 budget 秒。

        网络设备（IP:端口，如 WiFi ADB / 桥接模拟器）不会出现在 127.0.0.1 端口表里，
        通道一掉就再也连不回来；所以把 __init__ 记下的网络地址排在候选最前面。
        """
        budget = self._SCAN_BUDGET if budget is None else float(budget)
        deadline = time.monotonic() + budget
        open_ports = []
        for port in dict.fromkeys(self.KNOWN_PORTS):   # 去重（21503 写了两次）
            if port == 0:
                continue
            if time.monotonic() >= deadline:
                print(f"{C.WARN}[ADB] 端口扫描已达 {budget:g}s 预算，停止扫描{C.R}")
                break
            if self._port_open(port):
                open_ports.append(port)
        addrs = list(dict.fromkeys(
            list(getattr(self, "_net_addrs", ()) or ())
            + adb_reconnect_addrs(open_ports=open_ports)))
        for addr in addrs:
            if time.monotonic() >= deadline:
                print(f"{C.WARN}[ADB] 连接已达 {budget:g}s 预算，停止重连{C.R}")
                break
            try:
                self.raw("connect", addr, timeout=2)
            except Exception:
                pass
        return self.list_devices()

    def set_device(self, dev):
        """选择指定设备并读取分辨率"""
        self.device = dev
        self.w, self.h = self._get_resolution()
        return self.w, self.h

    def _list_devices(self):
        return [d for d, s in self.list_devices() if s == "device"]

    def _pick_device(self, fatal=True):
        devices = self._list_devices()
        if devices:
            return devices[0]
        self.scan_and_connect()
        devices = self._list_devices()
        if devices:
            return devices[0]
        if fatal:
            print(f"{C.ERR}没有检测到模拟器！{C.R}请确认：1)模拟器已启动 2)设置里已打开 ADB 调试")
            sys.exit(1)
        return None

    def try_reconnect(self):
        dev = self._pick_device(fatal=False)
        if dev:
            self.device = dev
            self.w, self.h = self._get_resolution()
            return True
        return False

    def _get_resolution(self):
        if not self.device:
            return DESIGN_W, DESIGN_H
        try:
            out = self.run("shell", "wm", "size").stdout.decode("utf-8", "replace")
            m = re.search(r"(\d+)x(\d+)", out)
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                # 设计基准是横屏 2310x1080；wm size 可能报竖屏姿态，归一化为横屏
                return (max(a, b), min(a, b))
        except Exception:
            pass
        return DESIGN_W, DESIGN_H

    def screenshot(self):
        # 全屏 PNG 传输比普通 shell 命令慢，单独放宽超时
        raw = self.run("exec-out", "screencap", "-p", timeout=20).stdout
        img = None
        if raw[:4] == b"\x89PNG":
            img = np.array(Image.open(io.BytesIO(raw)).convert("RGB"))
        else:
            self.run("shell", "screencap", "-p", "/sdcard/_lag_shot.png", timeout=20)
            tmp = os.path.join(app_dir(), "_lag_shot.png")
            try:
                self.run("pull", "/sdcard/_lag_shot.png", tmp, timeout=20)
                img = np.array(Image.open(tmp).convert("RGB"))
            finally:
                # 清理临时文件，避免在模拟器和本地越积越多
                self.run("shell", "rm", "-f", "/sdcard/_lag_shot.png")
                if os.path.isfile(tmp):
                    os.remove(tmp)
        # 以实际画面为准校正分辨率（不信任 wm size 的姿态报告）
        if img is not None:
            fh, fw = img.shape[:2]
            self.w, self.h = max(fw, fh), min(fw, fh)
        return img

    def tap(self, x, y):
        self.run("shell", "input", "tap", str(int(x)), str(int(y)))


# ==================== OCR / 工具 ====================
_ocr = None
def get_ocr():
    global _ocr
    if _ocr is None:
        apply_ocr_env(perf_settings()["ocr_threads"])   # 必须在 import onnxruntime 前
        print(f"{C.ACCENT}▸ 正在加载 OCR 模型（首次稍慢）...{C.R}")
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
    return _ocr


def scale_box(adb, box):
    sx, sy = adb.w / DESIGN_W, adb.h / DESIGN_H
    x1, y1, x2, y2 = box
    return (int(x1 * sx), int(y1 * sy), int(x2 * sx), int(y2 * sy))


def select_device(devs, want=""):
    """从在线设备列表里挑一台。返回 (serial, why, error)。

    want 留空 = 自动挑第一个在线通道；指定了但不在线 = 报错且绝不回退到别的设备，
    避免指定设备掉线后误连到另一台机子继续挂机。纯函数，便于无设备单测。
    """
    online = [str(d).strip() for d in (devs or []) if str(d).strip()]
    want = str(want or "").strip()
    if not online:
        return None, "", "没有在线设备（请确认模拟器已启动 / 手机已开 USB 调试）"
    if not want:
        return online[0], "自动选择（第一个在线通道）", ""
    if want not in online:
        return None, "", (f"指定的设备不在线：{want}\n"
                          f"  当前在线：{', '.join(online)}")
    return want, "指定设备", ""


def _pick_device_by_pref(adb):
    """按 config.device.serial 选设备。留空自动；指定但离线则明确退出，不误连。"""
    dcfg = CONFIG.get("device", {}) or {}
    want = str(dcfg.get("serial", "")).strip()
    devs = adb._list_devices()
    target, why, err = select_device(devs, want)
    if err:
        print(f"{C.ERR}▸ {err}{C.R}")
        if want:
            print(f"{C.DIM}  请在测试仪「设备」下拉里刷新后重新选择，或把 device.serial 清空改为自动{C.R}")
            sys.exit(2)          # 指定设备不在线：绝不拿别的设备凑合
        return False, "无设备"

    changed = (adb.device != target)
    if changed:
        adb.set_device(target)
        # 不再猜测真机/模拟器：序列号特征不可靠，界面统一合并展示在线设备
        print(f"{C.ACCENT}▸ 设备{C.R}     {C.HL}{adb.device}{C.R}  "
              f"{C.DIM}{adb.w}x{adb.h} · {why}{C.R}")
    return changed, why



def parse_seconds(text, max_seconds=2592000, min_seconds=0):
    """从文本解析时长（秒）。支持：
       - HH:MM:SS / H:MM:SS（如 34:30:00、9:05:00）
       - MM:SS（如 59:59）
       - 中文 X小时Y分Z秒 / X时Y分Z秒 / X分Y秒 / X小时Y分
       超出 [min_seconds, max_seconds] 视为无效返回 None（滤掉 OCR 偶发误识成的超大/负数值）。
       注：小数点(.)不当分隔符，避免把 '1.03万' 等误判为时间。
    """
    if not text:
        return None
    t = text.replace("：", ":")  # 全角冒号归一化
    # 1) HH:MM:SS（小时 1~2 位，分/秒 00~59），左右不为数字/冒号，避免截断
    m = re.search(r"(?<![\d:])([0-9]{1,2}):([0-5]\d):([0-5]\d)(?![\d:])", t)
    if m:
        sec = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        if min_seconds <= sec <= max_seconds:
            return sec
    # 2) MM:SS（不与上面的 HH:MM:SS 重叠）
    m = re.search(r"(?<![\d:])([0-9]{1,3}):([0-5]\d)(?![\d:])", t)
    if m:
        sec = int(m.group(1)) * 60 + int(m.group(2))
        if min_seconds <= sec <= max_seconds:
            return sec
    # 3) 中文 小时/分/秒
    m = re.search(r"([0-9]+)\s*小时?\s*([0-9]+)\s*分(?:\s*([0-9]+)\s*秒?)?", t)
    if m:
        sec = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3) or 0)
        if min_seconds <= sec <= max_seconds:
            return sec
    m = re.search(r"([0-9]+)\s*分\s*([0-9]+)\s*秒", t)
    if m:
        sec = int(m.group(1)) * 60 + int(m.group(2))
        if min_seconds <= sec <= max_seconds:
            return sec
    return None


def _merge_lines(items, tol=22):
    """把同一行的 OCR 框按 x 合并成文本，容错 OCR 把 '34:30:00' 拆成多框的问题。
    返回 [(cx_mid, cy, merged_text, min_score)]，按 cy 排序。
    """
    rows = {}
    for cx, cy, text, score in items:
        key = round(cy / tol) * tol
        rows.setdefault(key, []).append((cx, text, score))
    merged = []
    for key in sorted(rows):
        row = sorted(rows[key], key=lambda t: t[0])
        text = " ".join(t for _, t, _ in row)
        score = min(s for _, _, s in row)
        cx_mid = int(sum(x for x, _, _ in row) / len(row))
        merged.append((cx_mid, key, text, score))
    return merged


def box_center(box):
    return (int(sum(p[0] for p in box) / 4),
            int(sum(p[1] for p in box) / 4))




# ==================== 通用识别 / 点击（OCR 文字优先 + 图标模板兜底） ====================
def find_button(items, keywords, exclude=None, region=None, min_score=0.45,
                prefer="any"):
    """在 OCR 结果里找文字按钮中心，返回 (cx,cy) 或 None。
    keywords: 任一包含即命中；exclude: 命中任一则排除；region:(x1,y1,x2,y2) 限定范围；
    prefer: 命中多个时 'right'/'bottom'/'right_bottom' 选最右/最下（适合右下角升级键）。
    这是“坐标识别”的替代方案：靠文字而不是固定像素点定位，抗界面/分辨率漂移。
    """
    cands = []
    for cx, cy, text, score in items:
        if score < min_score:
            continue
        t = text.strip()
        if not any(k in t for k in keywords):
            continue
        if exclude and any(k in t for k in exclude):
            continue
        if region:
            x1, y1, x2, y2 = region
            if not (x1 <= cx <= x2 and y1 <= cy <= y2):
                continue
        cands.append((cx, cy, text, score))
    if not cands:
        return None
    if prefer == "right":
        cands.sort(key=lambda c: -c[0])
    elif prefer == "bottom":
        cands.sort(key=lambda c: -c[1])
    elif prefer == "right_bottom":
        cands.sort(key=lambda c: (-c[1], -c[0]))
    return cands[0][0], cands[0][1]




def _load_template(path):
    if not os.path.isfile(path):
        return None
    return np.array(Image.open(path).convert("RGB"))


def template_match(img, tmpl, threshold=0.75):
    """在 img 里做图标模板匹配，返回最佳中心 (cx,cy) 或 None。"""
    if cv2 is None or tmpl is None or img is None:
        return None
    th, tw = tmpl.shape[:2]
    ih, iw = img.shape[:2]
    if th > ih or tw > iw:
        return None
    res = cv2.matchTemplate(img.astype(np.uint8), tmpl.astype(np.uint8),
                            cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    if max_val < threshold:
        return None
    return (int(max_loc[0] + tw / 2), int(max_loc[1] + th / 2))


def find_close_x(adb, img, search=None, threshold=0.7):
    """用图标模板找关闭(X)按钮中心；search=(x1,y1,x2,y2) 限定搜索范围。
    这是“关闭X”这类【没有文字】元素的更稳识别方式，替代固定坐标点。"""
    tmpl = _load_template(os.path.join(app_dir(), "refs", "close_x.png"))
    if tmpl is None:
        return None
    # 按设备分辨率缩放模板（截图已为设备分辨率，模板取自 2310x1080）
    sx, sy = adb.w / DESIGN_W, adb.h / DESIGN_H
    if abs(sx - 1) > 0.01 or abs(sy - 1) > 0.01:
        tmpl = np.array(Image.fromarray(tmpl).resize(
            (max(1, int(tmpl.shape[1] * sx)), max(1, int(tmpl.shape[0] * sy)))))
    sub = img
    if search:
        x1, y1, x2, y2 = search
        sub = img[max(0, y1):y2, max(0, x1):x2]
    pos = template_match(sub, tmpl, threshold)
    if pos and search:
        pos = (pos[0] + max(0, search[0]), pos[1] + max(0, search[1]))
    return pos


# ==================== 纯图标入口注册表（无文字，OCR 找不到，必须单独归类） ====================
# 所有坐标 / 区域均为 2310x1080 设计基准，运行时一律经 scale_box 换算。
# 识别优先级：图标模板（限定搜索区 + 多尺度）→ 设计坐标兜底 → 点击后 OCR 验证。
# 实机校准后可用 config.json 的 "icon_entries": {"<id>": {"fallback": [x, y]}} 覆盖任意字段。
ICON_ONLY_ENTRIES = {
    "task_panel_entry": {
        "name": "任务面板（左上星形徽记 #13）",
        "template": "icon_quest.png",
        "search": (0, 0, 200, 150),
        "fallback": (77, 52),
        "threshold": 0.66,
        "scales": (0.55, 0.7, 0.85, 1.0, 1.2, 1.45),
        "verify": ("主线任务", "委托任务", "支线任务", "活动任务"),
        "note": "2026-11-23 参考图标注 #13 + 实机确认：星形徽记 (53,43)@1600x900 → "
                "设计基准 (77,52)；点后 OCR 读到主线/委托/支线/活动任务。"
                "此前误用剪贴板图标当任务入口，导致 daily 任务段永远找不到页签",
    },
    "task_clipboard": {
        "name": "活动面板（左上剪贴板 #14）",
        "template": "icon_task.png",
        "search": (0, 0, 420, 150),
        "fallback": (217, 55),
        "threshold": 0.66,
        "scales": (0.55, 0.7, 0.85, 1.0, 1.2, 1.45),
        "verify": ("探险协议", "永恒战场", "月度登录"),
        "note": "2026-11-23 正名：这个剪贴板图标是【活动入口】不是任务；"
                "任务入口是 #13 星形徽记 task_panel_entry。实机命中 (150,46)@0.85",
    },
    "mail_envelope": {
        "name": "邮件（左下信封）",
        "template": "icon_mail.png",
        "search": (100, 760, 640, 960),
        "fallback": (422, 863),
        "threshold": 0.66,
        "scales": (0.55, 0.7, 0.85, 1.0, 1.2, 1.45),
        "verify": ("邮件", "系统", "全部"),
        "note": "2026-11-22 实机 1600x900 模板命中 (292,719)@0.85 → 设计基准 (422,863)；"
                "2310x1080 历史截图实测 (467,865)，说明不同设备布局不同，模板优先",
    },
    "activity_entry": {
        "name": "活动入口（左上剪贴板图标）",
        # 2026-11-22 实机确认：星图点左上 (150,46)@1600x900 打开的就是活动面板
        # （页签「活动 / 探险协议 / 永恒战场」+ 月度登录 / 活跃挑战领奖等侧栏条目）。
        # 之前一直没找到活动图标，是因为这个入口被命名成了「任务清单」。
        "template": "icon_task.png",
        "search": (0, 0, 420, 150),
        "fallback": (217, 55),
        "threshold": 0.66,
        "scales": (0.55, 0.7, 0.85, 1.0, 1.2, 1.45),
        "verify": ("探险协议", "永恒战场"),
        "note": "旧兜底 (320,60) 实测是等级图标 / 公告跑马灯，已彻底移除，绝不盲点",
    },
    "close_x": {
        "name": "面板关闭 X",
        "template": "close_x.png",
        "search": (0, 0, DESIGN_W, 320),
        "fallback": (2195, 70),
        "threshold": 0.70,
        "scales": (0.8, 1.0, 1.25),
        "note": "宽搜索区兼容左上/右上的 X；find_close_x 用的是同一模板。"
                "2026-11-22 实机：活动面板 X (1522,56)、战报面板 X (1525,61) @1600x900 → 设计基准 (2195,70)",
    },
    "task_close": {
        "name": "任务面板返回星图（右上展开/收起键）",
        # 2026-11-23 终版复核：当前游戏任务面板没有门形 X，旧 icon_task_close.png
        # 会误命中右上的展开/收起图标并导致「关闭残留任务面板」死循环。
        # 实机点击 (1520,45)@1600x900 可稳定退回星图，故停用模板，直接走设计坐标兜底。
        "template": None,
        "search": (DESIGN_W - 350, 0, DESIGN_W, 140),
        "fallback": (2194, 54),
        "threshold": 0.66,
        "scales": (0.55, 0.7, 0.85, 1.0, 1.2, 1.45),
        "note": "历史门形图标模板已停用；现版本用右上展开/收起键 (1520,45)@1600x900 → 设计 (2194,54)",
    },
    "live_widget": {
        "name": "基地建设页右上直播挂件",
        "template": None,
        "search": (1800, 0, DESIGN_W, 140),
        "fallback": (1915, 69),
        "forbidden": True,
        "note": "硬约束：不是「一键升级」，点它会陷入死循环，禁止点击",
    },
    "exit_cancel": {
        "name": "退出确认框取消",
        "template": None,
        "search": (1200, 550, 1800, 850),
        "fallback": (1495, 700),
        "note": "安全护栏：仅在误弹退出确认框时使用",
    },
}


def icon_entry_cfg(entry_id):
    """取注册表条目，允许 config.json 的 icon_entries 覆盖（实机校准后填坐标）。"""
    base = ICON_ONLY_ENTRIES.get(entry_id)
    if not base:
        return None
    cfg = dict(base)
    user = (CONFIG.get("icon_entries", {}) or {}).get(entry_id)
    if isinstance(user, dict):
        cfg.update(user)
    return cfg


def template_match_multiscale(img, tmpl, threshold=0.7, scales=(1.0,)):
    """多尺度模板匹配：返回 (中心x, 中心y, 分数, 实际缩放) 或 None。"""
    if cv2 is None or img is None or tmpl is None:
        return None
    best = None
    for s in scales:
        if abs(s - 1.0) < 0.01:
            t = tmpl
        else:
            nw = max(1, int(tmpl.shape[1] * s))
            nh = max(1, int(tmpl.shape[0] * s))
            t = np.array(Image.fromarray(tmpl).resize((nw, nh), Image.LANCZOS))
        if t.shape[0] > img.shape[0] or t.shape[1] > img.shape[1]:
            continue
        res = cv2.matchTemplate(img.astype(np.uint8), t.astype(np.uint8),
                                cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        if mx >= threshold and (best is None or mx > best[2]):
            best = (int(loc[0] + t.shape[1] / 2),
                    int(loc[1] + t.shape[0] / 2), float(mx), s)
    return best


def find_icon_entry(adb, img, entry_id, allow_fallback=True):
    """定位纯图标入口。返回 (x, y, source, score)；source = template / fallback。"""
    cfg = icon_entry_cfg(entry_id)
    if not cfg or cfg.get("forbidden"):
        return None
    if img is not None and cfg.get("template"):
        tmpl = _load_template(os.path.join(app_dir(), "refs", cfg["template"]))
        if tmpl is not None:
            # 等比多尺度匹配：实机实测 UI 缩放不是 sx/sy 线性（1600x900 上最佳尺度 0.85，
            # 而 sx=0.693/sy=0.833），按 sx/sy 非等比压缩模板会让图标变形、分数掉到阈值下。
            x1, y1, x2, y2 = scale_box(adb, cfg["search"])
            sub = img[max(0, y1):y2, max(0, x1):x2]
            hit = template_match_multiscale(sub, tmpl,
                                            float(cfg.get("threshold", 0.7)),
                                            tuple(cfg.get("scales", (1.0,))))
            if hit:
                return (x1 + hit[0], y1 + hit[1], "template", hit[2])
    fb = cfg.get("fallback")
    if allow_fallback and fb:
        x, y, _, _ = scale_box(adb, (fb[0], fb[1], 0, 0))
        return (x, y, "fallback", 0.0)
    return None


def tap_icon_entry(adb, entry_id, verify_texts=None, settle=2.5):
    """点击纯图标入口；可选点击后 OCR 验证界面是否真的打开。返回是否成功。"""
    cfg = icon_entry_cfg(entry_id)
    if not cfg or cfg.get("forbidden"):
        log(f"纯图标入口 {entry_id} 被标记为禁点，已拦截", "warn")
        return False
    img = adb.screenshot()
    hit = find_icon_entry(adb, img, entry_id)
    if not hit:
        log(f"纯图标入口「{cfg['name']}」未校准：模板未命中且无兜底坐标；"
            f"请运行 icons 校准，再把坐标写进 config.json 的 "
            f"icon_entries.{entry_id}.fallback", "warn")
        return False
    x, y, src, score = hit
    adb.tap(x, y)
    log(f"点击纯图标入口 {cfg['name']} ({x},{y}) · {src}"
        + (f" · 匹配 {score:.2f}" if src == "template" else " · 坐标兜底"),
        "step" if src == "template" else "warn")
    time.sleep(settle)
    if verify_texts:
        img2 = adb.screenshot()
        if img2 is None:
            return False
        texts = " ".join(t for _, _, t, _ in _au_ocr_all(img2))
        if not any(k in texts for k in verify_texts):
            log(f"纯图标入口 {cfg['name']} 点击后未出现预期界面 {verify_texts}，判定失败", "warn")
            return False
    return True


# ==================== 全量点位配置（照片/坐标/文字/流程/容错） ====================
def load_points():
    """读取 points.json（全量点位校准结果）。缺文件/损坏时返回空配置，不影响主流程。"""
    try:
        p = os.path.join(app_dir(), "points.json")
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def point_design_xy(point, cfg=None):
    """把点位记录换算成 2310x1080 设计坐标。

    points.json 的「坐标」是校准设备上的实机像素，「设计坐标」才是 scale_box 的输入。
    旧记录只有「坐标」时，按 points.json 的「分辨率」基准换算回来，绝不把实机像素
    当成设计坐标直接缩放（那会让 1600x900 设备上的点击点偏到左上角）。
    """
    if not isinstance(point, dict):
        return None
    d = point.get("设计坐标")
    if isinstance(d, (list, tuple)) and len(d) >= 2 and d[0] is not None and d[1] is not None:
        return int(d[0]), int(d[1])
    xy = point.get("坐标") or point.get("fallback")
    if not (isinstance(xy, (list, tuple)) and len(xy) >= 2):
        return None
    cfg = cfg if isinstance(cfg, dict) else load_points()
    base = cfg.get("分辨率") or (1600, 900)
    bw, bh = int(base[0]), int(base[1])
    if bw <= 0 or bh <= 0:
        return None
    return (round(int(xy[0]) * DESIGN_W / bw),
            round(int(xy[1]) * DESIGN_H / bh))


def point_expect_texts(point):
    """读取点位的目标文字：支持「验证文字」数组和旧记录里斜杠分隔的「目标文字」。"""
    expect = point.get("验证文字") or point.get("verify") or point.get("目标文字") or []
    if isinstance(expect, str):
        expect = [part.strip() for part in re.split(r"[/、|]", expect) if part.strip()]
    return [str(k).strip() for k in expect if str(k).strip()]


def point_double_check(adb, point, settle=4.0):
    """坐标定位 + 文字识别双重校验，返回 (是否成功, 界面文字, 说明)。

    流程：设计坐标 → scale_box 换算到当前分辨率 → ADB 点击 → 等待转场 →
    截图 OCR → 目标文字是否出现。校验失败不盲目重复点击，交给上层按容错规则处理。
    """
    cfg = load_points()
    if not isinstance(point, dict):
        return False, "", "点位配置无效"
    design = point_design_xy(point, cfg)
    if not design:
        return False, "", "点位坐标为空"
    x, y = scale_box(adb, (design[0], design[1], 0, 0))[:2]
    if not x or not y:
        return False, "", "点位坐标为空"
    expect = point_expect_texts(point)
    adb.tap(x, y)
    time.sleep(settle)
    img = adb.screenshot()
    if img is None:
        return False, "", "点击后截图失败（可能转场中）"
    items = _au_ocr_all(img)
    texts = [t for _, _, t, _ in items]
    if not texts:
        return False, "", "转场无文字（等 5 秒重截，不计失败）"
    if expect and not any(any(k in t for t in texts) for k in expect):
        return False, texts, f"坐标({x},{y})已点击但目标文字 {expect} 未出现"
    return True, texts, f"坐标({x},{y}) + 文字校验通过"


def _wait_after_click(seconds=4.0):
    """统一点击后等待，避开游戏转场（实测转场期 OCR 会读到 0 条文字）。"""
    time.sleep(seconds)


# ==================== 命令 ====================
def cmd_shot(adb):
    img = adb.screenshot()
    out = os.path.join(app_dir(), "shot.png")
    Image.fromarray(img).save(out)
    log(f"截图已保存: {out}（分辨率 {adb.w}x{adb.h}）")


def cmd_icons(adb):
    """纯图标入口校准：截图后逐个检测注册表，输出报告并生成标注图。

    绿点 = 模板命中，黄点 = 坐标兜底，红叉 = 禁点区，蓝框 = 搜索区。
    另外把活动入口搜索区单独裁剪放大，便于人工找图标 / 裁模板。
    """
    from PIL import ImageDraw, ImageFont
    img = adb.screenshot()
    if img is None:
        raise RuntimeError("图标校准：截图失败")
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 18)
    except Exception:
        font = ImageFont.load_default()
    annotated = Image.fromarray(img).copy()
    draw = ImageDraw.Draw(annotated)
    print(f"{C.ACCENT}纯图标入口校准 · 当前分辨率 {adb.w}x{adb.h}{C.R}")
    print(f"{C.DIM}{'入口 ID':<18}{'名称':<16}{'定位':<16}{'方式':<12}{'分数':<8}{C.R}")
    rows = []
    for eid in ICON_ONLY_ENTRIES:
        cfg = icon_entry_cfg(eid)
        forbidden = bool(cfg.get("forbidden"))
        x1, y1, x2, y2 = scale_box(adb, cfg["search"])
        draw.rectangle([x1, y1, x2, y2],
                       outline=(220, 70, 70) if forbidden else (80, 150, 210), width=2)
        hit = None if forbidden else find_icon_entry(adb, img, eid)
        if hit:
            cx, cy, src, score = hit
            col = (60, 215, 110) if src == "template" else (235, 185, 60)
            draw.ellipse([cx - 12, cy - 12, cx + 12, cy + 12], outline=col, width=3)
            draw.text((cx + 14, cy - 10), eid, fill=col, font=font)
            pos, how, sc = f"({cx},{cy})", src, f"{score:.2f}" if src == "template" else "-"
        elif forbidden:
            fb = cfg.get("fallback")
            if fb:
                fx, fy, _, _ = scale_box(adb, (fb[0], fb[1], 0, 0))
                draw.line([fx - 12, fy - 12, fx + 12, fy + 12], fill=(220, 70, 70), width=3)
                draw.line([fx - 12, fy + 12, fx + 12, fy - 12], fill=(220, 70, 70), width=3)
            pos, how, sc = "禁点", "forbidden", "-"
        else:
            pos, how, sc = "未命中", "需校准", "-"
        rows.append((eid, cfg["name"], pos, how, sc))
        print(f"  {eid:<18}{cfg['name'][:10]:<16}{pos:<16}{how:<12}{sc:<8}")
    out = os.path.join(app_dir(), "icons_标注.png")
    annotated.save(out)
    # 活动区放大图固定裁顶部整条供人工指认图标；与注册表搜索区解耦
    ax1, ay1, ax2, ay2 = scale_box(adb, (0, 0, DESIGN_W, 160))
    crop = Image.fromarray(img[max(0, ay1):ay2, max(0, ax1):ax2])
    crop = crop.resize((max(1, crop.width * 2), max(1, crop.height * 2)), Image.LANCZOS)
    cout = os.path.join(app_dir(), "icons_活动区.png")
    crop.save(cout)
    # 左下纯图标快捷条（邮件等入口都在这一排），单独放大便于指认活动图标
    bx1, by1, bx2, by2 = scale_box(adb, (60, 740, 620, 960))
    bar = Image.fromarray(img[max(0, by1):by2, max(0, bx1):bx2])
    bar = bar.resize((max(1, bar.width * 2), max(1, bar.height * 2)), Image.LANCZOS)
    bout = os.path.join(app_dir(), "icons_快捷条.png")
    bar.save(bout)
    print(f"{C.OK}标注图: {out}{C.R}")
    print(f"{C.OK}活动区放大图: {cout}{C.R}")
    print(f"{C.OK}左下快捷条放大图: {bout}{C.R}")
    print(f"{C.DIM}如需覆盖某个入口，把设计基准 2310x1080 的中心坐标写进 "
          f"config.json 的 icon_entries.<入口ID>.fallback，或把裁好的图标放 refs/{C.R}")
    return rows


def cmd_points():
    """点位配置自检：逐项核对设计坐标/实机坐标是否一致、验证文字与资源是否齐全。"""
    d = load_points()
    if not d:
        print(f"{C.ERR}points.json 缺失或损坏{C.R}")
        return
    base = d.get("分辨率") or (1600, 900)
    bw, bh = int(base[0]), int(base[1])
    print(f"{C.ACCENT}全量点位配置自检{C.R}  设备 {d.get('设备', '?')} / "
          f"校准分辨率 {bw}x{bh} / 设计基准 {DESIGN_W}x{DESIGN_H}")
    fails = []

    def check_xy(label, p):
        xy = p.get("坐标")
        design = point_design_xy(p, d)
        if not design:
            fails.append(f"{label} 坐标为空")
            print(f"  {label:22} {C.ERR}FAIL 坐标为空{C.R}")
            return
        back = (round(design[0] * bw / DESIGN_W), round(design[1] * bh / DESIGN_H))
        ok = isinstance(xy, (list, tuple)) and len(xy) >= 2 and \
             abs(int(xy[0]) - back[0]) <= 2 and abs(int(xy[1]) - back[1]) <= 2
        tag = f"{C.OK}OK{C.R}" if ok else f"{C.ERR}FAIL 换算不符（应为 {back}）{C.R}"
        if not ok:
            fails.append(f"{label} 设计/实机坐标不一致")
        print(f"  {label:22} 实机{str(xy):12} 设计{str(design):12} {tag}")

    print(f"{C.DIM}-- 关键入口 --{C.R}")
    for key, p in (d.get("关键入口") or {}).items():
        check_xy(key, p)
        expect = point_expect_texts(p)
        if not expect:
            print(f"  {key:22} {C.WARN}未登记验证文字{C.R}")
        else:
            print(f"  {'':22} 验证文字 {'/'.join(expect)}")
    rows = d.get("点位") or []
    print(f"{C.DIM}-- 标注点位 {len(rows)} 个 --{C.R}")
    for p in rows:
        check_xy(f"#{p.get('序号', '?')} {p.get('名称', '?')}", p)
    print(f"{C.DIM}-- 资源文件 --{C.R}")
    for k, rel in (d.get("资源文件") or {}).items():
        ok = os.path.isfile(os.path.join(app_dir(), rel))
        if not ok:
            fails.append(f"资源缺失 {rel}")
        print(f"  {k:12} {C.OK if ok else C.ERR}{'OK' if ok else '缺失'}{C.R}  {rel}")
    if fails:
        print(f"{C.ERR}自检失败 {len(fails)} 项：" + "；".join(fails) + C.R)
    else:
        print(f"{C.OK}自检通过：坐标换算 / 验证文字 / 资源文件全部一致{C.R}")


def cmd_scan(adb):
    img = adb.screenshot()
    result, _ = get_ocr()(img)
    if not result:
        log("整张图一个文字都没识别到，确认游戏画面是否正常显示", "warn")
        return
    annotated = Image.fromarray(img.copy())
    draw = ImageDraw.Draw(annotated)
    log(f"全屏文字列表（{C.HL}找按钮文字与倒计时坐标，用于校准识别区域{C.R}）：")
    print(f"  {C.DIM}{'#':<4}{'文字':<16}{'中心坐标':<16}{'置信度':<8}{C.R}")
    for i, (box, text, score) in enumerate(result):
        cx, cy = box_center(box)
        is_target = ("升级" in text) or re.search(r"\d{1,2}:\d{2}", text)
        col = C.HL if is_target else C.INFO
        sc = float(score)
        scol = C.OK if sc >= 0.7 else (C.WARN if sc >= 0.4 else C.ERR)
        print(f"  {C.ACCENT}#{i:<3}{C.R} {col}{text:<14}{C.R} {C.DIM}({cx},{cy}){C.R}  {scol}{sc:.2f}{C.R}")
        draw.rectangle([tuple(box[0]), tuple(box[2])], outline=(255, 0, 0), width=2)
        draw.text((box[0][0], max(0, box[0][1] - 18)), f"#{i}", fill=(255, 0, 0))
    out = os.path.join(app_dir(), "scan_标注.png")
    annotated.save(out)
    log(f"标注图已保存: {out}", "ok")
    log(f"以上坐标为当前设备实机像素（{adb.w}x{adb.h}）；写入 points.json 时请换算为 "
        f"{DESIGN_W}x{DESIGN_H} 设计基准，运行时由 scale_box 自动换算回来")


def cmd_times(adb):
    """识别当前屏幕上的所有倒计时：列出坐标、文本、解析出的秒数/时分秒。
    优先用『行合并』解析容错 OCR 拆框（如把 '34:30:00' 拆成多框）。"""
    img = adb.screenshot()
    if img is None:
        log("截图失败", "err")
        return
    result, _ = get_ocr()(img)
    items = []
    for box, text, score in (result or []):
        cx, cy = box_center(box)
        items.append((cx, cy, text, float(score)))
    log(f"分辨率 {adb.w}x{adb.h}，识别到 {len(items)} 个文字框", "info")
    log("=== 倒计时识别结果 ===", "step")

    # 原始框解析
    raw_secs = set()
    for cx, cy, text, score in sorted(items, key=lambda t: t[1]):
        sec = parse_seconds(text)
        if sec is not None and sec >= 1:
            raw_secs.add(sec)
            h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
            print(f"  {C.HL}({cx},{cy}){C.R} {C.OK}{sec}s{C.R} = "
                  f"{h:02d}:{m:02d}:{s:02d}  {C.DIM}[原始]{C.R} {text!r}  ({score:.2f})")

    # 合并解析（容错 OCR 拆框），仅输出原始未覆盖到的
    merged_hit = 0
    for cx, cy, text, score in _merge_lines(items):
        sec = parse_seconds(text)
        if sec is not None and sec >= 1 and sec not in raw_secs:
            merged_hit += 1
            h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
            print(f"  {C.HL}({cx},{cy}){C.R} {C.OK}{sec}s{C.R} = "
                  f"{h:02d}:{m:02d}:{s:02d}  {C.ACCENT}[合并]{C.R} {text!r}")

    if not raw_secs and merged_hit == 0:
        log("未识别到任何倒计时（当前界面可能没有倒计时，或数字/冒号被 OCR 误识）", "warn")
    else:
        log(f"共识别到 {len(raw_secs) + merged_hit} 个倒计时文本", "ok")


def cmd_status(adb):
    """一屏状态汇总：设备 / 屏幕 / 前台 / 建造队列倒计时 / 三项资源（持有/上限）。
    不点任何按钮，纯只读，可随时安全执行。"""
    w = 26
    on = _au_screen_on(adb)
    fg = _au_foreground(adb)
    ingame = _GAME_PKG in fg

    print(f"\n{C.HL}{'=' * 56}{C.R}")
    print(f"{C.ACCENT}  星际猎人挂机状态{C.R}   {C.DIM}{time.strftime('%Y-%m-%d %H:%M:%S')}{C.R}")
    print(f"{C.HL}{'=' * 56}{C.R}")
    print(f"  {'设备'.ljust(w - 12)}{adb.device}   {C.DIM}{adb.w}x{adb.h}{C.R}")
    print(f"  {'屏幕'.ljust(w - 12)}" + (f"{C.OK}亮{C.R}" if on else f"{C.WARN}灭（脚本会自动唤醒）{C.R}"))
    print(f"  {'前台'.ljust(w - 12)}" + (f"{C.OK}游戏在跑{C.R}" if ingame
                                     else f"{C.WARN}{fg or '未知'}（不在游戏）{C.R}"))

    img = adb.screenshot()
    if img is None:
        print(f"\n  {C.ERR}截图失败，无法读取资源/队列{C.R}\n")
        return
    items = _au_ocr_all(img)

    # 建造队列：底部状态栏（y>900）的倒计时，最多两条
    q = sorted(_au_queue_times(items, adb))
    if q:
        print(f"  {'建造队列'.ljust(w - 12)}" + f"{C.OK}{len(q)}/2 占用{C.R}   最近到期 {C.HL}{fmt_duration(q[0])}{C.R}")
    else:
        print(f"  {'建造队列'.ljust(w - 12)}{C.DIM}本视图不显示队列栏（切到基地 3D 折叠视图可见）{C.R}")

    # 资源：持有/上限
    t = " ".join(t for _, _, t, _ in items)
    for name, bx, by in _AU_RES_SLOTS:
        cand, best = None, 10 ** 9
        for cx, cy, text, score in items:
            if score < 0.4 or "/" not in text:
                continue
            d = abs(cx - bx) + abs(cy - by) * 2
            if d < best and d < 220:
                best, cand = d, text.replace(" ", "")
        if not cand:
            continue
        cur_s, _, max_s = cand.partition("/")
        print(f"  {name.ljust(w - 12)}{C.HL}{_fmt_num(_num_cn(cur_s))}{C.R}"
              f" {C.DIM}/ 上限 {_fmt_num(_num_cn(max_s))}{C.R}")

    # 三项资源没读全 → 大概率有面板遮挡或正在转场；且不要与 autoup 并发执行
    read_names = []
    for name, bx, by in _AU_RES_SLOTS:
        for cx, cy, text, score in items:
            if score < 0.4 or "/" not in text:
                continue
            if abs(cx - bx) + abs(cy - by) * 2 < 220:
                read_names.append(name)
                break
    if len(read_names) < len(_AU_RES_SLOTS):
        print(f"  {'提示'.ljust(w - 12)}{C.WARN}资源只读到 {len(read_names)}/3 项，"
              f"可能有面板遮挡或正在转场；请勿与 autoup 同时运行{C.R}")

    # 面板/等待中提示
    if any(k in t for k in _AU_PANEL_KEYS):
        print(f"  {'面板'.ljust(w - 12)}{C.WARN}一键升级/建筑面板处于打开状态{C.R}")
    print(f"{C.HL}{'=' * 56}{C.R}\n")


def cmd_mark(adb):
    """按键规划标记：在当前截图标记脚本会用到的所有按键。
    绿点=升级确认(兜底)  蓝点=面板X关闭  红点=退出框取消(仅弹出时醒目，否则灰点提示)
    橙框=资源区  紫框=礼包弹窗X  黄框=OCR动态按钮(一键升级/升级/基地建设/基地/确认等)
    """
    from PIL import ImageDraw, ImageFont
    img = adb.screenshot()
    if img is None:
        log("截图失败", "err")
        return
    h, w = img.shape[:2]
    annotated = Image.fromarray(img.copy())
    draw = ImageDraw.Draw(annotated)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 20)
    except Exception:
        font = ImageFont.load_default()

    result, _ = get_ocr()(img)
    texts = [t for _, _, t in (result or [])]

    # 1) 升级确认兜底坐标点（按实际分辨率缩放）
    hard_points = [
        ("升级确认·兜底", 1642, 887, (0, 200, 0)),
    ]
    for name, x, y, col in hard_points:
        px, py, _, _ = scale_box(adb, (x, y, 0, 0))
        draw.ellipse([px - 12, py - 12, px + 12, py + 12], outline=col, width=3)
        draw.text((px + 16, py - 12), f"{name}\n({x},{y})", fill=col, font=font)

    # 1b) 关闭X：优先图标模板识别，找不到用设计坐标兜底（与运行时一致）
    xpos = find_close_x(adb, img, search=(0, 0, w, 300))
    if xpos is None:
        xpos = scale_box(adb, (1792, 163, 0, 0))[:2]
    draw.ellipse([xpos[0] - 12, xpos[1] - 12, xpos[0] + 12, xpos[1] + 12],
                 outline=(0, 120, 255), width=3)
    draw.text((xpos[0] + 16, xpos[1] - 12), "面板X·关闭(模板/OCR)", fill=(0, 120, 255), font=font)

    # 2) 退出框取消（仅当检测到退出游戏确认框时才醒目标红；否则灰色提示，避免误以为当前有误）
    px, py, _, _ = scale_box(adb, (1495, 700, 0, 0))
    if any("确定要退出游戏" in t for t in texts):
        draw.ellipse([px - 12, py - 12, px + 12, py + 12], outline=(255, 0, 0), width=3)
        draw.text((px + 16, py - 12), "退出框·取消(护栏)\n(1495,700)", fill=(255, 0, 0), font=font)
    else:
        draw.ellipse([px - 6, py - 6, px + 6, py + 6], outline=(150, 150, 150), width=2)
        draw.text((px + 10, py - 8), "退出取消(仅退出框弹出)", fill=(150, 150, 150), font=font)

    # 3) 区域框
    regions = [
        ("资源区(消耗:需要)", (400, 760, 1060, 990), (255, 165, 0)),
    ]
    pcfg = CONFIG.get("popup", {})
    if pcfg.get("close_box"):
        regions.append(("礼包弹窗X", pcfg["close_box"], (255, 0, 255)))
    for name, box, col in regions:
        x1, y1, x2, y2 = scale_box(adb, tuple(box))
        draw.rectangle([x1, y1, x2, y2], outline=col, width=2)
        draw.text((x1, max(0, y1 - 22)), name, fill=col, font=font)

    # 4) OCR 动态按钮（找文字点中心）
    dyn_keys = ["一键升级", "升级", "基地建设", "基地", "确认",
                "消耗资源", "填满建造队列优先", "取消"]
    for box, text, score in (result or []):
        if any(k in text for k in dyn_keys) and float(score) >= 0.4:
            cx, cy = box_center(box)
            draw.rectangle([tuple(box[0]), tuple(box[2])], outline=(255, 255, 0), width=2)
            draw.text((box[0][0], max(0, box[0][1] - 20)),
                      f"{text} ({cx},{cy})", fill=(255, 255, 0), font=font)

    suffix = sys.argv[2] if len(sys.argv) > 2 else ""
    out = os.path.join(app_dir(), f"keys_标注{'_' + suffix if suffix else ''}.png")
    annotated.save(out)
    log(f"按键标记图已保存: {out}（分辨率 {w}x{h}）", "ok")
    log("绿=升级确认 蓝=面板X 红=退出取消(仅退出框时) 橙=资源区 紫=礼包X 黄=OCR动态按钮", "info")


def cmd_tap(adb, x, y):
    log(f"点击 ({x}, {y})")
    adb.tap(x, y)




def try_close_popup(adb, img, qq=None, stats=None):
    """全屏找礼包/购买类弹窗，找到就点右上角 X。返回 True 表示处理了弹窗。"""
    pcfg = CONFIG.get("popup", {})
    if not pcfg.get("enabled"):
        return False
    now = time.time()
    if now - getattr(try_close_popup, "_last", 0) < int(pcfg.get("cooldown", 30)):
        return False
    result, _ = get_ocr()(img)
    if not result:
        return False
    hit = [k for k in pcfg.get("keywords", [])
           if any(k in t for _, t, _ in result)]
    if not hit:
        return False
    # 优先图标模板找关闭X（上半屏）；找不到再用配置的 close_box 区域中心
    pos = find_close_x(adb, img, search=(0, 0, adb.w, int(adb.h * 0.4)))
    if pos is None:
        x1, y1, x2, y2 = scale_box(adb, pcfg["close_box"])
        pos = ((x1 + x2) // 2, (y1 + y2) // 2)
        log("未模板命中弹窗X，用 close_box 区域", "warn")
    adb.tap(*pos)
    try_close_popup._last = now
    log(f"检测到礼包弹窗（{','.join(hit)}），已关闭 ({pos[0]},{pos[1]})", "warn")
    if stats is not None:
        stats["popups"] += 1
    if qq is not None:
        qq.push("popup_closed", f"检测到礼包弹窗（{','.join(hit)}），已自动关闭。",
                img_bgr=img, with_shot=False)
    return True


def fmt_duration(sec):
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}小时{m}分"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"




# ==================== 一键升级自动循环（真机） ====================
# 设计原则：读倒计时 → 纯 sleep 零占用等待 → 到点补位，队列永远满载（先到先补）

_AU_PANEL_KEYS = ("填满建造队列", "消耗资源")          # 面板打开的特征文本
# 基地建设【建筑列表视图】右上「一键升级」按钮（实机 (1345,55)@1600x900 → 设计 (1946,67)）。
# 2026-11-23 实测：点它打开列出全部可升级建筑的面板（含各自倒计时）。
# 硬约束：这个坐标【只在建筑列表视图】有效！3D 全景视图同位置 (1345,50) 是【分享按钮】，
# 盲点会弹出分享面板（二维码 / 小红书），脚本此前就是这样误点的。
# 因此必须先 OCR 读到「一键升级」文字；读不到要先点进基地建设展开列表视图，绝不直接点坐标。
_AU_GLOBAL_UPGRADE_BTN = (1946, 67)
# 底部「基地建设」标签（实机 (801,823)@1600x900 → 设计 (1156,988)）：
# 3D 全景视图里点它会展开成建筑列表视图，右上角才出现「一键升级」按钮（转场约 4~7 秒）。
_AU_BASE_LIST_ENTRY = (1156, 988)
# 建筑列表视图右上「折叠/收起」图标（实机 (1510,55) → 设计 (2180,66)）：
# 点它回到 3D 全景视图，底部才显示建造队列条（两条倒计时 + 2/2）。
# 不收起就会一直读不到队列倒计时，主循环误判「有空位」而反复点一键升级。
_AU_BASE_COLLAPSE_BTN = (2180, 66)
# 误开的分享面板特征文字 + 右上 X（实机 (1520,45) → 设计 (2194,54)）
_AU_SHARE_PANEL_KEYS = ("小红书", "朋友圈", "分享给好友", "保存到相册", "二维码分享")
_AU_SHARE_CLOSE_BTN = (2194, 54)

# 注：旧版用 _AU_RES_RE 解析「消耗资源」做资源预判，2026-11-23 实测发现语义相反
# （前=本次消耗、后=当前拥有），会误判不足并长等 3 小时，已移除该预判，不再保留正则。


def _au_ocr_all(img):
    """全屏 OCR → [(cx, cy, text, score)]"""
    result, _ = get_ocr()(img)
    items = []
    for box, text, score in (result or []):
        cx, cy = box_center(box)
        items.append((cx, cy, text, float(score)))
    return items


def _au_find(items, keyword, min_score=0.5):
    """在 OCR 结果里找按钮中心：先精确包含，再对≥3字关键词做模糊匹配，
    容忍 OCR 漏字（如把「一键升级」漏成「键升级」）。返回最相似命中 (cx,cy) 或 None。"""
    kw = keyword.replace(" ", "")
    best, best_r = None, 0.0
    for cx, cy, text, score in items:
        if score < min_score:
            continue
        t = text.replace(" ", "")
        if kw in t:
            r = 1.0
        elif len(kw) >= 3:
            r = difflib.SequenceMatcher(None, kw, t).ratio()
        else:
            r = 0.0
        if r >= 0.6 and r > best_r:
            best, best_r = (cx, cy), r
    return best


def _au_bottom_nav(items, adb, keyword):
    """只在底部导航区（y > 0.78h）找关键词，取最靠下的命中。

    实机教训：星图中部提示文字「基地建设-舰队编组」会被普通 _au_find("基地")
    命中，但其 y≈596 不在底部，点了没反应还让主循环误判「页面未识别」。
    """
    hits = [(int(x), int(y)) for x, y, t, sc in items or []
            if keyword in str(t) and y > adb.h * 0.78 and sc >= 0.4]
    return max(hits, key=lambda p: p[1]) if hits else None




def _pick_eta(items):
    """从一键升级面板取『资源攒够 ETA』：ETA 在面板顶部时钟图标旁（y≈200~700），
    建造队列倒计时在下部（y≈900）。优先取上部时间最大值，排除下部队列区，避免误把队列倒计时当 ETA。
    返回秒数或 None。
    """
    cands = []
    for _, cy, text, _ in _merge_lines(items):
        sec = parse_seconds(text, max_seconds=360 * 3600)
        if sec and sec >= 60:
            cands.append((cy, sec))
    if not cands:
        return None
    upper = [s for cy, s in cands if cy < 700]
    pool = upper if upper else [s for _, s in cands]
    return max(pool)


def _au_swipe_unlock(adb):
    """上滑解锁。坐标按实际分辨率换算，模拟器/真机不同屏幕都适用。"""
    sx1, sy1, sx2, sy2 = scale_box(adb, (540, 1800, 540, 800))
    adb.run("shell", "input", "swipe", str(sx1), str(sy1), str(sx2), str(sy2), "300")


def _au_wake(adb):
    """亮屏 +（息屏时）上滑解锁。
    关键：屏幕已亮时只发一个无害的 WAKEUP 事件，绝对不在游戏界面上做滑动——否则每轮
    循环的上滑会触发 UI 误触（滚屏/误点）。有密码锁时上滑无效，会由 _au_ensure_game 通知人工。"""
    if _au_screen_on(adb):
        adb.run("shell", "input", "keyevent", "KEYCODE_WAKEUP")
        return
    adb.run("shell", "input", "keyevent", "KEYCODE_WAKEUP")
    time.sleep(0.8)
    _au_swipe_unlock(adb)
    time.sleep(1.0)


# 退出游戏确认框护栏：真机上系统返回键(keyevent 4)会触发该框，必须拦截，否则循环会卡死/误退出
_AU_EXIT_KEYWORDS = ("确定要退出游戏",)
_AU_EXIT_CANCEL = (1495, 700)
# 在「一键升级面板 / 建筑详情」里点升级按钮时，要排除这些易混淆文字，避免误点其它动作
_AU_UPGRADE_BTN_EXCLUDE = ["一键升级", "可升级", "升级中", "立即升级"]
# 注：基地建设页右上 (1879~1915, 67~69) 是【直播挂件】而非「一键升级」按钮，
# 点它会陷入死循环（见项目硬约束）。禁止再把该坐标用作点击兜底。
_AU_STRAY_MODAL_KEYS = ("扩建规模", "扩建条件", "基地扩建")  # 误开的遮挡弹窗关键词
# 活动弹窗（暗流汹涌 / 危机-防御劫掠者战役 / 反攻时机 / 资产技术成果展销…）：
# 整屏遮罩 + 中央大标题 + 下方「前往查看」按钮 + 右上 X。
# 用户定稿（2026-11-23）：【不识别主题】——标题每次都不一样，只认弹窗自身的
# 「前往查看」按钮特征，出现就点右上 X 关掉。不靠任何标题 / 副标题文字。
_AU_EVENT_BANNER_CTA = "前往查看"


def _au_guard_exit(adb, items):
    """安全护栏：若误弹「确定要退出游戏？」框（如系统返回键触发），优先 OCR 找「取消」按钮点击，避免误退出。返回 True 表示已处理。"""
    if any(any(k in t for k in _AU_EXIT_KEYWORDS) for _, _, t, _ in items):
        pos = find_button(items, ["取消"], min_score=0.4)
        if pos is None:
            x1, y1, _, _ = scale_box(adb, _AU_EXIT_CANCEL)
            pos = (x1, y1)
            log("未 OCR 命中取消，回退到兜底坐标", "warn")
        adb.tap(*pos)
        log("安全护栏：点取消，已拦截退出游戏框", "warn")
        time.sleep(2)
        return True
    return False


def _au_has_event_banner(items):
    """是否弹出这类活动弹窗——按用户定稿【不识别主题】，只认弹窗自身的
    「前往查看」按钮：整屏遮罩弹窗都有它，出现即判定并点右上 X 关闭。

    标题每次都不同（暗流汹涌 / 危机-防御劫掠者战役 / 反攻时机 / 资产技术成果展销…），
    所以完全不用标题 / 副标题文字判定，避免换个活动就漏判。
    """
    texts = [str(t) for _, _, t, _ in items or []]
    return any(_AU_EVENT_BANNER_CTA in t for t in texts)


def _au_close_event_banner(adb, img):
    """关闭活动弹窗：优先右上 X 模板（限定上半屏），兜底 close_x 注册表坐标。
    绝不点「前往查看」，避免跳进活动页打断自动升级 / 日常。"""
    pos = find_close_x(adb, img,
                       search=(0, 0, adb.w, scale_box(adb, (0, 0, 0, 320))[3]))
    if pos is None:
        pos = scale_box(adb, (*ICON_ONLY_ENTRIES["close_x"]["fallback"], 0, 0))[:2]
        log("活动弹窗 X 未模板命中，用 close_x 注册表兜底坐标", "warn")
    adb.tap(*pos)
    time.sleep(2)
    return pos


# ============ 前台/亮屏守护 + 资源读数（挂机可靠性增强） ============
_GAME_PKG = "com.netease.lglr"
_GAME_LAUNCH_WAIT = 20          # 重新拉起游戏后的等待秒数

# 顶部资源条三个数值的中心坐标（2310x1080 基准，实测）
_AU_RES_SLOTS = (("金属", 576, 69), ("原料", 800, 69), ("核燃料", 1028, 69))


def _num_cn(s):
    """把「1414万」「33.3万」「1.2亿」「4100」解析成整数；解析失败返回 None。"""
    if not s:
        return None
    t = str(s).replace(",", "").replace(" ", "").strip()
    m = re.search(r"(\d+(?:\.\d+)?)", t)
    if not m:
        return None
    v = float(m.group(1))
    if "亿" in t:
        v *= 100000000
    elif "万" in t:
        v *= 10000
    return int(v)


def _au_foreground(adb):
    """返回当前前台窗口的应用标识（如 com.netease.lglr/...）。取不到返回空串。"""
    try:
        r = adb.run("shell", "dumpsys", "window", timeout=25)
        txt = (r.stdout or b"").decode("utf-8", "replace")
    except Exception:
        return ""
    for line in txt.splitlines():
        if "mCurrentFocus" in line or "mFocusedApp" in line:
            m = re.search(r"([A-Za-z0-9_\.]+)/[A-Za-z0-9_\.]*", line)
            if m:
                return m.group(1)
    return ""


def _au_screen_on(adb):
    """屏幕是否亮着（mWakefulness=Awake）。探测失败按“亮着”处理，避免无谓唤醒。"""
    try:
        r = adb.run("shell", "dumpsys", "power", timeout=25)
        txt = (r.stdout or b"").decode("utf-8", "replace")
    except Exception:
        return True
    if "mWakefulness=Awake" in txt:
        return True
    if "mWakefulness=" in txt:
        return False
    return True


def _au_ensure_game(adb, qq=None):
    """挂机自愈：确保「屏幕亮 + 游戏在前台」。
    处理三种掉线场景：手机息屏、游戏被系统杀/崩、误切到其它应用。
    返回 True 表示做过干预（调用方可据此跳过本轮识别）。"""
    acted = False
    if not _au_screen_on(adb):
        adb.run("shell", "input", "keyevent", "KEYCODE_WAKEUP")
        time.sleep(1.2)
        _au_swipe_unlock(adb)
        time.sleep(1.2)
        log("屏幕已熄灭 → 唤醒并上滑解锁", "warn")
        acted = True

    fg = _au_foreground(adb)
    if _GAME_PKG not in fg:
        log(f"前台不是游戏（{fg or '未知'}）→ 重新拉起游戏", "warn")
        try:
            adb.run("shell", "monkey", "-p", _GAME_PKG,
                    "-c", "android.intent.category.LAUNCHER", "1", timeout=45)
        except Exception as e:
            log(f"拉起游戏失败：{e}", "err")
        time.sleep(_GAME_LAUNCH_WAIT)
        if qq:
            qq.push("relaunch", "检测到游戏不在前台，已自动重新拉起", force=True)
        acted = True
    return acted


def _au_res_raw(adb):
    """读取顶部资源条，返回 {名称: (持有int, 上限int)}。读不到的项不出现。"""
    img = adb.screenshot()
    if img is None:
        return {}
    items = _au_ocr_all(img)
    out = {}
    for name, bx, by in _AU_RES_SLOTS:
        cand = None
        best = 10 ** 9
        for cx, cy, text, score in items:
            if score < 0.4 or "/" not in text:
                continue
            d = abs(cx - bx) + abs(cy - by) * 2      # 纵向偏差权重大：只认顶部那一行
            if d < best and d < 220:
                best, cand = d, text.replace(" ", "")
        if not cand:
            continue
        cur_s, _, max_s = cand.partition("/")
        cur, mx = _num_cn(cur_s), _num_cn(max_s)
        if cur is not None:
            out[name] = (cur, mx)
    return out


def _au_res_dict(adb):
    """只要持有量：{名称: int}"""
    return {k: v[0] for k, v in _au_res_raw(adb).items()}


def _au_res_dict_retry(adb, tries=3, gap=2.0):
    """读资源，读空就重试（游戏加载中/转场时顶部资源条 OCR 不出来）。"""
    for _ in range(max(1, tries)):
        d = _au_res_dict(adb)
        if d:
            return d
        time.sleep(gap)
    return {}


def _fmt_num(n):
    """整数转「1,414万」这类紧凑显示"""
    if n is None:
        return "?"
    if abs(n) >= 100000000:
        return f"{n / 100000000:.2f}亿"
    if abs(n) >= 10000:
        return f"{n / 10000:.1f}万"
    return str(n)


def _au_res_diff(before, after):
    """对比两次资源快照，返回 [(名称, 增量)]，只保留有变化的项"""
    diff = []
    for k, v in after.items():
        b = before.get(k)
        if b is not None and v != b:
            diff.append((k, v - b))
    return diff


def _state_file():
    return os.path.join(app_dir(), "state.json")


def _load_state():
    """读取运行状态（跨天标记等）。文件缺失/损坏时返回空字典，不抛异常。"""
    try:
        with open(_state_file(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_state(d):
    try:
        with open(_state_file(), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"写入 state.json 失败：{e}", "warn")


def _auto_daily_due():
    """判断「跨天自动日常」是否到期：过了配置的重置时刻且今天还没跑过。"""
    cfg = CONFIG.get("daily", {})
    if not cfg.get("auto_daily", True):
        return False
    hour = int(cfg.get("auto_daily_hour", 4))
    now = time.localtime()
    if now.tm_hour < hour:
        return False
    return _load_state().get("last_daily") != time.strftime("%Y-%m-%d", now)


def _mark_daily_done():
    st = _load_state()
    st["last_daily"] = time.strftime("%Y-%m-%d")
    st["last_daily_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _save_state(st)


def _publish_upgrade_etas(times, names=None, count=None):
    """把当前两条升级倒计时写进 state.json，供 GUI 底部倒计时条实时显示。

    times: 倒计时秒数列表（最多取 2 条）；names: 对应建筑名（可选）；
    count: 正在升级的数量（可选，缺省按 len(times)）。
    结构：upgrade_etas = [{"sec":秒, "name":名, "at":写入时刻}, ...]
    GUI 读取后用 at + sec 本地逐秒递减，即使脚本在静默等待也能实时走动。
    空列表表示当前没有在升级，GUI 会清空显示。
    """
    try:
        st = _load_state()
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        slots = []
        for i, sec in enumerate(list(times or [])[:2]):
            slots.append({
                "sec": int(sec),
                "name": str((names or [])[i]) if names and i < len(names) else "",
                "at": now,
            })
        st["upgrade_etas"] = slots
        st["upgrade_count"] = int(count) if count is not None else len(slots)
        st["upgrade_at"] = now
        _save_state(st)
    except Exception as e:
        log(f"写入升级倒计时到 state.json 失败：{e}", "warn")


def _au_close_panel(adb):
    """关闭建筑详情面板：优先图标模板找 X；否则点面板外空白区（建筑详情是左侧抽屉，
    点右侧基地视图即收起，回到星图）。关闭后验证面板已收起，否则再点一次外侧。"""
    img = adb.screenshot()
    pos = None
    if img is not None:
        pos = find_close_x(adb, img, search=(0, 0, adb.w, scale_box(adb, (0, 0, 0, 320))[3]))
    if pos is None:
        # 左侧抽屉面板：点右侧基地视图空白区收起（约屏幕 60% 宽、50% 高处）
        pos = (int(adb.w * 0.6), int(adb.h * 0.5))
        log("未模板命中关闭X，点面板外空白区收起", "warn")
    adb.tap(*pos)
    time.sleep(1.5)
    # 验证：面板若还在（仍能识别到详情特征）则再点一次外侧
    img2 = adb.screenshot()
    if img2 is not None and _au_in_building_detail(_au_ocr_all(img2)):
        log("面板似乎未关闭，再点一次外侧", "warn")
        adb.tap(int(adb.w * 0.6), int(adb.h * 0.5))
        time.sleep(1.5)


def _au_back_to_panorama(adb):
    """点完一键升级后收起【建筑列表视图】→ 回到 3D 全景视图。

    建造队列条（两条倒计时 + 2/2）只在 3D 全景视图底部；停在建筑列表视图
    会读不到队列倒计时，主循环误判「有空位」而反复点一键升级（2026-11-23 实测）。
    收起前先确认当前确实是列表视图，避免在全景视图里误点右上空白/分享区。
    """
    img = adb.screenshot()
    items = _au_ocr_all(img) if img is not None else []
    if not _au_base_list_view(items, adb):
        log("已不在建筑列表视图，无需收起", "info")
        return
    adb.tap(*scale_box(adb, (*_AU_BASE_COLLAPSE_BTN, 0, 0))[:2])
    time.sleep(3)
    log("已收起建筑列表，回到全景视图读两条建造队列倒计时", "step")


def _au_handle_panel(adb, qq, items):
    """一键升级面板：资源够→点升级；不够→读预估时间等待；队列满→关闭读倒计时"""
    texts = [t for _, _, t, _ in items]
    log("处理一键升级面板", "step")
    # 把面板里每个待升级项的倒计时打出来，便于确认「一次能否排两个」以及各自还要多久
    panel_times = [(cx, cy, t, parse_seconds(t)) for cx, cy, t, _ in items
                   if parse_seconds(t) is not None]
    if panel_times:
        detail = "；".join(f"({cx},{cy}) {t}" for cx, cy, t, _ in panel_times[:8])
        log(f"面板内识别到 {len(panel_times)} 条倒计时：{detail}", "info")
    names = [t for _, _, t, _ in items if "升级" in t and "一键" not in t]
    if names:
        log("待升级项：" + "、".join(names[:8]), "info")

    # 2026-11-23 实机修正：面板左下「消耗资源」显示的是「本次消耗 / 当前拥有」
    # （如 23.5万/106.4万），斜杠前小、斜杠后大恰好表示【资源充足】。旧逻辑把它当成
    # 「当前 < 需要」→ 误判资源不足，还把面板里的升级耗时 03:00:00 当成攒够 ETA，
    # 直接关面板长等 3 小时，结果脚本点开了一键升级却从不去点升级按钮。
    # 用户定稿：循环只做「点一键升级 + 看倒计时」，资源够不够交给游戏自己判定
    # （点击后若弹出不足提示，下面的校验分支会处理），这里不再做资源预判等待。
    # 橙色「升级」按钮：OCR 优先（右下区域、排除"一键升级/可升级/升级中/立即升级"），找不到用设计坐标兜底
    btn = find_button(items, ["升级"], exclude=_AU_UPGRADE_BTN_EXCLUDE,
                      region=(int(adb.w * 0.5), int(adb.h * 0.65), adb.w, adb.h),
                      min_score=0.5)
    if btn is None:
        bx, by, _, _ = scale_box(adb, (1642, 887, 0, 0))
        btn = (bx, by)
        log("OCR 未命中升级键，回退到兜底坐标", "warn")
    adb.tap(*btn)
    globals()["_AU_LAST_CLICK_TS"] = time.time()
    log(f"点击一键升级面板的升级按钮 {btn}（本轮只点一次）", "ok")
    time.sleep(2.5)

    # 校验点击结果
    img = adb.screenshot()
    items2 = _au_ocr_all(img) if img is not None else []
    texts2 = [t for _, _, t, _ in items2]
    if any("上限" in t or "队列已满" in t for t in texts2):
        globals()["_AU_FULL_STREAK"] = _AU_FULL_STREAK + 1
        log(f"建造队列已满（连续第 {_AU_FULL_STREAK} 次），关闭面板", "warn")
        _au_close_panel(adb)
        _au_back_to_panorama(adb)
        return
    if any("不足" in t for t in texts2):
        log("点击后提示资源不足，关闭面板，下轮改由倒计时/空位判断处理", "warn")
        _au_close_panel(adb)
        _au_back_to_panorama(adb)
        return
    qq.push("upgrade_click", "已点击升级（加入建造队列）")
    # 点「升级」后游戏会自动关闭一键升级面板，不必再点外侧空白（那会误触基地模型）。
    # 直接收起列表回全景视图，下一轮才能读到两条队列倒计时并静默等待，不反复点。
    _au_back_to_panorama(adb)


_AU_BOTTOM_Y = 900      # 底部建造队列状态栏（判定/选点阈值）
# 建筑卡里的倒计时位置比旧假设更高：1600x900 实机约 y=720~860，
# 所以单独给倒计时读取用更宽的阈值，不影响「建筑名只在底部选点」的规则。
_AU_TIMER_BOTTOM_Y = 820
# 底部队列条的真实区域（设计基准）：建筑名/倒计时在这里；左侧「返回地图」、底部
# 「基地建设 / 行政」是导航，绝不能当成可点的建筑条目（2026-11-22 实机 bug）。
_AU_QUEUE_BAR_BOX = (1450, 880, 2150, 1010)
_AU_NAV_WORDS = ("返回地图", "返回", "基地建设", "基地", "行政", "星图", "计划",
                 "舰队", "战报", "商业中心", "商城", "蓝图", "研究", "活动时",
                 # 基地页展开视图底部的导航/功能键，绝不能当建筑点（2026-11-22 实测死循环根因）
                 "对接信息", "视野收集", "环绕视角", "舰队编组", "战舰生产", "资源管理",
                 "舰船管理", "情报搜索", "查看", "装备和对接点", "已方计划", "情报")


def _au_queue_full(items, adb=None):
    """底部队列状态判定满载：读到『2/2』这类 a/a（a>=2）或『队列已满』即为满。

    实机教训：底部两条倒计时中常有一条被 OCR 读成 `881510` 之类无法解析的数字，
    只看 `_au_queue_times()` 的长度会漏判满载 → 误进补位循环、反复开合面板。
    """
    texts = [str(t).strip() for _, _, t, _ in items or []]
    if any(("队列已满" in t) or ("已达上限" in t) or ("队列满" in t) for t in texts):
        return True
    bottom = int(adb.h * _AU_BOTTOM_Y / DESIGN_H) if adb is not None else _AU_BOTTOM_Y
    for _, cy, t, sc in items or []:
        if cy <= bottom or sc < 0.4:
            continue
        s = str(t).replace(" ", "")
        m = re.fullmatch(r"(\d+)/(\d+)", s)
        # 实机底部同时有建筑等级「4/4」「5/5」，不能当队列满载；
        # 建造队列只有 2 个槽位，满载特征就是「2/2」。
        if m and m.group(1) == m.group(2) and int(m.group(1)) == 2:
            return True
    return False


def _au_queue_times(items, adb=None):
    """从底部建造队列状态栏读取进行中升级的倒计时（秒）。仅取底部栏，
    排除面板/详情里中部的倒计时，避免把建筑升级倒计时误当队列状态。
    adb 传入时按实际分辨率换算底部阈值，保证模拟器（720p/1080p/1440p）同样生效。"""
    bottom = int(adb.h * _AU_TIMER_BOTTOM_Y / DESIGN_H) if adb is not None else _AU_TIMER_BOTTOM_Y
    times = []
    for _, cy, text, _ in items:
        if cy < bottom:
            continue
        sec = parse_seconds(text)
        if sec and sec >= 30:
            times.append(sec)
    return times


def _au_upgrade_state(items, adb=None):
    """【第一时间判定】当前有几个建筑正在升级。

    返回 (count, times, readable)：
      count    —— 0/1/2，正在升级的数量（0 仅在明确读到空队列时给出）
      times    —— 底部读到的倒计时秒数列表
      readable —— 是否读到了可靠队列状态（2/2、a/2 或至少一条倒计时）。
                  False 表示视图不对/OCR 漏读，绝不能当"有空位"去点升级空转。

    实机（1600x900）3D 全景视图底部：通用港口4 02:00:40、金属炼化 00:33:42、2/2；
    建筑列表视图底部是建筑卡片等级（4/4、2/4…），没有队列条 —— 必须先回到全景视图再判。
    """
    qtimes = _au_queue_times(items, adb)
    full = _au_queue_full(items, adb)
    bottom = int(adb.h * _AU_BOTTOM_Y / DESIGN_H) if adb is not None else _AU_BOTTOM_Y
    slots = []
    for _, cy, t, sc in items or []:
        if cy <= bottom or sc < 0.4:
            continue
        m = re.fullmatch(r"(\d+)/(\d+)", str(t).replace(" ", ""))
        if m and 1 <= int(m.group(2)) <= 2:     # 建造队列只有 2 个槽位
            slots.append((int(m.group(1)), int(m.group(2))))
    readable = bool(full or qtimes or slots)
    if full or len(qtimes) >= 2 or any(a >= 2 and b >= 2 for a, b in slots):
        return 2, qtimes, True
    if len(qtimes) == 1:
        return 1, qtimes, True
    if slots:
        return max(a for a, _ in slots), qtimes, True
    return 0, qtimes, readable


# 连续“队列已满”次数：用于抑制「开详情→队列已满→关面板→立刻再开」的振荡。
# 队列栏 OCR 在面板开合瞬间容易漏读 <2 条，会被误判成“有空位”。
_AU_FULL_STREAK = 0
_AU_NOREAD_STREAK = 0    # 连续「读不到队列又没进展」计数，用于熔断盲点死循环
# 2026-11-23 升级循环定稿：只读倒计时 + 到期后点一次「一键升级」。
_AU_LAST_CLICK_TS = 0.0  # 上次点升级的时间，防止倒计时结束后反复空点
_AU_WAIT_SIGNATURE = None  # 本轮正在等的倒计时签名，避免同一条倒计时重复等待/重复点击


def _au_in_building_detail(items):
    """是否已进入某建筑详情面板：有『等级/升至满级/连续升级设置』等特征字。"""
    texts = [t for _, _, t, _ in items]
    return (any(k in t for t in texts for k in ("升至满级", "连续升级设置", "升至2级"))
            or any("等级" in t for t in texts)) \
        and any("基地建设" in t for t in texts)


def _au_on_base_build(items):
    """是否在基地建设页：必须同时看到『基地建设』和基地页左下角『返回地图』。

    2026-11-22 实机 bug：星图主界面里有一处提示文字「基地建设-舰队编组」，
    旧逻辑只要出现"基地建设"就认为已在基地页，于是永远不会导航进去，
    底部只有『对接信息 / 视野收集』导航，补位环节一直空转、从不升级。
    """
    texts = [t for _, _, t, _ in items]
    # 基地页有两种布局：常规视图左下有「返回地图」；3D/折叠视图只剩顶部
    # 「基地建设」+ 中央区/港区/工业区分类栏。星图提示语「基地建设-舰队编组」
    # 不会有这些分区名，所以可以安全区分。
    in_base = any("返回地图" in t for t in texts) or any(
        k in t for t in texts for k in ("中央区", "港区", "工业区"))
    return (any("基地建设" in t for t in texts)
            and in_base
            and not _au_in_building_detail(items))


def _au_base_list_view(items, adb):
    """是否已在基地建设【建筑列表视图】（右上角有「一键升级」按钮的那种）。

    1600x900 实机 OCR 对比（2026-11-23）：
      · 建筑列表视图：左上 y≈57 有「基地建设」标题，中部 y≈578 有
        「中央区/港区/工业区」分区标题 + 建筑卡片等级（2/4、4/4…），
        右上 y≈56 有「一键升级」文字按钮。
      · 3D 全景视图：左上无标题，「基地建设 / 中央区 / 港区」全挤在底部
        y≈823~842，中部是「资源管理 / 战舰生产 / 舰队编组」等功能标签，
        右上只有分享 + 眼睛图标（同坐标点下去就是分享面板）。
    只有确认是列表视图，才允许用 _AU_GLOBAL_UPGRADE_BTN 兜底点按钮。
    """
    h = getattr(adb, "h", None) or DESIGN_H
    title = any("基地建设" in str(t) and y < h * 0.2
                for _, y, t, _ in items or [])
    zones = any(any(k in str(t) for k in ("中央区", "港区", "工业区"))
                and h * 0.4 < y < h * 0.8
                for _, y, t, _ in items or [])
    return title and zones


def _au_select_open_target(items, adb, target=None, locked=None):
    """纯逻辑：从底部队列条里挑一个建筑条目用于打开详情补位。
    返回 (cx, cy, kind) 或 None；kind ∈ {'name','time'} 表示命中的是建筑名还是倒计时文字。
    规则：优先目标建筑（名字含 target）→ 否则最左的建筑名（比倒计时更可靠地点）
    → 否则最左的倒计时文字兜底。命中目标/候选名若落在 locked 名单内则跳过（保护保级建筑）。
    不含任何点击，便于单测。"""
    bottom = int(adb.h * _AU_BOTTOM_Y / DESIGN_H)
    bx1, by1, bx2, by2 = scale_box(adb, _AU_QUEUE_BAR_BOX)
    _locked = [str(x).strip() for x in (locked or []) if str(x).strip()]
    # 底栏里可能出现的非建筑名文字（标签/状态/资源/导航），不应被当作可点的建筑条目
    _NON_NAME = ("建造队列", "资源", "全部", "队列", "金属", "原料", "核燃料",
                 "建造中", "升级中", "进行中", "使用中", "已暂停", "已满", "暂停") + _AU_NAV_WORDS
    def _locked_hit(name):
        return any(k in name for k in _locked)
    name_items, time_items = [], []
    out_names, out_times = [], []          # 队列条区域外的候选，仅作兜底（导航词已排除）
    for cx, cy, t, sc in items:
        if cy <= bottom or sc < 0.4:
            continue
        ts = t.strip()
        if not ts:
            continue
        in_bar = (bx1 <= cx <= bx2 and by1 <= cy <= by2)
        if parse_seconds(ts):                       # 倒计时文字（如 34:30:00）
            (time_items if in_bar else out_times).append((cx, cy, ts))
        elif re.search(r"[一-鿿]", ts) and ts not in _NON_NAME and len(ts) >= 2:
            if _locked_hit(ts):
                continue                            # 锁定名单内的建筑名直接跳过
            (name_items if in_bar else out_names).append((cx, cy, ts))
    # 优先用队列条区域内的条目；区域外只作兜底（旧设备/异形布局）。
    # 实机 bug：左侧「返回地图」在 y 阈值之下，曾因取最左中文名而被当成建筑点掉。
    if not name_items:
        name_items = out_names
    if not time_items:
        time_items = out_times
    if target:
        for cx, cy, t in name_items:
            if target in t:
                return (cx, cy, "name")
    if name_items:
        name_items.sort(key=lambda q: q[0])
        return (name_items[0][0], name_items[0][1], "name")
    if time_items:
        time_items.sort(key=lambda q: q[0])
        return (time_items[0][0], time_items[0][1], "time")
    return None


def _au_open_target(adb, items):
    """补位：打开一个建筑详情以加入建造队列。
    唯一稳定入口是底部『建造队列状态栏』里的建筑条目（点它即开详情）。
    启用速本方案时按清单顺序挑；strict=true 且清单未命中则不补位（不乱升）；
    未启用方案时保持原有『单个目标建筑 + 锁定名单』逻辑。"""
    pname, prof = active_profile()
    locked = profile_locked(prof) if prof else list(CONFIG.get("au_locked_buildings") or [])
    if prof:
        sel = profile_pick_target(items, adb, prof, skipped=_AU_REACHED, locked=locked)
        if sel:
            cx, cy, name, lvl = sel
            log(f"速本方案[{pname}] → {name}（目标 {lvl or '不限'} 级）：{cx},{cy}", "step")
            adb.tap(cx, cy)
            time.sleep(2.5)
            return True
        if prof.get("strict"):
            log(f"速本方案[{pname}] 清单里的建筑都没在队列条出现 → 严格模式不补位", "warn")
            return False
        log(f"速本方案[{pname}] 未命中清单，回退默认补位逻辑", "warn")
    target = (CONFIG.get("au_target_building") or "").strip()
    sel = _au_select_open_target(items, adb, target, locked)
    if sel:
        log(f"打开建筑详情补位（{sel[2]}）：{sel[0]},{sel[1]}", "step")
        adb.tap(sel[0], sel[1])
        time.sleep(2.5)
        return True
    # 读不到可点建筑时不再盲点左下固定坐标：实机证明那个位置在展开视图下是
    # 「对接信息 / 视野收集」导航，点了会在基地页两种视图间来回切换、永远补不了位。
    # 返回 False 让主循环走「读不到队列 → 稍后重试 / 熔断」的安全路径。
    log("底栏未读到可点建筑，跳过本轮补位（不盲点导航区）", "warn")
    return False


def _au_skip_reached(adb, items):
    """建筑详情里：若清单建筑已达目标等级，记入跳过集合并关面板，避免反复打开。
    读不到当前等级时一律返回 False（保守，照常升级，绝不因 OCR 漏读而漏升）。"""
    _, prof = active_profile()
    if not prof:
        return False
    hit = match_profile_building(items, prof)
    if not hit:
        return False
    name, target = hit
    if target <= 0:
        return False
    lvl = parse_level_from_items(items)
    if lvl is None or lvl < target:
        return False
    if name not in _AU_REACHED:
        log(f"速本方案：{name} 已达 {lvl}/{target} 级，跳过", "ok")
        _AU_REACHED.add(name)
    _au_close_panel(adb)
    return True


def _au_tap_upgrade(adb, qq, items):
    """在建筑详情面板里点游戏内的【一键升级】按钮发起升级。

    用户要求：autoup 升级循环只走「一键升级」这一条路线。
    找不到「一键升级」就不点，绝不用「升至满级 / 连续升级设置 / 普通升级」替代。
    返回 True 表示已处理（点了/资源不足/队列已满），False 表示面板里没找到一键升级。
    """
    btn = find_button(items, ["一键升级"], min_score=0.5)
    if btn is None:
        log("建筑详情内未识别到「一键升级」，本轮不升级（不用普通升级替代）", "warn")
        return False
    log(f"点击一键升级 ({btn[0]},{btn[1]})", "ok")
    adb.tap(*btn)
    time.sleep(2.5)

    # 可能弹出等级选择子面板（连续升级设置 / 升至2级 / 升至满级）→ 再点『升至满级』
    img = adb.screenshot()
    items2 = _au_ocr_all(img) if img is not None else []
    texts2 = [t for _, _, t, _ in items2]
    if any(k in t for t in texts2 for k in ("连续升级设置", "升至2级")):
        b2 = find_button(items2, ["升至满级"], min_score=0.5)
        if b2:
            log("等级选择子面板 → 点『升至满级』", "step")
            adb.tap(*b2)
            time.sleep(2.5)
            img = adb.screenshot()
            items2 = _au_ocr_all(img) if img is not None else []
            texts2 = [t for _, _, t, _ in items2]

    # 资源不足：读 ETA 后关面板等待
    if any("不足" in t for t in texts2):
        eta = _pick_eta(items2)
        wait = (eta or 600) + 60
        log(f"资源不足，预估 {fmt_duration(eta or 600)} 后攒够，等待 {fmt_duration(wait)}", "warn")
        qq.push("insufficient", f"资源不足，{fmt_duration(wait)} 后重试")
        _au_close_panel(adb)
        time.sleep(wait)
        return True
    # 队列已满：关面板，下轮重新判断
    if any(("队列已满" in t) or ("已达上限" in t) or ("上限" in t) for t in texts2):
        log("建造队列已满，关闭面板", "warn")
        global _AU_FULL_STREAK
        _AU_FULL_STREAK += 1
        _au_close_panel(adb)
        return True
    qq.push("upgrade_click", "已加入建造队列（连续升级）")
    _au_close_panel(adb)
    return True


def cmd_autoup(adb, qq):
    """一键升级自动循环入口：倒计时驱动，队列先到先补。
    真实 UI 流程：基地建设页底部队列栏读倒计时 → 满载则等待 → 有空位则打开目标建筑详情
    → 点『升至满级/连续升级设置』→ 处理等级选择/资源不足/队列已满。"""
    # 模拟器 / 真机都支持：按 config.device.serial 选设备（留空自动挑在线通道）
    _pick_device_by_pref(adb)

    qq.push("start", "一键升级自动循环已启动（真机/模拟器通用）")
    perf = perf_settings()
    log(f"进入自动升级循环：倒计时驱动，等待期间零占用  ·  性能档位 "
        f"{PERF_LABELS.get(perf['mode'], perf['mode'])}"
        f"（OCR {perf['ocr_threads'] or '不限'} 线程 / 每轮间歇 {perf['loop_min_interval']:g}s"
        f" / 卡住退避上限 {perf['backoff_max']:g}s）", "step")
    fails = 0
    no_progress = 0
    try:
        while True:
            progress = False
            try:
                _au_wake_paced(adb, perf["wake_every"])
                # 0a) 挂机自愈：屏幕灭了就唤醒、游戏被切走/被杀就重新拉起
                if _au_health_paced(adb, qq, perf["health_every"]):
                    fails = 0
                    progress = True
                    continue

                # 0b) 跨天自动日常：过了重置时刻且今天没跑过，先领一轮再继续升级
                if _auto_daily_due():
                    log("跨天日常到期 → 先执行一轮日常领取", "step")
                    try:
                        cmd_daily(adb, qq)
                        _mark_daily_done()
                        log("跨天日常完成，回到升级循环", "ok")
                    except Exception as e:
                        log(f"跨天日常失败（稍后重试）：{e}", "err")
                        time.sleep(120)
                    fails = 0
                    progress = True
                    continue

                img = adb.screenshot()
                if img is None:
                    raise RuntimeError("截图失败")
                items = _au_ocr_all(img)
                texts = [t for _, _, t, _ in items]

                # 0) 安全护栏：优先处理误弹的退出游戏确认框（真机系统返回键会触发），避免误退出
                if _au_guard_exit(adb, items):
                    fails = 0
                    continue

                # 0b) 误开的「基地扩建」类弹窗挡住目标（右上角有自己的X）→ 用图标模板关掉
                if any(k in t for t in texts for k in _AU_STRAY_MODAL_KEYS):
                    xpos = find_close_x(adb, img, search=(0, 0, adb.w, scale_box(adb, (0, 0, 0, 320))[3]))
                    if xpos:
                        log("关闭误开的遮挡弹窗（基地扩建）", "warn")
                        adb.tap(*xpos)
                        time.sleep(2)
                        continue

                # 0c) 误开的「分享」面板：3D 全景视图点右上会弹出（OCR 可读到「小红书」等），
                #     先点右上 X 关掉，再由后续分支重新点进基地建设，不让它挡住整个循环。
                if any(k in t for t in texts for k in _AU_SHARE_PANEL_KEYS):
                    log("检测到误开的分享面板 → 点右上 X 关闭", "warn")
                    adb.tap(*scale_box(adb, (*_AU_SHARE_CLOSE_BTN, 0, 0))[:2])
                    time.sleep(2)
                    fails = 0
                    progress = True
                    continue

                # 0d) 「暗流汹涌 · 新章节开启」活动弹窗（整屏遮罩 + 前往查看按钮）
                #     挡住基地页 → 点右上 X 关掉；不点「前往查看」，避免跳进活动打断循环。
                if _au_has_event_banner(items):
                    pos = _au_close_event_banner(adb, img)
                    log(f"检测到「暗流汹涌」活动弹窗 → 点右上 X 关闭 {pos}", "warn")
                    fails = 0
                    progress = True
                    continue

                # 1) 【第一时间】确保在能读到队列的 3D 全景视图：
                #    建筑列表视图底部是建筑卡片、没有队列条，读不到"几个在升级"，
                #    旧逻辑会误判空位 → 反复点升级又点不开 → 30 秒复查空转（死循环根因）。
                #    所以先数升级数量：若 readable=False（视图不对/OCR漏读），先收起回全景重读。
                count, qtimes, readable = _au_upgrade_state(items, adb)
                if not readable and _au_base_list_view(items, adb):
                    log("在建筑列表视图读不到队列 → 先收起回全景视图确认升级状态", "step")
                    _au_back_to_panorama(adb)
                    img = adb.screenshot()
                    items = _au_ocr_all(img) if img is not None else items
                    texts = [t for _, _, t, _ in items]
                    count, qtimes, readable = _au_upgrade_state(items, adb)
                    progress = True

                # 把读到的倒计时发布给 GUI 底部倒计时条（GUI 定时读 state.json）。
                # 只在可靠读到队列时发布，避免建筑列表视图的误读污染显示。
                if readable:
                    qnames = [t for _, cy, t, _ in items
                              if cy >= int(adb.h * _AU_TIMER_BOTTOM_Y / DESIGN_H)
                              and not re.fullmatch(r"[\d:]+", str(t).strip())
                              and not re.fullmatch(r"\d+/\d+", str(t).replace(" ", ""))
                              and str(t).strip() not in _AU_NAV_WORDS][:2]
                    _publish_upgrade_etas(qtimes, qnames, count)

                # 1a) 数出"几个在升级"后分流（用户定稿 2026-11-23）：
                #     2 个在升级 → 看倒计时还有多久，不管它（静默等待）
                #     1 个在升级 → 看这一个还有多久，去点一次一键升级补位
                #     0 个       → 去点一次一键升级
                if count >= 2:
                    globals()["_AU_FULL_STREAK"] = 0
                    globals()["_AU_NOREAD_STREAK"] = 0
                    eta = min(qtimes) if qtimes else _pick_eta(items)
                    signature = (len(qtimes), round(eta or 0))
                    if globals().get("_AU_WAIT_SIGNATURE") == signature:
                        # 同一条倒计时已经被等待过一次：游戏还没释放队列，短等复查，绝不反复点
                        time.sleep(30)
                        progress = True
                        continue
                    globals()["_AU_WAIT_SIGNATURE"] = signature
                    near = fmt_duration(eta) if eta else "倒计时未读到"
                    wait = max(20, eta + 20) if eta else 300
                    log(f"2 个建筑都在升级（读到{len(qtimes)}条倒计时），最近到期 {near}，"
                        f"不管它、静默等待 {fmt_duration(wait)}", "ok")
                    qq.push("long_upgrade", f"两个升级倒计时，{near} 后到期再点一键升级")
                    time.sleep(wait)
                    fails = 0
                    progress = True
                    continue
                if count == 1:
                    eta = qtimes[0] if qtimes else _pick_eta(items)
                    near = fmt_duration(eta) if eta else "倒计时未读到"
                    log(f"1 个建筑在升级（还需 {near}）→ 去点一次一键升级补位", "step")

                # 1b) 连续两次“队列已满”说明队列栏只是 OCR 漏读、其实仍是满载 → 强制歇一轮
                if _AU_FULL_STREAK >= 2:
                    globals()["_AU_FULL_STREAK"] = 0
                    log("连续判定『队列已满』，判定为满载但队列栏 OCR 漏读，强制等待 10 分钟", "warn")
                    qq.push("long_upgrade", "队列已满（OCR 漏读），等待 10 分钟后再试")
                    time.sleep(600)
                    progress = True
                    continue

                # 2a) 误进建筑详情面板：只关闭，不走详情里的单建筑升级。
                #     用户定稿：升级循环只走游戏内「一键升级」面板这一条路线。
                if _au_in_building_detail(items):
                    log("建筑详情面板已关闭（本循环只走一键升级）", "warn")
                    _au_close_panel(adb)
                    continue

                # 1a) 一键升级/建筑面板已打开（非详情页）→ 直接处理（这是判断队列状态最可靠的入口）
                if any(k in t for t in texts for k in _AU_PANEL_KEYS):
                    if time.time() - globals().get("_AU_LAST_CLICK_TS", 0) < 90:
                        log("刚点过一键升级，关闭面板等下一轮，不重复点", "warn")
                        _au_close_panel(adb)
                        continue
                    _au_handle_panel(adb, qq, items)
                    fails = 0
                    progress = True
                    continue

                # 队列已出现空位（或读不到倒计时）→ 允许点一次一键升级。
                # 冷却 90 秒，防止倒计时刚结束游戏还没刷新时反复空点。
                globals()["_AU_WAIT_SIGNATURE"] = None
                if time.time() - globals().get("_AU_LAST_CLICK_TS", 0) < 90:
                    time.sleep(30)
                    progress = True
                    continue

                # 2b) 在基地建设页 → 只打开右上角「一键升级」面板；
                #     不再从底部队列条打开建筑详情，避免走回单建筑升级的旧路线。
                if _au_on_base_build(items):
                    globals()["_AU_NOREAD_STREAK"] = _AU_NOREAD_STREAK + 1
                    if _AU_NOREAD_STREAK > 3:
                        globals()["_AU_NOREAD_STREAK"] = 0
                        globals()["_AU_FULL_STREAK"] = 0
                        log("连续 3 次读不到队列且界面无进展 → 熔断，等待 15 分钟", "warn")
                        qq.push("stuck", "读不到队列状态，已熔断并等待 15 分钟", force=True)
                        time.sleep(900)
                        progress = True
                        continue
                    # 「一键升级」文字按钮只存在于【建筑列表视图】；3D 全景视图的
                    # 右上同坐标是分享按钮，绝不直接点固定坐标（2026-11-23 实机误点根因）。
                    pos = _au_find(items, "一键升级", min_score=0.45)
                    if pos is None:
                        if not _au_base_list_view(items, adb):
                            # 当前是 3D 全景视图 / 其它页 → 先「点进基地建设」展开建筑列表视图
                            nav = _au_bottom_nav(items, adb, "基地建设")
                            if nav is None:
                                nav = scale_box(adb, (*_AU_BASE_LIST_ENTRY, 0, 0))[:2]
                            log(f"当前不是建筑列表视图，先点进基地建设展开列表 {nav}", "step")
                            adb.tap(*nav)
                            time.sleep(5)      # 展开转场实测 4~7 秒，等够再 OCR
                            img1 = adb.screenshot()
                            items1 = _au_ocr_all(img1) if img1 is not None else []
                            pos = _au_find(items1, "一键升级", min_score=0.45)
                            if pos is None:
                                log("点进基地建设后仍未读到「一键升级」按钮，30 秒后复查（绝不盲点右上）", "warn")
                                time.sleep(30)
                                continue
                        else:
                            # 已在建筑列表视图、只是 OCR 漏读按钮文字：
                            # 此视图该坐标确定是「一键升级」，可以安全用校准坐标兜底。
                            pos = scale_box(adb, (*_AU_GLOBAL_UPGRADE_BTN, 0, 0))[:2]
                            log(f"建筑列表视图内 OCR 漏读「一键升级」，用已校准按钮坐标 {pos}", "warn")
                    adb.tap(*pos)
                    time.sleep(2.5)
                    img2 = adb.screenshot()
                    items2 = _au_ocr_all(img2) if img2 is not None else items
                    if any(k in t for t in (t for _, _, t, _ in items2)
                           for k in _AU_PANEL_KEYS):
                        log("一键升级面板已打开", "step")
                        _au_handle_panel(adb, qq, items2)
                        globals()["_AU_NOREAD_STREAK"] = 0
                        fails = 0
                        progress = True
                        continue
                    # 面板没开：绝不原地 30 秒复查空转（那是死循环根因）。
                    # 先关掉可能误弹的面板，再回全景视图重新数"几个在升级"：
                    # 若其实已经 2 个在升级（刚才那次点已生效），下一轮会正确进入等待。
                    if any(k in t for t in (t for _, _, t, _ in items2)
                           for k in _AU_SHARE_PANEL_KEYS):
                        log("点击后误开分享面板 → 点右上 X 关闭", "warn")
                        adb.tap(*scale_box(adb, (*_AU_SHARE_CLOSE_BTN, 0, 0))[:2])
                        time.sleep(2)
                    if _au_has_event_banner(items2):
                        _au_close_event_banner(adb, img2)
                    # 回到全景视图，让下一轮第一时间能读到队列状态
                    if not _au_on_base_build(items2):
                        _au_close_panel(adb)
                    _au_back_to_panorama(adb)
                    log("一键升级面板未打开 → 已回全景视图，下轮重新数升级数量（不空转）", "warn")
                    continue

                # 3) 其它页：导航回基地。
                #    必须限定底部导航区：星图中部有提示文字「基地建设-舰队编组」，
                #    旧 _au_find("基地") 会先命中它（y=596），因 y 不够低又不点，
                #    最终每次都落到「页面未识别」，从不进基地页（2026-11-22 实机 bug）。
                pos = _au_bottom_nav(items, adb, "基地建设")
                if pos:
                    log("进入基地建设页", "step")
                    adb.tap(*pos)
                    time.sleep(2.5)
                    continue
                pos = _au_bottom_nav(items, adb, "基地")
                if pos:
                    log("主界面 → 进入基地", "step")
                    adb.tap(*pos)
                    time.sleep(2.5)
                    continue

                # 4) 页面未识别 → 绝不按系统返回键（keyevent 4）。
                # 2026-11-22 实机 P0 bug：按返回键会弹「确定退出游戏」，护栏点取消后
                # 又回到未识别页面，形成「未识别→返回键→退出框→取消」无限循环，从不升级。
                fails += 1
                log(f"页面未识别（{fails}/5），不按返回键，等待下一轮", "warn")
                if fails >= 5:
                    qq.push("lost", "连续 5 次未识别页面，请检查游戏状态", force=True)
                    fails = 0
                    time.sleep(180)
                else:
                    time.sleep(20)

            except KeyboardInterrupt:
                raise
            except Exception as e:
                fails += 1
                log(f"异常（{fails}/5）: {e}", "err")
                if fails >= 5:
                    qq.push("exception", f"自动升级连续异常: {e}", force=True)
                    fails = 0
                    time.sleep(600)
                else:
                    time.sleep(30)
            finally:
                # 每轮结束后固定休息 rest 秒。注意不能按「本轮总时长凑够 iv」扣减：
                # 实测 2 线程下一次全屏 OCR 就要 10~13 秒，比间隔还长，扣减后等于没有喘息，
                # CPU 会重新被占满。连续无进展按 1.7 倍指数退避到上限；长睡/成功升级后重置。
                if progress:
                    no_progress = 0
                else:
                    no_progress += 1
                rest = pace_interval(no_progress - 1, perf["loop_min_interval"],
                                     perf["backoff_max"])
                if rest > 0:
                    time.sleep(rest)
    except KeyboardInterrupt:
        qq.push("stopped", "一键升级自动循环已手动停止")
        log("已手动停止", "warn")


# ==================== 日常一键领取（活动 / 邮件） ====================
# 安全原则：只用 OCR 识别“领取/领奖/签到”文字按钮点击；
# 黑名单排除“补签/可补签/升级/购买/强化点/充值/确认/关闭/取消/返回”，绝不误点消费/升级。
_DAILY_CLAIM_KW = ("领取", "领奖", "签到")
_DAILY_EXCLUDE_KW = ("补签", "可补签", "升级", "购买", "强化点", "充值",
                     "确认", "关闭", "取消", "返回", "领取中", "已领取", "已领")
# 只认“按钮级”短文本（≤6 字），避免把「签到25天领专属礼装」这类横幅/说明文字当按钮点
_DAILY_BTN_MAXLEN = 6
_DAILY_CLOSE_X = (2195, 70)              # 活动/战报面板关闭 X（2026-11-22 实机校准）
_DAILY_SIDEBAR_BOX = (100, 100, 620, 1000)   # 活动面板左侧活动列表（2026-11-22 实机校准：旧左边界 300 会把 x<207 的条目全排除，只识别到 1 条）
_MAIL_LIST_BOX = (300, 90, 630, 930)     # 邮件列表区域（中间栏）
_MAIL_CAT_WORDS = ("联合体", "小队", "组织", "系统", "邮件", "收藏", "全部")
_MAIL_CLAIM_KW = ("领取", "提取附件", "一键领取", "全部领取", "领取附件")

# 任务面板（星图左上角「清单打勾」图标，无文字标签，只能靠坐标；实测 2310x1080）
_TASK_ICON = (77, 52)        # 任务面板星形徽记 #13 的最后兜底；正常路径由 ICON_ONLY_ENTRIES.task_panel_entry 模板命中
_TASK_TABS = ("主线任务", "委托任务", "支线任务", "赏金任务", "集结")
_TASK_CLOSE = (2194, 54)     # 任务面板右上展开/收起键 (1520,45)@1600x900，实机可退回星图
_TASK_CLOSE_ALT = (2100, 65)  # 旧版门形关闭图标，仅作布局差异兜底


def _daily_claim_tasks(adb, qq):
    """任务面板：逐个页签点「一键领取」。这是每日奖励的大头（主线/委托/支线/赏金/集结）。
    按钮置灰时点击无害，所以不必判断颜色。返回点击次数。"""
    img0 = adb.screenshot()
    hit = find_icon_entry(adb, img0, "task_panel_entry")
    if hit:
        x, y = hit[0], hit[1]
        log(f"任务入口由纯图标注册表定位 ({x},{y}) · {hit[2]}"
            + (f" · 匹配 {hit[3]:.2f}" if hit[2] == "template" else ""),
            "step" if hit[2] == "template" else "warn")
    else:
        x, y = scale_box(adb, (*_TASK_ICON, 0, 0))[:2]
    adb.tap(x, y)
    time.sleep(3)
    img = adb.screenshot()
    if img is None:
        return 0
    items = _au_ocr_all(img)
    if not any("主线任务" in t for _, _, t, _ in items):
        log("任务面板未打开（缺『主线任务』页签），跳过任务领取", "warn")
        return 0

    # 新环境/首次打开任务面板会弹「任务界面说明」帮助页，整块遮住页签和「一键领取」，
    # 不关掉就会一直找不到按钮而空转。点面板外侧空白关闭，最多试 3 次。
    for _ in range(3):
        items = _au_ocr_all(img) if img is not None else []
        if not any("界面说明" in t for _, _, t, _ in items):
            break
        ox, oy = scale_box(adb, (2000, 500, 0, 0))[:2]
        log("检测到『任务界面说明』帮助页，点外侧空白关闭", "warn")
        adb.tap(ox, oy)
        time.sleep(1.5)
        img = adb.screenshot()

    n = 0
    for tab in _TASK_TABS:
        img = adb.screenshot()
        if img is None:
            break
        pos = find_button(_au_ocr_all(img), [tab], min_score=0.5,
                          region=(0, 0, adb.w, scale_box(adb, (0, 0, 0, 130))[3]))
        if pos is None:
            continue
        adb.tap(*pos)
        time.sleep(1.8)
        img = adb.screenshot()
        if img is None:
            break
        btn = find_button(_au_ocr_all(img), ["一键领取"], min_score=0.5)
        if btn is None:
            continue
        log(f"任务领取: {tab} → 一键领取 ({btn[0]},{btn[1]})", "ok")
        adb.tap(*btn)
        n += 1
        qq.push("upgrade_click", f"任务领取: {tab}")
        time.sleep(2)
        # 领取后可能弹出的奖励窗/确认键
        img2 = adb.screenshot()
        if img2 is not None:
            items2 = _au_ocr_all(img2)
            p2 = find_button(items2, ["确定", "确认", "点击继续", "关闭"], min_score=0.45)
            if p2:
                adb.tap(*p2)
                time.sleep(1.5)
            elif any("奖励选择" in t for _, _, t, _ in items2):
                # 任务一键领取会弹「奖励选择」，需要玩家自己挑奖励；脚本不替用户做选择，
                # 只关掉弹窗，否则它会挡住后续所有导航，导致 daily/autoup 一直空转。
                x, y = scale_box(adb, (1678, 79, 0, 0))[:2]
                log(f"奖励选择弹窗需人工挑选，已关闭继续：{x},{y}", "warn")
                adb.tap(x, y)
                time.sleep(2)

    _daily_tap_task_close(adb)
    return n


def _daily_tap_task_close(adb, img=None):
    """关闭任务面板：统一走纯图标入口注册表（模板优先 → 坐标兜底），不再盲点写死坐标。"""
    img = img if img is not None else adb.screenshot()
    hit = find_icon_entry(adb, img, "task_close") if img is not None else None
    if hit:
        x, y, src, _score = hit
        log(f"关闭任务面板（{src}）：{x},{y}", "step" if src == "template" else "warn")
    else:
        x, y = scale_box(adb, (*_TASK_CLOSE, 0, 0))[:2]
        log(f"关闭任务面板（旧常量兜底）：{x},{y}", "warn")
    adb.tap(x, y)
    time.sleep(2)


def _daily_ensure_starmap(adb):
    """尽量回到星图顶层：先关掉残留面板（邮件/活动），再点『返回地图』/『星图』。"""
    for _ in range(6):
        img = adb.screenshot()
        if img is None:
            return False
        items = _au_ocr_all(img)
        texts = " ".join(t for _, _, t, _ in items)
        if _au_guard_exit(adb, items):          # 误弹退出框 → 取消
            continue
        if "行政规划" in texts:                 # 基地管理菜单遮挡 → 点外侧关
            adb.tap(int(adb.w * 0.3), int(adb.h * 0.5))
            time.sleep(1.5)
            continue
        if _au_has_event_banner(items):         # 「暗流汹涌」活动弹窗 → 右上 X 关掉，不点前往查看
            pos = _au_close_event_banner(adb, img)
            log(f"关闭「暗流汹涌」活动弹窗：{pos}", "warn")
            continue
        if "奖励选择" in texts:                 # 一键领取后的奖励挑选弹窗 → 关掉，不替用户选
            x, y = scale_box(adb, (1678, 79, 0, 0))[:2]
            log(f"关闭奖励选择弹窗（需人工挑选）：{x},{y}", "warn")
            adb.tap(x, y)
            time.sleep(1.5)
            continue
        # 邮件面板残留（标题『邮件』+ 至少两个分类竖栏，或出现发件人/日期字段）→ 点外侧关闭
        cat_hits = sum(1 for k in ("联合体", "小队", "组织", "系统") if k in texts)
        if ("发件人" in texts or "日期" in texts) or ("邮件" in texts and cat_hits >= 2):
            log("关闭残留的邮件面板", "warn")
            adb.tap(int(adb.w * 0.72), int(adb.h * 0.55))
            time.sleep(1.8)
            continue
        # 任务面板残留（有五个任务页签）→ 点右上角门形图标关闭
        if "主线任务" in texts or "赏金任务" in texts:
            log("关闭残留的任务面板", "warn")
            _daily_tap_task_close(adb, img)
            # 布局差异兜底：仍未关掉就换旧坐标再试一次，避免在面板里反复空转
            img_c = adb.screenshot()
            if img_c is not None:
                t2 = " ".join(t for _, _, t, _ in _au_ocr_all(img_c))
                if "主线任务" in t2 or "赏金任务" in t2:
                    bx, by = scale_box(adb, (*_TASK_CLOSE_ALT, 0, 0))[:2]
                    adb.tap(bx, by)
                    time.sleep(2)
            continue
        if "探险协议" in texts and "星图" not in texts:
            log("关闭残留的活动面板", "warn")
            _daily_close_overlay(adb)
            continue
        pos = find_button(items, ["返回地图"], min_score=0.4)
        if pos:
            log("返回地图 → 星图", "step")
            adb.tap(*pos)
            time.sleep(2.5)
            continue
        pos = find_button(items, ["星图"], min_score=0.4)
        if pos and pos[1] > adb.h * 0.83:      # 底部导航区（900/1080），按分辨率换算
            log("点星图", "step")
            adb.tap(*pos)
            time.sleep(2.5)
            continue
        return True                            # 没导航键 → 视为已在星图顶层
    return True


def _daily_close_overlay(adb):
    """关闭活动/弹窗：优先图标模板找 X；否则点实测关闭位（2090,64 @2310x1080）。"""
    img = adb.screenshot()
    pos = find_close_x(adb, img, search=(0, 0, adb.w, int(adb.h * 0.18))) if img is not None else None
    if pos is None:
        hit = find_icon_entry(adb, None, "close_x")   # img=None：跳过模板，直接取注册表兜底
        pos = hit[:2] if hit else scale_box(adb, (*_DAILY_CLOSE_X, 0, 0))[:2]
        log(f"未模板命中关闭X，用注册表兜底坐标 {pos}", "warn")
    adb.tap(*pos)
    time.sleep(1.5)


def _daily_claim_loop(adb, qq, max_rounds=10, min_x=0):
    """在已打开面板里循环点击“按钮级”领取键（领取/领奖/签到）。
    - 只认 ≤6 字短文本，排除补签/购买/已领取等，绝不误点消费或升级；
    - min_x: 只在该 x 右侧找（避开活动面板左侧的侧栏）；
    - 若连续两次命中同一位置且文本不变（置灰/不可领），停止。
    返回点击次数。"""
    claimed = 0
    last_key = None
    for _ in range(max_rounds):
        img = adb.screenshot()
        if img is None:
            break
        items = _au_ocr_all(img)
        cands = []
        for cx, cy, text, score in items:
            if score < 0.5 or cx < min_x:
                continue
            t = text.replace(" ", "").strip()
            if not t or len(t) > _DAILY_BTN_MAXLEN:
                continue
            if any(k in t for k in _DAILY_CLAIM_KW) and \
               not any(e in t for e in _DAILY_EXCLUDE_KW):
                cands.append((cx, cy, t))
        if not cands:
            break
        cands.sort(key=lambda c: c[1])          # 从顶部开始，避免重复点同一处
        cx, cy, t = cands[0]
        key = (round(cx / 8), round(cy / 8), t)
        if key == last_key:
            log(f"「{t}」无变化（置灰/不可领取），停止", "warn")
            break
        last_key = key
        log(f"日常领取: {t} ({cx},{cy})", "ok")
        adb.tap(cx + random.randint(-CLICK_JITTER, CLICK_JITTER),
                cy + random.randint(-CLICK_JITTER, CLICK_JITTER))
        claimed += 1
        qq.push("upgrade_click", f"日常领取: {t}")
        time.sleep(2.5)
        # 领取后可能出现的“确定/确认/关闭”弹窗
        img2 = adb.screenshot()
        if img2 is not None:
            items2 = _au_ocr_all(img2)
            p2 = find_button(items2, ["确定", "确认"], min_score=0.4)
            if p2:
                log("领取弹窗 → 点确定", "step")
                adb.tap(*p2)
                time.sleep(1.5)
    return claimed


def _select_activity_entries(items, adb):
    """从 OCR 结果里挑出活动面板左侧栏的条目（纯逻辑，便于单测）。
    返回去重后的 [(cx, cy, text)]，按 cy 排序。不在侧栏区域或文字过短（<2 字）会被忽略。"""
    x1, y1, x2, y2 = scale_box(adb, _DAILY_SIDEBAR_BOX)
    side = [(cx, cy, t) for cx, cy, t, sc in items
            if x1 <= cx <= x2 and y1 <= cy <= y2 and sc >= 0.4 and len(t.strip()) >= 2]
    side.sort(key=lambda s: s[1])
    # 去重：同一活动可能被 OCR 拆成两行（标题+副标题），按 y 间距 60px 合并
    picked = []
    for cx, cy, t in side:
        if picked and abs(cy - picked[-1][1]) < 60:
            continue
        picked.append((cx, cy, t))
    return picked


def _daily_activity_crawl(adb, qq, max_rounds):
    """活动面板：左侧栏逐条切换活动，在右侧详情区域领取。返回总点击次数。

    判定是否在活动面板用“证据”而非单一页签文字：优先认『探险协议』页签，
    若整屏 OCR 漏识该页签，则回退到“左侧栏确实存在活动条目”判定，避免漏领。
    每条活动单独 try/except，单条异常不影响其余条目。"""
    total = 0
    img = adb.screenshot()
    if img is None:
        log("活动面板：截图失败，跳过", "err")
        return 0
    items = _au_ocr_all(img)
    texts = " ".join(t for _, _, t, _ in items)

    entries = _select_activity_entries(items, adb)
    in_panel = ("探险协议" in texts) or bool(entries)
    if not in_panel:
        log("未确认在活动面板（缺『探险协议』页签且无侧栏活动条目），跳过活动领取", "warn")
        return 0

    log(f"活动侧栏识别到 {len(entries)} 条", "step")
    min_x = scale_box(adb, _DAILY_SIDEBAR_BOX)[2] + 20   # 只在侧栏右侧找领取键
    visited = []
    for cx, cy, t in entries:
        if any(abs(cy - v) < 40 for v in visited):
            continue
        visited.append(cy)
        log(f"切换到活动: {t.strip()[:16]}", "step")
        adb.tap(cx, cy)
        time.sleep(2.2)
        try:
            total += _daily_claim_loop(adb, qq, max_rounds=max_rounds, min_x=min_x)
        except Exception as e:
            log(f"活动『{t.strip()[:12]}』领取异常（跳到下一条）: {e}", "err")
    return total


def _daily_enter_activity(adb):
    """打开活动面板：先 OCR 找『活动』文字，找不到再查纯图标入口注册表；
    点完必须验证『探险协议』页签或左侧活动条目出现，否则明确跳过。

    注：旧兜底 (320,60) 实测是等级图标 / 公告跑马灯，已彻底移除，绝不盲点。
    2026-11-22 实机确认活动入口就是左上剪贴板图标（与任务清单入口同一个），
    注册表已内置模板与坐标；可用 config.json 的 icon_entries.activity_entry 覆盖。"""
    img = adb.screenshot()
    if img is None:
        raise RuntimeError("活动入口：截图失败")
    items = _au_ocr_all(img)
    apos = find_button(items, ["活动"], min_score=0.45)
    src = "OCR 文字"
    if apos is None:
        apos = find_button(items, ["活动"], min_score=0.45,
                          region=scale_box(adb, (0, 0, DESIGN_W, 130)))
    if apos is None:
        hit = find_icon_entry(adb, img, "activity_entry")
        if hit:
            apos = hit[:2]
            src = f"纯图标注册表/{hit[2]}"
            log(f"活动入口由{src}定位到 ({apos[0]},{apos[1]})", "step")
    if apos is None:
        raise RuntimeError("活动入口未校准：OCR 无『活动』文字，注册表也没有图标模板/坐标。"
                           "请运行 icons 命令校准，把坐标写入 config.json 的 "
                           "icon_entries.activity_entry.fallback")
    adb.tap(*apos)
    time.sleep(3)
    img2 = adb.screenshot()
    if img2 is None:
        raise RuntimeError("活动入口：验证截图失败")
    items2 = _au_ocr_all(img2)
    texts2 = " ".join(t for _, _, t, _ in items2)
    if "探险协议" in texts2 or _select_activity_entries(items2, adb):
        log("活动面板已打开", "ok")
        return
    raise RuntimeError(f"活动入口（{src}）点击后未进入活动面板，请用 icons 命令复核坐标")


def _daily_find_claim_btn(items, min_x=0, min_y=0):
    """在 OCR 结果里找“按钮级”领取键：短文本(≤6字)+含领取关键词+非排除词。
    正文/说明文字（如『邀您添加企微领取见面礼』）因超长被过滤，避免误点。
    多个命中时取最靠下的（领取按钮通常在底部）。返回 (cx,cy,text) 或 None。"""
    best = None
    for cx, cy, t, sc in items:
        if sc < 0.45 or cx < min_x or cy < min_y:
            continue
        ts = t.replace(" ", "").strip()
        if not ts or len(ts) > _DAILY_BTN_MAXLEN:
            continue
        if any(k in ts for k in _MAIL_CLAIM_KW) and \
           not any(e in ts for e in _DAILY_EXCLUDE_KW):
            if best is None or cy > best[1]:
                best = (cx, cy, ts)
    return best


def _daily_claim_mail(adb, qq, max_mails=8, categories=("系统", "联合体", "组织", "小队")):
    """邮件领取：逐个分类、逐封打开列表邮件，若正文区出现「领取/提取附件」短按钮则点。
    实测本游戏邮件附件为自动入库（点物品只弹说明），故默认关闭；
    开启后也做了快速失败：连续 3 封无领取键即跳过该分类，避免空跑耗时。
    返回领取次数。"""
    claimed = 0
    x1, y1, x2, y2 = scale_box(adb, _MAIL_LIST_BOX)
    for cat in categories:
        img = adb.screenshot()
        if img is None:
            break
        items = _au_ocr_all(img)
        pos = find_button(items, [cat], min_score=0.45,
                          region=scale_box(adb, (0, 80, 330, DESIGN_H)))
        if pos is None:
            continue
        log(f"邮件分类 → {cat}", "step")
        adb.tap(*pos)
        time.sleep(2)

        visited = set()
        stall = 0
        miss = 0
        for _ in range(max_mails):
            img = adb.screenshot()
            if img is None:
                break
            items = _au_ocr_all(img)
            rows = []
            for cx, cy, t, sc in items:
                if not (x1 <= cx <= x2 and y1 <= cy <= y2) or sc < 0.45:
                    continue
                ts = t.strip()
                if len(ts) < 2 or ts in _MAIL_CAT_WORDS:
                    continue
                rows.append((cy, cx, ts))
            if not rows:
                break
            rows.sort()
            # 相邻 55px 内的多行视作同一封邮件（标题+摘要），取标题行
            merged, last_y = [], -999
            for cy, cx, ts in rows:
                if cy - last_y < 55:
                    continue
                merged.append((cy, cx, ts))
                last_y = cy
            target = next((m for m in merged if round(m[0] / 30) not in visited), None)
            if target is None:
                # 可见邮件都点过 → 向下滚动列表再试
                stall += 1
                if stall >= 2:
                    break
                adb.run("shell", "input", "swipe", str((x1 + x2) // 2), str(y2 - 60),
                        str((x1 + x2) // 2), str(y1 + 120), "400")
                time.sleep(1.8)
                continue
            stall = 0
            cy, cx, ts = target
            visited.add(round(cy / 30))
            adb.tap(cx, cy)
            time.sleep(2.2)
            img2 = adb.screenshot()
            if img2 is None:
                continue
            items2 = _au_ocr_all(img2)
            cpos = _daily_find_claim_btn(items2, min_x=x2 + 20)
            if cpos:
                miss = 0
                log(f"邮件领取附件: {ts[:12]} → 按钮「{cpos[2]}」", "ok")
                adb.tap(cpos[0], cpos[1])
                claimed += 1
                qq.push("upgrade_click", f"邮件领取: {ts[:12]}")
                time.sleep(2)
            else:
                miss += 1
                if miss >= 3:      # 连续 3 封都没有领取键 → 该分类无附件可领，直接跳过省时
                    log(f"「{cat}」连续 {miss} 封无领取键，跳过该分类", "warn")
                    break
    return claimed


def cmd_daily(adb, qq):
    """日常一键领取：任务一键领取 → 活动面板(签到/活跃挑战/各活动) → 邮件(可选)。"""
    cfg = CONFIG.get("daily", {})
    if not cfg.get("enabled", True):
        log("daily 未启用（config.json daily.enabled=false）", "warn")
        return
    max_rounds = int(cfg.get("max_rounds", 10))
    mail_max = int(cfg.get("mail_max", 8))
    qq.push("start", "日常一键领取开始")
    log("日常一键领取：任务面板 → 活动面板 → 邮件", "step")
    _au_wake(adb)
    if _au_ensure_game(adb, qq):
        pass
    _daily_ensure_starmap(adb)

    res_before = _au_res_dict_retry(adb)      # 领取前资源快照（用于回执）
    if res_before:
        log("领取前资源：" + "，".join(f"{k} {_fmt_num(v)}" for k, v in res_before.items()), "step")
    else:
        log("领取前资源未读到（跳过回执对比）", "warn")
    total = 0
    # 0) 任务面板：主线/委托/支线/赏金/集结 逐页签「一键领取」（每日奖励大头）
    if cfg.get("claim_tasks", True):
        try:
            n = _daily_claim_tasks(adb, qq)
            total += n
            log(f"任务面板领取 {n} 次", "ok")
        except Exception as e:
            log(f"任务面板领取异常（继续后续步骤）: {e}", "err")
        _daily_ensure_starmap(adb)

    # 1) 活动面板：左侧栏逐条切换活动，右侧详情区领取（签到/活跃挑战/各活动）
    if cfg.get("claim_activity", True):
        try:
            _daily_enter_activity(adb)
            n = _daily_activity_crawl(adb, qq, max_rounds=max_rounds)
            total += n
            log(f"活动面板领取 {n} 次", "ok")
        except Exception as e:
            log(f"活动面板领取异常（继续后续步骤）: {e}", "err")
        finally:
            _daily_close_overlay(adb)
            time.sleep(1.5)

    # 2) 邮件：逐分类逐封领取附件
    if cfg.get("claim_mail", True):
        try:
            _daily_ensure_starmap(adb)
            img = adb.screenshot()
            items = _au_ocr_all(img) if img is not None else []
            mpos = find_button(items, ["邮件"], min_score=0.4)
            if mpos is None:
                hit = find_icon_entry(adb, img, "mail_envelope")
                mpos = hit[:2] if hit else scale_box(adb, (422, 863, 0, 0))[:2]
                log(f"OCR 未命中邮件文字，改用纯图标入口 ({mpos[0]},{mpos[1]}) · "
                    + (f"模板匹配 {hit[3]:.2f}" if hit and hit[2] == "template"
                       else "坐标兜底"), "warn")
            adb.tap(*mpos)
            time.sleep(3)
            n = _daily_claim_mail(adb, qq, max_mails=mail_max)
            total += n
            log(f"邮件领取 {n} 次", "ok")
        except Exception as e:
            log(f"邮件领取异常（继续后续步骤）: {e}", "err")
        _daily_close_overlay(adb)

    qq.push("stopped", f"日常领取完成，共点击 {total} 次", with_shot=False)
    log(f"日常领取完成，共 {total} 次", "ok")

    # 领取回执：对比前后资源，让「到底有没有真领到」可验证
    try:
        _daily_ensure_starmap(adb)
        res_after = _au_res_dict_retry(adb)
        if not res_before or not res_after:
            log("领取回执：资源未读全，本次不做增减对比", "warn")
        else:
            diff = _au_res_diff(res_before, res_after)
            if diff:
                parts = [f"{k} +{_fmt_num(v)}" for k, v in diff]
                log("领取回执：" + "，".join(parts), "ok")
                qq.push("daily_receipt", "日常领取到账：" + "，".join(parts), with_shot=False)
            else:
                log("领取回执：资源无变化（本日奖励此前已领完）", "warn")
    except Exception as e:
        log(f"资源回执读取失败（不影响领取）：{e}", "warn")


# ===================== 单实例锁 + 干净退出 =====================
# 杜绝“多开抢占同一台设备的 ADB 信道”：同一项目只允许一个实例驱动设备。
# 用 pid 文件实现；进程被强杀导致锁残留时，靠“pid 是否仍存活”自愈。
_INSTANCE_LOCK = os.path.join(app_dir(), ".run_lock")


def _pid_alive(pid):
    if not pid or pid <= 0:
        return False
    # 优先用 Windows 原生 tasklist 探测（比 os.kill(pid,0) 更可靠，避免误判死进程为存活）
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
            capture_output=True, timeout=10,
        ).stdout
        # 中文 Windows 下 tasklist 输出为 GBK；pid 本身是 ASCII，忽略解码错误即可
        text = out.decode("gbk", "ignore")
        # 有匹配进程时输出含该 PID 的数据行；无匹配时只含“信息/INFO”提示行（不含 PID）
        return str(int(pid)) in text
    except Exception:
        # 非 Windows 或 tasklist 不可用，退回 os.kill 探测
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, OSError):
            return False


def _acquire_instance_lock():
    try:
        if os.path.exists(_INSTANCE_LOCK):
            try:
                with open(_INSTANCE_LOCK, "r", encoding="utf-8") as f:
                    old = int((f.read().strip() or "0"))
            except Exception:
                old = 0
            if old and _pid_alive(old):
                print(f"{C.ERR}⚠ 已有实例在运行 (pid={old})，为避免抢占同一台设备的 ADB 信道，已拒绝启动。{C.R}")
                print(f"{C.DIM}请先停止旧实例（任务管理器结束 python.exe / 星际猎人测试仪.exe，或运行「停止全部.bat」）。{C.R}")
                return False
            try:
                os.remove(_INSTANCE_LOCK)
            except OSError:
                pass
        with open(_INSTANCE_LOCK, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        return True
    except OSError as e:
        print(f"{C.WARN}无法写入实例锁 {_INSTANCE_LOCK}：{e}（本次不限制多开）{C.R}")
        return True              # 写入失败则放行，避免把用户挡在门外


def _release_instance_lock():
    try:
        if os.path.exists(_INSTANCE_LOCK):
            with open(_INSTANCE_LOCK, "r", encoding="utf-8") as f:
                cur = f.read().strip()
            if cur == str(os.getpid()):
                os.remove(_INSTANCE_LOCK)
    except OSError:
        pass


def _cleanup_adb(adb):
    """停止 adb server，彻底释放设备信道（自动脚本停止时调用）"""
    try:
        if adb is not None and getattr(adb, "path", None):
            adb.raw("kill-server", timeout=10)
            print(f"{C.DIM}[ADB] 已停止 adb server，设备信道已释放{C.R}")
    except Exception:
        pass


def main():
    os.chdir(app_dir())
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""

    if cmd == "genconfig":
        banner()
        print(f"{C.ACCENT}config.json 路径:{C.R} {C.HL}{os.path.join(app_dir(), 'config.json')}{C.R}")
        return
    if cmd == "points":
        banner()
        cmd_points()
        return

    # 无参数 / 未知命令：只打印用法，不连 ADB（避免因设备不在线而白等）
    _VALID_CMDS = ("check", "scan", "shot", "icons", "points", "times", "status", "mark",
                   "tap", "autoup", "daily", "all")
    if cmd not in _VALID_CMDS:
        banner()
        print(__doc__)
        return

    banner()
    adb = Adb()
    # 统一按 config.device.serial 选设备：所有命令都用同一台
    _pick_device_by_pref(adb)

    # 单实例锁：自动脚本/日常等会持续驱动设备，禁止第二个实例抢占同一 ADB 信道
    _LOCK_CMDS = ("autoup", "daily", "all")
    _KILL_ADB_CMDS = ("autoup", "all")
    if cmd in _LOCK_CMDS and not _acquire_instance_lock():
        sys.exit(2)
    if cmd in _LOCK_CMDS:
        atexit.register(_release_instance_lock)            # 退出时释放实例锁
    if cmd in _KILL_ADB_CMDS:
        atexit.register(lambda: _cleanup_adb(adb))         # 退出时停止 adb server，释放设备

    if cmd == "check":
        pass  # Adb() 初始化已打印 adb 路径/设备/分辨率，供 GUI 自检解析
    elif cmd == "scan":
        cmd_scan(adb)
    elif cmd == "shot":
        cmd_shot(adb)
    elif cmd == "icons":
        cmd_icons(adb)
    elif cmd == "times":
        cmd_times(adb)
    elif cmd == "status":
        cmd_status(adb)
    elif cmd == "mark":
        cmd_mark(adb)
    elif cmd == "tap" and len(sys.argv) >= 4:
        cmd_tap(adb, int(sys.argv[2]), int(sys.argv[3]))
    elif cmd == "autoup":
        cmd_autoup(adb, Notifier(CONFIG))
    elif cmd == "daily":
        cmd_daily(adb, Notifier(CONFIG))
    elif cmd == "all":
        qq = Notifier(CONFIG)
        log("一体化流程：先日常领取，再进入一键升级自动循环", "step")
        try:
            cmd_daily(adb, qq)
        except Exception as e:
            log(f"日常领取异常（继续升级循环）: {e}", "err")
        time.sleep(3)
        cmd_autoup(adb, qq)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()

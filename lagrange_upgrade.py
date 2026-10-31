# -*- coding: utf-8 -*-
"""
无尽的拉格朗日（星际猎人）建筑自动升级脚本
功能：截图 -> OCR识别倒计时 -> 睡到点前 -> 再识别 -> 点击"升级" ->（可选）点"确认"
推送：材料不足 / 升级成功 / 异常卡住 时，通过 QQ 机器人(OneBot v11) 发文字+截图

命令：
  LagrangeUpgrade.exe scan      校准：列出全屏文字坐标，生成 scan_标注.png
  LagrangeUpgrade.exe shot      只截一张图（shot.png）
  LagrangeUpgrade.exe tap X Y   测试点击某坐标
  LagrangeUpgrade.exe run       正式运行（不带参数也是 run）
  LagrangeUpgrade.exe autoup    真机一键升级自动循环（倒计时驱动，队列先到先补）
  LagrangeUpgrade.exe daily     日常一键领取（任务一键领取 + 活动签到/领奖）
  LagrangeUpgrade.exe all       一体化：日常领取 → 一键升级自动循环（挂机首选）
  LagrangeUpgrade.exe status    一屏状态汇总（屏幕/前台/建造队列/三项资源），纯只读
  LagrangeUpgrade.exe check     只检测 ADB 连接并打印设备信息（GUI 启动自检用）
  LagrangeUpgrade.exe genconfig 重新生成默认 config.json

所有配置在程序同目录的 config.json，首次运行自动生成。
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
    # 坐标基准 2310x1080，会自动按实际分辨率等比缩放
    "btn_box": [350, 530, 800, 700],          # 资源行+倒计时+升级按钮 整块区域
    "time_box_enabled": False,                 # 倒计时若不在按钮区，改成 true 单独框
    "time_box": [500, 570, 700, 625],
    "confirm_enabled": False,                  # 点升级后是否还要点“确认”（实测无弹窗就保持 false）
    "confirm_box": [700, 700, 1600, 950],
    "adb_path": "",                            # 留空自动寻找雷电/MuMu/夜神 adb

    # 设备选择：模拟器与真机都支持，同时连接时按这里决定用哪台
    #   auto     = 自动（有真机优先用真机，只有模拟器就用模拟器）——默认
    #   phone    = 只用真机（USB 连接的手机）
    #   emulator = 只用模拟器（雷电/MuMu/夜神/逍遥/蓝叠/腾讯手游助手等）
    #   serial   = 只用 device.serial 指定的序列号
    "device": {
        "mode": "auto",
        "serial": ""
    },

    # 一键升级自动循环：要自动升级的目标建筑名（OCR 模糊匹配，含该名的第一个设施即命中）。
    # 例如 "平台指挥中心" / "资源港口" / "战地指挥中心"。留空则队列有空位时升“当前可见第一个可升设施”。
    "au_target_building": "平台指挥中心",

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
        "close_box": [2040, 20, 2170, 100],   # 右上角 X 按钮区域
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

# 识别关键词
# 注意：按钮必须【精确】匹配。画面上同时有“可升级”状态字和“一键升级”按钮，
# 用包含匹配会点错，所以目标按钮只认文字恰好等于“升级/立即升级”。
READY_EXACT = ["升级", "立即升级"]
READY_EXCLUDE = ["一键升级", "可升级", "升级中"]
INSUFFICIENT_KEYWORDS = ["不足", "缺少"]
CONFIRM_KEYWORDS = ["确认", "确定"]


def is_ready_button(text):
    t = text.strip()
    if any(k in t for k in READY_EXCLUDE):
        return False
    return t in READY_EXACT

# 节奏（秒）
EARLY_WAKE = 20
MIN_SLEEP = 15
IDLE_SLEEP = 20
INSUFFICIENT_WAIT = 600
CLICK_JITTER = 6
WAIT_AFTER_CLICK = 8


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


class Adb:
    # 主流模拟器 ADB 端口（覆盖雷电/MuMu/夜神/逍遥/蓝叠/腾讯）
    KNOWN_PORTS = (16384, 5555, 7555, 62001, 21503, 5556, 5557, 5559,
                   5554, 5558, 5560, 5561, 5562, 5563, 5564, 5565,
                   21503, 21513, 21523, 21533, 21543, 21553,
                   7401, 7402, 7403, 7404, 7405,        # 夜神多开
                   5037, 0)                              # 5037=默认 server

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
        if auto_connect:
            self.device = self._pick_device()
            self.w, self.h = self._get_resolution()
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

    def run(self, *args, timeout=30):
        r = subprocess.run([self.path, "-s", self.device, *args],
                           capture_output=True, timeout=timeout)
        # protocol fault / device not found → server 冲突，重启后重试一次
        err = (r.stderr or b"").decode("utf-8", "replace")
        if ("protocol fault" in err or "not found" in err) and self._reset_server():
            self.device = self._pick_device(fatal=False) or self.device
            r = subprocess.run([self.path, "-s", self.device, *args],
                               capture_output=True, timeout=timeout)
        return r

    def raw(self, *args, timeout=30):
        """不指定 -s 设备的原始 adb 命令（用于 devices / connect）"""
        return subprocess.run([self.path, *args],
                              capture_output=True, timeout=timeout)

    def list_devices(self):
        """返回已连接的设备 ID 列表（含状态）"""
        try:
            out = self.raw("devices").stdout.decode("utf-8", "replace")
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
            r = self.raw("connect", addr, timeout=15)
            msg = r.stdout.decode("utf-8", "replace").strip()
            return ("connected" in msg.lower() or "already" in msg.lower()), msg
        except Exception as e:
            return False, str(e)

    def scan_and_connect(self):
        """扫描所有已知端口并连接，返回连接上的设备列表"""
        for port in self.KNOWN_PORTS:
            if port == 0:
                continue
            try:
                self.raw("connect", f"127.0.0.1:{port}", timeout=8)
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
        raw = self.run("exec-out", "screencap", "-p").stdout
        img = None
        if raw[:4] == b"\x89PNG":
            img = np.array(Image.open(io.BytesIO(raw)).convert("RGB"))
        else:
            self.run("shell", "screencap", "-p", "/sdcard/_lag_shot.png")
            tmp = os.path.join(app_dir(), "_lag_shot.png")
            try:
                self.run("pull", "/sdcard/_lag_shot.png", tmp)
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
        print(f"{C.ACCENT}▸ 正在加载 OCR 模型（首次稍慢）...{C.R}")
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
    return _ocr


def scale_box(adb, box):
    sx, sy = adb.w / DESIGN_W, adb.h / DESIGN_H
    x1, y1, x2, y2 = box
    return (int(x1 * sx), int(y1 * sy), int(x2 * sx), int(y2 * sy))


def _is_emulator_serial(dev):
    """模拟器的 adb 序列号特征：127.0.0.1:端口 / emulator-5554 / localhost。"""
    d = (dev or "").lower()
    return ("127.0.0.1" in d) or ("localhost" in d) or d.startswith("emulator")


def _pick_device_by_pref(adb):
    """按 config.device.mode 选择设备，让模拟器和真机都能用。
    返回 (是否切换过, 说明文字)。真机序列号通常是类似 ABCDEF123456 的硬件号。"""
    dcfg = CONFIG.get("device", {}) or {}
    mode = str(dcfg.get("mode", "auto")).lower().strip()
    want_serial = str(dcfg.get("serial", "")).strip()

    devs = adb._list_devices()
    if not devs:
        print(f"{C.WARN}▸ 未检测到任何设备{C.R}  {C.DIM}请确认模拟器已启动 / 手机已开 USB 调试{C.R}")
        return False, "无设备"

    phones = [d for d in devs if not _is_emulator_serial(d)]
    emus = [d for d in devs if _is_emulator_serial(d)]

    if mode == "serial" and want_serial:
        target = want_serial if want_serial in devs else None
        why = f"指定序列号 {want_serial}"
    elif mode == "phone":
        target = phones[0] if phones else None
        why = "只选真机"
    elif mode == "emulator":
        target = emus[0] if emus else None
        why = "只选模拟器"
    else:                                   # auto：有真机先用真机，否则用模拟器
        target = phones[0] if phones else devs[0]
        why = "自动选择"

    # 指定了但没找到 → 退回当前设备并告警，避免直接退出
    if target is None:
        print(f"{C.WARN}▸ {why}：未找到匹配设备{C.R}")
        print(f"{C.DIM}  当前连接：{', '.join(devs)}  已连接 {len(phones)} 台真机 / {len(emus)} 台模拟器{C.R}")
        return False, f"{why}未命中"

    changed = (adb.device != target)
    if changed:
        adb.set_device(target)
    kind = "真机" if not _is_emulator_serial(target) else "模拟器"
    if changed:
        print(f"{C.ACCENT}▸ 设备{C.R}     {C.HL}{adb.device}{C.R}  "
              f"{C.DIM}{adb.w}x{adb.h} · {kind} · {why}{C.R}")
    return changed, kind



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


def ocr_region(adb, img, region_box):
    x1, y1, x2, y2 = scale_box(adb, tuple(region_box))
    part = img[max(0, y1):y2, max(0, x1):x2]
    result, _ = get_ocr()(part)
    items = []
    if result:
        for box, text, score in result:
            cx, cy = box_center(box)
            items.append((cx + x1, cy + y1, text, float(score)))
    return items


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


def tap_text(adb, items, *keywords, jitter=CLICK_JITTER, exclude=None, region=None,
             min_score=0.45, prefer="any", fallback=None):
    """OCR 找文字按钮并点击，返回是否点到。未识别到时用 fallback=(x,y) 兜底并报警。"""
    pos = find_button(items, keywords, exclude=exclude, region=region,
                      min_score=min_score, prefer=prefer)
    if pos is None:
        if fallback:
            log(f"OCR 未命中 {keywords}，回退到兜底坐标 {fallback}", "warn")
            x, y = fallback
            adb.tap(x + random.randint(-jitter, jitter),
                    y + random.randint(-jitter, jitter))
            return True
        log(f"OCR 未命中 {keywords}，跳过", "warn")
        return False
    log(f"OCR 命中 {keywords} → 点击 ({pos[0]},{pos[1]})", "info")
    adb.tap(pos[0] + random.randint(-jitter, jitter),
            pos[1] + random.randint(-jitter, jitter))
    return True


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


# ==================== 命令 ====================
def cmd_shot(adb):
    img = adb.screenshot()
    out = os.path.join(app_dir(), "shot.png")
    Image.fromarray(img).save(out)
    log(f"截图已保存: {out}（分辨率 {adb.w}x{adb.h}）")


def cmd_scan(adb):
    img = adb.screenshot()
    result, _ = get_ocr()(img)
    if not result:
        log("整张图一个文字都没识别到，确认游戏画面是否正常显示", "warn")
        return
    annotated = Image.fromarray(img.copy())
    draw = ImageDraw.Draw(annotated)
    log(f"全屏文字列表（{C.HL}找「升级」和倒计时坐标{C.R}，据此修改 config.json 的 btn_box）：")
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
    log("把目标坐标上下左右各放宽 40~60 像素，填进 config.json 的 btn_box")


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


def click_confirm_if_any(adb, img):
    for cx, cy, text, _ in ocr_region(adb, img, CONFIG["confirm_box"]):
        if any(k in text for k in CONFIRM_KEYWORDS):
            adb.tap(cx + random.randint(-CLICK_JITTER, CLICK_JITTER),
                    cy + random.randint(-CLICK_JITTER, CLICK_JITTER))
            log(f"已点击确认按钮 ({cx},{cy})")
            return True
    return False


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


def cmd_run(adb, qq):
    qcfg = CONFIG["qq"]
    lost_threshold = int(qcfg.get("lost_threshold", 3))
    long_wait_alert = int(qcfg.get("long_wait_alert", 0))
    log("开始循环，按 Ctrl+C 停止", "ok")

    # 当日统计（跨天自动汇总并清零）
    stats = {"date": time.strftime("%Y-%m-%d"), "started_at": time.time(),
             "clicks": 0, "insufficient": 0, "popups": 0, "lost": 0,
             "errors": 0, "disconnects": 0, "last_event": "刚启动"}
    heartbeat_stop = threading.Event()

    def daily_rollover(force=False):
        today = time.strftime("%Y-%m-%d")
        if stats["date"] == today and not force:
            return
        uptime = time.time() - stats["started_at"]
        qq.push("daily_summary",
                f"{stats['date']} 运行汇总\n"
                f"━━━━━━━━━━━━━\n"
                f"🔨 点击升级：{stats['clicks']} 次\n"
                f"⚠️ 材料不足：{stats['insufficient']} 次\n"
                f"🧹 关闭弹窗：{stats['popups']} 次\n"
                f"❓ 卡住报警：{stats['lost']} 次\n"
                f"❗ 报错次数：{stats['errors']} 次\n"
                f"🔌 断连次数：{stats['disconnects']} 次\n"
                f"⏱️ 运行时长：{fmt_duration(uptime)}",
                force=True, with_shot=False)
        stats.update(date=today, started_at=time.time(), clicks=0,
                     insufficient=0, popups=0, lost=0, errors=0, disconnects=0)

    def heartbeat_loop():
        interval = int(qcfg.get("heartbeat_interval", 3600))
        while not heartbeat_stop.wait(interval):
            daily_rollover()
            uptime = time.time() - stats["started_at"]
            qq.push("heartbeat",
                    f"运行中，一切正常\n"
                    f"━━━━━━━━━━━━━\n"
                    f"⏱️ 已值守：{fmt_duration(uptime)}\n"
                    f"🔨 今日点击升级：{stats['clicks']} 次\n"
                    f"📌 最近状态：{stats['last_event']}",
                    with_shot=False)

    hb = threading.Thread(target=heartbeat_loop, daemon=True)
    hb.start()

    qq.push("start",
            f"脚本已启动，开始值守\n"
            f"设备：{adb.device}（{adb.w}x{adb.h}）\n"
            f"请确认游戏已停在【建筑详情面板】", force=True, with_shot=False)

    fail_streak = 0
    shot_fail_streak = 0
    insufficient_notified = False
    long_alerted = False

    try:
      while True:
        try:
            daily_rollover()
            try:
                img = adb.screenshot()
                shot_fail_streak = 0
            except Exception as e:
                shot_fail_streak += 1
                stats["errors"] += 1
                log(f"截图失败({shot_fail_streak})：{e}", "err")
                if shot_fail_streak >= 2:
                    stats["disconnects"] += 1
                    qq.push("disconnect",
                            f"无法连接模拟器（连续 {shot_fail_streak} 次截图失败）\n"
                            f"正在尝试重新连接，请检查模拟器是否关闭/重启了")
                    if adb.try_reconnect():
                        log(f"重连成功：{adb.device}", "ok")
                        qq.push("start", f"模拟器已重新连上：{adb.device}",
                                force=True, with_shot=False)
                        shot_fail_streak = 0
                time.sleep(20)
                continue

            items = ocr_region(adb, img, CONFIG["btn_box"])
            all_text = " ".join(t for _, _, t, _ in items)

            # 1) 材料不足：不点，截图通知，长等
            if any(k in all_text for k in INSUFFICIENT_KEYWORDS):
                stats["last_event"] = "材料不足等待中"
                if not insufficient_notified:
                    log("材料不足，已停止点击，等待 10 分钟", "warn")
                    stats["insufficient"] += 1
                    qq.push("insufficient",
                            "建筑升级材料不足，脚本已暂停点击\n"
                            "10 分钟后自动再检查一次，补充材料后无需操作",
                            img_bgr=img)
                    insufficient_notified = True
                time.sleep(INSUFFICIENT_WAIT)
                continue
            insufficient_notified = False

            # 2) 先找“升级”按钮（精确匹配，排除一键升级/可升级）
            target = None
            for cx, cy, text, _ in items:
                if is_ready_button(text):
                    target = (cx, cy, text.strip())
                    break

            if target:
                jx = target[0] + random.randint(-CLICK_JITTER, CLICK_JITTER)
                jy = target[1] + random.randint(-CLICK_JITTER, CLICK_JITTER)
                adb.tap(jx, jy)
                stats["clicks"] += 1
                stats["last_event"] = f"已点击升级 {time.strftime('%H:%M:%S')}"
                log(f"可升级，已点击「{target[2]}」({jx},{jy})，今日第 {stats['clicks']} 次", "ok")
                qq.push("upgrade_click",
                        f"已点击「升级」，建筑开始升级\n"
                        f"今日累计点击：{stats['clicks']} 次",
                        img_bgr=img)
                long_alerted = False
                time.sleep(3)
                if CONFIG.get("confirm_enabled"):
                    img2 = adb.screenshot()
                    for _ in range(2):
                        if click_confirm_if_any(adb, img2):
                            break
                        time.sleep(2)
                        img2 = adb.screenshot()
                time.sleep(WAIT_AFTER_CLICK)
                fail_streak = 0
                continue

            # 3) 没有按钮 -> 找倒计时（升级中）
            secs = None
            for _, _, text, _ in items:
                secs = parse_seconds(text)
                if secs is not None:
                    break
            if secs is None and CONFIG.get("time_box_enabled"):
                for _, _, text, _ in ocr_region(adb, img, CONFIG["time_box"]):
                    secs = parse_seconds(text)
                    if secs is not None:
                        break

            if secs is not None and secs > 5:
                stats["last_event"] = f"升级中，剩 {fmt_duration(secs)}"
                wait = max(MIN_SLEEP, secs - EARLY_WAKE)
                h, m, s = secs // 3600, secs % 3600 // 60, secs % 60
                log(f"升级中，剩余 {h:02d}:{m:02d}:{s:02d}，{wait} 秒后再看", "info")
                # 单次升级很久：每个升级周期只提醒一次
                if long_wait_alert and secs > long_wait_alert and not long_alerted:
                    qq.push("long_upgrade",
                            f"本次升级预计需要 {fmt_duration(secs)}，时间较长\n"
                            f"完成后会自动点击下一次升级并通知你",
                            img_bgr=img, with_shot=False)
                    long_alerted = True
                # 长睡眠期间也要响应跨天汇总
                slept = 0
                while slept < wait:
                    chunk = min(wait - slept, 60)
                    time.sleep(chunk)
                    slept += chunk
                    daily_rollover()
                fail_streak = 0
                continue

            # 4) 按钮和倒计时都没有：可能被弹窗挡住，尝试关弹窗
            if try_close_popup(adb, img, qq, stats):
                stats["last_event"] = "已关闭弹窗"
                time.sleep(3)
                continue

            # 5) 确实什么都没读到
            fail_streak += 1
            stats["last_event"] = f"找不到按钮（第{fail_streak}次）"
            log(f"按钮区没有倒计时也没有「升级」，{IDLE_SLEEP} 秒后重试"
                + ("（已连续多次，请检查界面/弹窗）"
                   if fail_streak >= lost_threshold else ""),
                "warn" if fail_streak >= lost_threshold else "info")
            if fail_streak == lost_threshold:
                stats["lost"] += 1
                qq.push("lost",
                        f"连续 {lost_threshold} 次没识别到升级按钮\n"
                        f"可能被弹窗挡住、建筑面板被关闭或游戏掉线，请查看截图\n"
                        f"脚本会继续重试，恢复后自动正常",
                        img_bgr=img)
            time.sleep(IDLE_SLEEP)

        except KeyboardInterrupt:
            raise
        except Exception as e:
            stats["errors"] += 1
            stats["last_event"] = f"报错：{e}"
            log(f"出错：{e}，30 秒后重试", "err")
            qq.push("exception", f"脚本运行报错，将在 30 秒后自动重试\n错误：{e}")
            time.sleep(30)
    except KeyboardInterrupt:
        log("已手动停止", "warn")
        qq.push("stopped",
                f"脚本被手动停止\n今日共点击升级 {stats['clicks']} 次",
                with_shot=False)
    finally:
        heartbeat_stop.set()
        qq.flush()        # 合并模式：把没发的通知发出去
        daily_rollover(force=True)


# ==================== 一键升级自动循环（真机） ====================
# 设计原则：读倒计时 → 纯 sleep 零占用等待 → 到点补位，队列永远满载（先到先补）

_AU_PANEL_KEYS = ("填满建造队列", "消耗资源")          # 面板打开的特征文本
_AU_RES_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万)?\s*/\s*(\d+(?:\.\d+)?)\s*(万)?")


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


def _au_collect_times(items):
    """收集屏幕上所有倒计时（秒），按行合并解析以容错 OCR 拆框；排除顶部状态栏。"""
    times = []
    for _, cy, text, _ in _merge_lines(items):
        if cy < 90:
            continue
        sec = parse_seconds(text)
        if sec and sec >= 60:
            times.append(sec)
    return times


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
# 注：基地建设页右上 (1879~1915, 67~69) 在新版里是【直播挂件】而非「一键升级」按钮，
# 点它会陷入死循环（见项目硬约束）。本常量已废弃，禁止再用作点击兜底坐标。
_AU_ONKEY_BTN_DEPRECATED = (1915, 69)
_AU_STRAY_MODAL_KEYS = ("扩建规模", "扩建条件", "基地扩建")  # 误开的遮挡弹窗关键词


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


def _au_handle_panel(adb, qq, items):
    """一键升级面板：资源够→点升级；不够→读预估时间等待；队列满→关闭读倒计时"""
    texts = [t for _, _, t, _ in items]
    log("处理一键升级面板", "step")

    # 解析消耗资源区（面板左下），任一资源 当前<需要 即不足
    x1, y1, x2, y2 = scale_box(adb, (400, 760, 1060, 990))
    res = [it for it in items if x1 <= it[0] <= x2 and y1 <= it[1] <= y2]
    lack = any("不足" in t for t in texts)
    for _, _, t, _ in res:
        m = _AU_RES_RE.search(t)
        if m and float(m.group(1)) + 1e-9 < float(m.group(3)):
            lack = True
    if lack:
        # ETA 在面板顶部时钟图标旁(如 "C 34:30:00"，y≈200~700)；建造队列倒计时在下部(y≈900)。
        # 用 _pick_eta 优先取上部时间最大值，排除下部队列区，避免把队列倒计时误当攒够时间。
        eta = _pick_eta(items)
        wait = (eta or 600) + 60
        log(f"资源不足，预估 {fmt_duration(eta or 600)} 后攒够，等待 {fmt_duration(wait)}", "warn")
        qq.push("insufficient", f"资源不足，{fmt_duration(wait)} 后重试")
        _au_close_panel(adb)
        time.sleep(wait)
        return

    # 橙色「升级」按钮：OCR 优先（右下区域、排除"一键升级/可升级/升级中/立即升级"），找不到用设计坐标兜底
    btn = find_button(items, ["升级"], exclude=_AU_UPGRADE_BTN_EXCLUDE,
                      region=(int(adb.w * 0.5), int(adb.h * 0.65), adb.w, adb.h),
                      min_score=0.5)
    if btn is None:
        bx, by, _, _ = scale_box(adb, (1642, 887, 0, 0))
        btn = (bx, by)
        log("OCR 未命中升级键，回退到兜底坐标", "warn")
    adb.tap(*btn)
    log(f"点击升级按钮 {btn}", "ok")
    time.sleep(2.5)

    # 校验点击结果
    img = adb.screenshot()
    items2 = _au_ocr_all(img) if img is not None else []
    texts2 = [t for _, _, t, _ in items2]
    if any("上限" in t or "队列已满" in t for t in texts2):
        globals()["_AU_FULL_STREAK"] = _AU_FULL_STREAK + 1
        log(f"建造队列已满（连续第 {_AU_FULL_STREAK} 次），关闭面板", "warn")
        _au_close_panel(adb)
        return
    if any("不足" in t for t in texts2):
        log("点击后提示资源不足，下轮走资源等待分支", "warn")
        return                                     # 面板还开着，下轮处理
    qq.push("upgrade_click", "已点击升级（加入建造队列）")
    _au_close_panel(adb)


_AU_BOTTOM_Y = 900      # 底部建造队列状态栏（倒计时/2-2）所在区域 y 下界


def _au_queue_times(items, adb=None):
    """从底部建造队列状态栏读取进行中升级的倒计时（秒）。仅取底部栏，
    排除面板/详情里中部的倒计时，避免把建筑升级倒计时误当队列状态。
    adb 传入时按实际分辨率换算底部阈值，保证模拟器（720p/1080p/1440p）同样生效。"""
    bottom = int(adb.h * _AU_BOTTOM_Y / DESIGN_H) if adb is not None else _AU_BOTTOM_Y
    times = []
    for _, cy, text, _ in items:
        if cy < bottom:
            continue
        sec = parse_seconds(text)
        if sec and sec >= 30:
            times.append(sec)
    return times


# 连续“队列已满”次数：用于抑制「开详情→队列已满→关面板→立刻再开」的振荡。
# 队列栏 OCR 在面板开合瞬间容易漏读 <2 条，会被误判成“有空位”。
_AU_FULL_STREAK = 0
_AU_NOREAD_STREAK = 0    # 连续「读不到队列又没进展」计数，用于熔断盲点死循环


def _au_in_building_detail(items):
    """是否已进入某建筑详情面板：有『等级/升至满级/连续升级设置』等特征字。"""
    texts = [t for _, _, t, _ in items]
    return (any(k in t for t in texts for k in ("升至满级", "连续升级设置", "升至2级"))
            or any("等级" in t for t in texts)) \
        and any("基地建设" in t for t in texts)


def _au_on_base_build(items):
    """是否在基地建设页（底部有『基地建设』导航，且不在建筑详情面板里）。"""
    texts = [t for _, _, t, _ in items]
    return any("基地建设" in t for t in texts) and not _au_in_building_detail(items)


def _au_select_open_target(items, adb, target=None):
    """纯逻辑：从底部队列条里挑一个建筑条目用于打开详情补位。
    返回 (cx, cy, kind) 或 None；kind ∈ {'name','time'} 表示命中的是建筑名还是倒计时文字。
    规则：优先目标建筑（名字含 target）→ 否则最左的建筑名（比倒计时更可靠地点）
    → 否则最左的倒计时文字兜底。不含任何点击，便于单测。"""
    bottom = int(adb.h * _AU_BOTTOM_Y / DESIGN_H)
    # 底栏里可能出现的非建筑名文字（标签/状态/资源），不应被当作可点的建筑条目
    _NON_NAME = ("建造队列", "资源", "全部", "队列", "金属", "原料", "核燃料",
                 "建造中", "升级中", "进行中", "使用中", "已暂停", "已满", "暂停")
    name_items, time_items = [], []
    for cx, cy, t, sc in items:
        if cy <= bottom or sc < 0.4:
            continue
        ts = t.strip()
        if not ts:
            continue
        if parse_seconds(ts):                       # 倒计时文字（如 34:30:00）
            time_items.append((cx, cy, ts))
        elif re.search(r"[一-鿿]", ts) and ts not in _NON_NAME and len(ts) >= 2:
            name_items.append((cx, cy, ts))
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
    优先目标建筑；没有可读条目时再用底栏左下固定坐标兜底（远离右上角直播挂件，避免误触）。"""
    target = (CONFIG.get("au_target_building") or "").strip()
    sel = _au_select_open_target(items, adb, target)
    if sel:
        log(f"打开建筑详情补位（{sel[2]}）：{sel[0]},{sel[1]}", "step")
        adb.tap(sel[0], sel[1])
        time.sleep(2.5)
        return True
    # 兜底：底栏左下固定坐标（底部区域，远离右上角直播挂件，避免陷入死循环）
    log("未读到队列项，兜底点队列条左下区域", "warn")
    adb.tap(int(adb.w * 0.18), int(adb.h * 0.93))
    time.sleep(2.5)
    return True


def _au_tap_upgrade(adb, qq, items):
    """在建筑详情面板里点『升至满级/连续升级设置』发起（连续）升级。
    返回 True 表示已处理（点了/资源不足/队列已满），False 表示面板里没找到升级按钮。"""
    btn = find_button(items, ["升至满级", "连续升级设置"], min_score=0.5)
    if btn is None:
        btn = find_button(items, ["升级"], exclude=_AU_UPGRADE_BTN_EXCLUDE, min_score=0.5)
    if btn is None:
        return False
    log(f"点击升级入口 ({btn[0]},{btn[1]})", "ok")
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
    # 模拟器 / 真机都支持：按 config.device.mode 选设备（auto 默认有真机优先）
    _pick_device_by_pref(adb)

    qq.push("start", "一键升级自动循环已启动（真机/模拟器通用）")
    log("进入自动升级循环：倒计时驱动，等待期间零占用", "step")
    fails = 0
    try:
        while True:
            try:
                _au_wake(adb)
                # 0a) 挂机自愈：屏幕灭了就唤醒、游戏被切走/被杀就重新拉起
                if _au_ensure_game(adb, qq):
                    fails = 0
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

                # 1) 底部建造队列状态栏读倒计时（y>900），判断队列是否满载
                qtimes = _au_queue_times(items, adb)
                if len(qtimes) >= 2:
                    globals()["_AU_FULL_STREAK"] = 0
                    globals()["_AU_NOREAD_STREAK"] = 0
                    wait = min(qtimes) + 45
                    log(f"队列满载 2/2，最近到期 {fmt_duration(min(qtimes))}，等待 {fmt_duration(wait)}", "ok")
                    qq.push("long_upgrade", f"队列满载，{fmt_duration(min(qtimes))} 后到期补位")
                    time.sleep(wait)
                    fails = 0
                    continue

                # 1b) 连续两次“队列已满”说明队列栏只是 OCR 漏读、其实仍是满载 → 强制歇一轮
                if _AU_FULL_STREAK >= 2:
                    globals()["_AU_FULL_STREAK"] = 0
                    log("连续判定『队列已满』，判定为满载但队列栏 OCR 漏读，强制等待 10 分钟", "warn")
                    qq.push("long_upgrade", "队列已满（OCR 漏读），等待 10 分钟后再试")
                    time.sleep(600)
                    continue

                # 2a) 已在建筑详情面板 → 直接点升级入口（优先于面板键判断，
                #     避免把详情页里的“消耗资源”误当一键升级面板处理）
                if _au_in_building_detail(items):
                    if _au_tap_upgrade(adb, qq, items):
                        fails = 0
                        continue
                    _au_close_panel(adb)   # 点不到升级按钮，关面板回基地重试
                    continue

                # 1a) 一键升级/建筑面板已打开（非详情页）→ 直接处理（这是判断队列状态最可靠的入口）
                if any(k in t for t in texts for k in _AU_PANEL_KEYS):
                    _au_handle_panel(adb, qq, items)
                    fails = 0
                    continue

                # 2b) 在基地建设页 → 队列栏读不到空位时，从底部队列条打开一个建筑详情补位；
                #     找不到「一键升级」按钮时绝不用固定坐标兜底去点右上角（那是直播挂件，会死循环），
                #     连试 3 次仍无进展 → 熔断长等待，避免盲点坐标的死循环
                if _au_on_base_build(items):
                    globals()["_AU_NOREAD_STREAK"] = _AU_NOREAD_STREAK + 1
                    if _AU_NOREAD_STREAK > 3:
                        globals()["_AU_NOREAD_STREAK"] = 0
                        globals()["_AU_FULL_STREAK"] = 0
                        log("连续 3 次读不到队列且界面无进展 → 熔断，等待 15 分钟", "warn")
                        qq.push("stuck", "读不到队列状态，已熔断并等待 15 分钟", force=True)
                        time.sleep(900)
                        continue
                    pos = _au_find(items, "一键升级")
                    if pos is None:
                        # 没有「一键升级」按钮：直接走底部队列条补位（安全、稳定），不碰右上角挂件
                        if _au_open_target(adb, items):
                            globals()["_AU_NOREAD_STREAK"] = 0
                            fails = 0
                            continue
                        log("队列栏未读到且无法定位一键升级/建筑入口，稍后重试", "warn")
                        time.sleep(30)
                        continue
                    log(f"队列栏未读到（视图不显示），点一键升级确认状态 ({pos[0]},{pos[1]})", "warn")
                    adb.tap(*pos)
                    time.sleep(2.5)
                    fails = 0
                    continue

                # 3) 其它页：导航回基地
                pos = _au_find(items, "基地建设")
                if pos and pos[1] > 700:
                    log("进入基地建设页", "step")
                    adb.tap(*pos)
                    time.sleep(2.5)
                    continue
                pos = _au_find(items, "基地")
                if pos and pos[1] > adb.h * 0.83:      # 底部导航区（900/1080），按分辨率换算
                    log("主界面 → 进入基地", "step")
                    adb.tap(*pos)
                    time.sleep(2.5)
                    continue

                # 4) 页面未识别 → 返回键退一层再试（护栏会拦截退出框）
                fails += 1
                log(f"页面未识别（{fails}/5），按返回键", "warn")
                adb.run("shell", "input", "keyevent", "4")
                time.sleep(2)
                if fails >= 5:
                    qq.push("lost", "连续 5 次未识别页面，请检查游戏状态", force=True)
                    fails = 0
                    time.sleep(600)

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
_DAILY_CLOSE_X = (2090, 64)              # 活动面板关闭 X（2310x1080 实测）
_DAILY_SIDEBAR_BOX = (300, 100, 560, 1000)   # 活动面板左侧活动列表（用于逐条切换）
_MAIL_LIST_BOX = (300, 90, 630, 930)     # 邮件列表区域（中间栏）
_MAIL_CAT_WORDS = ("联合体", "小队", "组织", "系统", "邮件", "收藏", "全部")
_MAIL_CLAIM_KW = ("领取", "提取附件", "一键领取", "全部领取", "领取附件")

# 任务面板（星图左上角「清单打勾」图标，无文字标签，只能靠坐标；实测 2310x1080）
_TASK_ICON = (147, 30)
_TASK_TABS = ("主线任务", "委托任务", "支线任务", "赏金任务", "集结")
_TASK_CLOSE = (2143, 66)      # 任务面板右上角门形关闭图标（模拟器实测；真机旧值 2080,43 作备选）
_TASK_CLOSE_ALT = (2080, 43)


def _daily_claim_tasks(adb, qq):
    """任务面板：逐个页签点「一键领取」。这是每日奖励的大头（主线/委托/支线/赏金/集结）。
    按钮置灰时点击无害，所以不必判断颜色。返回点击次数。"""
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
            p2 = find_button(_au_ocr_all(img2), ["确定", "确认", "点击继续", "关闭"], min_score=0.45)
            if p2:
                adb.tap(*p2)
                time.sleep(1.5)

    cx, cy = scale_box(adb, (*_TASK_CLOSE, 0, 0))[:2]
    adb.tap(cx, cy)
    time.sleep(2)
    return n


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
            cx, cy = scale_box(adb, (*_TASK_CLOSE, 0, 0))[:2]
            adb.tap(cx, cy)
            time.sleep(2)
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
        x, y = _DAILY_CLOSE_X
        pos = scale_box(adb, (x, y, 0, 0))[:2]
        log(f"未模板命中关闭X，用兜底坐标 {pos}", "warn")
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
    """多策略打开活动面板：先全屏 OCR 找『活动』，找不到则限定星图顶部栏区域扫描；
    点完后验证『探险协议』页签或左侧栏活动条目确实出现，否则抛错让上层明确跳过。

    注：实测星图左上 (320,60) 现已是等级图标而非活动横幅，故该兜底坐标仅作最后尝试，
    且点完必然验证，避免像旧逻辑那样“盲点一个已知错误坐标”导致整轮 0 领取。"""
    for attempt in range(3):
        img = adb.screenshot()
        if img is None:
            raise RuntimeError("活动入口：截图失败")
        items = _au_ocr_all(img)
        apos = find_button(items, ["活动"], min_score=0.45)
        if apos is None:
            # 限定星图顶部条（设计坐标 y<130）扫描，避免误点别处
            apos = find_button(items, ["活动"], min_score=0.45,
                              region=scale_box(adb, (0, 0, DESIGN_W, 130)))
        if apos is None:
            # 最后兜底：旧文档记录的横幅位（需真机确认是否仍有效）
            apos = scale_box(adb, (320, 60, 0, 0))[:2]
            log("OCR 未命中活动入口，尝试兜底坐标 (320,60)（需真机确认）", "warn")
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
        log(f"活动入口尝试 {attempt + 1}/3：点击后未进入活动面板，重试", "warn")
    raise RuntimeError("活动入口：3 次尝试均未打开活动面板（需真机确认入口坐标）")


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
                mpos = scale_box(adb, (465, 860, 0, 0))[:2]
                log("OCR 未命中邮件图标，用兜底坐标 (465,860)", "warn")
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
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"

    if cmd == "genconfig":
        banner()
        print(f"{C.ACCENT}config.json 路径:{C.R} {C.HL}{os.path.join(app_dir(), 'config.json')}{C.R}")
        return

    banner()
    adb = Adb()
    # 统一按 config.device.mode 选设备：模拟器与真机都支持，所有命令用同一台
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
    elif cmd == "times":
        cmd_times(adb)
    elif cmd == "status":
        cmd_status(adb)
    elif cmd == "mark":
        cmd_mark(adb)
    elif cmd == "tap" and len(sys.argv) >= 4:
        cmd_tap(adb, int(sys.argv[2]), int(sys.argv[3]))
    elif cmd == "run":
        cmd_run(adb, Notifier(CONFIG))
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

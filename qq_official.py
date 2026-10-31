# -*- coding: utf-8 -*-
"""
qq_official.py —— QQ 开放平台官方机器人推送（与三月七助手同款方案）
====================================================================
申请与教程：https://q.qq.com/ （QQ 开放平台，创建机器人后拿 AppID/AppSecret）

能力：
  1. getAppAccessToken 鉴权（本地缓存令牌，到期自动续）
  2. 群消息 / 单聊消息（文字 + 图片，图片走富媒体 files 接口，base64 直传）
  3. QQBotListener：WebSocket 长连接，收到消息即自动绑定
     user_openid / group_openid / event_id（主动推送需要 7 天内的事件 id）

主动推送规则（QQ 官方限制）：
  群/单聊主动消息必须携带 msg_id（5分钟内被动回复）或 event_id（7天内交互事件）。
  所以需要用 QQ 给机器人发一条消息（或在群里 @机器人），监听器会自动保存 event_id。
"""

import os
import json
import time
import threading
import urllib.request
import urllib.error

API = "https://api.sgroup.qq.com"
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"

# 事件订阅 intents
INTENT_C2C_GROUP = 1 << 25     # C2C_MESSAGE_CREATE / GROUP_AT_MESSAGE_CREATE
INTENT_INTERACTION = 1 << 26


def _now_ms():
    return int(time.time() * 1000)


class QQOfficialBot:
    def __init__(self, cfg, cache_path=None, log=print):
        q = cfg or {}
        self.app_id = (q.get("app_id") or "").strip()
        self.client_secret = (q.get("client_secret") or "").strip()
        self.user_openid = (q.get("user_openid") or "").strip()
        self.group_openid = (q.get("group_openid") or "").strip()
        self.target_type = q.get("target_type", "group")  # group / c2c
        self.log = log
        self.cache_path = cache_path or os.path.join(_base_dir(), "qq_token_cache.json")
        self._token = None
        self._expires_ms = 0
        self._load_cached_token()

    @property
    def enabled(self):
        return bool(self.app_id and self.client_secret)

    # ---------- 配置（由 GUI 保存） ----------
    @staticmethod
    def read_config(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        return cfg.setdefault("qq_official", {})

    @staticmethod
    def write_config(path, patch):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        q = cfg.setdefault("qq_official", {})
        q.update(patch)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    # ---------- HTTP ----------
    def _http(self, method, url, payload, with_auth=True, timeout=15):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if with_auth:
            headers["Authorization"] = f"QQBot {self.get_token()}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(body)
            except Exception:
                return e.code, {"message": body}

    def _load_cached_token(self):
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if d.get("app_id") == self.app_id and int(d.get("expires_time", 0)) > _now_ms() + 60000:
                self._token = d.get("access_token")
                self._expires_ms = int(d.get("expires_time", 0))
        except Exception:
            pass

    def _save_cached_token(self, token, expires_in_s):
        self._expires_ms = _now_ms() + max(60, int(expires_in_s) - 120) * 1000
        try:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump({"app_id": self.app_id, "access_token": token,
                           "expires_time": self._expires_ms}, f, ensure_ascii=False)
        except Exception:
            pass

    def get_token(self, force=False):
        if not force and self._token and _now_ms() < self._expires_ms - 60000:
            return self._token
        code, d = self._http("POST", TOKEN_URL,
                             {"appID": self.app_id, "clientSecret": self.client_secret},
                             with_auth=False, timeout=12)
        token = d.get("access_token")
        if code != 200 or not token:
            raise RuntimeError(f"获取令牌失败({code})：{d.get('message') or d}")
        self._token = token
        self._save_cached_token(token, int(float(d.get("expires_in", 7200))))
        return token

    # ---------- 收件目标 ----------
    def _target(self, target_type=None):
        tt = target_type or self.target_type
        if tt == "c2c":
            return tt, "/v2/users/%s/messages" % self.user_openid, "/v2/users/%s/files" % self.user_openid
        return tt, "/v2/groups/%s/messages" % self.group_openid, "/v2/groups/%s/files" % self.group_openid

    def _event_id(self, target_type=None):
        qpath = _config_path()
        try:
            q = self.read_config(qpath)
        except Exception:
            return None
        key = "c2c_event_id" if (target_type or self.target_type) == "c2c" else "group_event_id"
        eid = q.get(key)
        # event_id 有效期 7 天（604800 秒）
        ts = q.get(key.replace("event_id", "event_ts"), 0)
        if eid and (not ts or time.time() - float(ts) < 7 * 86400 - 3600):
            return eid
        return None

    def _upload_media(self, files_url, img_b64):
        code, d = self._http("POST", API + files_url,
                             {"file_type": 1, "file_data": img_b64})
        info = d.get("file_info")
        if code != 200 or not info:
            raise RuntimeError(f"图片上传失败({code})：{d.get('message') or d}")
        return info

    def send(self, text, img_b64=None, target_type=None):
        """发送文字(+图片)。返回 (成功, 消息)"""
        if not self.enabled:
            return False, "AppID/AppSecret 未配置"
        tt, msg_path, files_path = self._target(target_type)
        if tt == "c2c" and not self.user_openid:
            return False, "单聊 user_openid 未绑定（先用 QQ 给机器人发一条消息）"
        if tt == "group" and not self.group_openid:
            return False, "群聊 group_openid 未绑定（先在群里 @机器人 发一条消息）"
        event_id = self._event_id(tt)
        if not event_id:
            return False, ("缺少有效 event_id：请用 QQ 给机器人发条消息（单聊）或在群里 @机器人，"
                          "并在助手设置里开启自动绑定")
        try:
            body = {"msg_type": 0, "content": text, "event_id": event_id}
            if img_b64:
                info = self._upload_media(files_path, img_b64)
                body = {"msg_type": 7, "content": text,
                        "media": {"file_info": info}, "event_id": event_id}
            code, d = self._http("POST", API + msg_path, body)
            if code in (200, 201, 202) and (d.get("id") or d.get("send_time")):
                return True, "发送成功"
            # 令牌过期 → 强刷一次重试
            if code in (401, 404):
                self.get_token(force=True)
                code, d = self._http("POST", API + msg_path, body)
                if code in (200, 201, 202):
                    return True, "发送成功（令牌刷新后重试）"
            return False, f"发送失败({code})：{d.get('message') or d}"
        except Exception as e:
            return False, str(e)


def _base_dir():
    if getattr(__import__("sys"), "frozen", False):
        return os.path.dirname(__import__("sys").executable)
    return os.path.dirname(os.path.abspath(__file__))


def _config_path():
    return os.path.join(_base_dir(), "config.json")


# ==================== WebSocket 监听器（自动绑定 openid / event_id） ====================
class QQBotListener(threading.Thread):
    def __init__(self, bot, on_event=None, log=print):
        super().__init__(daemon=True)
        self.bot = bot
        self.on_event = on_event or (lambda kind, data: None)
        self.log = log
        self._stop = threading.Event()
        self.ws = None

    def stop(self):
        self._stop.set()
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass

    def run(self):
        import websocket  # websocket-client
        backoff = 3
        while not self._stop.is_set():
            try:
                token = self.bot.get_token()
                url = self._get_gateway(token)
                self.log("QQ机器人监听已连接，等待消息事件…")
                self.on_event("status", {"text": "监听中：请用 QQ 给机器人发消息"})
                self.ws = websocket.WebSocketApp(
                    url,
                    header=[f"Authorization: QQBot {token}"],
                    on_message=self._on_message,
                    on_error=lambda w, e: self.log(f"QQ监听错误：{e}"),
                    on_close=lambda w, *a: self.log("QQ监听连接关闭"),
                )
                self.ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                self.log(f"QQ监听异常：{e}")
            if self._stop.is_set():
                break
            self.log(f"{backoff} 秒后重连…")
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 60)

    def _get_gateway(self, token):
        req = urllib.request.Request(API + "/gateway",
                                     headers={"Authorization": f"QQBot {token}"})
        with urllib.request.urlopen(req, timeout=12) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        return d["url"]

    def _on_message(self, ws, message):
        try:
            evt = json.loads(message)
        except Exception:
            return
        op = evt.get("op")
        if op == 10:   # Hello
            interval = int(evt["d"]["heartbeat_interval"]) / 1000.0
            self._start_heartbeat(ws, interval)
            ws.send(json.dumps({"op": 2, "d": {
                "token": f"QQBot {self.bot.get_token()}",
                "intents": INTENT_C2C_GROUP,
                "shard": [0, 1],
                "properties": {}}}))
            return
        if op == 11:  # Heartbeat ACK
            return
        if op == 0:
            self._dispatch(evt)

    def _start_heartbeat(self, ws, interval):
        state = {"seq": None, "alive": True}

        def beat():
            while state["alive"] and not self._stop.is_set():
                if self._stop.wait(interval):
                    break
                try:
                    ws.send(json.dumps({"op": 1, "d": state["seq"]}))
                except Exception:
                    break

        # seq 由 _dispatch() 从每条事件里提取并写入 state（on_message 回调已固定，无法挂 track）
        ws.seq_state = state
        threading.Thread(target=beat, daemon=True).start()

    def _dispatch(self, evt):
        # 心跳 seq 更新
        st = getattr(self.ws, "seq_state", None)
        if st is not None and evt.get("s") is not None:
            st["seq"] = evt["s"]

        t = evt.get("t")
        d = evt.get("d") or {}
        event_id = evt.get("id")
        patch = {}
        kind = None
        if t == "C2C_MESSAGE_CREATE":
            openid = (d.get("author") or {}).get("user_openid") or d.get("user_openid")
            if openid:
                patch["user_openid"] = openid
                self.bot.user_openid = openid
            if event_id:
                patch["c2c_event_id"] = event_id
                patch["c2c_event_ts"] = time.time()
            kind = "c2c"
            self.log(f"收到单聊消息，已绑定 user_openid={openid}")
        elif t == "GROUP_AT_MESSAGE_CREATE":
            gopenid = d.get("group_openid")
            if gopenid:
                patch["group_openid"] = gopenid
                self.bot.group_openid = gopenid
            mopenid = (d.get("author") or {}).get("member_openid")
            if mopenid:
                patch["member_openid"] = mopenid
            if event_id:
                patch["group_event_id"] = event_id
                patch["group_event_ts"] = time.time()
            kind = "group"
            self.log(f"收到群@消息，已绑定 group_openid={gopenid}")
        if patch:
            try:
                self.bot.write_config(_config_path(), patch)
            except Exception as e:
                self.log(f"绑定信息写回失败：{e}")
            self.on_event("bind", {"kind": kind, **patch})

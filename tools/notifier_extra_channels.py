# -*- coding: utf-8 -*-
"""
通知渠道归档（暂未启用，留作以后扩展用）
==================================================
当前主程序只保留 QQ + 邮箱 两个渠道。
以下渠道代码归档保存，需要时把对应类加回 Notifier.channels 即可。

每个渠道都继承 _BaseChannel，实现 _do_send(title, content, img_b64)。
本文件自包含：_BaseChannel 与主程序中定义保持一致（enabled 检查 + 异常兜底），
无需导入 lagrange_upgrade（避免其配置加载等副作用）。
"""
import json


class _BaseChannel:
    """通知渠道基类（与 lagrange_upgrade._BaseChannel 保持一致）。"""
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


# ============== 通用 Webhook 渠道 ==============
# 一条配置对接 Server酱/Bark/钉钉/飞书/Discord/企业微信/Gotify 等所有 HTTP 通知服务
class WebhookChannel(_BaseChannel):
    """自定义 body 模板占位符：{title} {content} {image}(base64)"""
    name = "webhook"
    def __init__(self, cfg):
        super().__init__(cfg)
        self.url = cfg.get("url", "")
        self.method = (cfg.get("method") or "POST").upper()
        self.headers = cfg.get("headers") or {}
        self.body_template = cfg.get("body_template", "") or ""

    def _do_send(self, title, content, img_b64):
        import urllib.request
        if not self.body_template:
            payload = {"title": title, "content": content}
            if img_b64:
                payload["image"] = img_b64
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers = {"Content-Type": "application/json", **self.headers}
        else:
            rendered = self.body_template.replace("{title}", title).replace("{content}", content)
            if img_b64:
                rendered = rendered.replace("{image}", img_b64)
            try:
                data = json.dumps(json.loads(rendered), ensure_ascii=False).encode("utf-8")
                headers = {"Content-Type": "application/json", **self.headers}
            except Exception:
                data = rendered.encode("utf-8")
                headers = dict(self.headers)
        req = urllib.request.Request(self.url, data=data, headers=headers, method=self.method)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status

# 配置示例（放到 config.json 顶层）：
# "webhook": {
#   "enabled": false,
#   "url": "https://sctapi.ftqq.com/你的SendKey.send",
#   "method": "POST",
#   "headers": {},
#   "body_template": '{"title":"{title}","desp":"{content}"}'
# }


# ============== Windows 原生弹窗渠道（依赖 winotify） ==============
# pip install winotify   打包时加 --collect-all winotify
class WinToastChannel(_BaseChannel):
    name = "win_toast"
    def __init__(self, cfg):
        super().__init__(cfg)
        self.app_id = cfg.get("app_id", "拉格朗日助手")
        self._lib = None
        if self.enabled:
            try:
                from winotify import Notification
                self._lib = Notification
            except Exception:
                self.enabled = False

    def _do_send(self, title, content, img_b64):
        if self._lib is None:
            return
        msg = content[:250] if content else " "
        n = self._lib(app_id=self.app_id, title=title, msg=msg)
        n.show()

# 配置示例：
# "win_toast": {"enabled": true, "app_id": "拉格朗日助手"}


# ============== 启用方式 ==============
# 在主程序 Notifier.__init__ 的 channels 列表里加回：
#   self.channels = [QQChannel(qcfg), EmailChannel(cfg.get("email", {})),
#                    WebhookChannel(cfg.get("webhook", {})),
#                    WinToastChannel(cfg.get("win_toast", {}))]

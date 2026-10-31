# -*- coding: utf-8 -*-
"""
element_survey.py —— autoup 界面元素全量摸排工具
================================================
用途：把自动升级脚本需要的所有页面（主界面/基地建设页/一键升级面板/资源不足面板等）
      逐页截图 + OCR，记录全部「文字 + 坐标 + 置信度」，写入 survey_log.md。

用法（真机需 USB 调试已连，游戏前台）：
  python element_survey.py shot <页面名>   截图当前页面并 OCR 记录
  python element_survey.py ocr              只 OCR 不落盘记录（快速看）
  python element_survey.py tap <x> <y> [wait]  点击坐标（默认等1.5s）
  python element_survey.py auto            自动摸排：主界面→基地建设→一键升级面板全链路
"""

import os
import re
import sys
import time
import subprocess

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from rapidocr_onnxruntime import RapidOCR
from adb_locator import find_adb

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SHOT_DIR = os.path.join(BASE_DIR, "survey_shots")
LOG_PATH = os.path.join(BASE_DIR, "survey_log.md")
ADB_EXE = find_adb() or "adb"

DESIGN_W, DESIGN_H = 2310, 1080


def adb_run(*args, timeout=25):
    """跑 adb 命令，返回 stdout bytes"""
    cmd = [ADB_EXE, "-s", pick_device(), *args]
    return subprocess.run(cmd, capture_output=True, timeout=timeout).stdout


def pick_device():
    out = subprocess.run([ADB_EXE, "devices"], capture_output=True, timeout=10).stdout.decode("utf-8", "replace")
    devs = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line or "\t" not in line:
            continue
        serial, state = line.split("\t", 1)
        if state.startswith("device"):
            devs.append(serial)
    # 真机优先
    phones = [d for d in devs if "127.0.0.1" not in d and "emulator" not in d]
    return (phones or devs)[0]


_ocr = None


def ocr_engine():
    global _ocr
    if _ocr is None:
        _ocr = RapidOCR()
    return _ocr


def screenshot():
    """截图 → np.ndarray(BGR)；失败重试"""
    import numpy as np
    from PIL import Image
    import io as _io
    for _ in range(3):
        try:
            raw = adb_run("exec-out", "screencap", "-p")
            if raw[:8] != b"\x89PNG\r\n\x1a\n":
                raise ValueError("not png")
            img = Image.open(_io.BytesIO(raw)).convert("RGB")
            return np.array(img)[:, :, ::-1].copy()
        except Exception:
            time.sleep(1)
    raise RuntimeError("screenshot failed")


def ocr_all(img):
    """全屏 OCR → [(cx, cy, text, score)]，按 y 排序"""
    result, _ = ocr_engine()(img)
    items = []
    if result:
        for box, text, score in result:
            try:
                sc = float(score)
            except (TypeError, ValueError):
                sc = 0.0
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            items.append((sum(xs) / 4, sum(ys) / 4, str(text).strip(), sc))
    items.sort(key=lambda t: (t[1], t[0]))
    return items


def tap(x, y, wait=1.5):
    adb_run("shell", "input", "tap", str(int(x)), str(int(y)))
    time.sleep(wait)


def ensure_dirs():
    os.makedirs(SHOT_DIR, exist_ok=True)


def next_shot_no():
    n = 1
    while os.path.exists(os.path.join(SHOT_DIR, f"p{n:02d}.png")):
        n += 1
    return n


def survey_page(page_name, note=""):
    """截图 + OCR + 写记录。返回 items。"""
    ensure_dirs()
    no = next_shot_no()
    img = screenshot()
    from PIL import Image
    Image.fromarray(img[:, :, ::-1]).save(os.path.join(SHOT_DIR, f"p{no:02d}.png"))
    items = ocr_all(img)
    w = img.shape[1]
    h = img.shape[0]
    ts = time.strftime("%H:%M:%S")
    lines = [
        f"\n## p{no:02d} · {page_name}  ({ts})\n",
        f"- 截图：survey_shots/p{no:02d}.png（{w}x{h}）",
    ]
    if note:
        lines.append(f"- 备注：{note}")
    lines.append(f"- OCR 元素 {len(items)} 个：\n")
    lines.append("| cx | cy | 文字 | 置信度 |")
    lines.append("|---:|---:|---|---:|")
    for cx, cy, text, score in items:
        lines.append(f"| {int(cx)} | {int(cy)} | {text} | {score:.2f} |")
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n===== p{no:02d} · {page_name} =====")
    print(f"截图 p{no:02d}.png 已存，OCR {len(items)} 项（已写 survey_log.md）")
    for cx, cy, text, score in items:
        print(f"  ({int(cx):4d},{int(cy):4d}) {score:.2f}  {text}")
    return items


def wait_stable(sec=2.0):
    time.sleep(sec)


# ==================== 自动摸排链路 ====================
def auto_survey():
    """主链路自动摸排：
    S0 当前页面 → S1 点底部「基地建设」 → S2 基地建设页 → S3 点「一键升级」
    → S4 一键升级面板 → S5 尝试关闭面板 → 每一步都截图+OCR 记录"""
    print("== 自动摸排开始：每步截图+OCR 全量记录 ==")
    # S0 当前页面
    items = survey_page("S0_当前页面_起点")
    # S1 找「基地建设」入口（主界面底部金色标签）
    hit = None
    for cx, cy, text, score in items:
        if "基地建设" in text:
            hit = (cx, cy, text)
            break
    if not hit:
        # 兜底：找「基地」
        for cx, cy, text, score in items:
            if text == "基地" or "基地" in text:
                hit = (cx, cy, text)
                break
    if not hit:
        print("!! 没找到「基地建设」入口，停在这里，请人工切页面后重跑")
        return
    print(f"--> 点击基地建设入口 ({int(hit[0])},{int(hit[1])}) '{hit[2]}'")
    tap(hit[0], hit[1], wait=2.5)

    # S2 基地建设页
    items = survey_page("S2_基地建设页")
    hit = None
    for cx, cy, text, score in items:
        if "一键升级" in text:
            hit = (cx, cy, text)
            break
    if not hit:
        print("!! 基地建设页没找到「一键升级」，可能页面状态不同，记录后停止")
        return
    print(f"--> 点击一键升级 ({int(hit[0])},{int(hit[1])}) '{hit[2]}'")
    tap(hit[0], hit[1], wait=2.5)

    # S3 一键升级面板
    items = survey_page("S3_一键升级面板")
    # 记录关键元素
    times = [t for t in items if re.match(r"^\d{1,2}:\d{2}:\d{2}$", t[2])]
    print(f"面板上识别到 {len(times)} 个三段式倒计时")
    for t in times:
        print(f"    ({int(t[0])},{int(t[1])}) {t[2]}")

    # S4 尝试点面板里的「升级」按钮（如果有）
    up_hit = None
    for cx, cy, text, score in items:
        if text.strip() in ("升级", "立即升级") and cx > w_hint * 0.5:
            up_hit = (cx, cy, text)
            break
    if up_hit:
        print(f"--> 发现升级按钮 ({int(up_hit[0])},{int(up_hit[1])})，先不点（摸排模式不产生真实操作）")

    print("== 自动摸排完成，详见 survey_log.md ==")


w_hint = DESIGN_W  # 占位，auto_survey 中比较用


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd = args[0]
    if cmd == "shot" and len(args) >= 2:
        survey_page(args[1], note=" ".join(args[2:]))
    elif cmd == "ocr":
        items = ocr_all(screenshot())
        for cx, cy, text, score in items:
            print(f"({int(cx):4d},{int(cy):4d}) {score:.2f}  {text}")
    elif cmd == "tap" and len(args) >= 3:
        tap(int(args[1]), int(args[2]), float(args[3]) if len(args) > 3 else 1.5)
        print(f"tapped ({args[1]},{args[2]})")
    elif cmd == "auto":
        auto_survey()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()

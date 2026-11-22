# -*- coding: utf-8 -*-
"""
lagrange_upgrade.py 纯逻辑单元测试（无需真机 / 无需 ADB / 无需 OCR 引擎）。

运行方式：
    cd E:\\拉格朗日自动升级
    "C:/Program Files/Python314/python.exe" -m unittest tests.test_lagrange -v
    # 或
    "C:/Program Files/Python314/python.exe" tests/test_lagrange.py
"""
import os
import sys
import unittest

# 把脚本所在目录加进 path，便于 import lagrange_upgrade
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import lagrange_upgrade as L


class _FakeAdb:
    """最小可用 Adb 替身：只提供 scale_box / _daily_* 用的 w,h 与截图/点击桩。"""
    def __init__(self, w=2310, h=1080):
        self.w, self.h = w, h
        self.calls = []          # 记录 run() 调用参数，便于验证“是否误触”

    def screenshot(self):
        return None

    def tap(self, x, y):
        self.calls.append(("tap", x, y))

    def run(self, *args, **kwargs):
        self.calls.append(args)
        return None


class TestParseSeconds(unittest.TestCase):
    def test_hhmmss(self):
        self.assertEqual(L.parse_seconds("34:30:00"), 34 * 3600 + 30 * 60)
        self.assertEqual(L.parse_seconds("9:05:00"), 9 * 3600 + 5 * 60)

    def test_fullwidth_colon(self):
        # 全角冒号应被归一化
        self.assertEqual(L.parse_seconds("59：59"), 59 * 60 + 59)

    def test_mmss(self):
        self.assertEqual(L.parse_seconds("59:59"), 59 * 60 + 59)
        self.assertEqual(L.parse_seconds("1:30"), 90)

    def test_chinese(self):
        self.assertEqual(L.parse_seconds("2小时30分"), 2 * 3600 + 30 * 60)
        self.assertEqual(L.parse_seconds("45分30秒"), 45 * 60 + 30)

    def test_decimal_rejected(self):
        # 小数点不当分隔符，避免把 '1.03万' 误判为时间
        self.assertIsNone(L.parse_seconds("1.03万"))

    def test_out_of_range_rejected(self):
        self.assertIsNone(L.parse_seconds("999999:99:99"))     # 超上限
        self.assertIsNone(L.parse_seconds(""))                  # 空

    def test_boundary_values(self):
        # 恰好等于边界也应接受
        self.assertEqual(L.parse_seconds("00:00:01"), 1)
        self.assertEqual(L.parse_seconds("0:00"), 0)


class TestMergeLines(unittest.TestCase):
    def test_merged_same_row(self):
        items = [
            (100, 200, "34", 0.9),
            (140, 200, "30", 0.9),
            (160, 200, "00", 0.9),
            (200, 260, "金属", 0.8),
        ]
        merged = L._merge_lines(items)
        texts = [t for _, _, t, _ in merged]
        self.assertIn("34 30 00", texts)
        self.assertIn("金属", texts)
        self.assertEqual(len(merged), 2)

    def test_row_order(self):
        merged = L._merge_lines([(100, 300, "b", 0.9), (100, 200, "a", 0.9)])
        self.assertEqual([t for _, _, t, _ in merged], ["a", "b"])


class TestScaleBox(unittest.TestCase):
    def test_identity_at_design(self):
        adb = _FakeAdb(2310, 1080)
        self.assertEqual(L.scale_box(adb, (100, 200, 300, 400)), (100, 200, 300, 400))

    def test_half_resolution(self):
        adb = _FakeAdb(1155, 540)
        self.assertEqual(L.scale_box(adb, (100, 200, 300, 400)), (50, 100, 150, 200))

    def test_double_resolution(self):
        adb = _FakeAdb(4620, 2160)
        self.assertEqual(L.scale_box(adb, (100, 200, 300, 400)), (200, 400, 600, 800))


class TestResDiff(unittest.TestCase):
    def test_diff_only_changed(self):
        before = {"金属": 100, "晶体": 50, "重氢": 30}
        after = {"金属": 110, "晶体": 50, "重氢": 35}
        diff = L._au_res_diff(before, after)
        self.assertIn(("金属", 10), diff)
        self.assertIn(("重氢", 5), diff)
        self.assertNotIn(("晶体", 0), diff)

    def test_missing_key_ignored(self):
        before = {"金属": 100}
        after = {"金属": 100, "晶体": 5}
        self.assertEqual(L._au_res_diff(before, after), [])


class TestFindButton(unittest.TestCase):
    def setUp(self):
        self.items = [
            (100, 100, "升级", 0.9),
            (2000, 1000, "一键升级", 0.9),
            (2000, 1050, "一键升级", 0.9),
        ]

    def test_region_filter(self):
        p = L.find_button(self.items, ["一键升级"],
                          region=(1900, 900, 2310, 1080))
        self.assertEqual(p, (2000, 1000))

    def test_prefer_bottom(self):
        p = L.find_button(self.items, ["一键升级"], prefer="bottom")
        self.assertEqual(p, (2000, 1050))

    def test_exclude(self):
        p = L.find_button(self.items, ["升级"], exclude=["一键升级"])
        self.assertEqual(p, (100, 100))

    def test_not_found(self):
        self.assertIsNone(L.find_button(self.items, ["不存在"]))

    def test_min_score(self):
        items = [(100, 100, "领取", 0.3)]
        self.assertIsNone(L.find_button(items, ["领取"], min_score=0.45))


class TestDailyFindClaimBtn(unittest.TestCase):
    def test_picks_bottommost_valid(self):
        items = [
            (100, 200, "领取", 0.9),
            (100, 300, "已领取", 0.9),          # 排除词
            (100, 100, "升级", 0.9),           # 不在 MAIL 关键词
            (100, 400, "签到领取大礼包", 0.9),  # 超长被过滤
            (100, 500, "领取附件", 0.9),
        ]
        best = L._daily_find_claim_btn(items)
        self.assertIsNotNone(best)
        self.assertEqual(best[2], "领取附件")

    def test_min_xy_filter(self):
        items = [(100, 200, "领取", 0.9), (800, 200, "领取", 0.9)]
        self.assertEqual(L._daily_find_claim_btn(items, min_x=500)[0], 800)
        self.assertIsNone(L._daily_find_claim_btn(items, min_x=900))

    def test_short_filtered(self):
        items = [(100, 200, "x", 0.9)]
        self.assertIsNone(L._daily_find_claim_btn(items))


class TestSelectActivityEntries(unittest.TestCase):
    def test_region_and_dedup(self):
        adb = _FakeAdb()
        items = [
            (400, 200, "每日签到", 0.8),       # 侧栏内
            (400, 250, "每日签到副标题", 0.7),  # 同一条，按 y<60 去重
            (400, 400, "活跃挑战", 0.8),       # 侧栏内
            (1500, 400, "领取", 0.9),          # x 超出侧栏
            (400, 50, "x", 0.8),               # 文字 <2 字
        ]
        entries = L._select_activity_entries(items, adb)
        texts = [t for _, _, t in entries]
        self.assertEqual(texts, ["每日签到", "活跃挑战"])

    def test_scaled_resolution(self):
        # 半分辨率下侧栏区域被缩放到一半，x=150 应落在侧栏外、x=200 在内
        adb = _FakeAdb(1155, 540)
        items = [
            (200, 100, "活动A", 0.8),
            (150, 100, "活动B", 0.8),
        ]
        entries = L._select_activity_entries(items, adb)
        texts = [t for _, _, t in entries]
        self.assertEqual(texts, ["活动A"])


class TestAuSelectOpenTarget(unittest.TestCase):
    """_au_select_open_target 纯逻辑（无点击），覆盖底栏目标选择的各种情况。"""
    def _adb(self, w=2310, h=1080):
        return _FakeAdb(w, h)

    def test_target_name_priority(self):
        adb = self._adb()
        items = [
            (400, 950, "资源港口", 0.9),
            (400, 980, "平台指挥中心", 0.9),   # 目标建筑
            (300, 950, "采矿场", 0.9),
        ]
        sel = L._au_select_open_target(items, adb, target="平台指挥中心")
        self.assertEqual(sel, (400, 980, "name"))

    def test_leftmost_name_when_no_target(self):
        adb = self._adb()
        items = [
            (500, 950, "B基地", 0.9),
            (300, 950, "A基地", 0.9),         # 最左
            (700, 950, "C基地", 0.9),
        ]
        sel = L._au_select_open_target(items, adb, target="")
        self.assertEqual(sel, (300, 950, "name"))

    def test_only_time_items(self):
        adb = self._adb()
        items = [(400, 950, "34:30:00", 0.9)]
        sel = L._au_select_open_target(items, adb, target="")
        self.assertEqual(sel, (400, 950, "time"))

    def test_non_name_chinese_excluded(self):
        # “建造中/金属”等状态/资源文字不应被当作可点建筑名
        adb = self._adb()
        items = [(400, 950, "建造中", 0.9), (420, 950, "金属", 0.9)]
        self.assertIsNone(L._au_select_open_target(items, adb, target=""))

    def test_above_bottom_excluded(self):
        # 设计基准 1080 高，阈值 y=900；cy=860 在阈值之上应被忽略
        adb = self._adb()
        items = [(400, 860, "平台指挥中心", 0.9)]
        self.assertIsNone(L._au_select_open_target(items, adb, target="平台指挥中心"))

    def test_scaled_resolution(self):
        # 半分辨率 1155x540，阈值 = 540*900/1080 = 450；cy=460 在内、cy=400 在外
        adb = self._adb(1155, 540)
        items = [
            (200, 460, "平台指挥中心", 0.9),   # 在内
            (200, 400, "资源港口", 0.9),       # 在外
        ]
        sel = L._au_select_open_target(items, adb, target="")
        self.assertEqual(sel, (200, 460, "name"))

    def test_empty(self):
        self.assertIsNone(L._au_select_open_target([], self._adb(), target=""))

    def test_lock_skips_target_building(self):
        # 目标建筑在锁定名单里 → 锁定优先于目标，跳过它，改选其它可见建筑名
        adb = self._adb()
        items = [
            (400, 950, "平台指挥中心", 0.9),   # 目标，但被锁定
            (300, 950, "资源港口", 0.9),
        ]
        sel = L._au_select_open_target(items, adb, target="平台指挥中心", locked=["平台指挥中心"])
        self.assertEqual(sel, (300, 950, "name"))

    def test_lock_skips_leftmost_then_picks_next(self):
        # 最左建筑被锁定 → 选下一个最左的可升级建筑名
        adb = self._adb()
        items = [
            (300, 950, "居住区", 0.9),         # 最左，但被锁定
            (500, 950, "A基地", 0.9),
            (700, 950, "B基地", 0.9),
        ]
        sel = L._au_select_open_target(items, adb, target="", locked=["居住区"])
        self.assertEqual(sel, (500, 950, "name"))

    def test_lock_all_names_falls_to_time(self):
        # 所有建筑名都被锁定 → 退化为倒计时文字兜底
        adb = self._adb()
        items = [
            (300, 950, "居住区", 0.9),
            (500, 950, "研究实验室", 0.9),
            (400, 950, "34:30:00", 0.9),
        ]
        sel = L._au_select_open_target(items, adb, target="", locked=["居住区", "研究实验室"])
        self.assertEqual(sel, (400, 950, "time"))

    def test_lock_all_names_no_time_returns_none(self):
        # 建筑名全被锁定且无倒计时 → 返回 None（不会盲点）
        adb = self._adb()
        items = [
            (300, 950, "居住区", 0.9),
            (500, 950, "研究实验室", 0.9),
        ]
        self.assertIsNone(L._au_select_open_target(items, adb, target="", locked=["居住区", "研究实验室"]))


class TestAuWake(unittest.TestCase):
    """_au_wake 必须：亮屏时绝不滑动（避免误触游戏 UI），息屏时才上滑解锁。"""
    def test_no_swipe_when_screen_on(self):
        adb = _FakeAdb()
        orig = L._au_screen_on
        L._au_screen_on = lambda a: True
        try:
            L._au_wake(adb)
        finally:
            L._au_screen_on = orig
        joined = " ".join(str(c) for c in adb.calls)
        self.assertIn("KEYCODE_WAKEUP", joined)
        self.assertNotIn("swipe", joined)      # 关键：亮屏不能瞎滑

    def test_swipe_only_when_screen_off(self):
        adb = _FakeAdb()
        orig = L._au_screen_on
        L._au_screen_on = lambda a: False
        try:
            L._au_wake(adb)
        finally:
            L._au_screen_on = orig
        joined = " ".join(str(c) for c in adb.calls)
        self.assertIn("KEYCODE_WAKEUP", joined)
        self.assertIn("swipe", joined)


class TestSpeedProfiles(unittest.TestCase):
    """速本方案：多套配置解析、按清单顺序选目标、等级识别与达标跳过。"""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="lglr_prof_")
        self.path = os.path.join(self.tmp, "profiles.json")
        self.prof = {
            "note": "t",
            "strict": False,
            "locked_buildings": ["居住区"],
            "build_order": [
                {"name": "平台指挥中心", "target_level": 8},
                {"name": "资源港口", "target_level": 10},
                "通用港口",
                {"name": "", "target_level": 3},
                {"name": "资源港口", "target_level": 99},
                {"name": "战地指挥中心", "target_level": "x"},
            ],
        }

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_autocreate_template(self):
        data = L.load_profiles(self.path)
        self.assertTrue(os.path.isfile(self.path))
        self.assertEqual(data["active"], "速本")
        self.assertIn("速本", data["profiles"])

    def test_bad_json_falls_back(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{broken")
        self.assertIn("速本", L.load_profiles(self.path)["profiles"])

    def test_active_off_and_missing(self):
        L.save_profiles({"active": "", "profiles": {"速本": self.prof}}, self.path)
        self.assertEqual(L.active_profile(self.path), ("", {}))
        L.save_profiles({"active": "不存在的方案", "profiles": {}}, self.path)
        self.assertEqual(L.active_profile(self.path), ("", {}))

    def test_active_ok(self):
        L.save_profiles({"active": "速本", "profiles": {"速本": self.prof}}, self.path)
        name, prof = L.active_profile(self.path)
        self.assertEqual(name, "速本")
        self.assertIn("build_order", prof)

    def test_save_and_reload(self):
        self.assertTrue(L.save_profiles({"active": "A", "profiles": {"A": self.prof}}, self.path))
        self.assertEqual(L.load_profiles(self.path)["active"], "A")

    def test_targets_normalize(self):
        out = L.profile_targets(self.prof)
        self.assertEqual(out[0], ("平台指挥中心", 8))
        self.assertEqual(out[1], ("资源港口", 10))
        self.assertEqual(out[2], ("通用港口", 0))
        self.assertEqual(len(out), 4)             # 空名 / 重复名去掉，非法等级归 0
        self.assertEqual(out[-1], ("战地指挥中心", 0))

    def test_targets_empty_profile(self):
        self.assertEqual(L.profile_targets({}), [])
        self.assertIsNone(L.match_profile_building([], {}))

    def test_locked_merges_config(self):
        orig = L.CONFIG
        L.CONFIG = {"au_locked_buildings": ["研究实验室", "居住区"]}
        try:
            self.assertEqual(L.profile_locked(self.prof), ["居住区", "研究实验室"])
        finally:
            L.CONFIG = orig

    def test_pick_by_order_not_leftmost(self):
        items = [(300, 1000, "通用港口", 0.9),
                 (200, 1000, "资源港口", 0.9),
                 (100, 1000, "平台指挥中心", 0.9)]
        sel = L.profile_pick_target(items, _FakeAdb(), self.prof)
        self.assertEqual(sel[2], "平台指挥中心")
        self.assertEqual(sel[0], 100)

    def test_pick_skips_locked_and_reached(self):
        items = [(300, 1000, "居住区", 0.9), (200, 1000, "资源港口", 0.9),
                 (100, 1000, "平台指挥中心", 0.9)]
        sel = L.profile_pick_target(items, _FakeAdb(), self.prof,
                                    skipped={"平台指挥中心"}, locked=["居住区"])
        self.assertEqual(sel[2], "资源港口")

    def test_pick_ignores_countdown_and_nonname(self):
        items = [(50, 1000, "34:30:00", 0.9), (60, 1000, "建造队列", 0.9),
                 (200, 1000, "资源港口", 0.9)]
        self.assertEqual(L.profile_pick_target(items, _FakeAdb(), self.prof)[2], "资源港口")

    def test_pick_no_match(self):
        items = [(200, 1000, "陌生设施", 0.9)]
        self.assertIsNone(L.profile_pick_target(items, _FakeAdb(), self.prof))

    def test_pick_respects_bottom_threshold(self):
        items = [(200, 300, "资源港口", 0.9)]     # y 不在底部队列条
        self.assertIsNone(L.profile_pick_target(items, _FakeAdb(), self.prof))

    def test_parse_level_variants(self):
        for text, want in (("Lv.7", 7), ("LV 12", 12), ("等级 3", 3),
                           ("等级：9", 9), ("7级", 7)):
            self.assertEqual(L.parse_level_from_items([(0, 0, text, 0.9)]), want)

    def test_parse_level_excludes_target_text(self):
        for text in ("升至2级", "目标等级 8", "升至满级", "升级后 5级"):
            self.assertIsNone(L.parse_level_from_items([(0, 0, text, 0.9)]))

    def test_parse_level_none_when_absent(self):
        self.assertIsNone(L.parse_level_from_items([(0, 0, "资源港口", 0.9)]))
        self.assertIsNone(L.parse_level_from_items([]))

    def test_match_profile_building(self):
        items = [(0, 0, "资源港口", 0.9), (0, 0, "Lv.5", 0.9)]
        self.assertEqual(L.match_profile_building(items, self.prof), ("资源港口", 10))
        self.assertIsNone(L.match_profile_building([(0, 0, "陌生设施", 0.9)], self.prof))

    def _patch_skip(self, profile):
        """替换会碰设备/文件的外部依赖，返回还原函数。"""
        orig = (L.active_profile, L._au_close_panel, L._AU_REACHED)
        L.active_profile = lambda *a, **k: ("速本", profile)
        L._au_close_panel = lambda a: a.calls.append("close")
        L._AU_REACHED = set()

        def restore():
            L.active_profile, L._au_close_panel, L._AU_REACHED = orig
        return restore

    def test_skip_reached_closes_panel(self):
        adb = _FakeAdb()
        restore = self._patch_skip({"build_order": [{"name": "资源港口", "target_level": 8}]})
        try:
            items = [(0, 0, "资源港口", 0.9), (0, 0, "Lv.8", 0.9)]
            self.assertTrue(L._au_skip_reached(adb, items))
            self.assertIn("资源港口", L._AU_REACHED)
            self.assertIn("close", adb.calls)
        finally:
            restore()

    def test_no_skip_when_level_unreadable(self):
        adb = _FakeAdb()
        restore = self._patch_skip({"build_order": [{"name": "资源港口", "target_level": 8}]})
        try:
            self.assertFalse(L._au_skip_reached(adb, [(0, 0, "资源港口", 0.9)]))
            self.assertEqual(L._AU_REACHED, set())   # 读不到等级：宁可多升不漏升
            self.assertNotIn("close", adb.calls)
        finally:
            restore()

    def test_no_skip_below_target(self):
        restore = self._patch_skip({"build_order": [{"name": "资源港口", "target_level": 8}]})
        try:
            items = [(0, 0, "资源港口", 0.9), (0, 0, "Lv.5", 0.9)]
            self.assertFalse(L._au_skip_reached(_FakeAdb(), items))
        finally:
            restore()

    def test_no_skip_when_profile_off(self):
        restore = self._patch_skip({})
        L.active_profile = lambda *a, **k: ("", {})
        try:
            self.assertFalse(L._au_skip_reached(_FakeAdb(), [(0, 0, "资源港口", 0.9)]))
        finally:
            restore()


class TestPerformance(unittest.TestCase):
    """性能/发热控制：档位解析、循环节流退避、健康检查节流（纯逻辑，无需设备）。"""

    def setUp(self):
        self._saved = (L._AU_LAST_WAKE_CHECK, L._AU_LAST_HEALTH_CHECK, dict(os.environ))

    def tearDown(self):
        L._AU_LAST_WAKE_CHECK, L._AU_LAST_HEALTH_CHECK = self._saved[:2]
        os.environ.clear()
        os.environ.update(self._saved[2])

    def test_three_modes(self):
        for m, th, itv in (("eco", 2, 10.0), ("balanced", 4, 5.0), ("turbo", 0, 0.0)):
            p = L.perf_settings(mode=m)
            self.assertEqual(p["mode"], m)
            self.assertEqual(p["ocr_threads"], th)
            self.assertEqual(p["loop_min_interval"], itv)
            self.assertIn("gui_poll_ms", p)

    def test_invalid_mode_falls_back_to_eco(self):
        for bad in (None, "", "ultra", 123, {"x": 1}):
            self.assertEqual(L.perf_settings(mode=bad)["mode"], "eco")
        self.assertEqual(L.perf_settings(mode="ECO")["mode"], "eco")   # 大小写归一

    def test_read_mode_from_config(self):
        self.assertEqual(L.perf_settings({"performance": {"mode": "balanced"}})["mode"], "balanced")
        self.assertEqual(L.perf_settings({"performance": {}})["mode"], "eco")
        self.assertEqual(L.perf_settings({"performance": "oops"})["mode"], "eco")
        self.assertEqual(L.perf_settings({})["mode"], "eco")

    def test_numeric_overrides_and_bad_values(self):
        p = L.perf_settings({"performance": {"mode": "eco", "ocr_threads": 3,
                                             "loop_min_interval": 7,
                                             "backoff_max": 40, "gui_poll_ms": 250}})
        self.assertEqual((p["ocr_threads"], p["loop_min_interval"], p["backoff_max"],
                          p["gui_poll_ms"]), (3, 7.0, 40.0, 250))
        bad = L.perf_settings({"performance": {"ocr_threads": -1, "loop_min_interval": "x",
                                               "backoff_max": 99999, "gui_poll_ms": True}})
        self.assertEqual((bad["ocr_threads"], bad["loop_min_interval"], bad["backoff_max"],
                          bad["gui_poll_ms"]), (2, 10.0, 90.0, 500))

    def test_pace_interval_backoff(self):
        seq = [L.pace_interval(n, 10, 90) for n in range(6)]
        self.assertEqual(round(seq[0], 1), 10.0)
        self.assertTrue(all(seq[i] < seq[i + 1] for i in range(4)))
        self.assertEqual(round(seq[-1], 1), 90.0)
        self.assertEqual(L.pace_interval(99, 10, 90), 90.0)      # 不超过上限
        self.assertEqual(L.pace_interval(-5, 10, 90), 10.0)      # 负数按 0 处理
        self.assertEqual(L.pace_interval(0, 0, 0), 0.0)          # turbo 不节流
        self.assertAlmostEqual(L.pace_interval(3, 10, 0), 10 * 1.7 ** 3)

    def test_pace_sleep_never_negative(self):
        self.assertAlmostEqual(L.pace_sleep(3.2, 10), 6.8)
        self.assertEqual(L.pace_sleep(12, 10), 0.0)
        self.assertEqual(L.pace_sleep(-1, 10), 11.0)

    def test_should_wake_check(self):
        self.assertTrue(L.should_wake_check(100, 0, 30))
        self.assertFalse(L.should_wake_check(120, 95, 30))
        self.assertTrue(L.should_wake_check(125, 95, 30))
        self.assertTrue(L.should_wake_check(100, 95, 0))         # every=0 每轮都查

    def test_apply_ocr_env(self):
        os.environ.pop("OMP_NUM_THREADS", None)
        L.apply_ocr_env(2)
        self.assertEqual(os.environ["OMP_NUM_THREADS"], "2")
        self.assertEqual(os.environ["OMP_WAIT_POLICY"], "PASSIVE")
        self.assertEqual(os.environ["OMP_DYNAMIC"], "FALSE")
        os.environ["OMP_NUM_THREADS"] = "8"
        L.apply_ocr_env(0)                                      # 0 = 不改动
        self.assertEqual(os.environ["OMP_NUM_THREADS"], "8")

    def test_wake_paced_only_one_full_check(self):
        from unittest import mock
        adb = _FakeAdb()
        full = []
        orig = L._au_wake
        L._au_wake = lambda a: full.append(1)
        try:
            L._AU_LAST_WAKE_CHECK = 0.0
            with mock.patch.object(L.time, "monotonic", return_value=1000.0):
                L._au_wake_paced(adb, 30.0)                     # 到点 → 完整检查
                L._au_wake_paced(adb, 30.0)                     # 未到点 → 只发 WAKEUP
            self.assertEqual(len(full), 1)
            self.assertEqual(adb.calls[-1],
                             ("shell", "input", "keyevent", "KEYCODE_WAKEUP"))
            with mock.patch.object(L.time, "monotonic", return_value=1031.0):
                L._au_wake_paced(adb, 30.0)
            self.assertEqual(len(full), 2)
        finally:
            L._au_wake = orig

    def test_health_paced(self):
        from unittest import mock
        hits = []
        orig = L._au_ensure_game
        L._au_ensure_game = lambda a, q=None: hits.append(1) or False
        try:
            L._AU_LAST_HEALTH_CHECK = 0.0
            with mock.patch.object(L.time, "monotonic", return_value=2000.0):
                self.assertFalse(L._au_health_paced(_FakeAdb(), None, 60.0))
                self.assertFalse(L._au_health_paced(_FakeAdb(), None, 60.0))
            self.assertEqual(len(hits), 1)
            with mock.patch.object(L.time, "monotonic", return_value=2061.0):
                L._au_health_paced(_FakeAdb(), None, 60.0)
            self.assertEqual(len(hits), 2)
            L._AU_LAST_HEALTH_CHECK = 0.0
            with mock.patch.object(L.time, "monotonic", return_value=2000.0):
                for _ in range(3):
                    L._au_health_paced(_FakeAdb(), None, 0.0)  # turbo：每轮都查
            self.assertEqual(len(hits), 5)
        finally:
            L._au_ensure_game = orig

    def test_gui_poll_map_matches_core(self):
        import tester
        for mode, ms in tester.PERF_POLL_MS.items():
            self.assertEqual(L.perf_settings(mode=mode)["gui_poll_ms"], ms)


class TestRobustness(unittest.TestCase):
    """启动不卡死（ADB 端口扫描预算）+ 日志不堆积（按天轮转）的纯逻辑测试。"""

    def test_port_open(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        try:
            self.assertTrue(L.Adb._port_open(port))
        finally:
            s.close()
        self.assertFalse(L.Adb._port_open(port))

    def _bare_adb(self, raw_fn, port_open_fn, devices=None):
        adb = object.__new__(L.Adb)          # 跳过 __init__，不碰真实 adb
        adb.path = "adb.exe"
        adb.raw = raw_fn
        adb._port_open = port_open_fn
        adb.list_devices = lambda: (devices or [])
        return adb

    def test_scan_only_connects_listening_ports(self):
        calls = []

        def raw(*args, **kw):
            calls.append(args)
            class R:
                stdout = b""
            return R()

        adb = self._bare_adb(raw, lambda p, timeout=0.2: p == 16384)
        adb.scan_and_connect(budget=5)
        self.assertTrue(calls)
        self.assertTrue(all("127.0.0.1:16384" in c for c in calls))

    def test_scan_respects_time_budget(self):
        import time
        calls = []

        def raw(*args, **kw):
            calls.append(args)
            time.sleep(0.05)
            class R:
                stdout = b""
            return R()

        adb = self._bare_adb(raw, lambda p, timeout=0.2: True)
        t0 = time.monotonic()
        adb.scan_and_connect(budget=0.16)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 0.6)                  # 不会扫满 29 个端口
        self.assertLess(len(calls), 6)                 # 预算内只连了少数几个

    def test_prune_old_logs_keeps_recent_days(self):
        import tempfile
        import tester
        with tempfile.TemporaryDirectory() as d:
            for i in range(1, 11):
                open(os.path.join(d, f"202611{i:02d}_autoup.log"), "w").write("x")
            open(os.path.join(d, "keep_me.txt"), "w").write("x")
            open(os.path.join(d, "all_run.log"), "w").write("x")   # 非本程序命名，不能删
            removed = tester.prune_old_logs(d, keep_days=7)
            names = os.listdir(d)
            self.assertEqual(len([n for n in names if n.endswith("_autoup.log")]), 7)
            self.assertEqual(len(removed), 3)
            self.assertIn("keep_me.txt", names)
            self.assertIn("all_run.log", names)
            self.assertTrue(all(n.endswith("_autoup.log") for n in removed))

    def test_daily_log_path_reuses_same_day(self):
        import tempfile
        import tester
        with tempfile.TemporaryDirectory() as d:
            p1 = tester.daily_log_path(d, "autoup", day="20261122")
            p2 = tester.daily_log_path(d, "autoup", day="20261122")
            self.assertEqual(p1, p2)
            self.assertTrue(p1.endswith("20261122_autoup.log"))


class TestDeviceSelection(unittest.TestCase):
    """设备模式重构：serial-only 选择逻辑与 adb devices 解析（纯逻辑，无需设备）。"""

    def test_auto_picks_first_online(self):
        devs = ["127.0.0.1:16384", "127.0.0.1:5555", "127.0.0.1:7555"]
        target, why, err = L.select_device(devs)
        self.assertEqual(target, devs[0])
        self.assertEqual(err, "")
        self.assertIn("自动", why)

    def test_serial_selected_when_online(self):
        devs = ["127.0.0.1:16384", "127.0.0.1:5555"]
        target, why, err = L.select_device(devs, "127.0.0.1:5555")
        self.assertEqual(target, "127.0.0.1:5555")
        self.assertEqual(err, "")
        self.assertEqual(why, "指定设备")

    def test_offline_serial_never_falls_back(self):
        devs = ["127.0.0.1:16384", "127.0.0.1:5555"]
        target, why, err = L.select_device(devs, "ABCDEF123456")
        self.assertIsNone(target)                 # 绝不回退到别的在线设备
        self.assertIn("不在线", err)
        self.assertIn("ABCDEF123456", err)
        self.assertIn("127.0.0.1:16384", err)     # 错误信息里列出在线设备

    def test_no_devices(self):
        target, _, err = L.select_device([], "")
        self.assertIsNone(target)
        self.assertIn("没有在线设备", err)
        target2, _, err2 = L.select_device(None, "127.0.0.1:5555")
        self.assertIsNone(target2)
        self.assertTrue(err2)

    def test_whitespace_and_blank_entries(self):
        devs = ["", "  ", "127.0.0.1:5555", "127.0.0.1:7555"]
        self.assertEqual(L.select_device(devs)[0], "127.0.0.1:5555")
        self.assertEqual(L.select_device(devs, "  127.0.0.1:7555  ")[0],
                         "127.0.0.1:7555")

    def test_default_config_device_is_serial_only(self):
        dev = L.DEFAULT_CONFIG["device"]
        self.assertNotIn("mode", dev)
        self.assertIn("serial", dev)
        self.assertEqual(dev["serial"], "")

    def test_parse_adb_devices_ignores_offline(self):
        import tester
        text = ("List of devices attached\n"
                "127.0.0.1:16384\tdevice\n"
                "emulator-5554\toffline\n"
                "ABCDEF123456\tunauthorized\n"
                "127.0.0.1:5555\tdevice\n\n")
        self.assertEqual(tester.parse_adb_devices(text),
                         ["127.0.0.1:16384", "127.0.0.1:5555"])
        self.assertEqual(tester.parse_adb_devices(""), [])
        self.assertEqual(tester.parse_adb_devices("List of devices attached\n"), [])

    def test_device_auto_label(self):
        import tester
        self.assertIn("自动", tester.DEVICE_AUTO_LABEL)


class TestDeviceListMerge(unittest.TestCase):
    """合并后的在线设备列表：全状态解析、紧凑展示、选择与截图反馈。"""

    SAMPLE = ("List of devices attached\n"
              "127.0.0.1:16384  device product:ali model:PGBM10 device:ali transport_id:1\n"
              "127.0.0.1:5555   offline transport_id:2\n"
              "5ENDU19313001205 unauthorized transport_id:3\n"
              "emulator-5554    device product:sdk model:Pixel_5 transport_id:4\n"
              "* daemon not running *\n")

    def _rows(self):
        import tester
        return tester.parse_adb_device_rows(self.SAMPLE)

    def test_parse_all_statuses(self):
        import tester as T
        rows = self._rows()
        self.assertEqual(len(rows), 4)                    # daemon 提示行被忽略
        self.assertEqual(rows[0]["status_cn"], "在线")
        self.assertEqual(rows[1]["status_cn"], "离线")
        self.assertEqual(rows[2]["status_cn"], "未授权")
        self.assertEqual(rows[0]["model"], "PGBM10")
        self.assertNotIn("kind", rows[0])                # 不再猜测真机/模拟器
        self.assertEqual(T.parse_adb_devices(self.SAMPLE),
                         ["127.0.0.1:16384", "emulator-5554"])

    def test_compact_online_text(self):
        import tester as T
        rows = self._rows()
        for d in rows:
            d["resolution"] = "1600x900"
        txt = T.compact_online_text(rows)
        self.assertIn("在线设备（2）", txt)
        self.assertIn("127.0.0.1:16384", txt)
        self.assertIn("emulator-5554", txt)
        self.assertNotIn("127.0.0.1:5555", txt)          # 离线设备不挤进来
        self.assertNotIn("5ENDU19313001205", txt)        # 未授权设备不挤进来
        self.assertEqual(T.compact_online_text([]), "在线设备（0）：无")

    def test_pick_device_merged(self):
        import tester as T
        rows = self._rows()
        self.assertEqual(T.pick_device(rows)[0]["serial"], "127.0.0.1:16384")
        self.assertEqual(T.pick_device(rows, serial="emulator-5554")[0]["serial"],
                         "emulator-5554")
        dev, err = T.pick_device(rows, serial="127.0.0.1:5555")
        self.assertIsNone(dev)                            # 离线设备不可选
        self.assertIn("不在线", err)
        self.assertIsNone(T.pick_device([d for d in rows if not d["online"]])[0])

    def test_format_device_info_has_no_kind(self):
        import tester as T
        d = self._rows()[0]
        d["resolution"] = "1600x900"
        info = T.format_device_info(d)
        for kw in ("127.0.0.1:16384", "在线", "1600x900", "PGBM10"):
            self.assertIn(kw, info)
        self.assertNotIn("真机", info)
        self.assertNotIn("模拟器", info)
        self.assertEqual(T.format_device_info(None), "未选择设备")

    def test_scan_ignores_daemon_notice(self):
        import tester as T
        self.assertEqual(T.parse_adb_device_rows(
            "List of devices attached\n* daemon started successfully *\n"), [])

    def test_capture_shot_writes_png(self):
        import tempfile
        import tester as T
        from unittest import mock
        png = b"\x89PNG\r\n\x1a\n" + b"x" * 2048

        class R:
            stdout = png
            stderr = b""

        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "shot.png")
            with mock.patch.object(T.subprocess, "run", return_value=R()), \
                 mock.patch.object(T, "_device_adb_path", return_value="adb.exe"):
                ok, msg, n = T.capture_device_shot("127.0.0.1:5555", out)
            self.assertTrue(ok)
            self.assertEqual(n, len(png))
            self.assertIn("截图", msg)
            self.assertEqual(open(out, "rb").read(), png)

    def test_capture_shot_rejects_non_png(self):
        import tester as T
        from unittest import mock

        class R:
            stdout = b"error: no devices"
            stderr = b""

        with mock.patch.object(T.subprocess, "run", return_value=R()), \
             mock.patch.object(T, "_device_adb_path", return_value="adb.exe"):
            ok, msg, n = T.capture_device_shot("127.0.0.1:5555")
        self.assertFalse(ok)
        self.assertEqual(n, 0)
        self.assertIn("PNG", msg)

    def test_capture_shot_without_serial(self):
        import tester as T
        ok, msg, n = T.capture_device_shot("")
        self.assertFalse(ok)
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

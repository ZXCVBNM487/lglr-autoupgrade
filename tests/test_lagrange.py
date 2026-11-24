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

    def test_parent_pid_alive(self):
        import tester
        self.assertTrue(tester._parent_pid_alive(os.getpid()))
        self.assertFalse(tester._parent_pid_alive(99999999))


class TestTerminateAll(unittest.TestCase):
    """2026-11-24「全部终止」按钮：清残留但不杀自己。"""

    def test_wired_in_gui(self):
        import tester, inspect
        src = inspect.getsource(tester.run_gui)
        self.assertIn("全部终止", src)
        self.assertIn("stop_all_task()", src)
        self.assertIn("terminate_all_processes", inspect.getsource(tester))

    def test_skips_self_pid(self):
        """必须排除 self_pid，否则 dist 版 GUI 会杀掉自己。"""
        import tester, inspect
        src = inspect.getsource(tester.terminate_all_processes)
        self.assertIn("ProcessId -ne $me", src)
        self.assertIn("str(self_pid)", src)

    def test_scope_limited_to_project(self):
        """【防误杀】exe 限定项目目录、python 同时含项目路径+脚本名。"""
        import tester, inspect
        src = inspect.getsource(tester.terminate_all_processes)
        self.assertIn("psutil.process_iter", src)
        self.assertIn("exe_norm.startswith(root_norm", src)   # exe 路径白名单
        self.assertIn("root_norm in os.path.normcase(cmd)", src)  # python 路径白名单
        self.assertNotIn('"/IM"', src)                  # 无 taskkill /IM 全局杀

    def test_adb_not_globally_killed(self):
        """【防误杀】绝不 taskkill 系统所有 adb.exe，只对本项目的 adb kill-server。"""
        import tester, inspect
        src = inspect.getsource(tester.terminate_all_processes)
        self.assertIn("kill-server", src)
        self.assertNotIn('"/IM"', src)         # 绝不 taskkill /IM 全局杀所有 adb
        self.assertNotIn('"adb.exe"', src)     # 不硬编码系统 adb 进程名

    def test_removes_both_lock_locations(self):
        """锁文件只删本项目根和 dist 两处。"""
        import tester, inspect
        src = inspect.getsource(tester.terminate_all_processes)
        self.assertIn(".run_lock", src)
        self.assertIn('os.path.join("dist", ".run_lock")', src)


class TestScanAllAdbs(unittest.TestCase):
    """2026-11-24 全盘 ADB 扫描：14 个 adb.exe 全部覆盖、逐个验证。"""

    def test_static_table_covers_all_scanned_adbs(self):
        """全盘扫描发现的 14 个 adb.exe 必须都在静态表里（不再遗漏）。"""
        import adb_locator as A
        known = {os.path.normcase(os.path.normpath(p)) for p in A._STATIC}
        # 用户特别指出的两个 MuMu 路径 + 之前遗漏的 platform-tools
        must = [
            r"D:\FeverApps\MuMuFever\nx_device\12.0\shell\adb.exe",
            r"D:\FeverApps\MuMuFever\nx_main\adb.exe",
            r"E:\AndroidBuild\platform-tools-latest-windows\platform-tools\adb.exe",
            r"E:\项目文件2\platform-tools\adb.exe",
            r"E:\项目文件2\android_usb_assistant\platform-tools\adb.exe",
            r"C:\Program Files (x86)\Android\android-sdk\platform-tools\adb.exe",
        ]
        for p in must:
            with self.subTest(p=p):
                self.assertIn(os.path.normcase(os.path.normpath(p)), known)

    def test_scan_all_adbs_structure(self):
        """scan_all_adbs 返回结构完整（路径/版本/可用/在线/来源）。"""
        import adb_locator as A
        # 只测静态表部分（timeout=0 跳过目录扫描），不实际跑全部 adb 以节省时间
        res = A.scan_all_adbs(timeout=0.01)
        self.assertIsInstance(res, list)
        for r in res:
            for k in ("path", "size", "version", "usable", "devices",
                      "online", "connect_ok", "source"):
                self.assertIn(k, r)

    def test_adbs_command_wired(self):
        import inspect
        src = inspect.getsource(L.main)
        self.assertIn('cmd == "adbs"', src)
        self.assertIn('"adbs"', src)
        self.assertIn("cmd_adbs", inspect.getsource(L))


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


class TestIconEntries(unittest.TestCase):
    """纯图标入口注册表、模板匹配与兜底规则（纯逻辑，无需设备）。"""

    def test_registry_shape(self):
        for eid, c in L.ICON_ONLY_ENTRIES.items():
            self.assertIn("name", c)
            self.assertIn("search", c)
            self.assertEqual(len(c["search"]), 4)
            if c.get("fallback"):
                self.assertEqual(len(c["fallback"]), 2)
        self.assertTrue(L.ICON_ONLY_ENTRIES["live_widget"]["forbidden"])
        # 2026-11-22 实机确认活动入口 = 左上剪贴板图标，已有模板与坐标；
        # 只禁止再出现旧的 (320,60) 盲点兜底。
        act = L.ICON_ONLY_ENTRIES["activity_entry"]
        self.assertEqual(act["fallback"], (217, 55))
        self.assertEqual(act["template"], "icon_task.png")

    def test_forbidden_entry_never_located(self):
        import numpy as np
        img = np.zeros((100, 100, 3), dtype="uint8")
        self.assertIsNone(L.find_icon_entry(_FakeAdb(), img, "live_widget"))
        self.assertFalse(L.tap_icon_entry(_FakeAdb(), "live_widget"))

    def test_allow_fallback_false_returns_none(self):
        # 模板未加载 / 无图且禁用兜底时应返回 None（校准前的严格模式）
        self.assertIsNone(L.find_icon_entry(_FakeAdb(), None, "activity_entry",
                                            allow_fallback=False))
        self.assertIsNone(L.find_icon_entry(_FakeAdb(), None, "task_close",
                                            allow_fallback=False))

    def test_config_override_supplies_fallback(self):
        from unittest import mock
        with mock.patch.object(L, "CONFIG",
                               {"icon_entries": {"activity_entry": {"fallback": [333, 77]}}}):
            cfg = L.icon_entry_cfg("activity_entry")
            self.assertEqual(cfg["fallback"], [333, 77])
            hit = L.find_icon_entry(_FakeAdb(), None, "activity_entry")
            self.assertEqual(hit[2], "fallback")
            self.assertEqual(hit[:2], (333, 77))

    def test_template_hit_takes_priority_over_fallback(self):
        import numpy as np
        from unittest import mock
        img = np.zeros((200, 300, 3), dtype="uint8")
        with mock.patch.object(L, "_load_template",
                               return_value=np.zeros((10, 10, 3), dtype="uint8")), \
             mock.patch.object(L, "template_match_multiscale",
                               return_value=(50, 40, 0.93, 1.0)):
            hit = L.find_icon_entry(_FakeAdb(), img, "task_clipboard")
        self.assertEqual(hit[2], "template")
        self.assertGreater(hit[3], 0.9)
        self.assertNotEqual(hit[:2], (147, 30))       # 不是旧硬编码坐标

    def test_multiscale_match_finds_square(self):
        import numpy as np
        img = np.zeros((60, 80, 3), dtype="uint8")
        img[20:30, 40:50] = 255
        img[22:26, 42:46] = 0                 # 让模板有纹理（纯色模板会让 CCOEFF 退化）
        tmpl = img[20:30, 40:50].copy()
        hit = L.template_match_multiscale(img, tmpl, threshold=0.9,
                                          scales=(0.8, 1.0, 1.2))
        self.assertIsNotNone(hit)
        self.assertAlmostEqual(hit[0], 45, delta=2)
        self.assertAlmostEqual(hit[1], 25, delta=2)
        self.assertGreaterEqual(hit[2], 0.9)

    def test_fallback_scaled_by_resolution(self):
        adb = _FakeAdb(w=1600, h=900)
        hit = L.find_icon_entry(adb, None, "mail_envelope")
        x, y = hit[:2]
        self.assertAlmostEqual(x, 422 * 1600 / 2310, delta=1)
        self.assertAlmostEqual(y, 863 * 900 / 1080, delta=1)


class TestNetworkAdbReconnect(unittest.TestCase):
    """局域网 ADB 掉线自动重连（实机基线暴露的真实缺陷）。"""

    def test_is_network_adb_addr(self):
        self.assertTrue(L.is_network_adb_addr("192.168.100.17:5555"))
        self.assertTrue(L.is_network_adb_addr("10.0.0.5:7555"))
        self.assertFalse(L.is_network_adb_addr("127.0.0.1:16384"))
        self.assertFalse(L.is_network_adb_addr("emulator-5554"))
        self.assertFalse(L.is_network_adb_addr("ABCDEF123456"))
        self.assertFalse(L.is_network_adb_addr(""))
        self.assertFalse(L.is_network_adb_addr(None))

    def test_reconnect_addrs_priority(self):
        cfg = {"device": {"serial": "192.168.100.17:5555"}}
        st = {"last_device": "192.168.1.9:5555"}
        out = L.adb_reconnect_addrs(cfg, st, [16384, 7555])
        self.assertEqual(out[:2], ["192.168.100.17:5555", "192.168.1.9:5555"])
        self.assertEqual(out[2:], ["127.0.0.1:16384", "127.0.0.1:7555"])

    def test_reconnect_addrs_dedup(self):
        cfg = {"device": {"serial": "192.168.100.17:5555"}}
        st = {"last_device": "192.168.100.17:5555"}
        self.assertEqual(L.adb_reconnect_addrs(cfg, st, [5555]),
                         ["192.168.100.17:5555", "127.0.0.1:5555"])

    def test_localhost_serial_not_treated_as_network(self):
        cfg = {"device": {"serial": "127.0.0.1:16384"}}
        self.assertEqual(L.adb_reconnect_addrs(cfg, {}, [16384]),
                         ["127.0.0.1:16384"])

    def test_scan_connects_network_addr_first(self):
        calls = []
        adb = object.__new__(L.Adb)
        adb.path = "adb.exe"
        adb._port_open = lambda p, timeout=0.2: False
        adb.list_devices = lambda: []
        adb._net_addrs = ["192.168.100.17:5555"]

        def raw(*args, **kw):
            calls.append(args)
            class R:
                stdout = b""
            return R()

        adb.raw = raw
        adb.scan_and_connect(budget=5)
        self.assertEqual(calls[0], ("connect", "192.168.100.17:5555"))


class TestTaskCloseTemplate(unittest.TestCase):
    """2026-11-23：旧门形图标模板会误命中展开键，改走展开/收起坐标。"""

    def test_registry_uses_expand_fallback(self):
        c = L.ICON_ONLY_ENTRIES["task_close"]
        self.assertIsNone(c["template"])
        self.assertEqual(c["fallback"], L._TASK_CLOSE)
        self.assertEqual(L._TASK_CLOSE, (2194, 54))
        self.assertFalse(c.get("forbidden", False))

    def test_close_helper_exists(self):
        self.assertTrue(callable(L._daily_tap_task_close))


class TestActivityEntryCalibrated(unittest.TestCase):
    """活动入口：2026-11-22 实机确认 = 左上剪贴板图标，已内置模板与坐标。"""

    def test_registry_complete(self):
        c = L.ICON_ONLY_ENTRIES["activity_entry"]
        self.assertEqual(c["template"], "icon_task.png")
        self.assertTrue(c["fallback"])
        self.assertGreaterEqual(c["threshold"], 0.6)
        self.assertIn("探险协议", c["verify"])
        self.assertFalse(c.get("forbidden", False))

    def test_fallback_available_without_image(self):
        import sys as _s
        _s.path.insert(0, r'E:\拉格朗日自动升级')
        hit = L.find_icon_entry(_FakeAdb(1600, 900), None, "activity_entry")
        self.assertIsNotNone(hit)
        self.assertEqual(hit[2], "fallback")
        self.assertAlmostEqual(hit[0], 217 * 1600 / 2310, delta=1)
        self.assertAlmostEqual(hit[1], 55 * 900 / 1080, delta=1)


class TestActivitySidebarRegion(unittest.TestCase):
    """活动侧栏识别区域：2026-11-22 实机校准，旧左边界会把大部分条目排除。"""

    def test_region_covers_left_entries(self):
        class F:
            w, h = 1600, 900
        x1, y1, x2, y2 = L.scale_box(F(), L._DAILY_SIDEBAR_BOX)
        self.assertLess(x1, 148)          # 实测条目中心最小 x=148
        self.assertGreater(x2, 276)       # 实测条目中心最大 x=276
        self.assertLess(y1, 156)

    def test_select_keeps_multiple_entries(self):
        class F:
            w, h = 1600, 900
        items = [(157, 156, "月度登录", 0.9), (148, 262, "活跃挑战领奖", 0.9),
                 (200, 368, "星系焕新", 0.9), (189, 476, "官方设定集", 0.9)]
        self.assertEqual(len(L._select_activity_entries(items, F())), 4)


class TestAutoupTargetAndQueueState(unittest.TestCase):
    """2026-11-22 实机 bug：底部导航被当建筑点；队列 2/2 满未参与判定。"""

    def test_select_ignores_navigation_words(self):
        items = [(82, 856, "返回地图", 0.9), (801, 823, "基地建设", 0.9),
                 (1521, 850, "行政", 0.9), (1090, 831, "通用港口4", 0.9)]
        sel = L._au_select_open_target(items, _FakeAdb(1600, 900), "平台指挥中心", [])
        self.assertIsNotNone(sel)
        self.assertEqual(sel[2], "name")
        self.assertGreater(sel[0], 1000)          # 绝不能是左侧「返回地图」的 x=82

    def test_queue_full_by_state_even_with_one_timer(self):
        items = [(801, 853, "2/2", 0.9), (1074, 852, "01:50:52", 0.9)]
        self.assertTrue(L._au_queue_full(items, _FakeAdb(1600, 900)))
        self.assertFalse(L._au_queue_full(
            [(801, 853, "1/2", 0.9)], _FakeAdb(1600, 900)))
        self.assertTrue(L._au_queue_full(
            [(800, 850, "建造队列已满", 0.9)], _FakeAdb(1600, 900)))

    def test_profile_pick_ignores_navigation(self):
        prof = {"build_order": [{"name": "基地", "target_level": 5}]}
        items = [(82, 856, "返回地图", 0.9), (1521, 850, "行政", 0.9)]
        self.assertIsNone(L.profile_pick_target(items, _FakeAdb(1600, 900), prof))

    def test_expanded_view_navigation_not_picked(self):
        # 基地页展开视图底部全是功能键，曾被当成建筑点，导致两种视图来回切换
        items = [(199, 859, "对接信息", 0.9), (307, 859, "视野收集", 0.9),
                 (1513, 858, "环绕视角", 0.9), (73, 863, "基地", 0.9),
                 (801, 823, "基地建设", 0.9)]
        self.assertIsNone(L._au_select_open_target(items, _FakeAdb(1600, 900), "", []))

    def test_on_base_build_requires_back_map(self):
        # 星图里的提示文字「基地建设-舰队编组」不能算已在基地页
        star = [(328, 596, "基地建设-舰队编组", 0.9), (73, 863, "基地", 0.9)]
        self.assertFalse(L._au_on_base_build(star))
        base = [(82, 856, "返回地图", 0.9), (801, 823, "基地建设", 0.9),
                (801, 853, "2/2", 0.9)]
        self.assertTrue(L._au_on_base_build(base))

    def test_bottom_nav_ignores_mid_screen_hint(self):
        # 星图中部「基地建设-舰队编组」不能当底部导航；真正按钮在 (73,863)
        items = [(328, 596, "基地建设-舰队编组", 0.84), (73, 863, "基地", 0.66)]
        fake = _FakeAdb(1600, 900)
        self.assertEqual(L._au_bottom_nav(items, fake, "基地"), (73, 863))
        self.assertIsNone(L._au_bottom_nav(items, fake, "基地建设"))

    def test_autoup_never_uses_system_back_key(self):
        # 硬约束：系统返回键会弹「确定退出游戏」，旧实现在此无限循环
        import inspect
        src = inspect.getsource(L.cmd_autoup)
        # 注释里会提到 keyevent，因此只检查真正的调用语句
        self.assertNotIn('"input", "keyevent"', src)
        self.assertNotIn("'input', 'keyevent'", src)
        self.assertNotIn('"keyevent", "4"', src)


class TestFullPointCalibration(unittest.TestCase):
    """2026-11-23 全量点位校准：任务/活动双入口 + 坐标/文字双重校验。"""

    def test_task_panel_entry_is_star_emblem(self):
        c = L.ICON_ONLY_ENTRIES["task_panel_entry"]
        self.assertEqual(c["template"], "icon_quest.png")
        self.assertEqual(c["fallback"], L._TASK_ICON)
        self.assertIn("主线任务", c["verify"])
        self.assertNotEqual(c["fallback"], L.ICON_ONLY_ENTRIES["activity_entry"]["fallback"])

    def test_task_claim_uses_panel_entry(self):
        import inspect
        src = inspect.getsource(L._daily_claim_tasks)
        self.assertIn('"task_panel_entry"', src)
        self.assertNotIn('find_icon_entry(adb, img0, "task_clipboard")', src)

    def test_points_config_loads(self):
        import os
        p = os.path.join(r'E:\拉格朗日自动升级', 'points.json')
        self.assertTrue(os.path.isfile(p))
        d = L.load_points()
        self.assertEqual(len(d.get("点位", [])), 17)
        self.assertIn("task_panel", d.get("关键入口", {}))
        self.assertIn("activity", d.get("关键入口", {}))
        self.assertEqual(d["关键入口"]["task_panel"]["坐标"], [53, 43])

    def test_one_click_upgrade_is_first_priority(self):
        import inspect
        src = inspect.getsource(L._au_tap_upgrade)
        first = src.find('find_button(')
        self.assertIn('"一键升级"', src[first:first + 80])
        self.assertIn('只走「一键升级」', src)

    def test_one_click_upgrade_in_points(self):
        d = L.load_points()
        ocu = (d.get('关键入口') or {}).get('one_click_upgrade') or {}
        self.assertEqual(ocu.get('坐标'), [359, 608])
        self.assertIn('一键升级', ocu.get('验证文字', []))

    def test_double_check_transparency(self):
        import numpy as np
        from unittest import mock
        fake = _FakeAdb(1600, 900)
        fake.tap = lambda *a, **k: None
        fake.screenshot = lambda: np.zeros((100, 100, 3), dtype="uint8")
        with mock.patch.object(L, "_au_ocr_all", return_value=[]):
            ok, texts, msg = L.point_double_check(
                fake, {"坐标": [53, 43], "验证文字": ["主线任务"]})
        self.assertFalse(ok)
        self.assertIn("转场", msg)


    # ===== 2026-11-23 复核新增：坐标基准 / 文字解析 / 一键升级唯一路线 =====
    def test_point_design_xy_prefers_design(self):
        self.assertEqual(
            L.point_design_xy({"坐标": [150, 43], "设计坐标": [217, 55]}), (217, 55))

    def test_point_design_xy_converts_device_pixels(self):
        cfg = {"分辨率": [1600, 900]}
        self.assertEqual(
            L.point_design_xy({"坐标": [53, 43]}, cfg), (77, 52))
        self.assertEqual(
            L.point_design_xy({"坐标": [292, 719]}, cfg), (422, 863))

    def test_point_expect_texts_splits_legacy_field(self):
        self.assertEqual(
            L.point_expect_texts({"目标文字": "主线任务/委托任务、支线任务"}),
            ["主线任务", "委托任务", "支线任务"])

    def test_point_double_check_taps_scaled_design_coordinate(self):
        import numpy as np
        from unittest import mock
        fake = _FakeAdb(1600, 900)
        tapped = []
        fake.tap = lambda x, y: tapped.append((x, y))
        fake.screenshot = lambda: np.zeros((100, 100, 3), dtype="uint8")
        with mock.patch.object(L, "_au_ocr_all", return_value=[(10, 10, "主线任务", 0.9)]):
            ok, _, msg = L.point_double_check(
                fake, {"设计坐标": [77, 52], "验证文字": ["主线任务"]})
        self.assertTrue(ok)
        self.assertEqual(tapped, [(53, 43)])
        self.assertIn("文字校验通过", msg)

    def test_one_click_upgrade_has_no_fallback(self):
        import inspect
        src = inspect.getsource(L._au_tap_upgrade)
        self.assertNotIn('find_button(items, ["升级"]', src)
        self.assertNotIn('find_button(items, ["升至满级"', src)
        self.assertNotIn("exclude=_AU_UPGRADE_BTN_EXCLUDE", src)

    def test_points_activity_coordinate_fixed(self):
        d = L.load_points()
        e = d["关键入口"]["activity"]
        self.assertEqual(e["坐标"], [150, 46])
        self.assertEqual(e["设计坐标"], [217, 55])

    def test_points_all_have_consistent_design_coordinates(self):
        d = L.load_points()
        bw, bh = d.get("分辨率", [1600, 900])
        for p in d.get("点位", []):
            with self.subTest(p.get("序号")):
                design = L.point_design_xy(p, d)
                self.assertIsNotNone(design)
                self.assertEqual(
                    p.get("设计坐标"), list(design),
                    f"#{p.get('序号')} 设计坐标与实机坐标不一致")
                self.assertEqual(
                    [round(design[0] * bw / 2310), round(design[1] * bh / 1080)],
                    list(p["坐标"]))


    # ===== 2026-11-23 升级循环定稿 =====
    def test_autoup_loop_uses_only_global_one_click(self):
        import inspect
        src = inspect.getsource(L.cmd_autoup)
        self.assertNotIn("_au_open_target(", src)
        self.assertNotIn("_au_tap_upgrade(", src)
        self.assertIn("_AU_LAST_CLICK_TS", src)
        self.assertIn("_AU_WAIT_SIGNATURE", src)

    def test_queue_full_only_exact_two_slash_two(self):
        fake = _FakeAdb(1600, 900)
        self.assertFalse(L._au_queue_full([(500, 860, "4/4", 0.9)], fake))
        self.assertFalse(L._au_queue_full([(500, 860, "5/5", 0.9)], fake))
        self.assertTrue(L._au_queue_full([(500, 860, "2/2", 0.9)], fake))


class TestAutoUpEnterBaseListView(unittest.TestCase):
    """2026-11-23 实机修复：3D 全景视图右上 (1946,67) 是「分享」按钮，
    盲点坐标会误开分享面板。必须先「点进基地建设」展开建筑列表视图，
    OCR 明确读到「一键升级」文字才点；只有列表视图内才允许坐标兜底。"""

    def test_constants_scale_back_to_device_pixels(self):
        self.assertEqual(L._AU_BASE_LIST_ENTRY, (1156, 988))
        self.assertEqual(L._AU_SHARE_CLOSE_BTN, (2194, 54))
        self.assertIn("小红书", L._AU_SHARE_PANEL_KEYS)
        a = _FakeAdb(1600, 900)
        bx, by = L.scale_box(a, (*L._AU_BASE_LIST_ENTRY, 0, 0))[:2]
        self.assertAlmostEqual(bx, 801, delta=2)   # 实机 (801,823) 底部「基地建设」标签
        self.assertAlmostEqual(by, 823, delta=2)
        cx, cy = L.scale_box(a, (*L._AU_SHARE_CLOSE_BTN, 0, 0))[:2]
        self.assertAlmostEqual(cx, 1520, delta=2)  # 实机 (1520,45) 分享面板右上 X
        self.assertAlmostEqual(cy, 45, delta=2)
        ux, uy = L.scale_box(a, (*L._AU_GLOBAL_UPGRADE_BTN, 0, 0))[:2]
        self.assertAlmostEqual(ux, 1348, delta=2)  # 实机 (1348,56) 折叠视图一键升级按钮
        self.assertAlmostEqual(uy, 56, delta=2)

    def test_list_view_vs_panorama_view(self):
        a = _FakeAdb(1600, 900)
        # 建筑列表视图：左上 y≈57 有标题，中部 y≈578 有分区标题
        lv = [(129, 57, "基地建设", 0.8), (309, 578, "中央区", 0.8),
              (746, 579, "港区", 0.8), (1207, 577, "工业区", 0.8),
              (1351, 56, "一键升级", 0.71)]
        self.assertTrue(L._au_base_list_view(lv, a))
        # 3D 全景视图：标题与分区全在底部 y≈823~842
        pv = [(801, 823, "基地建设", 0.8), (1019, 841, "中央区", 0.8),
              (1162, 842, "港区", 0.8), (82, 856, "返回地图", 0.8)]
        self.assertFalse(L._au_base_list_view(pv, a))
        self.assertFalse(L._au_base_list_view([], a))

    def test_no_blind_coordinate_tap_in_panorama(self):
        import inspect
        src = inspect.getsource(L.cmd_autoup)
        # 旧的「OCR 未读到就直接点右上坐标」必须已删除
        self.assertNotIn('OCR 未读到「一键升级」，用已校准的右上按钮坐标', src)
        # 新流程：读不到按钮先点进基地建设；坐标兜底只在列表视图内
        self.assertIn("先点进基地建设展开列表", src)
        self.assertIn("_au_base_list_view(items, adb)", src)
        self.assertIn("_AU_BASE_LIST_ENTRY", src)
        # 分享面板有识别与关闭
        self.assertIn("_AU_SHARE_PANEL_KEYS", src)
        self.assertIn("_AU_SHARE_CLOSE_BTN", src)

    def test_share_panel_keyword_does_not_hit_base_page(self):
        share = [(1359, 837, "小红书", 0.75), (1220, 61, "艾星际猎人", 0.64)]
        self.assertTrue(any(k in t for _, _, t, _ in share
                            for k in L._AU_SHARE_PANEL_KEYS))
        base = [(801, 823, "基地建设", 0.8), (1351, 56, "一键升级", 0.71),
                (1019, 841, "中央区", 0.8)]
        self.assertFalse(any(k in t for _, _, t, _ in base
                             for k in L._AU_SHARE_PANEL_KEYS))

    def test_panel_does_not_prediction_wait_for_resources(self):
        """2026-11-23 实机：消耗资源是「本次消耗/当前拥有」，旧预判反着理解，
        导致点开一键升级面板后长等 3 小时、从不去点升级按钮。现在直接点升级。"""
        import inspect
        src = inspect.getsource(L._au_handle_panel)
        self.assertNotIn("资源不足，预估", src)
        self.assertNotIn("lack", src)
        self.assertNotIn("_AU_RES_RE", src)
        self.assertIn("点击一键升级面板的升级按钮", src)
        self.assertIn("_AU_LAST_CLICK_TS", src)

    def test_collapse_back_to_panorama_after_click(self):
        """点完升级必须收起建筑列表回 3D 全景视图，否则读不到两条队列倒计时会反复点。"""
        import inspect
        self.assertEqual(L._AU_BASE_COLLAPSE_BTN, (2180, 66))
        a = _FakeAdb(1600, 900)
        cx, cy = L.scale_box(a, (*L._AU_BASE_COLLAPSE_BTN, 0, 0))[:2]
        self.assertAlmostEqual(cx, 1510, delta=2)   # 实机折叠图标 (1510,55)
        self.assertAlmostEqual(cy, 55, delta=2)
        self.assertIn("_au_back_to_panorama(adb)",
                      inspect.getsource(L._au_handle_panel))
        # 非列表视图时不点击，避免误点右上分享区
        fake = _FakeAdb(1600, 900)
        L._au_back_to_panorama(fake)
        self.assertFalse(any(c[0] == "tap" for c in fake.calls))


class TestCodeReviewFixes(unittest.TestCase):
    """2026-11-24 代码审查修复的回归测试。"""

    def test_queue_times_filters_low_score(self):
        """P1-2：低分 OCR 幻影倒计时不能污染队列状态（否则静默空等数小时）。"""
        a = _FakeAdb(1600, 900)
        items = [(1000, 852, "00:33:42", 0.2),   # 低分幻影
                 (1265, 852, "02:00:40", 0.85)]  # 真实
        self.assertEqual(L._au_queue_times(items, a), [7240])

    def test_close_event_banner_handles_none_img(self):
        """P1-1：截图失败 img=None 时不能抛 TypeError（会让挂机停摆）。"""
        fake = _FakeAdb(1600, 900)
        pos = L._au_close_event_banner(fake, None)
        self.assertEqual(len(pos), 2)
        self.assertTrue(any(c[0] == "tap" for c in fake.calls))

    def test_publish_skips_bad_sec(self):
        """P2-6：一条脏秒数不能导致两条 ETA 全丢。"""
        import json, tempfile, os
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "state.json")
            orig = L._state_file
            L._state_file = lambda: p
            try:
                L._publish_upgrade_etas(["bad", 2022], ["坏数据", "金属炼化"], 2)
                st = json.load(open(p, encoding="utf-8"))
                self.assertEqual(len(st["upgrade_etas"]), 1)
                self.assertEqual(st["upgrade_etas"][0]["sec"], 2022)
            finally:
                L._state_file = orig

    def test_save_state_atomic_no_tmp_left(self):
        """P2-4：原子写后不留 .tmp，且内容可正常读回。"""
        import json, tempfile, os
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "state.json")
            orig = L._state_file
            L._state_file = lambda: p
            try:
                L._save_state({"k": "v"})
                self.assertFalse(os.path.exists(p + ".tmp"))
                self.assertEqual(json.load(open(p, encoding="utf-8"))["k"], "v")
            finally:
                L._state_file = orig

    def test_close_panel_taps_left_outside(self):
        """P-实机：建筑详情是右侧抽屉，关闭必须点左侧空白（旧点 60% 宽落在面板内死循环）。"""
        import inspect
        src = inspect.getsource(L._au_close_panel)
        self.assertIn("0.28", src)      # 左侧 28% 宽
        self.assertIn("0.61", src)      # 61% 高
        self.assertNotIn("adb.w * 0.6", src)

    def test_no_legacy_upgrade_route_in_loop(self):
        """确认旧单建筑升级路线未回流。"""
        import inspect
        src = inspect.getsource(L.cmd_autoup)
        self.assertNotIn("_au_open_target(", src)
        self.assertNotIn("_au_tap_upgrade(", src)


class TestQueuePairsByColumn(unittest.TestCase):
    """2026-11-24 修复：倒计时与建筑名按 x 坐标（列）配对，左右不能颠倒。"""

    def setUp(self):
        self.adb = _FakeAdb(1600, 900)

    def test_pairs_by_x_not_ocr_order(self):
        # OCR 返回顺序故意打乱：右边的先出现、左边的后出现（模拟"搞反"场景）
        items = [
            (1265, 852, "00:33:42", 0.87),   # 右边倒计时，OCR 先给
            (1086, 852, "02:00:40", 0.83),   # 左边倒计时，OCR 后给
            (1265, 831, "金属炼化工", 0.83),  # 右边名字
            (1089, 831, "通用港口4", 0.77),   # 左边名字
        ]
        pairs = L._au_queue_pairs(items, self.adb)
        # 结果必须按 x 从左到右：左边是通用港口4/02:00:40，右边是金属炼化/00:33:42
        self.assertEqual(len(pairs), 2)
        self.assertEqual(pairs[0][1], "通用港口4")
        self.assertEqual(pairs[0][0], 7240)
        self.assertEqual(pairs[1][1], "金属炼化工")
        self.assertEqual(pairs[1][0], 2022)
        self.assertLess(pairs[0][2], pairs[1][2])   # 左列 x < 右列 x

    def test_order_swapped_buildings(self):
        # 游戏里左右建筑互换：金属炼化在左、通用港口在右 → 也要按 x 正确跟随
        items = [
            (1069, 852, "01:30:00", 0.8), (1255, 852, "00:20:27", 0.8),
            (1060, 831, "金属炼化工", 0.8), (1258, 831, "通用港口4", 0.8),
        ]
        pairs = L._au_queue_pairs(items, self.adb)
        self.assertEqual(pairs[0][1], "金属炼化工")
        self.assertEqual(pairs[0][0], 5400)
        self.assertEqual(pairs[1][1], "通用港口4")
        self.assertEqual(pairs[1][0], 1227)

    def test_name_missing_keeps_blank(self):
        # 某条名字漏读：时间仍保留，名字留空，不错配到别的列
        items = [(1086, 852, "02:00:40", 0.83), (1265, 852, "00:33:42", 0.87),
                 (1089, 831, "通用港口4", 0.77)]
        pairs = L._au_queue_pairs(items, self.adb)
        self.assertEqual(pairs[0][1], "通用港口4")
        self.assertEqual(pairs[1][1], "")

    def test_loop_uses_column_pairs(self):
        import inspect
        src = inspect.getsource(L.cmd_autoup)
        self.assertIn("_au_queue_pairs(items, adb)", src)
        self.assertNotIn("qtimes[i]", src)


class TestEtaDisplayEdgeCases(unittest.TestCase):
    """GUI 底部倒计时的递减纯函数：覆盖过期/陈旧/坏数据等边界，防 bug。"""

    def setUp(self):
        import tester
        self.eta = tester.eta_display

    def _at(self, offset):
        import time
        return time.strftime("%Y-%m-%d %H:%M:%S",
                             time.localtime(time.time() + offset))

    def test_normal_countdown(self):
        text, active = self.eta({"sec": 9000, "at": self._at(-10)})
        self.assertTrue(active)
        self.assertTrue(text.startswith("02:29:"), text)

    def test_expired_shows_done_not_zero(self):
        # bug 修复：过期不再卡 00:00:00，显示"已完成"
        text, active = self.eta({"sec": 60, "at": self._at(-120)})
        self.assertEqual(text, "已完成 ✓")
        self.assertFalse(active)

    def test_stale_data_shows_expired(self):
        # 完成时刻已过超 10 分钟 → 数据已过期
        text, active = self.eta({"sec": 60, "at": self._at(-1200)})
        self.assertEqual(text, "数据已过期")
        self.assertFalse(active)

    def test_long_countdown_not_false_expired(self):
        """核心 bug 修复：脚本静默等待 1 小时不写文件，长倒计时不能误报过期。
        完成时刻 = at + sec，只要没到就有效，与距上次写入多久无关。"""
        # 2 小时前读到还剩 3 小时 → 现在还有 1 小时，必须仍活跃
        text, active = self.eta({"sec": 10800, "at": self._at(-7200)})
        self.assertTrue(active, text)
        self.assertTrue(text.startswith("00:59:"), text)

    def test_bad_data_safe(self):
        for slot in ({}, {"sec": None, "at": "x"}, {"sec": 9000, "at": "bad-time"},
                     {"sec": "abc", "at": self._at(0)}, None):
            with self.subTest(slot=slot):
                text, active = self.eta(slot)
                self.assertEqual(text, "--:--:--")
                self.assertFalse(active)

    def test_five_minutes_still_active(self):
        text, active = self.eta({"sec": 300, "at": self._at(-1)})
        self.assertTrue(active)
        self.assertTrue(text.startswith("00:04:"), text)


class TestUpgradeEtaPublish(unittest.TestCase):
    """GUI 底部实时倒计时条的数据源：state.json.upgrade_etas。"""

    def test_publish_writes_two_slots_without_losing_fields(self):
        import json, tempfile, os
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "state.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"last_daily": "2026-11-23", "last_device": "dev1"}, f)
            orig = L._state_file
            L._state_file = lambda: p
            try:
                L._publish_upgrade_etas([7240, 2022], ["通用港口4", "金属炼化"], 2)
                with open(p, encoding="utf-8") as f:
                    st = json.load(f)
                self.assertEqual(st["last_daily"], "2026-11-23")
                self.assertEqual(st["last_device"], "dev1")
                self.assertEqual(st["upgrade_count"], 2)
                self.assertEqual(len(st["upgrade_etas"]), 2)
                self.assertEqual(st["upgrade_etas"][0]["sec"], 7240)
                self.assertEqual(st["upgrade_etas"][0]["name"], "通用港口4")
                self.assertTrue(st["upgrade_etas"][0]["at"])
            finally:
                L._state_file = orig

    def test_publish_empty_clears_slots(self):
        import json, tempfile, os
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "state.json")
            orig = L._state_file
            L._state_file = lambda: p
            try:
                L._publish_upgrade_etas([], [], 0)
                with open(p, encoding="utf-8") as f:
                    st = json.load(f)
                self.assertEqual(st["upgrade_etas"], [])
                self.assertEqual(st["upgrade_count"], 0)
            finally:
                L._state_file = orig

    def test_loop_publishes_etas(self):
        import inspect
        self.assertIn("_publish_upgrade_etas(", inspect.getsource(L.cmd_autoup))


class TestUpgradeStateFirstLook(unittest.TestCase):
    """2026-11-23 用户定稿：循环第一时间数出几个在升级，2个等待/1个补位/0个升级。"""

    def setUp(self):
        self.adb = _FakeAdb(1600, 900)

    def test_two_upgrading(self):
        # 用户截图：2/2 + 两条倒计时
        items = [(801, 853, "2/2", 0.8), (1086, 852, "02:00:40", 0.83),
                 (1265, 852, "00:33:42", 0.87), (1089, 831, "通用港口4", 0.77)]
        self.assertEqual(L._au_upgrade_state(items, self.adb), (2, [7240, 2022], True))

    def test_one_upgrading(self):
        # 1/2 + 一条倒计时 → 1 个在升级
        items = [(801, 853, "1/2", 0.8), (1086, 852, "00:20:27", 0.8)]
        count, times, readable = L._au_upgrade_state(items, self.adb)
        self.assertEqual(count, 1)
        self.assertEqual(len(times), 1)
        self.assertTrue(readable)

    def test_zero_upgrading(self):
        # 0/2 无倒计时 → 明确空队列
        items = [(801, 853, "0/2", 0.8)]
        count, times, readable = L._au_upgrade_state(items, self.adb)
        self.assertEqual(count, 0)
        self.assertTrue(readable)

    def test_unreadable_not_treated_as_empty(self):
        # 建筑列表视图（只有建筑卡等级）没有队列条 → readable=False，绝不能当空位空转
        items = [(386, 635, "前哨站指挥中心", 0.8), (1022, 635, "2/4", 0.8),
                 (1022, 702, "4/4", 0.8)]
        count, times, readable = L._au_upgrade_state(items, self.adb)
        self.assertFalse(readable)
        self.assertEqual(times, [])

    def test_loop_uses_upgrade_state(self):
        import inspect
        src = inspect.getsource(L.cmd_autoup)
        self.assertIn("_au_upgrade_state(items, adb)", src)
        self.assertIn("2 个建筑都在升级", src)
        self.assertIn("1 个建筑在升级", src)
        # 死循环根因「30 秒复查空转」必须已移除
        self.assertNotIn("一键升级面板未打开，30 秒后复查", src)


class TestEventBannerPopup(unittest.TestCase):
    """2026-11-23 用户截图：暗流汹涌 / 新章节开启 / 前往查看 活动弹窗。"""

    def test_recognizes_real_screenshot_ocr(self):
        # 用户截图 664x372 的真实 OCR：汹涌被读成「凶涌」
        real = [(332, 176, "暗流凶涌", 0.65), (332, 206, "新章节开启", 0.83),
                (332, 309, "前往查看", 0.63)]
        self.assertTrue(L._au_has_event_banner(real))
        good = [(332, 176, "暗流汹涌", 0.9), (332, 309, "前往查看", 0.9)]
        self.assertTrue(L._au_has_event_banner(good))

    def test_does_not_mistake_ordinary_screen(self):
        # 没有「前往查看」的普通界面 → 不误判
        self.assertFalse(L._au_has_event_banner([(332, 176, "暗流凶涌", 0.9)]))
        self.assertFalse(L._au_has_event_banner([(87, 857, "基地", 0.8)]))
        self.assertFalse(L._au_has_event_banner([]))

    def test_does_not_use_title_or_subtitle(self):
        """用户定稿：不识别主题，只认弹窗自身的「前往查看」按钮。
        标题 / 副标题怎么变都无所谓，有无「前往查看」是唯一判据。"""
        # 三个不同标题的实机弹窗，只要带「前往查看」都应识别
        for title, sub in (("暗流凶涌", "新章节开启"),
                           ("危机-防御劫掠者战役", "态势达成"),
                           ("反攻时机", "新章节开启"),
                           ("资产技术成果展销", "展销剩余时间")):
            with self.subTest(title=title):
                items = [(500, 280, title, 0.9), (600, 330, sub, 0.8),
                         (802, 737, "前往查看", 0.8)]
                self.assertTrue(L._au_has_event_banner(items))
        # 光有标题没有任何按钮 → 不识别
        self.assertFalse(L._au_has_event_banner([(500, 280, "暗流汹涌", 0.9)]))

    def test_close_uses_x_not_cta(self):
        import inspect
        src = inspect.getsource(L._au_close_event_banner)
        self.assertIn("find_close_x", src)
        self.assertIn("close_x", src)
        # 兜底坐标 close_x (2195,70) 换算回实机应与截图右上 X ≈(1513,53) 接近
        a = _FakeAdb(1600, 900)
        x, y = L.scale_box(a, (*L.ICON_ONLY_ENTRIES["close_x"]["fallback"], 0, 0))[:2]
        self.assertAlmostEqual(x, 1513, delta=12)
        self.assertAlmostEqual(y, 53, delta=12)

    def test_wired_into_autoup_and_daily(self):
        import inspect
        self.assertIn("_au_has_event_banner(items)", inspect.getsource(L.cmd_autoup))
        self.assertIn("_au_has_event_banner(items)",
                      inspect.getsource(L._daily_ensure_starmap))


if __name__ == "__main__":
    unittest.main(verbosity=2)

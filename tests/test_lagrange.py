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


class TestIsEmulatorSerial(unittest.TestCase):
    def test_emulator(self):
        self.assertTrue(L._is_emulator_serial("127.0.0.1:16384"))
        self.assertTrue(L._is_emulator_serial("emulator-5554"))
        self.assertTrue(L._is_emulator_serial("localhost:5555"))

    def test_phone(self):
        self.assertFalse(L._is_emulator_serial("ABCDEF1234567890"))


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


if __name__ == "__main__":
    unittest.main(verbosity=2)

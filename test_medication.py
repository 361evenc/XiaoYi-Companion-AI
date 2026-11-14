# -*- coding: utf-8 -*-
"""T-4 用药闭环验收测试（余量自报 + 购药待办 + 未回应通知子女 + 口语提取 + 持久化）。"""
import os
import shutil
import tempfile
import unittest

from medication import (MedicationStore, extract_remaining, extract_daily_dose)


class FakeClock:
    def __init__(self, start=1_800_000_000.0):
        self.t = start

    def __call__(self):
        return self.t

    def tick(self, seconds=0):
        self.t += seconds
        return self.t


def _mk(testcase):
    tmp = tempfile.mkdtemp()
    testcase.addCleanup(shutil.rmtree, tmp, True)
    clock = FakeClock()
    path = os.path.join(tmp, "med.json")
    return MedicationStore(path=path, clock=clock), clock


class TestRemaining(unittest.TestCase):
    """余量自报 + 购药待办判定。"""

    def test_sufficient(self):
        store, clock = _mk(self)
        r = store.report_remaining("降压药", 30, now=clock())
        self.assertTrue(r["ok"])
        self.assertEqual(r["days_left"], 30)
        self.assertFalse(r["low"])

    def test_low_triggers_purchase(self):
        store, clock = _mk(self)
        r = store.report_remaining("降压药", 2, now=clock())
        self.assertTrue(r["low"])
        need = store.need_purchase_to_do(now=clock())
        self.assertIsNotNone(need)
        self.assertEqual(need["name"], "降压药")

    def test_purchase_no_dup(self):
        store, clock = _mk(self)
        store.report_remaining("降压药", 2, now=clock())
        store.mark_purchase_todo("降压药", now=clock())
        self.assertIsNone(store.need_purchase_to_do(now=clock()))

    def test_dose_recalc(self):
        store, clock = _mk(self)
        store.report_remaining("降压药", 4, now=clock())
        r = store.set_daily_dose("降压药", 2, now=clock())
        self.assertEqual(r["days_left"], 2)  # 4 片 / 2 次 = 2 天

    def test_re_report_resets_todo(self):
        store, clock = _mk(self)
        store.report_remaining("降压药", 1, now=clock())
        store.mark_purchase_todo("降压药", now=clock())
        store.report_remaining("降压药", 30, now=clock())
        self.assertIsNone(store.need_purchase_to_do(now=clock()))


class TestEscalation(unittest.TestCase):
    """未回应通知子女判定。"""

    def test_escalation_due(self):
        store, clock = _mk(self)
        # 未确认 + 2 次补催 → 该升级
        self.assertTrue(store.escalation_due(
            {"thing": "吃降压药", "confirmed": False, "reremind_count": 2}))
        # 已确认 → 不升级
        self.assertFalse(store.escalation_due(
            {"thing": "吃降压药", "confirmed": True, "reremind_count": 2}))
        # 补催不够 → 不升级
        self.assertFalse(store.escalation_due(
            {"thing": "吃降压药", "confirmed": False, "reremind_count": 0}))
        # 非吃药提醒 → 不升级
        self.assertFalse(store.escalation_due(
            {"thing": "买菜", "confirmed": False, "reremind_count": 2}))


class TestExtraction(unittest.TestCase):
    """口语提取（余量 / 剂量 / 中文数字 / 非句式）。"""

    def test_extract_remaining(self):
        e = extract_remaining("降压药还剩5片")
        self.assertEqual(e, {"med": "降压药", "num": 5, "unit": "片"})
        self.assertIsNone(extract_remaining("我没事"))

    def test_extract_dose(self):
        e = extract_daily_dose("降压药一天吃2次")
        self.assertEqual(e, {"med": "降压药", "num": 2})
        self.assertIsNone(extract_daily_dose("我没事"))

    def test_cn_num(self):
        e = extract_remaining("降压药还剩三片")
        self.assertIsNotNone(e)
        self.assertEqual(e["num"], 3)


class TestInvalid(unittest.TestCase):
    """非法输入拒收。"""

    def test_invalid_input(self):
        store, clock = _mk(self)
        self.assertFalse(store.report_remaining("降压药", "abc", now=clock())["ok"])
        self.assertFalse(store.report_remaining("降压药", -5, now=clock())["ok"])
        self.assertFalse(store.report_remaining("", 5, now=clock())["ok"])


class TestPersistence(unittest.TestCase):
    """持久化（重启不丢记录）。"""

    def test_reload(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        clock = FakeClock()
        path = os.path.join(tmp, "med.json")
        store = MedicationStore(path=path, clock=clock)
        store.report_remaining("降压药", 8, now=clock())
        store2 = MedicationStore(path=path, clock=clock)
        self.assertEqual(store2.get("降压药")["remaining"], 8)


if __name__ == "__main__":
    unittest.main()

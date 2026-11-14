# -*- coding: utf-8 -*-
"""T-3 冷启动编排验收测试（Day1-7 脚本 + essential 不跳过 + 状态落盘 + 依赖标注）。"""
import os
import shutil
import tempfile
import unittest

from cold_start import ColdStartOrchestrator


class FakeClock:
    def __init__(self, start=1_800_000_000.0):
        self.t = start

    def __call__(self):
        return self.t

    def tick(self, days=0):
        self.t += days * 86400
        return self.t


def _mk(testcase):
    tmp = tempfile.mkdtemp()
    testcase.addCleanup(shutil.rmtree, tmp, True)
    base = 1_800_000_000.0
    clock = FakeClock(start=base)
    path = os.path.join(tmp, "cs.json")
    store = ColdStartOrchestrator(path=path, clock=clock, activation_ts=base)
    return store, clock, base


class TestDayCalc(unittest.TestCase):
    """天数计算（Day1-7 + Day8 结束）。"""

    def test_day1_on_activation(self):
        store, clock, _ = _mk(self)
        self.assertEqual(store.current_day(), 1)
        self.assertTrue(store.in_cold_start())

    def test_day_progression(self):
        store, clock, base = _mk(self)
        clock.tick(1)
        self.assertEqual(store.current_day(), 2)
        clock.tick(1)  # Day 3
        self.assertEqual(store.current_day(), 3)

    def test_day8_ends_cold_start(self):
        store, clock, base = _mk(self)
        clock.tick(7)
        self.assertEqual(store.current_day(), 8)
        self.assertFalse(store.in_cold_start())
        self.assertEqual(store.pending_tasks(), [])

    def test_day_capped_at_8(self):
        store, clock, base = _mk(self)
        clock.tick(30)
        self.assertEqual(store.current_day(), 8)


class TestDay1Script(unittest.TestCase):
    """Day 1 脚本（3 任务 + essential 优先 + 依赖标注）。"""

    def test_three_tasks(self):
        store, clock, _ = _mk(self)
        pending = store.pending_tasks()
        self.assertEqual(len(pending), 3)

    def test_essential_first(self):
        store, clock, _ = _mk(self)
        pending = store.pending_tasks()
        self.assertTrue(pending[0]["essential"], "essential 任务排前")

    def test_dependency_tags(self):
        store, clock, _ = _mk(self)
        pending = {t["id"]: t for t in store.pending_tasks()}
        self.assertEqual(pending["emergency_contact"].get("depends"), "T-5")
        self.assertEqual(pending["medication"].get("depends"), "T-4")


class TestSkipRules(unittest.TestCase):
    """跳过规则（essential 不跳过 / 非 essential 可跳过）。"""

    def test_essential_not_skippable(self):
        store, clock, _ = _mk(self)
        self.assertFalse(store.mark_skipped("emergency_contact"))
        self.assertFalse(store.mark_skipped("greeting"))

    def test_non_essential_skippable(self):
        store, clock, _ = _mk(self)
        self.assertTrue(store.mark_skipped("medication"))
        self.assertTrue(all(t["id"] != "medication" for t in store.pending_tasks()))


class TestComplete(unittest.TestCase):
    """完成任务。"""

    def test_mark_done(self):
        store, clock, _ = _mk(self)
        store.mark_done("greeting")
        self.assertTrue(all(t["id"] != "greeting" for t in store.pending_tasks()))

    def test_mark_done_idempotent(self):
        store, clock, _ = _mk(self)
        store.mark_done("greeting")
        store.mark_done("greeting")
        self.assertEqual(store.completed.get("1", []).count("greeting"), 1)


class TestDay2Onward(unittest.TestCase):
    """Day 2-7 脚本（一天一件事）。"""

    def test_day2_remember(self):
        store, clock, _ = _mk(self)
        clock.tick(1)
        self.assertEqual(store.pending_tasks()[0]["id"], "remember")

    def test_day7_review(self):
        store, clock, _ = _mk(self)
        clock.tick(6)
        self.assertEqual(store.pending_tasks()[0]["id"], "review")


class TestPersistence(unittest.TestCase):
    """状态落盘（重启不丢进度）。"""

    def test_reload(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        base = 1_800_000_000.0
        clock = FakeClock(start=base)
        path = os.path.join(tmp, "cs.json")
        store = ColdStartOrchestrator(path=path, clock=clock, activation_ts=base)
        store.mark_done("greeting")
        store.mark_done("emergency_contact")
        store.mark_skipped("medication")
        store2 = ColdStartOrchestrator(path=path, clock=clock, activation_ts=base)
        self.assertEqual(sorted(store2.completed.get("1", [])),
                         ["emergency_contact", "greeting"])
        self.assertIn("medication", store2.skipped.get("1", []))


class TestStatus(unittest.TestCase):
    """状态总览。"""

    def test_status_shape(self):
        store, clock, _ = _mk(self)
        st = store.status()
        self.assertTrue(st["in_cold_start"])
        self.assertEqual(st["current_day"], 1)
        self.assertEqual(st["pending_count"], 3)
        self.assertIsNotNone(st["next_task"])


if __name__ == "__main__":
    unittest.main()

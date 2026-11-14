# -*- coding: utf-8 -*-
"""T-5 救命链路验收测试（EmergencyRescue 控制器 + store 双向校验/写记忆）。

验收口径：
  1. 触发：喊"救命"触发 + 无活动两档阈值（白天4h/夜间10h）+ 基线期只触发人工确认
  2. AI确认：应答"没事"=误报 / 确认异常=进倒计时 / 无应答(60s追问1次)=直接进倒计时
  3. 倒计时：30s可取消（"别打了"→取消+记录）
  4. 路径：喊"救命"=先亲属后120 / 喊"打120"=120优先 / 呼叫失败兜底（绝不静默）
  5. 号码二次验证：双向校验后4位回读（符=confirmed / 不符=重来）
  6. 号码备份：写记忆系统（高优先级条目）
"""
import os
import shutil
import tempfile
import time as _time
import unittest

from memory_system import EmergencyContactStore, EmergencyRescue


class FakeClock:
    def __init__(self, start=1_800_000_000.0):
        self.t = start

    def __call__(self):
        return self.t

    def tick(self, seconds=0):
        self.t += seconds
        return self.t


def _mk_store(testcase, two_way_verify=False, memory_system=None):
    tmp = tempfile.mkdtemp()
    testcase.addCleanup(shutil.rmtree, tmp, True)
    clock = FakeClock()
    path = os.path.join(tmp, "emergency_contact.json")
    store = EmergencyContactStore(path=path, clock=clock,
                                  two_way_verify=two_way_verify,
                                  memory_system=memory_system)
    return store, clock


def _day_clock(hour=12):
    """构造一个白天指定小时的时钟起点。"""
    base = _time.mktime(_time.struct_time((2026, 11, 13, hour, 0, 0, 0, 0, 0)))
    return FakeClock(start=base)


class FakeCaller:
    """假呼叫通道（可控制成功/失败）。"""
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def __call__(self, phone, name):
        self.calls.append((phone, name))
        return self.ok


class TestTrigger(unittest.TestCase):
    """验收1：触发条件（喊救命 + 无活动两档 + 基线期）。"""

    def test_rescue_word_triggers_countdown(self):
        store, clock = _mk_store(self)
        rescue = EmergencyRescue(store, clock=clock)
        res = rescue.trigger_rescue("救命", now=clock())
        self.assertEqual(res["action"], "countdown")
        self.assertEqual(res["path"], "contact_first")
        self.assertEqual(rescue.state, "countdown")

    def test_call120_word_triggers_120_first(self):
        store, clock = _mk_store(self)
        rescue = EmergencyRescue(store, clock=clock)
        res = rescue.trigger_rescue("打120", now=clock())
        self.assertEqual(res["action"], "countdown")
        self.assertEqual(res["path"], "120_first")

    def test_no_activity_day_threshold(self):
        clock = _day_clock(hour=12)
        store = EmergencyContactStore(
            path=os.path.join(tempfile.mkdtemp(), "ec.json"), clock=clock)
        # 正式期（30天前激活，非基线期）
        rescue = EmergencyRescue(store, clock=clock,
                                 activation_ts=clock.t - 30 * 86400)
        self.assertFalse(rescue.is_baseline())
        # 无活动 3h → 不触发
        res = rescue.check_no_activity(now=clock.t + 3 * 3600)
        self.assertIsNone(res)
        # 无活动 4h+1s → 触发（白天阈值 4h）
        res = rescue.check_no_activity(now=clock.t + 4 * 3600 + 1)
        self.assertIsNotNone(res)
        self.assertEqual(res["type"], "ai_confirm")
        self.assertIn("没事吧", res["message"])

    def test_baseline_period_only_nudge(self):
        clock = _day_clock(hour=12)
        store = EmergencyContactStore(
            path=os.path.join(tempfile.mkdtemp(), "ec.json"), clock=clock)
        # 基线期（1天前激活）
        rescue = EmergencyRescue(store, clock=clock,
                                 activation_ts=clock.t - 1 * 86400)
        self.assertTrue(rescue.is_baseline())
        # 无活动 5h → 基线期不触发（< 宽松阈值 8h）
        res = rescue.check_no_activity(now=clock.t + 5 * 3600)
        self.assertIsNone(res)
        # 无活动 8h+1s → 基线期只触发人工确认（不触发正式告警）
        res = rescue.check_no_activity(now=clock.t + 8 * 3600 + 1)
        self.assertIsNotNone(res)
        self.assertEqual(res["type"], "baseline_nudge")
        self.assertIn("还好吗", res["message"])
        # 状态仍 idle（基线期不触发正式确认流程）
        self.assertEqual(rescue.state, "idle")


class TestAIConfirm(unittest.TestCase):
    """验收2：AI确认（含无应答分支）。"""

    def test_dismissed(self):
        store, clock = _mk_store(self)
        rescue = EmergencyRescue(store, clock=clock)
        rescue.state = "confirming"
        rescue._state_ts = clock()
        res = rescue.handle_confirm_reply("没事", now=clock())
        self.assertEqual(res["action"], "dismissed")
        self.assertEqual(rescue.state, "idle")

    def test_confirmed_goes_countdown(self):
        store, clock = _mk_store(self)
        rescue = EmergencyRescue(store, clock=clock)
        rescue.state = "confirming"
        rescue._state_ts = clock()
        res = rescue.handle_confirm_reply("我要帮忙", now=clock())
        self.assertEqual(res["action"], "countdown")
        self.assertEqual(rescue.state, "countdown")

    def test_no_response_timeout_retry_then_countdown(self):
        store, clock = _mk_store(self)
        rescue = EmergencyRescue(store, clock=clock)
        rescue.state = "confirming"
        rescue._state_ts = clock()
        # 60s 无应答 → 追问1次
        res = rescue.handle_confirm_reply("", now=clock() + 61)
        self.assertEqual(res["action"], "retry")
        # 再 60s 无应答 → 直接进倒计时
        res = rescue.handle_confirm_reply("", now=clock() + 122)
        self.assertEqual(res["action"], "countdown")
        self.assertEqual(rescue.state, "countdown")


class TestCountdown(unittest.TestCase):
    """验收3：30s可取消倒计时。"""

    def test_cancel(self):
        store, clock = _mk_store(self)
        rescue = EmergencyRescue(store, clock=clock)
        rescue.state = "countdown"
        rescue._state_ts = clock()
        res = rescue.handle_countdown_reply("别打了", now=clock() + 10)
        self.assertEqual(res["action"], "cancelled")
        self.assertEqual(rescue.state, "cancelled")

    def test_timeout_executes_call(self):
        store, clock = _mk_store(self)
        store.set_caller(FakeCaller(ok=True))
        store.submit("小明", "13812345678", relation="儿子")
        store.resolve_confirm("对")
        rescue = EmergencyRescue(store, clock=clock)
        rescue._path = "contact_first"
        rescue.state = "countdown"
        rescue._state_ts = clock()
        # 30s 超时 → 执行呼叫
        res = rescue.handle_countdown_reply("", now=clock() + 31)
        self.assertEqual(res["ok"], True)
        self.assertEqual(rescue.state, "done")


class TestPath(unittest.TestCase):
    """验收4：路径决定（先亲属后120 / 120优先 / 绝不静默）。"""

    def test_contact_first_path(self):
        store, clock = _mk_store(self)
        caller = FakeCaller(ok=True)
        store.set_caller(caller)
        store.submit("小明", "13812345678", relation="儿子")
        store.resolve_confirm("对")
        rescue = EmergencyRescue(store, clock=clock)
        rescue.trigger_rescue("救命", now=clock())
        # 等倒计时超时 → 先打给亲属（接通）
        res = rescue.handle_countdown_reply("", now=clock() + 31)
        self.assertEqual(res["ok"], True)
        self.assertEqual(caller.calls[0][0], "13812345678")
        self.assertEqual(len(caller.calls), 1)  # 亲属接通，不打120

    def test_contact_failed_fallback_120(self):
        store, clock = _mk_store(self)
        store.set_caller(FakeCaller(ok=False))
        store.submit("小明", "13812345678", relation="儿子")
        store.resolve_confirm("对")
        rescue = EmergencyRescue(store, clock=clock)
        rescue.trigger_rescue("救命", now=clock())
        res = rescue.handle_countdown_reply("", now=clock() + 31)
        # 亲属无应答 → 120 兜底（绝不静默）
        self.assertIn("120", res["message"])

    def test_120_first_path(self):
        store, clock = _mk_store(self)
        store.set_caller(FakeCaller(ok=True))
        rescue = EmergencyRescue(store, clock=clock)
        rescue.trigger_rescue("打120", now=clock())
        res = rescue.handle_countdown_reply("", now=clock() + 31)
        # 120 优先（MVP 不自主拨，诚实话术）
        self.assertIn("120", res["message"])
        self.assertEqual(rescue.state, "done")


class TestTwoWayVerify(unittest.TestCase):
    """验收5：号码二次验证（双向校验后4位回读）。"""

    def test_two_way_verify_match(self):
        store, clock = _mk_store(self, two_way_verify=True)
        store.submit("小明", "13812345678", relation="儿子", source="elder")
        res = store.resolve_confirm("对", now=clock())
        self.assertEqual(res["action"], "verify_back4")
        self.assertIsNone(store.get())  # 确认前不落库
        # 后4位回读（相符）
        res = store.resolve_back4("5678", now=clock())
        self.assertEqual(res["action"], "confirmed")
        c = store.get()
        self.assertIsNotNone(c)
        self.assertEqual(c["phone"], "13812345678")
        self.assertTrue(c.get("two_way_verified"))

    def test_two_way_verify_mismatch(self):
        store, clock = _mk_store(self, two_way_verify=True)
        store.submit("小明", "13812345678", relation="儿子", source="elder")
        store.resolve_confirm("对", now=clock())
        # 后4位不符 → 从头再来
        res = store.resolve_back4("0000", now=clock())
        self.assertEqual(res["action"], "rejected")
        self.assertIsNone(store.get())

    def test_no_two_way_for_child_source(self):
        """代填路径不要求双向校验（由代填人负责准确性）。"""
        store, clock = _mk_store(self, two_way_verify=True)
        store.submit("小红", "15987654321", relation="女儿", source="child")
        res = store.resolve_confirm("没错", now=clock())
        self.assertEqual(res["action"], "confirmed")  # 直接确认（不要求后4位）


class TestMemoryBackup(unittest.TestCase):
    """验收6：号码备份（写记忆系统）。"""

    def test_write_to_memory_system(self):
        class FakeMemory:
            def __init__(self):
                self.facts = []
            def _add_fact(self, text, category=None):
                self.facts.append((text, category))

        mem = FakeMemory()
        store, clock = _mk_store(self, two_way_verify=True, memory_system=mem)
        store.submit("小明", "13812345678", relation="儿子", source="elder")
        store.resolve_confirm("对", now=clock())
        store.resolve_back4("5678", now=clock())
        # 号码写入记忆系统
        self.assertTrue(len(mem.facts) >= 1)
        self.assertIn("13812345678", mem.facts[0][0])
        self.assertEqual(mem.facts[0][1], "emergency")


if __name__ == "__main__":
    unittest.main()

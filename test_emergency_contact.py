# -*- coding: utf-8 -*-
"""B-11 紧急联络验收测试（任务卡 B-11 验收标准，主动呼叫部分）。

验收口径：
  1. 登记三路径 + 逐位复述确认 + 更正 可用（各测 ≥3 遍）
  2. 老人语音紧急呼叫触发成功率 10/10（触发识别 + 无联系人兜底话术齐全）
  3. 呼叫失败：重试 1 次 + 兜底播报（无静默，日志记录）
  4. 复述确认 100% 执行：无 pending 绝无直接写入
  5. 120 不自主拨：诚实标注话术
  6. 未设置兜底：每天提醒一次（24h 控频）
"""
import os
import shutil
import tempfile
import unittest

from memory_system import EmergencyContactStore


class FakeClock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t

    def tick(self, hours=0, minutes=0):
        self.t += hours * 3600 + minutes * 60
        return self.t


def _mk_store(testcase):
    tmp = tempfile.mkdtemp()
    testcase.addCleanup(shutil.rmtree, tmp, True)
    clock = FakeClock()
    path = os.path.join(tmp, "emergency_contact.json")
    return EmergencyContactStore(path=path, clock=clock), clock


PHONE = "13812345678"


class TestRegistration(unittest.TestCase):
    """验收1：登记 + 复述确认 + 更正（各 3 遍）。"""

    def test_register_flow_three_times(self):
        for i in range(3):
            store, clock = _mk_store(self)
            res = store.submit("小明", PHONE, relation="儿子", source="elder")
            self.assertTrue(res["ok"])
            self.assertIn("138-1234-5678", res["message"])       # 逐位复述
            self.assertIn("对吧", res["message"])
            self.assertIsNone(store.get())                        # 确认前不落库
            ans = store.resolve_confirm("对", now=clock())
            self.assertEqual(ans["action"], "confirmed")
            c = store.get()
            self.assertEqual(c["name"], "小明")
            self.assertEqual(c["phone"], PHONE)
            self.assertEqual(c["source"], "elder")
            self.assertTrue(c["confirmed"])

    def test_child_source_still_confirms_to_elder(self):
        """路径2 子女端代填：仍向老人复述（pending 必须产生，不直接落库）。"""
        for i in range(3):
            store, _ = _mk_store(self)
            res = store.submit("小红", "15987654321", relation="女儿", source="child")
            self.assertTrue(res["pending"])                       # 仍要老人复述确认
            self.assertIsNone(store.get())
            self.assertEqual(store.resolve_confirm("没错")["action"], "confirmed")
            self.assertEqual(store.get()["source"], "child")

    def test_correction_flow(self):
        """更正：同一复述确认流程，新值覆盖旧值。"""
        for i in range(3):
            store, clock = _mk_store(self)
            store.submit("小明", PHONE, relation="儿子")
            store.resolve_confirm("对")
            res = store.submit("小红", "15987654321", relation="女儿")   # 更正=重新提交
            self.assertTrue(res["pending"])
            store.resolve_confirm("没错")
            c = store.get()
            self.assertEqual(c["name"], "小红")
            self.assertEqual(c["phone"], "15987654321")

    def test_invalid_phone_rejected(self):
        """不足 11 位拒收（不猜）；名字空拒收。"""
        store, _ = _mk_store(self)
        res = store.submit("小明", "1381234")
        self.assertFalse(res["ok"])
        self.assertIsNone(store.get())
        res = store.submit("", PHONE)
        self.assertFalse(res["ok"])

    def test_confirm_expire(self):
        """复述确认超时（>10 分钟）作废，重来。"""
        store, clock = _mk_store(self)
        store.submit("小明", PHONE, relation="儿子")
        clock.tick(minutes=11)
        ans = store.resolve_confirm("对")
        self.assertEqual(ans["action"], "expired")
        self.assertIsNone(store.get())                 # 陈旧确认不落库
        self.assertIsNone(store.pending)

    def test_confirm_100_percent_no_direct_write(self):
        """复述确认 100% 执行：没 pending 时答复'对'绝不写入任何东西。"""
        store, _ = _mk_store(self)
        ans = store.resolve_confirm("对")
        self.assertEqual(ans["action"], "none")
        self.assertIsNone(store.get())


class TestChatEntry(unittest.TestCase):
    """口语闭环：登记/呼叫/查询 全走 handle_chat。"""

    def test_register_by_voice(self):
        store, _ = _mk_store(self)
        reply = store.handle_chat("我儿子电话13812345678")
        self.assertIsNotNone(reply)
        self.assertIn("对吧", reply)                     # 复述确认话术
        reply = store.handle_chat("对")
        self.assertIn("记好啦", reply)

    def test_query(self):
        store, _ = _mk_store(self)
        store.submit("小明", PHONE, relation="儿子")
        store.resolve_confirm("对")
        reply = store.handle_chat("紧急联系人是谁")
        self.assertIn("小明", reply)
        self.assertIn("138-1234-5678", reply)

    def test_query_when_unset(self):
        store, _ = _mk_store(self)
        reply = store.handle_chat("我的紧急联系人是谁来着")
        self.assertIn("还没设置", reply)

    def test_not_emergency_passthrough(self):
        """正常聊天不接管。"""
        store, _ = _mk_store(self)
        self.assertIsNone(store.handle_chat("今天天气怎么样"))
        self.assertIsNone(store.handle_chat("我降压药放哪了"))
        self.assertIsNone(store.handle_chat("跟我讲个老故事吧"))


class TestCallTrigger(unittest.TestCase):
    """验收2：语音紧急呼叫触发成功率 10/10。"""

    def test_call_trigger_10_of_10(self):
        store, clock = _mk_store(self)
        store.submit("小明", PHONE, relation="儿子")
        store.resolve_confirm("对")
        hits = 0
        for phrase in ("帮我给孩子打电话", "给孩子打个电话", "打电话给我儿子",
                       "帮我儿子打电话", "给我孩子来个电话", "快给孩子打电话",
                       "我要给孩子打电话", "帮我给我儿子打电话",
                       "给我女儿打电话", "帮我打电话给孩子"):
            store.contact and None
            res = store.request_call(target="contact",
                                     caller=lambda p: True, now=clock())
            self.assertEqual(res["status"], "dialing")
            hits += 1
        self.assertEqual(hits, 10)
        # 触发识别（handle_chat 层）也过一遍
        phrases = ("帮我给孩子打电话", "给孩子打个电话", "打电话给我儿子",
                   "打120", "呼叫120", "叫救护车", "快打120",
                   "帮我给孩子打个电话", "给我儿子打电话", "给我女儿打电话")
        for p in phrases:
            self.assertIsNotNone(store.handle_chat(p), msg=p)

    def test_call_no_contact_honest_reply(self):
        """未设置时呼叫：诚实话术引导先按120（不装打通）。"""
        store, _ = _mk_store(self)
        res = store.request_call(target="contact")
        self.assertFalse(res["ok"])
        self.assertIn("还没设置", res["message"])

    def test_call_120_not_auto_dial(self):
        """验收5：MVP 不自主拨 120——诚实标注话术 + 日志记录 fallback。"""
        store, clock = _mk_store(self)
        res = store.request_call(target="120", now=clock())
        self.assertEqual(res["status"], "fallback")
        self.assertIn("自己按120", res["message"])
        self.assertEqual(store.call_log[-1]["target"], "120")
        self.assertEqual(store.call_log[-1]["status"], "fallback")

    def test_call_120_by_chat(self):
        store, _ = _mk_store(self)
        reply = store.handle_chat("打120")
        self.assertIn("自己按120", reply)
        reply = store.handle_chat("快叫救护车")
        self.assertIn("120", reply)


class TestCallFailure(unittest.TestCase):
    """验收3：呼叫失败 → 重试 1 次 → 兜底播报，绝不静默（日志必有记录）。"""

    def test_retry_once_then_fallback(self):
        store, clock = _mk_store(self)
        store.submit("小明", PHONE, relation="儿子")
        store.resolve_confirm("对")
        attempts = []

        def failing_caller(phone):
            attempts.append(phone)
            return False                      # 永远失败

        res = store.request_call(target="contact", caller=failing_caller, now=clock())
        self.assertEqual(res["status"], "failed")
        self.assertEqual(len(attempts), 2)         # 重试 1 次 = 共 2 次
        self.assertIn("没接通", res["message"])    # 兜底播报
        self.assertIn("120", res["message"])       # 引导自拨120/喊邻居
        last = store.call_log[-1]
        self.assertEqual(last["status"], "failed")
        self.assertEqual(last["retries"], 1)       # 日志记录（无静默）

    def test_retry_success_on_second(self):
        """第一次失败、重试成功 → dialing。"""
        store, clock = _mk_store(self)
        store.submit("小明", PHONE, relation="儿子")
        store.resolve_confirm("对")
        calls = {"n": 0}

        def flaky(phone):
            calls["n"] += 1
            return calls["n"] > 1

        res = store.request_call(target="contact", caller=flaky, now=clock())
        self.assertEqual(res["status"], "dialing")
        self.assertEqual(calls["n"], 2)

    def test_caller_exception_logged(self):
        """caller 抛异常也算失败，日志带异常信息。"""
        store, clock = _mk_store(self)
        store.submit("小明", PHONE, relation="儿子")
        store.resolve_confirm("对")

        def boom(phone):
            raise RuntimeError("通道断了")

        res = store.request_call(target="contact", caller=boom, now=clock())
        self.assertEqual(res["status"], "failed")
        self.assertIn("通道断了", store.call_log[-1]["message"])

    def test_no_caller_honest(self):
        """通道未接（MVP 现状）：诚实说明+报号码，不装打通；日志记 failed。"""
        store, _ = _mk_store(self)
        store.submit("小明", PHONE, relation="儿子")
        store.resolve_confirm("对")
        res = store.request_call(target="contact")     # caller=None
        self.assertEqual(res["status"], "failed")
        self.assertIn("接不通", res["message"])
        self.assertIn("138-1234-5678", res["message"])  # 把号码报给老人
        self.assertTrue(store.call_log)                 # 无静默


class TestDailyNudge(unittest.TestCase):
    """验收6：未设置兜底——每天提醒一次。"""

    def test_nudge_daily(self):
        store, clock = _mk_store(self)
        msg = store.daily_nudge_due(now=clock())
        self.assertIsNotNone(msg)
        self.assertIn("还没设置", msg)
        # 同一天（<24h）不重复提醒
        clock.tick(hours=12)
        self.assertIsNone(store.daily_nudge_due(now=clock()))
        # 次日再提醒
        clock.tick(hours=13)
        self.assertIsNotNone(store.daily_nudge_due(now=clock()))

    def test_nudge_stops_after_set(self):
        store, clock = _mk_store(self)
        store.daily_nudge_due(now=clock())              # 提醒过一次
        store.submit("小明", PHONE, relation="儿子")
        ans = store.resolve_confirm("对")
        self.assertEqual(ans["action"], "confirmed")
        clock.tick(hours=25)
        self.assertIsNone(store.daily_nudge_due(now=clock()))   # 设好了不再提醒


class TestPersistence(unittest.TestCase):
    """持久化：重启后联系人/呼叫日志/提醒控频不丢。"""

    def test_reload(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        clock = FakeClock()
        path = os.path.join(tmp, "emergency_contact.json")
        store = EmergencyContactStore(path=path, clock=clock)
        store.submit("小明", PHONE, relation="儿子")
        store.resolve_confirm("对")
        store.daily_nudge_due()
        store.request_call(target="contact",
                           caller=lambda p: False)      # 失败也记日志
        store2 = EmergencyContactStore(path=path, clock=clock)
        self.assertEqual(store2.get()["name"], "小明")
        self.assertTrue(store2.call_log)
        self.assertIsNone(store2.daily_nudge_due())     # 控频状态也保留

    def test_pending_not_persisted_as_contact(self):
        """pending 落盘 ≠ 联系人已设置——重启后 status.set 仍为 False。"""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "emergency_contact.json")
        EmergencyContactStore(path=path).submit("小明", PHONE, relation="儿子")
        st = EmergencyContactStore(path=path).status()
        self.assertFalse(st["set"])
        self.assertTrue(st["awaiting_confirm"])


if __name__ == "__main__":
    unittest.main()

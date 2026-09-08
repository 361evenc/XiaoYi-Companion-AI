# -*- coding: utf-8 -*-
"""
提醒系统单元测试：中文时间语义（相对/绝对/周期/锚点/跨天/边界）+ 存储调度 + 依从性闭环
运行：《小忆：面向独居老人的本地化大语言模型陪伴系统——人格注入、记忆增强与确定性关怀闭环的设计与实现》
"""
import os
import tempfile
import time as _time
import unittest
from datetime import datetime, timedelta

import reminder as R
from reminder import (ReminderStore, cn_to_int, parse_time_expr, parse_duration_seconds,
                      humanize_ts, describe_reminder, match_confirmation,
                      fallback_extract, looks_like_reminder, repeat_desc,
                      ack_set_message, trigger_message, list_message)


# 固定"现在"：2026-09-06（周日）09:00
NOW = datetime(2026, 9, 6, 9, 0, 0)
TS = NOW.timestamp()


def ts_of(dt):
    return dt.timestamp()


def dt_of(result):
    return datetime.fromtimestamp(result["fire_ts"])


class FakeClock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, secs):
        self.t += secs


class TestCnNum(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(cn_to_int("五"), 5)
        self.assertEqual(cn_to_int("两"), 2)
        self.assertEqual(cn_to_int("十"), 10)
        self.assertEqual(cn_to_int("十二"), 12)
        self.assertEqual(cn_to_int("二十五"), 25)
        self.assertEqual(cn_to_int("45"), 45)
        self.assertIsNone(cn_to_int("abc"))


class TestDuration(unittest.TestCase):
    def test_cases(self):
        cases = {
            "五分钟后": 300, "10分钟后": 600, "半小时后": 1800,
            "一个半小时后": 5400, "两小时后": 7200, "一刻钟后": 900,
            "三刻钟后": 2700, "45分钟以后": 2700, "十秒后": 10,
            "一个钟头后": 3600,
        }
        for text, want in cases.items():
            self.assertEqual(parse_duration_seconds(text), want, text)


class TestRelativeAndImmediate(unittest.TestCase):
    def test_five_minutes(self):
        r = parse_time_expr("五分钟后提醒我喝水", NOW)
        self.assertEqual(r["kind"], "fixed")
        self.assertAlmostEqual(r["fire_ts"], TS + 300, delta=1)

    def test_ten_seconds_demo(self):
        r = parse_time_expr("十秒后提醒我", NOW)
        self.assertAlmostEqual(r["fire_ts"], TS + 10, delta=1)

    def test_immediate(self):
        r = parse_time_expr("马上提醒我吃药", NOW)
        self.assertAlmostEqual(r["fire_ts"], TS + 60, delta=1)


class TestAbsoluteClock(unittest.TestCase):
    def test_tomorrow_morning(self):
        r = parse_time_expr("明天早上八点提醒我吃药", NOW)
        self.assertEqual(dt_of(r), datetime(2026, 9, 7, 8, 0))

    def test_today_passed_rolls_to_tomorrow(self):
        # 现在 16 点，"今天下午三点" 已过 -> 顺延到明天
        r = parse_time_expr("今天下午三点提醒我", datetime(2026, 9, 6, 16, 0))
        self.assertEqual(dt_of(r), datetime(2026, 9, 7, 15, 0))

    def test_future_today(self):
        r = parse_time_expr("今天下午三点提醒我", datetime(2026, 9, 6, 10, 0))
        self.assertEqual(dt_of(r), datetime(2026, 9, 6, 15, 0))

    def test_cross_day_late_night(self):
        # 23:45 说 "晚上十一点半" -> 已过，顺延明天 23:30
        now = datetime(2026, 9, 6, 23, 45)
        r = parse_time_expr("晚上十一点半提醒我", now)
        self.assertEqual(dt_of(r), datetime(2026, 9, 7, 23, 30))

    def test_qualifiers(self):
        self.assertEqual(dt_of(parse_time_expr("下午四点一刻", NOW)),
                         datetime(2026, 9, 6, 16, 15))
        self.assertEqual(dt_of(parse_time_expr("中午十二点", NOW)),
                         datetime(2026, 9, 6, 12, 0))
        self.assertEqual(dt_of(parse_time_expr("凌晨三点", NOW)),
                         datetime(2026, 9, 7, 3, 0))  # 已过 -> 明天
        # "晚上十二点" 归一到次日 0 点
        r = parse_time_expr("晚上十二点", NOW)
        self.assertEqual(dt_of(r), datetime(2026, 9, 7, 0, 0))

    def test_bare_hour_nearest_future(self):
        # 上午 9 点说 "两点" -> 今天 14 点
        self.assertEqual(dt_of(parse_time_expr("两点提醒我", NOW)),
                         datetime(2026, 9, 6, 14, 0))
        # 下午 4 点说 "两点" -> 明天凌晨 2 点（最近未来）
        r = parse_time_expr("两点提醒我", datetime(2026, 9, 6, 16, 0))
        self.assertEqual(dt_of(r), datetime(2026, 9, 7, 2, 0))
        # 晚上 9 点说 "十点" -> 今晚 22 点（比明早 10 点近）
        r = parse_time_expr("十点提醒我", datetime(2026, 9, 6, 21, 0))
        self.assertEqual(dt_of(r), datetime(2026, 9, 6, 22, 0))

    def test_day_after_day(self):
        r = parse_time_expr("后天早上七点半", NOW)
        self.assertEqual(dt_of(r), datetime(2026, 9, 8, 7, 30))
        r = parse_time_expr("大后天早上七点", NOW)
        self.assertEqual(dt_of(r), datetime(2026, 9, 9, 7, 0))

    def test_monthday(self):
        r = parse_time_expr("15号早上九点复查", NOW)
        self.assertEqual(dt_of(r), datetime(2026, 9, 15, 9, 0))
        r = parse_time_expr("9月20号下午三点", NOW)
        self.assertEqual(dt_of(r), datetime(2026, 9, 20, 15, 0))

    def test_weekday_includes_today(self):
        # 周X：若今天就是且时刻未过，算今天；否则下一个周X
        now = datetime(2026, 9, 7, 10, 0)  # 周一
        want_wed = now + timedelta(days=(2 - now.weekday()) % 7)
        r = parse_time_expr("周三下午三点复诊", now)
        self.assertEqual(dt_of(r), want_wed.replace(hour=15, minute=0))

    def test_unparseable(self):
        self.assertIsNone(parse_time_expr("讲讲以前的事", NOW))


class TestPeriodic(unittest.TestCase):
    def test_daily_before_time(self):
        r = parse_time_expr("每天早上八点吃药", datetime(2026, 9, 6, 7, 0))
        self.assertEqual(r["repeat"]["type"], "daily")
        self.assertEqual(r["repeat"]["hour"], 8)
        self.assertEqual(dt_of(r), datetime(2026, 9, 6, 8, 0))

    def test_daily_after_time_tomorrow(self):
        r = parse_time_expr("每天早上八点吃药", NOW)
        self.assertEqual(dt_of(r), datetime(2026, 9, 7, 8, 0))

    def test_daily_evening(self):
        r = parse_time_expr("每天晚上九点泡脚", datetime(2026, 9, 6, 7, 0))
        self.assertEqual(r["repeat"]["hour"], 21)
        self.assertEqual(dt_of(r), datetime(2026, 9, 6, 21, 0))

    def test_weekly(self):
        r = parse_time_expr("每周三下午四点量血压", datetime(2026, 9, 7, 10, 0))  # 周一
        self.assertEqual(r["repeat"]["type"], "weekly")
        self.assertEqual(r["repeat"]["weekday"], 2)
        self.assertEqual(dt_of(r), datetime(2026, 9, 9, 16, 0))

    def test_daily_without_clock_asks_once(self):
        r = parse_time_expr("每天提醒我吃药", NOW)
        self.assertEqual(r["kind"], "clarify")
        self.assertEqual(r["repeat_type"], "daily")
        self.assertIsNone(r["suggestion"])


class TestAnchorsClarify(unittest.TestCase):
    def test_anchor_sleep(self):
        r = parse_time_expr("睡醒后提醒我吃药", NOW)
        self.assertEqual(r["kind"], "clarify")
        self.assertEqual(r["suggestion"], 7200)

    def test_anchor_meal(self):
        r = parse_time_expr("吃完饭提醒我锻炼", NOW)
        self.assertEqual(r["kind"], "clarify")
        self.assertEqual(r["suggestion"], 2700)

    def test_anchor_vague(self):
        r = parse_time_expr("过会儿提醒我关火", NOW)
        self.assertEqual(r["kind"], "clarify")
        self.assertEqual(r["suggestion"], 1800)

    def test_date_only_asks_once(self):
        r = parse_time_expr("明天提醒我复查", NOW)
        self.assertEqual(r["kind"], "clarify")
        self.assertEqual(r.get("date_offset"), 1)
        self.assertIsNone(r["suggestion"])


class TestHumanize(unittest.TestCase):
    def test_phrases(self):
        self.assertEqual(humanize_ts(ts_of(datetime(2026, 9, 6, 15, 0)), NOW), "今天下午3点")
        self.assertEqual(humanize_ts(ts_of(datetime(2026, 9, 6, 15, 30)), NOW), "今天下午3点半")
        self.assertEqual(humanize_ts(ts_of(datetime(2026, 9, 7, 8, 0)), NOW), "明天早上8点")
        self.assertEqual(humanize_ts(ts_of(datetime(2026, 9, 6, 0, 30)), NOW), "今天半夜12点半")
        self.assertEqual(humanize_ts(ts_of(datetime(2026, 9, 6, 12, 0)), NOW), "今天中午12点")
        self.assertEqual(humanize_ts(ts_of(datetime(2026, 10, 1, 9, 0)), NOW), "10月1号早上9点")

    def test_describe_repeat(self):
        r = {"thing": "吃药", "repeat": {"type": "daily", "hour": 8, "minute": 0}}
        self.assertEqual(describe_reminder(r, NOW), "每天早上8点 吃药")
        r2 = {"thing": "量血压", "repeat": {"type": "weekly", "weekday": 2, "hour": 16, "minute": 30}}
        self.assertEqual(describe_reminder(r2, NOW), "每周三下午4点半 量血压")


class TestConfirmation(unittest.TestCase):
    def test_done(self):
        self.assertEqual(match_confirmation("我吃完了"), "done")
        self.assertEqual(match_confirmation("喝完了"), "done")
        self.assertEqual(match_confirmation("办好了"), "done")

    def test_later(self):
        self.assertEqual(match_confirmation("还没吃呢"), "later")
        self.assertEqual(match_confirmation("等会儿再说"), "later")

    def test_none(self):
        self.assertIsNone(match_confirmation("今天天气真不错"))


class TestFallbackExtract(unittest.TestCase):
    def test_set(self):
        d = fallback_extract("五分钟后提醒我喝水")
        self.assertEqual(d["intent"], "set")
        self.assertEqual(d["thing"], "喝水")

    def test_cancel(self):
        self.assertEqual(fallback_extract("把提醒取消了")["intent"], "cancel")

    def test_list(self):
        self.assertEqual(fallback_extract("我有什么提醒")["intent"], "list")

    def test_prefilter(self):
        self.assertTrue(looks_like_reminder("五分钟后提醒我喝水"))
        self.assertTrue(looks_like_reminder("每天早上八点吃药"))
        self.assertFalse(looks_like_reminder("我今天挺好的"))
        self.assertFalse(looks_like_reminder("讲讲以前的事"))

    def test_set_periodic_no_keyword(self):
        d = fallback_extract("每天早上八点吃药")
        self.assertEqual(d["intent"], "set")
        self.assertEqual(d["thing"], "吃药")
        # 纯闲聊不误触
        self.assertIsNone(fallback_extract("我每天都散步"))


class TestStoreLoop(unittest.TestCase):
    def setUp(self):
        self.fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(self.fd)
        self.clock = FakeClock(TS)
        self.store = ReminderStore(self.path, clock=self.clock)

    def tearDown(self):
        os.remove(self.path)

    def test_one_shot_fire_and_confirm(self):
        r = self.store.add("喝水", TS + 300)
        self.assertEqual(len(self.store.due()), 0)
        self.clock.advance(301)
        due = self.store.due()
        self.assertEqual([x["id"] for x in due], [r["id"]])
        self.store.mark_fired(r["id"])
        self.assertEqual(r["status"], "fired")
        self.assertEqual(self.store.due(), [])
        awaiting = self.store.awaiting_confirm()
        self.assertEqual(len(awaiting), 1)
        self.store.confirm(r["id"])
        self.assertEqual(r["status"], "done")
        stats = self.store.stats()
        self.assertEqual((stats["total"], stats["done"], stats["rate"]), (1, 1, 100))

    def test_escalation_after_two_rereminds(self):
        r = self.store.add("吃药", TS + 1)
        self.clock.advance(2)
        self.store.mark_fired(r["id"])
        # interval=10s, max 2 次：10s 补提醒1，20s 补提醒2，30s 升级
        self.clock.advance(10)
        events = self.store.advance_awaiting(interval=10, max_n=2)
        self.assertEqual([k for _, k in events], ["reremind"])
        self.assertEqual(r["log"][-1]["re_reminds"], 1)
        self.clock.advance(10)
        events = self.store.advance_awaiting(interval=10, max_n=2)
        self.assertEqual([k for _, k in events], ["reremind"])
        self.clock.advance(10)
        events = self.store.advance_awaiting(interval=10, max_n=2)
        self.assertEqual([k for _, k in events], ["escalate"])
        self.assertTrue(r["log"][-1].get("escalated"))
        self.assertEqual(len(self.store.notices), 1)
        self.assertEqual(self.store.notices[0]["thing"], "吃药")
        # 升级后不再重复打扰
        self.clock.advance(30)
        self.assertEqual(self.store.advance_awaiting(interval=10, max_n=2), [])
        # 用户迟来的确认仍记为完成
        self.store.confirm(r["id"])
        self.assertEqual(r["log"][-1]["result"], "done")
        self.assertEqual(r["status"], "done")

    def test_repeat_reschedules_on_fire(self):
        base = datetime(2026, 9, 6, 8, 0).timestamp()
        r = self.store.add("吃药", base, {"type": "daily", "hour": 8, "minute": 0})
        self.clock.t = base + 5
        self.assertEqual(len(self.store.due()), 1)
        self.store.mark_fired(r["id"])
        self.assertEqual(r["status"], "scheduled")  # 周期任务保持调度
        self.assertEqual(datetime.fromtimestamp(r["time"]), datetime(2026, 9, 7, 8, 0))
        self.assertEqual(len(r["log"]), 1)
        # 确认后记录完成，但系列仍在
        self.store.confirm(r["id"])
        self.assertEqual(r["status"], "scheduled")
        self.assertEqual(r["log"][-1]["result"], "done")

    def test_missed_on_startup(self):
        # 一次性提醒已错过 -> missed
        r1 = self.store.add("喝水", TS - 100)
        # 周期提醒错过 -> 顺延下一次并记 missed
        base = datetime(2026, 9, 6, 8, 0).timestamp()
        r2 = self.store.add("吃药", base, {"type": "daily", "hour": 8, "minute": 0})
        missed = self.store.missed_on_startup()
        self.assertEqual({x["id"] for x in missed}, {r1["id"], r2["id"]})
        self.assertEqual(r1["status"], "missed")
        self.assertEqual(r2["status"], "scheduled")
        self.assertEqual(datetime.fromtimestamp(r2["time"]), datetime(2026, 9, 7, 8, 0))
        self.assertEqual(r2["log"][-1]["result"], "missed")

    def test_snooze(self):
        r = self.store.add("喝水", TS + 1)
        self.clock.advance(2)
        self.store.mark_fired(r["id"])
        self.store.snooze(r["id"], 600)
        self.assertEqual(r["status"], "scheduled")
        self.assertAlmostEqual(r["time"], self.clock.t + 600 - _time.time() + _time.time(), delta=1)
        self.assertEqual(self.store.due(), [])

    def test_snooze_repeat_spawns_one_shot(self):
        base = datetime(2026, 9, 6, 8, 0).timestamp()
        r = self.store.add("吃药", base, {"type": "daily", "hour": 8, "minute": 0})
        self.clock.t = base + 5
        self.store.mark_fired(r["id"])
        n_before = len(self.store.items)
        child = self.store.snooze(r["id"], 600)
        self.assertIsNone(child.get("repeat"))
        self.assertEqual(len(self.store.items), n_before + 1)
        self.assertEqual(child["thing"], "吃药")

    def test_modify_keeps_repeat_aligned(self):
        r = self.store.add("吃药", TS + 3600, {"type": "daily", "hour": 10, "minute": 0})
        new_ts = ts_of(datetime(2026, 9, 6, 15, 30))
        self.assertTrue(self.store.modify(r["id"], new_ts))
        self.assertEqual(r["repeat"], {"type": "daily", "hour": 15, "minute": 30})
        self.assertEqual(datetime.fromtimestamp(r["time"]), datetime(2026, 9, 6, 15, 30))

    def test_cancel_and_cancel_all(self):
        r1 = self.store.add("喝水", TS + 60)
        r2 = self.store.add("吃药", TS + 120)
        self.assertTrue(self.store.cancel(r1["id"]))
        self.assertFalse(self.store.cancel(r1["id"]))  # 已取消不能重复取消
        self.assertEqual(self.store.cancel_all(), 1)
        self.assertEqual(self.store.pending(), [])

    def test_stats_mixed(self):
        a = self.store.add("喝水", TS + 1)
        b = self.store.add("吃药", TS + 1)
        self.clock.advance(2)
        self.store.mark_fired(a["id"])
        self.store.mark_fired(b["id"])
        self.store.confirm(a["id"])
        stats = self.store.stats()
        self.assertEqual((stats["total"], stats["done"]), (2, 1))
        self.assertEqual(stats["rate"], 50)

    def test_persistence_roundtrip(self):
        r = self.store.add("喝水", TS + 60)
        store2 = ReminderStore(self.path, clock=self.clock)
        self.assertEqual(len(store2.pending()), 1)
        self.assertEqual(store2.get(r["id"])["thing"], "喝水")


class TestMessages(unittest.TestCase):
    def test_ack_personalities(self):
        for p in ("踏实务实", "风趣幽默", "暖心知心"):
            msg = ack_set_message(p, "王爷爷", "喝水", "今天下午3点")
            self.assertIn("喝水", msg)
            self.assertIn("3点", msg)

    def test_trigger_medication(self):
        msg = trigger_message("踏实务实", "王爷爷", "吃降压药")
        self.assertIn("药吃了吗", msg)
        msg2 = trigger_message("踏实务实", "王爷爷", "喝水")
        self.assertIn("水喝了吗", msg2)

    def test_list_empty_and_full(self):
        self.assertIn("没有待办", list_message("王爷爷", []))
        self.assertIn("1）", list_message("王爷爷", ["今天下午3点 喝水"]))

    def test_repeat_desc(self):
        self.assertEqual(repeat_desc({"type": "daily"}), "每天")
        self.assertEqual(repeat_desc({"type": "weekly", "weekday": 2}), "每周三")


if __name__ == "__main__":
    unittest.main(verbosity=2)

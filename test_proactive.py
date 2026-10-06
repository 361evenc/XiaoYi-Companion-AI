# -*- coding: utf-8 -*-
"""test_proactive.py —— 主动关怀引擎单元测试
运行：python -m unittest test_proactive -v
覆盖：五信号、三道闸（硬抑制/冷却/动机阈值）、反馈闭环、24h 防重、
     无记忆不开口、Bug① 回归（家人类不套"好点了吗"）、每日上限。
"""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime

import proactive


def _ts(y=2026, mo=10, d=4, h=12, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s).timestamp()


class ProactiveTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pro_test_")
        self.events = []
        self.profile = {}
        self.emo_log = []
        self.pending_reminders = []
        self.awaiting_reminders = []
        self.med = {"ts": 0.0}
        self.last_user = [0.0]
        self.llm_reply = [""]
        proactive.init(
            llm=lambda msgs, max_tokens=120, timeout=10: self.llm_reply[0],
            safety=None,
            persona_prompts={"踏实务实": "你是小忆，踏实晚辈。", "风趣幽默": "你是小忆，幽默晚辈。",
                             "暖心知心": "你是小忆，温柔晚辈。"},
            state_file=os.path.join(self.tmp, "state.json"),
            log_file=os.path.join(self.tmp, "log.json"),
            festival_file=os.path.join(self.tmp, "no_fest.json"),
            get_persona=lambda: "踏实务实",
            get_call_name=lambda: "张奶奶",
            last_interaction=lambda: self.last_user[0],
            get_profile=lambda: self.profile,
            get_keyword_events=lambda: self.events,
            save_keyword_events=lambda evts: None,
            get_emotion_log=lambda: self.emo_log,
            get_pending_reminders=lambda: self.pending_reminders,
            get_awaiting_reminders=lambda: self.awaiting_reminders,
            get_last_medication_time=lambda: self.med["ts"],
            touch_medication_time=lambda: self.med.__setitem__("ts", self._now),
            weather_provider=None,
        )
        self._now = _ts()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _state(self):
        return proactive._load_state()

    def _log(self):
        return proactive._load_log()


class TestSignals(ProactiveTestBase):
    def test_silence_with_profile_fires(self):
        """信号1：沉默5分钟+画像有爱好 → 主动开口且带上记忆内容"""
        self._now = _ts(h=13, mi=0)
        self.last_user[0] = self._now - 400
        self.profile = {"兴趣": [{"key": "听戏", "value": "听《贵妃醉酒》", "ts": self._now - 80000}]}
        msg = proactive.tick(self._now)
        self.assertIsNotNone(msg)
        self.assertIn("贵妃醉酒", msg)
        self.assertIn("张奶奶", msg)
        self.assertEqual(self._log()[-1]["signal"], "silence")

    def test_silence_without_memory_stays_quiet(self):
        """铁律：无记忆证据不开口"""
        self._now = _ts(h=13, mi=0)
        self.last_user[0] = self._now - 400
        self.profile = {}
        self.assertIsNone(proactive.tick(self._now))

    def test_anchor_meal_fires_once_a_day(self):
        """信号2：11:31 触发午饭话题，当天不重复"""
        self._now = _ts(h=11, mi=31)
        self.last_user[0] = self._now - 400
        msg = proactive.tick(self._now)
        self.assertIsNotNone(msg)
        self.assertIn("午饭", msg)
        msg2 = proactive.tick(self._now + 60)
        self.assertIsNone(msg2)

    def test_festival_fires_once(self):
        """信号5：10月1日国庆主动问候，当天一次"""
        self._now = _ts(mo=10, d=1, h=10)
        self.last_user[0] = self._now - 400
        msg = proactive.tick(self._now)
        self.assertIsNotNone(msg)
        self.assertIn("国庆", msg)
        self.assertIsNone(proactive.tick(self._now + 120))

    def test_birthday_from_profile(self):
        """信号5：画像重要日期命中今天 → 生日祝福"""
        self._now = _ts(mo=5, d=3, h=10)
        self.last_user[0] = self._now - 400
        self.profile = {"重要日期": [{"key": "生日", "value": "5月3日生日", "ts": self._now - 99999}]}
        msg = proactive.tick(self._now)
        self.assertIsNotNone(msg)
        self.assertIn("生日", msg)

    def test_emotion_low_after_silence(self):
        """信号4：低落后沉默3分钟 → 主动安抚（P3）"""
        self._now = _ts(h=15, mi=0)
        self.last_user[0] = self._now - 240   # 沉默4分钟（>3min，<5min 不触发闲聊）
        self.emo_log = [{"time": self._now - 240, "label": "low", "score": -0.6, "note": "情绪低落"}]
        msg = proactive.tick(self._now)
        self.assertIsNotNone(msg)
        self.assertEqual(self._log()[-1]["topic_type"], "emotion_care")

    def test_todo_stale_with_note(self):
        """信号3（P1）：提醒触发30分钟未确认 → 带位置备注再问"""
        self._now = _ts(h=14, mi=0)
        self.last_user[0] = self._now - 400
        self.awaiting_reminders = [{"id": "r1", "thing": "吃降压药",
                                    "note": "药在床头柜第二个抽屉",
                                    "time": self._now - 3600, "last_fired_at": self._now - 3600,
                                    "status": "fired"}]
        msg = proactive.tick(self._now)
        self.assertIsNotNone(msg)
        self.assertIn("吃降压药", msg)
        self.assertIn("床头柜", msg)
        self.assertEqual(self._log()[-1]["priority"], "P1")

    def test_health_followup_and_bug1_regression(self):
        """P2 健康跟进"好点了吗"；家人事件只标记、不追问（Bug① 回归）"""
        self._now = _ts(h=13, mi=0)
        self.last_user[0] = self._now - 120
        self.events = [
            {"type": "health", "content": "膝盖疼", "time": self._now - 120, "status": "active"},
        ]
        msg = proactive.tick(self._now)
        self.assertIsNotNone(msg)
        self.assertIn("好点了吗", msg)
        self.assertEqual(self.events[0]["status"], "cared")

        # 家人事件：不得套"好点了吗"
        setUp2_now = _ts(h=16, mi=0)
        self.last_user[0] = setUp2_now - 120
        self.events = [{"type": "family", "content": "儿子来看我了",
                        "time": setUp2_now - 120, "status": "active"}]
        st = self._state()
        st["last_proactive_ts"] = 0.0       # 清冷却，隔离测试
        st["awaiting_feedback"] = None
        proactive._save_state(st)
        self.assertIsNone(proactive.tick(setUp2_now))
        self.assertEqual(self.events[0]["status"], "noted")

    def test_medication_guess(self):
        """承继旧版：提过吃药且没设提醒 → 到点催药"""
        self._now = _ts(h=9, mi=10)
        self.last_user[0] = self._now - 400
        self.med["ts"] = self._now - 400
        self.pending_reminders = []
        msg = proactive.tick(self._now)
        self.assertIsNotNone(msg)
        self.assertIn("药", msg)

    def test_weather_provider_cross_health(self):
        """信号5（天气）：降温×膝盖怕凉 → 针对性叮嘱（P2）"""
        proactive._P["weather_provider"] = lambda: {"kind": "cold", "desc": "今儿降温了6度"}
        self._now = _ts(h=8, mi=30)
        self.last_user[0] = self._now - 400
        self.profile = {"健康": [{"key": "膝盖", "value": "膝盖怕凉", "ts": self._now - 99999}]}
        msg = proactive.tick(self._now)
        self.assertIsNotNone(msg)
        self.assertIn("膝盖", msg)
        self.assertEqual(self._log()[-1]["priority"], "P2")


class TestGates(ProactiveTestBase):
    def _prime_silence(self):
        self.last_user[0] = self._now - 400
        self.profile = {"兴趣": [{"key": "戏", "value": "听戏", "ts": self._now - 9999}]}

    def test_quiet_hours_block(self):
        """闸1：22:00–08:00 不主动开口"""
        self._now = _ts(h=23, mi=30)
        self._prime_silence()
        self.assertIsNone(proactive.tick(self._now))

    def test_user_active_no_interrupt(self):
        """闸1：老人60秒内刚说过话 → 不插嘴"""
        self._now = _ts(h=13, mi=0)
        self.profile = {"兴趣": [{"key": "戏", "value": "听戏", "ts": 1}]}
        self.last_user[0] = self._now - 30
        self.assertIsNone(proactive.tick(self._now))

    def test_cooldown_blocks_p4_but_not_p2(self):
        """闸2：冷却30分钟内 P4 被压制；P2 健康关怀可破冷却"""
        self._now = _ts(h=13, mi=0)
        self._prime_silence()
        first = proactive.tick(self._now)          # P4 闲聊开口
        self.assertIsNotNone(first)

        # 冷却内：P4 不得再说
        self.assertIsNone(proactive.tick(self._now + 120))

        # 冷却内：健康事件可破闸（urgency 4 ≥ 冷却阈值 4）
        t2 = self._now + 180
        self.events = [{"type": "health", "content": "头晕",
                        "time": t2 - 120, "status": "active"}]
        msg = proactive.tick(t2)
        self.assertIsNotNone(msg)
        self.assertIn("好点了吗", msg)

    def test_daily_cap(self):
        """每日硬上限 8 次"""
        self._now = _ts(h=13, mi=0)
        self._prime_silence()
        st = self._state()
        st["day"] = "2026-10-04"
        st["fired_today"] = 8
        proactive._save_state(st)
        self.assertIsNone(proactive.tick(self._now))

    def test_topic_no_repeat_24h(self):
        """同一话题 24h 内不重复"""
        self._now = _ts(h=13, mi=0)
        self._prime_silence()
        self.assertIsNotNone(proactive.tick(self._now))
        # 冷却结束后同话题 freshness=0.3 → 动机 0.9 < 阈值，不开口
        st = self._state()
        st["awaiting_feedback"] = None
        proactive._save_state(st)
        self.assertIsNone(proactive.tick(self._now + 1900))


class TestFeedback(ProactiveTestBase):
    def _fire_once(self):
        self._now = _ts(h=13, mi=0)
        self.last_user[0] = self._now - 400
        self.profile = {"兴趣": [{"key": "戏", "value": "听戏", "ts": 1}]}
        msg = proactive.tick(self._now)
        self.assertIsNotNone(msg)
        return self._now

    def test_accepted_lowers_k(self):
        t0 = self._fire_once()
        proactive.note_user_activity("好呀，放一段听听", now=t0 + 30)
        st = self._state()
        self.assertAlmostEqual(st["cooldown_k"], 0.9)
        self.assertEqual(self._log()[-1]["reaction"], "accepted")

    def test_rejected_raises_k_and_disables_p4(self):
        t0 = self._fire_once()
        proactive.note_user_activity("行了行了，别唠叨了", now=t0 + 30)
        st = self._state()
        self.assertAlmostEqual(st["cooldown_k"], 1.5)
        self.assertEqual(st["disable_p4_day"], st["day"])
        # 当日 P4 关闭：冷却结束后也不得闲聊
        st["last_proactive_ts"] = 0.0
        st["topic_history"] = {}
        proactive._save_state(st)
        self.assertIsNone(proactive.tick(t0 + 2000))

    def test_fuse_silences_until_tomorrow(self):
        """"想静静"：当天熔断，第二天恢复"""
        t0 = self._fire_once()
        proactive.note_user_activity("我想静静", now=t0 + 20)
        st = self._state()
        st["last_proactive_ts"] = 0.0
        st["topic_history"] = {}
        proactive._save_state(st)
        self.assertIsNone(proactive.tick(t0 + 3600))          # 当天仍熔断
        tomorrow = _ts(d=5, h=10, mi=0)
        self.last_user[0] = tomorrow - 400
        self.assertIsNotNone(proactive.tick(tomorrow))        # 第二天恢复

    def test_ignored_twice_raises_threshold(self):
        """连续两次无视 → 动机阈值 +0.5"""
        t0 = self._fire_once()
        # 第一次 ignored：60s 窗口过后再 tick，且期间用户没说话
        proactive.tick(t0 + 120)
        self.assertEqual(self._log()[-1]["reaction"], "ignored")
        st = self._state()
        self.assertEqual(st["ignored_streak"], 1)
        # 制造第二次开口机会并再次无视
        st["last_proactive_ts"] = 0.0
        st["topic_history"] = {}
        st["motivation_threshold"] = 3.0
        proactive._save_state(st)
        t1 = t0 + 4000
        self.last_user[0] = t1 - 400
        self.assertIsNotNone(proactive.tick(t1))
        proactive.tick(t1 + 120)
        self.assertAlmostEqual(self._state()["motivation_threshold"], 3.5)


class TestGeneration(ProactiveTestBase):
    def test_llm_phrase_used_and_logged(self):
        """LLM 在线时用现场生成的话术（不写死）"""
        self.llm_reply[0] = "张奶奶，您那段《贵妃醉酒》还想听不？我给您找着了。"
        self._now = _ts(h=13, mi=0)
        self.last_user[0] = self._now - 400
        self.profile = {"兴趣": [{"key": "戏", "value": "听《贵妃醉酒》", "ts": 1}]}
        msg = proactive.tick(self._now)
        self.assertEqual(msg, "张奶奶，您那段《贵妃醉酒》还想听不？我给您找着了。")

    def test_safety_filter_replaces(self):
        """主动话术与被动回复过同一道安全过滤"""
        proactive._safety = lambda text, persona, call: {
            "safe": False, "reason": "测试拦截", "safe_reply": f"{call}，咱换个话题吧。"}
        self.llm_reply[0] = "张奶奶，降压药别吃了。"
        self._now = _ts(h=13, mi=0)
        self.last_user[0] = self._now - 400
        self.profile = {"兴趣": [{"key": "戏", "value": "听戏", "ts": 1}]}
        msg = proactive.tick(self._now)
        self.assertEqual(msg, "张奶奶，咱换个话题吧。")

    def test_stats(self):
        t0 = self._now = _ts(h=13, mi=0)
        self.last_user[0] = t0 - 400
        self.profile = {"兴趣": [{"key": "戏", "value": "听戏", "ts": 1}]}
        proactive.tick(t0)
        proactive.note_user_activity("好啊", now=t0 + 10)
        s = proactive.get_stats(t0 + 20)
        self.assertEqual(s["fired_total"], 1)
        self.assertEqual(s["accepted"], 1)
        self.assertEqual(s["acceptance_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()

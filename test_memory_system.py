# -*- coding: utf-8 -*-
"""memory_system 单元测试：python -m unittest test_memory_system -v
纯标准库（memory_system 不依赖 torch/网络），可离线跑。"""
import json
import math
import os
import shutil
import tempfile
import threading
import time
import unittest

from memory_system import (
    MemoryBank, keyword_extract, similarity, retrievability,
    parse_llm_facts, parse_json_strings, classify_confirm_reply,
    humanize_ago, _strength_days, _reinforce,
)


class FakeClock:
    """可推进的假时钟（评测/单测共用）"""

    def __init__(self, start=None):
        self.t = start if start is not None else time.time()

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class ScriptedLLM:
    """按调用次序返回预设文本的假 LLM；记录收到的 prompt"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else "[]"


FACT_GRANDSON = json.dumps([
    {"field": "家庭", "key": "孙子的名字", "value": "豆豆", "quote": "我孙子叫豆豆",
     "importance": 7, "confidence": 0.9},
], ensure_ascii=False)

FACT_MED = json.dumps([
    {"field": "健康", "key": "慢性病", "value": "高血压", "quote": "我有高血压",
     "importance": 8, "confidence": 0.95},
], ensure_ascii=False)


class TmpBank(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="memtest_")
        self.path = os.path.join(self.dir, "memory_bank.json")
        self.clock = FakeClock()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def make(self, llm=None, **kw):
        return MemoryBank(self.path, llm=llm, clock=self.clock, **kw)


class TestParsing(unittest.TestCase):
    def test_fenced_json(self):
        txt = "```json\n[{\"field\":\"家庭\",\"key\":\"孙子\",\"value\":\"豆豆\",\"quote\":\"x\",\"importance\":7,\"confidence\":0.9}]\n```"
        facts = parse_llm_facts(txt)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["value"], "豆豆")

    def test_dict_wrapped_and_chinese_keys(self):
        txt = '{"facts": [{"类别": "家人", "字段名": "孙子的名字", "值": "豆豆", "原话": "x", "重要性": 7, "置信度": 0.9}]}'
        facts = parse_llm_facts(txt)
        self.assertEqual(facts[0]["field"], "家庭")   # 别名归一
        self.assertEqual(facts[0]["key"], "孙子")     # 去掉"的名字"
        self.assertEqual(facts[0]["value"], "豆豆")

    def test_junk_around_and_bad_items_skipped(self):
        txt = '好的，抽取结果如下：[{"field":"健康","key":"症状","value":"膝盖疼","importance":5},{"field":"健康","value":"","quote":"无"}] 以上。'
        facts = parse_llm_facts(txt)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["value"], "膝盖疼")
        self.assertEqual(facts[0]["confidence"], 0.8)  # 缺省置信度

    def test_importance_clamped(self):
        facts = parse_llm_facts('[{"field":"健康","key":"k","value":"v","importance":99,"confidence":2}]')
        self.assertEqual(facts[0]["importance"], 10)
        self.assertEqual(facts[0]["confidence"], 1.0)

    def test_empty_outputs(self):
        for txt in ("[]", "没有可抽取的信息", "", '{"facts": []}'):
            self.assertEqual(parse_llm_facts(txt), [])

    def test_parse_json_strings(self):
        self.assertEqual(parse_json_strings('["张奶奶常念叨孙子豆豆"]'),
                         ["张奶奶常念叨孙子豆豆"])
        self.assertEqual(parse_json_strings("思考中... [\"a\", \"b\"] 完毕"), ["a", "b"])
        self.assertEqual(parse_json_strings("[]"), [])


class TestObserveAndFacts(TmpBank):
    def test_llm_extraction_creates_fact_and_episode(self):
        llm = ScriptedLLM([FACT_GRANDSON, "[]"])   # 第2次是反思尝试
        bank = self.make(llm=llm)
        bank.observe("我孙子叫豆豆，在读初中。", "豆豆上初中啦？")
        self.assertEqual(len(bank.facts), 1)
        f = bank.facts[0]
        self.assertEqual((f["field"], f["key"], f["value"]), ("家庭", "孙子", "豆豆"))
        self.assertEqual(len(bank.episodes), 1)
        self.assertIn("豆豆", bank.episodes[0]["text"])
        # 持久化落盘
        self.assertTrue(os.path.exists(self.path))

    def test_conflict_recent_overrides_old(self):
        llm = ScriptedLLM([
            FACT_GRANDSON,
            json.dumps([{"field": "家庭", "key": "孙子", "value": "豆豆豆",
                         "quote": "叫豆豆豆", "importance": 7, "confidence": 0.9}],
                       ensure_ascii=False),
            "[]",
        ])
        bank = self.make(llm=llm)
        bank.observe("我孙子叫豆豆。", "")
        self.clock.advance(3600)
        bank.observe("不对，我孙子叫豆豆豆。", "")
        self.assertEqual(len(bank.facts), 1)              # 同键合并，不重复
        f = bank.facts[0]
        self.assertEqual(f["value"], "豆豆豆")            # 近期覆盖远期
        self.assertEqual(f["history"][0]["value"], "豆豆")  # 旧值带时间戳入历史
        self.assertGreater(f["ts"], f["history"][0]["ts"])

    def test_remention_reinforces(self):
        llm = ScriptedLLM([FACT_GRANDSON, FACT_GRANDSON, "[]"])
        bank = self.make(llm=llm)
        bank.observe("我孙子叫豆豆。", "")
        s0, conf0 = bank.facts[0]["strength"], bank.facts[0]["confidence"]
        self.clock.advance(7200)
        bank.observe("豆豆这孩子，就是我孙子。叫豆豆。", "")
        f = bank.facts[0]
        self.assertEqual(len(bank.facts), 1)
        self.assertGreater(f["strength"], s0)      # 遗忘曲线强度增长
        self.assertGreaterEqual(f["confidence"], conf0)
        self.assertGreater(f["recall_count"], 0)

    def test_smalltalk_no_memory(self):
        llm = ScriptedLLM(["[]"])
        bank = self.make(llm=llm)
        bank.observe("今天天气不错啊。", "是呀。")
        self.assertEqual(bank.facts, [])
        self.assertEqual(bank.episodes, [])

    def test_keyword_fallback_without_llm(self):
        bank = self.make(llm=None)
        bank.observe("我孙子叫豆豆，每天吃降压药，就是膝盖有点疼。", "")
        values = {(f["field"], f["key"]) for f in bank.facts}
        self.assertIn(("家庭", "孙子"), values)
        self.assertIn(("用药", "降压药"), values)          # 键=匹配词（非"常用药"）
        self.assertIn(("健康", "膝盖有点疼"), values)      # 允许"有点"间隔
        bank2 = self.make(llm=None)
        bank2.observe("我孙女叫小雨。", "")
        sw = next(f for f in bank2.facts if f["key"] == "孙女")
        self.assertEqual(sw["value"], "小雨")

    def test_keyword_multi_symptom_no_clobber(self):
        bank = self.make(llm=None)
        bank.observe("血压高，膝盖也有点疼。", "")
        bank.observe("腰又疼了。", "")
        keys = {(f["field"], f["key"]) for f in bank.facts}
        self.assertIn(("健康", "血压高"), keys)
        self.assertIn(("健康", "膝盖也有点疼"), keys)   # 带助词整句匹配
        self.assertIn(("健康", "腰又疼"), keys)          # 同类多条共存，不互相覆盖

    def test_llm_failure_degrades_to_keyword(self):
        class BoomLLM:
            def __call__(self, prompt):
                raise RuntimeError("API down")
        bank = self.make(llm=BoomLLM())
        bank.observe("我孙子叫豆豆。", "")
        self.assertEqual(bank.facts[0]["value"], "豆豆")

    def test_per_turn_cap(self):
        many = json.dumps([
            {"field": "其他", "key": f"k{i}", "value": f"v{i}", "importance": 3, "confidence": 0.8}
            for i in range(8)], ensure_ascii=False)
        bank = self.make(llm=ScriptedLLM([many, "[]"]))
        bank.observe("说了很多事", "")
        self.assertEqual(len(bank.facts), 3)   # 每轮最多3条


class TestRetrievalAndForgetting(TmpBank):
    def _fill(self, bank):
        bank._add_fact(field="家庭", key="孙子", value="豆豆", quote="",
                       importance=7, confidence=0.9, now=self.clock())
        bank._add_fact(field="兴趣", key="爱好", value="养花", quote="",
                       importance=4, confidence=0.8, now=self.clock())
        bank._add_fact(field="饮食", key="忌口", value="不吃辣", quote="",
                       importance=5, confidence=0.8, now=self.clock())

    def test_relevance_dominates_for_matching_query(self):
        bank = self.make()
        self._fill(bank)
        top = bank.retrieve("跟我孙子豆豆有关的事", k=1, now=self.clock())
        self.assertEqual(top[0]["key"], "孙子")

    def test_retrieval_reinforces(self):
        bank = self.make()
        self._fill(bank)
        s0 = bank.facts[0]["strength"]
        bank.retrieve("孙子豆豆", k=1, now=self.clock(), reinforce=True)
        self.assertGreater(bank.facts[0]["strength"], s0)
        self.assertEqual(bank.facts[0]["recall_count"], 1)

    def test_forgetting_curve_gates_old_trivial_episodes(self):
        bank = self.make()
        bank._add_fact(field="其他", key="电视", value="看了个电视剧", quote="",
                       importance=2, confidence=0.8, now=self.clock())
        fact = bank.facts[0]
        # imp=2 事实级强度 2 天：4天后 R=e^(-2)≈0.14 < 0.2 -> 淡忘
        self.clock.advance(4 * 86400)
        self.assertLess(retrievability(fact, self.clock()), 0.2)
        snap = bank.profile_snapshot()
        self.assertNotIn("其他", snap)
        # 重提一次即复活（ts 刷新）
        bank._add_fact(field="其他", key="电视", value="看了个电视剧", quote="",
                       importance=2, confidence=0.8, now=self.clock())
        self.clock.advance(3600)
        snap = bank.profile_snapshot()
        self.assertEqual(snap["其他"][0]["value"], "看了个电视剧")

    def test_important_fact_survives_longer(self):
        bank = self.make()
        bank._add_fact(field="家庭", key="孙子", value="豆豆", quote="",
                       importance=7, confidence=0.9, now=self.clock())
        fact = bank.facts[0]
        self.assertEqual(_strength_days(7, is_fact=True), 50.0)
        self.clock.advance(30 * 86400)
        self.assertGreater(retrievability(fact, self.clock()), 0.2)   # e^(-30/50)≈0.55

    def test_repeated_recall_makes_memory_permanent(self):
        mem = {"ts": 0.0, "strength": 1.0, "recall_count": 0, "last_recall": 0.0}
        now = 0.0
        for _ in range(12):                       # 反复回忆强化
            _reinforce(mem, now)
            now += 30 * 86400
        self.assertGreater(mem["strength"], 300)   # 一年后 R 仍接近 1
        self.assertGreater(retrievability(mem, now), 0.9)

    def test_build_chat_context_format(self):
        llm = ScriptedLLM([FACT_GRANDSON, "[]"])
        bank = self.make(llm=llm)
        bank.observe("我孙子叫豆豆。", "")
        ctx = bank.build_chat_context("孙子最近怎么样", now=self.clock())
        self.assertIn("孙子：豆豆", ctx)
        self.assertIn("自然地用", ctx)

    def test_build_chat_context_empty(self):
        bank = self.make()
        self.assertEqual(bank.build_chat_context("你好", now=self.clock()), "")


class TestReflection(TmpBank):
    def test_reflection_triggered_by_importance_sum(self):
        llm = ScriptedLLM([
            FACT_GRANDSON,      # day1 抽取
            FACT_MED,           # day2 抽取
            FACT_GRANDSON,      # day3 抽取
            '["张奶奶最近常念叨孙子豆豆"]',   # 反思
        ])
        bank = self.make(llm=llm)
        for _ in range(3):
            bank.observe("我孙子叫豆豆。", "")
            self.clock.advance(86400)
        self.assertEqual(len(bank.reflections), 1)
        self.assertEqual(bank.reflections[0]["text"], "张奶奶最近常念叨孙子豆豆")
        # 反思进入检索池
        top = bank.retrieve("小忆对张奶奶的理解", k=5, now=self.clock())
        self.assertTrue(any(r.get("text") == "张奶奶最近常念叨孙子豆豆" for r in top))

    def test_no_reflection_below_threshold(self):
        llm = ScriptedLLM([FACT_GRANDSON])
        bank = self.make(llm=llm)
        bank.observe("我孙子叫豆豆。", "")     # imp 7 < 15 且 cnt 1 < 3
        self.assertEqual(bank.reflections, [])

    def test_no_llm_no_reflection(self):
        bank = self.make(llm=None)
        for _ in range(5):
            bank.observe("我孙子叫豆豆。", "")
        self.assertEqual(bank.reflections, [])


class TestConfirmation(TmpBank):
    def test_low_confidence_enqueued_and_asked(self):
        low = json.dumps([{"field": "家庭", "key": "重孙子", "value": "小石头",
                           "quote": "大概叫小石头？", "importance": 5, "confidence": 0.4}],
                         ensure_ascii=False)
        llm = ScriptedLLM([low, "[]"])
        bank = self.make(llm=llm)
        bank.observe("重孙子好像叫小石头来着。", "")
        q = bank.pop_confirm_question(now=self.clock())
        self.assertIsNotNone(q)
        self.assertIn("小石头", q)

    def _ask_then_reply(self, reply):
        low = json.dumps([{"field": "家庭", "key": "重孙子", "value": "小石头",
                           "quote": "x", "importance": 5, "confidence": 0.4}],
                         ensure_ascii=False)
        bank = self.make(llm=ScriptedLLM([low, "[]"]))
        bank.observe("重孙子好像叫小石头来着。", "")
        bank.pop_confirm_question(now=self.clock())
        self.clock.advance(60)
        return bank, bank.resolve_confirm_reply(reply, now=self.clock())

    def test_yes_flow(self):
        bank, reply = self._ask_then_reply("对。")
        self.assertIsNotNone(reply)
        self.assertEqual(bank.facts[0]["confidence"], 1.0)

    def test_no_with_new_value_updates_fact(self):
        low = json.dumps([{"field": "家庭", "key": "重孙子", "value": "小石头",
                          "quote": "x", "importance": 5, "confidence": 0.4}],
                         ensure_ascii=False)
        bank = self.make(llm=ScriptedLLM([low, "[]"]))
        bank.observe("重孙子好像叫小石头。", "")
        bank.pop_confirm_question(now=self.clock())
        self.clock.advance(60)
        reply = bank.resolve_confirm_reply("不是，叫小石头头", now=self.clock())
        self.assertIsNotNone(reply)
        self.assertIn("小石头头", reply)                      # 复述新值，让老人放心
        self.assertEqual(bank.facts[0]["value"], "小石头头")  # 新值覆盖
        self.assertEqual(bank.facts[0]["confidence"], 0.9)
        self.assertEqual(bank.facts[0]["history"][0]["value"], "小石头")  # 旧值入历史

    def test_unrelated_reply_not_intercepted(self):
        bank, reply = self._ask_then_reply("今天天气怎么样啊？")
        self.assertIsNone(reply)

    def test_expired_confirmation_not_intercepted(self):
        low = json.dumps([{"field": "家庭", "key": "重孙子", "value": "小石头",
                           "quote": "x", "importance": 5, "confidence": 0.4}],
                         ensure_ascii=False)
        bank = self.make(llm=ScriptedLLM([low, "[]"]))
        bank.observe("重孙子好像叫小石头。", "")
        bank.pop_confirm_question(now=self.clock())
        self.clock.advance(600)   # 超过 CONFIRM_TTL=300s
        self.assertIsNone(bank.resolve_confirm_reply("对", now=self.clock()))

    def test_global_cooldown(self):
        low1 = json.dumps([{"field": "家庭", "key": "重孙子", "value": "小石头",
                            "quote": "x", "importance": 5, "confidence": 0.4}], ensure_ascii=False)
        low2 = json.dumps([{"field": "健康", "key": "症状", "value": "膝盖疼",
                            "quote": "x", "importance": 5, "confidence": 0.4}], ensure_ascii=False)
        bank = self.make(llm=ScriptedLLM([low1, low2, "[]"]))
        bank.observe("重孙子好像叫小石头。", "")
        self.clock.advance(60)
        bank.observe("不知道是不是膝盖疼。", "")
        q1 = bank.pop_confirm_question(now=self.clock())
        self.assertIsNotNone(q1)
        q2 = bank.pop_confirm_question(now=self.clock())   # 30分钟冷却内
        self.assertIsNone(q2)
        self.clock.advance(1801)
        q3 = bank.pop_confirm_question(now=self.clock())
        self.assertIsNotNone(q3)

    def test_max_two_asks_per_fact(self):
        low = json.dumps([{"field": "家庭", "key": "重孙子", "value": "小石头",
                           "quote": "x", "importance": 5, "confidence": 0.4}],
                         ensure_ascii=False)
        bank = self.make(llm=ScriptedLLM([low, "[]"]))
        bank.observe("重孙子好像叫小石头。", "")
        for i in range(3):
            self.clock.advance(2000)
            bank.pending_confirms = [{"fact_id": bank.facts[0]["id"], "ts": self.clock()}]
            q = bank.pop_confirm_question(now=self.clock())
            bank.resolve_confirm_reply("随便说点别的", now=self.clock())   # 不相关：不确认
        # ask_count 已达上限，不再问
        self.assertIsNone(bank.pop_confirm_question(now=self.clock()))


class TestPersistence(TmpBank):
    def test_roundtrip(self):
        llm = ScriptedLLM([FACT_GRANDSON, FACT_MED, FACT_GRANDSON,
                           '["张奶奶常念叨孙子豆豆"]'])
        bank = self.make(llm=llm)
        bank.observe("我孙子叫豆豆。", "")
        self.clock.advance(86400)
        bank.observe("我有高血压。", "")
        self.clock.advance(86400)
        bank.observe("豆豆就是我孙子。", "")     # imp 累计 22 且 cnt=3 -> 触发反思
        n_f, n_e = len(bank.facts), len(bank.episodes)
        self.assertEqual(len(bank.reflections), 1)

        bank2 = self.make(llm=None)   # 重新加载
        self.assertEqual(len(bank2.facts), n_f)
        self.assertEqual(len(bank2.episodes), n_e)
        self.assertEqual(len(bank2.reflections), 1)
        self.assertEqual(bank2.facts[0]["value"], "豆豆")

    def test_legacy_events_import(self):
        legacy = os.path.join(self.dir, "memory_events.json")
        now = self.clock()
        events = [
            {"type": "health", "content": "膝盖疼", "time": now - 100, "status": "active"},
            {"type": "medication", "content": "降压药", "time": now - 200, "status": "active"},
            {"type": "health", "content": "过期事件", "time": now - 10 * 86400, "status": "active"},
        ]
        with open(legacy, "w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False)
        bank = self.make(llm=None)
        n = bank.import_legacy_events(legacy)
        self.assertEqual(n, 2)   # 7天外的丢弃
        self.assertEqual(len(bank.episodes), 2)
        self.assertEqual(bank.episodes[0]["field"], "健康")
        # 幂等：重启后（新实例重新加载）再次导入不叠加
        bank2 = self.make(llm=None)
        n2 = bank2.import_legacy_events(legacy)
        self.assertEqual(n2, 0)
        self.assertEqual(len(bank2.episodes), 2)

    def test_thread_safety_smoke(self):
        bank = self.make(llm=None)
        errs = []

        def worker(i):
            try:
                for j in range(10):
                    bank.observe(f"我孙子叫豆豆{j}", "")
                    bank.build_chat_context("孙子")
            except Exception as e:
                errs.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errs, [])
        self.assertEqual(len(bank.facts), 1)   # 同键合并不竞态
        bank2 = self.make(llm=None)
        self.assertEqual(len(bank2.episodes), 40)


class TestPanelAndStats(TmpBank):
    def test_panel_html_contains_profile(self):
        llm = ScriptedLLM([FACT_GRANDSON, "[]"])
        bank = self.make(llm=llm)
        bank.observe("我孙子叫豆豆。", "")
        html = bank.panel_html(now=self.clock())
        self.assertIn("豆豆", html)
        self.assertIn("最近记下的", html)
        st = bank.stats(now=self.clock())
        self.assertEqual(st["facts"], 1)

    def test_panel_empty_state(self):
        bank = self.make(llm=None)
        html = bank.panel_html(now=self.clock())
        self.assertIn("多陪老人聊聊", html)

    def test_low_confidence_marked_in_panel(self):
        low = json.dumps([{"field": "家庭", "key": "重孙子", "value": "小石头",
                           "quote": "x", "importance": 5, "confidence": 0.4}],
                         ensure_ascii=False)
        bank = self.make(llm=ScriptedLLM([low, "[]"]))
        bank.observe("重孙子好像叫小石头。", "")
        self.assertIn("待确认", bank.panel_html(now=self.clock()))


class TestHelpers(unittest.TestCase):
    def test_similarity(self):
        self.assertGreater(similarity("我孙子叫豆豆", "孙子豆豆"), 0.3)
        self.assertEqual(similarity("完全", ""), 0.0)
        self.assertLess(similarity("膝盖疼", "股票行情"), 0.1)

    def test_humanize_ago(self):
        now = 1000000.0
        self.assertEqual(humanize_ago(now - 30, now), "刚刚")
        self.assertEqual(humanize_ago(now - 300, now), "5分钟前")
        self.assertEqual(humanize_ago(now - 7200, now), "2小时前")
        self.assertEqual(humanize_ago(now - 3 * 86400, now), "3天前")

    def test_classify_confirm(self):
        self.assertEqual(classify_confirm_reply("对"), ("yes", None))
        self.assertEqual(classify_confirm_reply("嗯嗯，没错"), ("yes", None))
        act, val = classify_confirm_reply("不是，叫小明")
        self.assertEqual(act, "no")
        self.assertEqual(val, "小明")
        self.assertEqual(classify_confirm_reply("今天天气怎么样啊"), (None, None))
        self.assertEqual(classify_confirm_reply("很长的回答" * 20), (None, None))


if __name__ == "__main__":
    unittest.main(verbosity=2)

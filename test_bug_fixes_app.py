# -*- coding: utf-8 -*-
"""
第一优先「6 个 Bug」的 app 级行为验证
====================================
桩掉 torch / transformers / gradio 三个重依赖，导入**真实的 app.py**，
按《任务清单》里的原始故障场景逐条行为验证：

  Bug① 乱问"儿子好点了吗"   —— check_active_trigger 按事件类型分流
  Bug② 健康关键词漏识别      —— app._keyword_extract 走症状变体表
  Bug③ "你还不提醒我"被误判  —— 抱怨优先于待确认答复，道歉+补提醒，不设新提醒
  Bug④ 到点提醒念整句        —— 超长事项拆"主事项+备注"，到点只念主事项
  Bug⑤ 记忆答错还编造        —— 值溯源校验 / 位置键归一覆盖 / 原话可溯源
  Bug⑥ 回复慢               —— 每轮回复不调 empty_cache；意图解析 DeepSeek 优先

运行：python test_bug_fixes_app.py
测试期间 chdir 到临时目录，app.py 的相对路径数据文件全部落在临时目录，
绝不触碰项目里的真实数据；DeepSeek keys 置空，全程不联网。
"""
import os
import sys
import time
import tempfile
import unittest
from unittest import mock

PROJECT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT)

TMP = tempfile.mkdtemp(prefix="xiaoyi_bugtest_")
os.chdir(TMP)  # 必须在 import app 之前：数据文件相对路径全部落到临时目录

# ---------- 桩掉重依赖（不加载 3B 模型、不起界面，只验证逻辑） ----------
torch_mock = mock.MagicMock(name="torch")
torch_mock.cuda.is_available.return_value = False
sys.modules["torch"] = torch_mock

transformers_mock = mock.MagicMock(name="transformers")
model_mock = mock.MagicMock(name="local_model")
tokenizer_mock = mock.MagicMock(name="tokenizer")
tokenizer_mock.pad_token = None
# 让本地生成路径走通：decode 返回真实字符串（模拟 3B 成功回复）
tokenizer_mock.decode.return_value = "奶奶，我听着呢，您说。"
transformers_mock.AutoModelForCausalLM.from_pretrained.return_value = model_mock
transformers_mock.AutoTokenizer.from_pretrained.return_value = tokenizer_mock
sys.modules["transformers"] = transformers_mock

gradio_mock = mock.MagicMock(name="gradio")
sys.modules["gradio"] = gradio_mock

import app  # noqa: E402  —— 导入真实 app.py（模型/界面均被桩）

app.DEEPSEEK_API_KEYS = []            # 全程不联网
app.needs_search = lambda _t: False   # 聊天路径不触发联网搜索


def cancel_all_reminders():
    for r in app.reminder_store.pending():
        app.reminder_store.cancel(r["id"])


def age_events(secs=120):
    """把关键词事件的时间拨早，模拟"过了一会儿"主动关怀触发窗口"""
    for e in app.user_memory["events"]:
        e["time"] -= secs


# ---------------------------------------------------------------- Bug①
class TestBug1ProactiveCare(unittest.TestCase):
    """只有健康/用药类才能问"好点了吗"；家人/习惯/物品类一律不准"""

    def setUp(self):
        cancel_all_reminders()
        app.user_memory["events"] = []
        app.user_memory["last_medication_time"] = 0.0
        app.user_memory["call_name"] = "张奶奶"

    def test_family_visit_never_asks_haodianleme(self):
        """原始故障：老人说"我儿子来看我了"→ 小忆问"儿子好点了吗" """
        app.extract_memory("我儿子来看我了")
        self.assertEqual(app.user_memory["events"][0]["type"], "family")
        age_events()
        msg = app.check_active_trigger()
        self.assertFalse(msg and "好点了吗" in msg, f"家人类事件被套用了模板: {msg}")
        self.assertEqual(app.user_memory["events"][0]["status"], "noted")

    def test_habit_and_item_never_ask(self):
        for said in ("我今天去公园散步了", "我老花镜找不到了"):
            self.setUp()
            app.extract_memory(said)
            age_events()
            msg = app.check_active_trigger()
            self.assertFalse(msg and "好点了吗" in msg, f"{said} -> {msg}")

    def test_health_asks_haodianleme(self):
        app.extract_memory("我今天头有点疼")
        age_events()
        msg = app.check_active_trigger()
        self.assertIsNotNone(msg)
        self.assertIn("好点了吗", msg)
        self.assertIn("头疼", msg)

    def test_medication_asks_haodianleme(self):
        app.extract_memory("我今天的降压药还没吃")
        age_events()
        msg = app.check_active_trigger()
        self.assertIsNotNone(msg)
        self.assertIn("好点了吗", msg)

    def test_emotion_uses_empathy_not_template(self):
        app.extract_memory("我心里有点难过")
        age_events()
        msg = app.check_active_trigger()
        self.assertIsNotNone(msg)
        self.assertNotIn("好点了吗", msg)
        self.assertTrue("心里" in msg or "惦记" in msg, msg)


# ---------------------------------------------------------------- Bug②
class TestBug2SymptomVariants(unittest.TestCase):
    """头痛/头昏/偏头风等变体都要触发健康事件（归一到规范名）"""

    def setUp(self):
        app.user_memory["events"] = []
        app.user_memory["last_medication_time"] = 0.0
        app.user_memory["call_name"] = "张奶奶"

    def test_variants_recognized(self):
        cases = [("我前几天都说头痛了", "头疼"), ("我今天头昏沉", "头疼"),
                 ("老毛病偏头风又犯了", "头疼"), ("有点眩晕", "头晕"),
                 ("心口闷得慌", "胸闷"), ("膝盖痛", "膝盖疼")]
        for said, canon in cases:
            app.user_memory["events"] = []
            events = app.extract_memory(said)
            health = [e for e in events if e["type"] == "health"]
            self.assertTrue(health, f"未识别: {said}")
            self.assertEqual(health[0]["content"], canon, said)

    def test_variant_triggers_proactive_followup(self):
        """原始故障：'我前几天都说头痛了'小忆没反应 -> 现在必须能健康回访"""
        app.extract_memory("我前几天都说头痛了")
        age_events()
        msg = app.check_active_trigger()
        self.assertIsNotNone(msg, "头痛变体没有触发健康回访")
        self.assertIn("好点了吗", msg)


# ---------------------------------------------------------------- Bug③
class TestBug3ComplaintNotSetReminder(unittest.TestCase):
    """"你还不提醒我"是抱怨不是设提醒：道歉+补提醒，绝不反问时间"""

    def setUp(self):
        cancel_all_reminders()
        app.pending_reminder["kind"] = None
        app.user_memory["call_name"] = "张奶奶"
        app.UI_STATE["personality"] = "踏实务实"

    def test_complaint_variants_apologize_and_never_ask_time(self):
        for said in ("你还不提醒我", "你怎么不提醒我吃药", "你为啥没提醒我",
                     "说好了要提醒我吃药的", "你怎么还不提醒我"):
            self.setUp()
            reply = app.try_handle_reminder(said)
            self.assertIsNotNone(reply, f"抱怨被放行了: {said}")
            self.assertNotIn("什么时候提醒您", reply, said)
            self.assertTrue(any(w in reply for w in ("对不起", "对不住", "疏忽", "没记住", "没记牢")),
                            f"没有道歉: {said} -> {reply}")
            self.assertEqual(app.reminder_store.pending(), [], f"抱怨被误设了新提醒: {said}")

    def test_complaint_with_overdue_rereminds_immediately(self):
        """有已触发未确认的提醒：道歉 + 3秒内补响（snooze 到 3 秒）"""
        r = app.reminder_store.add("吃药", time.time() + 60)
        app.reminder_store.mark_fired(r["id"], time.time())
        reply = app.try_handle_reminder("你还不提醒我")
        self.assertIn("对不住", reply)
        self.assertIn("吃药", reply)

    def test_normal_set_unaffected(self):
        reply = app.try_handle_reminder("五分钟后提醒我喝水")
        pend = app.reminder_store.pending()
        self.assertEqual(len(pend), 1)
        self.assertIn("喝水", pend[0]["thing"])
        self.assertIn("提醒您", reply)

    def test_legit_later_reply_is_not_complaint(self):
        """到点待确认时答"还没吃呢"是顺延，不是抱怨（防误伤回归）"""
        r = app.reminder_store.add("吃药", time.time() + 60)
        app.reminder_store.mark_fired(r["id"], time.time())
        reply = app.try_handle_reminder("还没吃呢")
        self.assertIn("10分钟", reply)

    def test_complaint_beats_confirmation_interception(self):
        """Bug③残留修复：待确认状态下"你怎么还没提醒我"必须走抱怨通道
        （含"还没"，旧代码会被 match_confirmation 误判为'等会儿'顺延10分钟）"""
        r = app.reminder_store.add("吃药", time.time() + 60)
        app.reminder_store.mark_fired(r["id"], time.time())
        reply = app.try_handle_reminder("你怎么还没提醒我吃药")
        self.assertNotIn("过10分钟", reply, "抱怨被误判为顺延")
        self.assertTrue(any(w in reply for w in ("对不起", "对不住", "疏忽")), reply)


# ---------------------------------------------------------------- Bug④
class TestBug4SplitThingNote(unittest.TestCase):
    """超长事项拆"主事项+备注"：到点只念主事项，备注放括号"""

    def setUp(self):
        cancel_all_reminders()
        app.pending_reminder["kind"] = None
        app.user_memory["call_name"] = "张奶奶"
        app.UI_STATE["personality"] = "踏实务实"

    def test_long_thing_split_at_set_time(self):
        """原始故障：到点念出"到点啦——我的降压药放在门口的抽屉里了啊后吃药" """
        reply = app.try_handle_reminder("半小时后提醒我吃药，我的降压药放在门口的抽屉里了啊")
        self.assertIsNotNone(reply)
        pend = app.reminder_store.pending()
        self.assertEqual(len(pend), 1)
        r = pend[0]
        self.assertLessEqual(len(r["thing"]), 15, r["thing"])
        self.assertIn("吃药", r["thing"])
        self.assertIn("抽屉", r["note"])

    def test_trigger_speaks_main_only(self):
        app.try_handle_reminder("半小时后提醒我吃药，我的降压药放在门口的抽屉里了啊")
        r = app.reminder_store.pending()[0]
        for persona in ("踏实务实", "风趣幽默", "暖心知心"):
            msg = app.trigger_message(persona, "张奶奶", r["thing"])
            self.assertIn("吃药", msg)
            self.assertNotIn("抽屉", msg, f"{persona} 到点播报泄漏了备注")
            self.assertNotIn("备注", msg)

    def test_note_shown_in_panel_brackets(self):
        app.try_handle_reminder("半小时后提醒我吃药，我的降压药放在门口的抽屉里了啊")
        r = app.reminder_store.pending()[0]
        desc = app.describe_reminder(r)
        self.assertIn("（备注：", desc)
        self.assertIn("抽屉", desc)

    def test_split_unit_and_short_untouched(self):
        main, note = app.split_thing_note("我的降压药放在门口的抽屉里了啊后吃药")
        self.assertLessEqual(len(main), 15)
        main2, note2 = app.split_thing_note("吃降压药")
        self.assertEqual((main2, note2), ("吃降压药", ""))


# ---------------------------------------------------------------- Bug⑤
class TestBug5MemoryGrounding(unittest.TestCase):
    """记忆治编造：值必须在原话可溯源；位置类一键覆盖；原话留存"""

    def setUp(self):
        mb = app.memory_bank
        mb.facts, mb.episodes, mb.reflections = [], [], []
        mb.pending_confirms, mb.active_confirm = [], None
        mb._reflect_imp = mb._reflect_cnt = 0

    def test_fabricated_value_dropped(self):
        """原始故障：老人说降压药放门口抽屉，小忆记成"床头柜透明塑料盒" """
        app.memory_bank.llm = lambda _p: (
            '[{"field":"物品","key":"降压药位置","value":"床头柜透明塑料盒",'
            '"quote":"","importance":6,"confidence":0.9}]')
        app.memory_bank.observe("我的降压药放在门口的抽屉里")
        self.assertEqual(app.memory_bank.facts, [], "编造值没有被丢弃")

    def test_grounded_value_stored_with_quote(self):
        app.memory_bank.llm = lambda _p: (
            '[{"field":"物品","key":"降压药位置","value":"门口的抽屉里",'
            '"quote":"","importance":6,"confidence":0.9}]')
        app.memory_bank.observe("我的降压药放在门口的抽屉里")
        self.assertEqual(len(app.memory_bank.facts), 1)
        f = app.memory_bank.facts[0]
        self.assertEqual(f["value"], "门口的抽屉里")
        self.assertTrue(f["quotes"], "原话片段没有留存")
        self.assertIn("抽屉", f["quotes"][0])

    def test_location_key_unified_and_overwritten(self):
        """"降压药放哪/存放位置"归一到同一键，新位置覆盖旧位置、旧值入历史"""
        app.memory_bank.llm = lambda _p: (
            '[{"field":"物品","key":"降压药放哪","value":"门口的抽屉里",'
            '"quote":"","importance":6,"confidence":0.9}]')
        app.memory_bank.observe("我的降压药放在门口的抽屉里")
        app.memory_bank.llm = lambda _p: (
            '[{"field":"物品","key":"降压药存放位置","value":"床头柜",'
            '"quote":"","importance":6,"confidence":0.9}]')
        app.memory_bank.observe("我的降压药改放床头柜了")
        loc = [f for f in app.memory_bank.facts if f["key"] == "降压药位置"]
        self.assertEqual(len(loc), 1, f"位置类信息没有归一到一个键: {[f['key'] for f in app.memory_bank.facts]}")
        self.assertEqual(loc[0]["value"], "床头柜")
        self.assertEqual(loc[0]["history"][-1]["value"], "门口的抽屉里")

    def test_quote_injected_for_traceable_answer(self):
        app.memory_bank.llm = lambda _p: (
            '[{"field":"物品","key":"降压药位置","value":"门口的抽屉里",'
            '"quote":"","importance":6,"confidence":0.9}]')
        app.memory_bank.observe("我的降压药放在门口的抽屉里")
        ctx = app.memory_bank.build_chat_context("我的降压药放哪了")
        self.assertIn("门口的抽屉里", ctx)
        self.assertIn("您说过", ctx)   # 回答可溯源出处


# ---------------------------------------------------------------- Bug⑥
class TestBug6Latency(unittest.TestCase):
    """a) 每轮回复不调 torch.cuda.empty_cache；b) 意图解析 DeepSeek 优先"""

    def setUp(self):
        cancel_all_reminders()
        app.pending_reminder["kind"] = None
        app.user_memory["call_name"] = "张奶奶"

    def test_no_empty_cache_in_chat_turn(self):
        torch_mock.cuda.empty_cache.reset_mock()
        out = app.chat_response("我今天挺好的", [], "张", "女", True,
                                "踏实务实", f"t{int(time.time()*1000)}", None)
        reply = out[1][-1]["content"]
        self.assertTrue(reply)
        self.assertEqual(torch_mock.cuda.empty_cache.call_count, 0,
                         "回复路径上调用了 torch.cuda.empty_cache()")

    def test_no_empty_cache_in_reminder_path(self):
        torch_mock.cuda.empty_cache.reset_mock()
        app.try_handle_reminder("五分钟后提醒我喝水")
        self.assertEqual(torch_mock.cuda.empty_cache.call_count, 0)

    def test_deepseek_intent_preferred_over_local(self):
        """规则未命中 -> DeepSeek API 解析成功 -> 本地 3B 不被调用（不抢锁）"""
        with mock.patch.object(app, "fallback_extract", return_value=None), \
             mock.patch.object(app, "deepseek_extract_intent",
                               return_value={"intent": "set", "time": "五分钟后",
                                             "thing": "喝水", "note": "", "target": ""}) as ds, \
             mock.patch.object(app, "llm_extract_intent") as local:
            reply = app.try_handle_reminder("待办提醒我喝水")
            self.assertTrue(ds.called, "DeepSeek 意图解析没有被调用")
            self.assertFalse(local.called, "DeepSeek 成功时本地模型仍被调用（抢锁）")
            self.assertEqual(len(app.reminder_store.pending()), 1)

    def test_local_model_is_last_resort(self):
        """DeepSeek 也失败时才落到本地 3B（断网兜底，维持离线可用）"""
        with mock.patch.object(app, "fallback_extract", return_value=None), \
             mock.patch.object(app, "deepseek_extract_intent", return_value=None), \
             mock.patch.object(app, "llm_extract_intent",
                               return_value={"intent": "set", "time": "五分钟后",
                                             "thing": "喝水"}) as local:
            reply = app.try_handle_reminder("待办提醒我喝水")
            self.assertTrue(local.called)
            self.assertEqual(len(app.reminder_store.pending()), 1)


# ---------------------------------------------------- 附加：模型不可用时的优雅降级
class TestGracefulDegradation(unittest.TestCase):
    """"模型不需要本地训练，只需要能调用"：本地模型缺失时走 DeepSeek，不崩"""

    def setUp(self):
        cancel_all_reminders()
        app.user_memory["call_name"] = "张奶奶"

    def test_chat_falls_back_to_deepseek_when_model_missing(self):
        saved_model, saved_tok = app._model, app._tokenizer
        try:
            app._model = app._tokenizer = None
            with mock.patch.object(app, "deepseek_chat_msgs",
                                   return_value="张奶奶，我在呢，您慢慢说。") as ds:
                out = app.chat_response("我今天挺好的", [], "张", "女", True,
                                        "踏实务实", f"d{int(time.time()*1000)}", None)
                reply = out[1][-1]["content"]
                self.assertEqual(reply, "张奶奶，我在呢，您慢慢说。")
                self.assertTrue(ds.called)
        finally:
            app._model, app._tokenizer = saved_model, saved_tok

    def test_intent_and_memory_llm_survive_missing_model(self):
        saved_model, saved_tok = app._model, app._tokenizer
        try:
            app._model = app._tokenizer = None
            self.assertIsNone(app.llm_extract_intent("五分钟后提醒我喝水"))
            self.assertEqual(app.memory_llm("test"), "")
        finally:
            app._model, app._tokenizer = saved_model, saved_tok


if __name__ == "__main__":
    unittest.main(verbosity=2)

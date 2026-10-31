# -*- coding: utf-8 -*-
"""
第二优先「找回4个功能」行为验证
================================
桩掉 torch / transformers / gradio，导入真实的 app.py + ai_services.py，
验证从 8 月备份分支搬回并适配新架构的功能：

  任务6  safety_filter        输出安全过滤（脏话/冷漠/医疗风险）
  任务5  suggest_topic_switch 冷场递话题 + 重复检测递话题
  任务10 build_biography      我的小传（DeepSeek 优先 / 离线拼装）
  任务9  analyze_emotion      情绪分析 + 每轮落盘 + 心情曲线
  顺带   跨会话主动问候（进入聊天/新对话）
  顺带   "忘了吧"真删记忆（关键词事件 + 记忆库双层）

运行：python test_care_features.py
数据文件全部落临时目录，不碰真实数据；DeepSeek 全程被桩，不联网。
"""
import os
import sys
import json
import time
import tempfile
import unittest
from unittest import mock

PROJECT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT)

TMP = tempfile.mkdtemp(prefix="xiaoyi_caretest_")
os.chdir(TMP)

torch_mock = mock.MagicMock(name="torch")
torch_mock.cuda.is_available.return_value = False
sys.modules["torch"] = torch_mock

transformers_mock = mock.MagicMock(name="transformers")
model_mock = mock.MagicMock(name="local_model")
tokenizer_mock = mock.MagicMock(name="tokenizer")
tokenizer_mock.pad_token = None
tokenizer_mock.decode.return_value = "奶奶，我听着呢，您说。"
transformers_mock.AutoModelForCausalLM.from_pretrained.return_value = model_mock
transformers_mock.AutoTokenizer.from_pretrained.return_value = tokenizer_mock
sys.modules["transformers"] = transformers_mock

gradio_mock = mock.MagicMock(name="gradio")
sys.modules["gradio"] = gradio_mock

import app  # noqa: E402
import ai_services  # noqa: E402

app.DEEPSEEK_API_KEYS = []
app.needs_search = lambda _t: False

YESTERDAY = time.time() - 86400 - 3600


def reset_state():
    for r in app.reminder_store.pending():
        app.reminder_store.cancel(r["id"])
    app.pending_reminder["kind"] = None
    app.user_memory["events"] = []
    app.user_memory["last_medication_time"] = 0.0
    app.user_memory["call_name"] = "张奶奶"
    mb = app.memory_bank
    mb.facts, mb.episodes, mb.reflections = [], [], []
    mb.pending_confirms, mb.active_confirm = [], None
    mb._reflect_imp = mb._reflect_cnt = 0
    app.save_emotion_log([])


def seed_fact(field, key, value, ts=None, importance=6):
    ts = ts or time.time()
    app.memory_bank.facts.append({
        "id": f"f{int(ts*1000)}{len(app.memory_bank.facts)}", "field": field,
        "key": key, "value": value, "quotes": [f"原话：{value}"],
        "ts": ts, "first_ts": ts, "confidence": 0.9, "importance": importance,
        "strength": 30.0, "recall_count": 0, "last_recall": ts,
        "history": [], "ask_count": 0})


def chat(text, history=None):
    return app.chat_response(text, history or [], "张", "女", True,
                             "踏实务实", f"c{int(time.time()*1000000)}", None)


# ---------------------------------------------------- 任务6：输出安全过滤
class TestSafetyFilter(unittest.TestCase):
    def test_profanity_replaced(self):
        r = ai_services.safety_filter("妈的，我听着呢", call_name="张奶奶")
        self.assertFalse(r["safe"])
        self.assertNotIn("妈的", r["safe_reply"])

    def test_cold_tone_replaced(self):
        r = ai_services.safety_filter("关我什么事，您自己看着办", call_name="张奶奶")
        self.assertFalse(r["safe"])

    def test_normal_reply_untouched_and_no_llm(self):
        with mock.patch.object(ai_services, "_llm") as fake_llm:
            r = ai_services.safety_filter("张奶奶，您今天气色真好", call_name="张奶奶")
            self.assertTrue(r["safe"])
            self.assertFalse(fake_llm.called, "普通回复不应触发 LLM 审核（Bug⑥ 延迟要求）")

    def test_medical_risk_audited_by_llm(self):
        unsafe_json = '{"safe": false, "reason": "劝老人停药", "safe_reply": "张奶奶，药可不能自己停，得听医生的。"}'
        with mock.patch.object(ai_services, "_llm", return_value=unsafe_json):
            r = ai_services.safety_filter("那降压药我看可以停药了", call_name="张奶奶")
            self.assertFalse(r["safe"])
            self.assertIn("医生", r["safe_reply"])

    def test_app_level_profanity_replaced(self):
        reset_state()
        saved = tokenizer_mock.decode.return_value
        tokenizer_mock.decode.return_value = "妈的，这事儿真烦人"
        try:
            out = chat("我今天有点烦")
            reply = out[1][-1]["content"]
            self.assertNotIn("妈的", reply)
        finally:
            tokenizer_mock.decode.return_value = saved


# ---------------------------------------------------- 任务9：情绪分析 + 落盘 + 曲线
class TestEmotion(unittest.TestCase):
    def test_keyword_channels(self):
        self.assertEqual(ai_services.analyze_emotion("我心里堵得慌")["label"], "low")
        self.assertEqual(ai_services.analyze_emotion("我昨晚又睡不着")["label"], "low")  # 躯体化
        self.assertEqual(ai_services.analyze_emotion("我今天真高兴")["label"], "high")
        self.assertEqual(ai_services.analyze_emotion("今天吃了面条")["label"], "neutral")

    def test_llm_channel_optional(self):
        with mock.patch.object(ai_services, "_llm",
                               return_value='{"label":"low","score":-0.8,"note":"孤独"}'):
            r = ai_services.analyze_emotion("儿女都不在身边", use_llm=True)
            self.assertEqual(r["label"], "low")
            self.assertEqual(r["score"], -0.8)

    def test_logged_every_turn(self):
        reset_state()
        chat("我心里有点难过")
        log = app.load_emotion_log()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["label"], "low")
        self.assertLess(log[0]["score"], 0)

    def test_chart_rendered(self):
        reset_state()
        now = time.time()
        app.save_emotion_log([
            {"time": now - 3600, "label": "low", "score": -0.6, "note": ""},
            {"time": now - 1800, "label": "neutral", "score": 0.0, "note": ""},
            {"time": now - 60, "label": "high", "score": 0.6, "note": ""},
        ])
        path = app.generate_emotion_chart()
        self.assertIsNotNone(path)
        self.assertTrue(os.path.exists(path) and path.endswith(".png"))

    def test_chart_none_when_insufficient(self):
        reset_state()
        app.save_emotion_log([{"time": time.time(), "label": "low", "score": -0.6, "note": ""}])
        self.assertIsNone(app.generate_emotion_chart())


# ---------------------------------------------------- 任务5：话题切换
class TestTopicSwitch(unittest.TestCase):
    def setUp(self):
        reset_state()

    def test_family_topic_preferred(self):
        seed_fact("家庭", "孙子", "豆豆")
        topic = ai_services.suggest_topic_switch(app.memory_bank, [], "张奶奶")
        self.assertIn("家里人", topic)

    def test_default_topic_when_no_memory(self):
        topic = ai_services.suggest_topic_switch(app.memory_bank, [], "张奶奶")
        self.assertIn("听戏", topic)

    def test_cold_keyword_preempts(self):
        out = chat("唉，没啥聊的啊")
        reply = out[1][-1]["content"]
        self.assertTrue("听戏" in reply or "念叨" in reply or "新鲜事" in reply, reply)

    def test_repetition_appends_topic(self):
        history = [
            {"role": "user", "content": "我睡不着"},
            {"role": "assistant", "content": "睡不着难受吧。"},
            {"role": "user", "content": "我睡不着"},
            {"role": "assistant", "content": "别着急。"},
        ]
        out = chat("我睡不着", history)
        reply = out[1][-1]["content"]
        self.assertTrue("听戏" in reply or "念叨" in reply or "新鲜事" in reply
                        or "好玩的事" in reply, f"重复3轮未递话题: {reply}")

    def test_no_false_trigger_on_varied_chat(self):
        history = [
            {"role": "user", "content": "我今天去公园了"},
            {"role": "assistant", "content": "真好。"},
            {"role": "user", "content": "走了好几圈"},
            {"role": "assistant", "content": "您腿脚真利索。"},
        ]
        out = chat("回来买了点菜", history)
        reply = out[1][-1]["content"]
        self.assertNotIn("听戏", reply)


# ---------------------------------------------------- 任务10：我的小传
class TestBiography(unittest.TestCase):
    def setUp(self):
        reset_state()

    def test_empty_bank_polite_message(self):
        text = ai_services.build_biography(app.memory_bank, "张奶奶")
        self.assertIn("相处时间还短", text)

    def test_offline_assembly(self):
        seed_fact("家庭", "孙子", "豆豆")
        seed_fact("健康", "膝盖疼", "膝盖疼")
        with mock.patch.object(ai_services, "_llm", None):
            text = ai_services.build_biography(app.memory_bank, "张奶奶")
        self.assertIn("豆豆", text)
        self.assertIn("膝盖疼", text)
        self.assertIn("小传", text)

    def test_llm_polished(self):
        seed_fact("家庭", "孙子", "豆豆")
        with mock.patch.object(ai_services, "_llm", return_value="张奶奶最疼孙子豆豆，常念叨他。"):
            text = ai_services.build_biography(app.memory_bank, "张奶奶")
            self.assertEqual(text, "张奶奶最疼孙子豆豆，常念叨他。")


# ---------------------------------------------------- 顺带：跨会话主动问候
class TestCrossSessionGreeting(unittest.TestCase):
    def setUp(self):
        reset_state()

    def test_greeting_from_yesterday_health(self):
        seed_fact("健康", "膝盖疼", "膝盖疼", ts=YESTERDAY)
        greet = ai_services.build_cross_session_greeting(app.memory_bank, "张奶奶")
        self.assertIsNotNone(greet)
        self.assertIn("好点没", greet)

    def test_no_greeting_when_all_today(self):
        seed_fact("健康", "膝盖疼", "膝盖疼", ts=time.time())
        self.assertIsNone(ai_services.build_cross_session_greeting(app.memory_bank, "张奶奶"))

    def test_new_conversation_includes_greeting(self):
        seed_fact("用药", "降压药", "降压药", ts=YESTERDAY)
        chat_history, _id, _u = app.new_conversation()
        self.assertIn("按时吃", chat_history[0]["content"])

    def test_setup_includes_greeting(self):
        # 旧版 Gradio enter_chat 已下线；等价入口是 FastAPI 的 chat_service.setup
        seed_fact("健康", "膝盖疼", "膝盖疼", ts=YESTERDAY)
        from backend.services import chat_service   # 首次导入会把 CWD 切到项目根
        try:
            out = chat_service.setup("张", "女", "踏实务实", True)
        finally:
            os.chdir(TMP)   # 恢复临时目录，保持本套测试的数据隔离
        self.assertIn("好点没", out["reply"])


# ---------------------------------------------------- 顺带："忘了吧"真删记忆
class TestForgetByRequest(unittest.TestCase):
    def setUp(self):
        reset_state()

    def test_intent_regex_no_false_positive(self):
        for innocent in ("我忘不掉那段日子", "我忘不了他", "我忘了吃药", "年纪大了老忘事"):
            self.assertIsNone(ai_services.FORGET_INTENT_RE.search(innocent), innocent)
        for real in ("把降压药的事忘了吧", "这个别记了", "把它删掉", "那段忘掉吧"):
            self.assertIsNotNone(ai_services.FORGET_INTENT_RE.search(real), real)

    def test_deletes_bank_fact_and_legacy_event(self):
        seed_fact("物品", "降压药位置", "门口的抽屉里")
        app.user_memory["events"] = [
            {"type": "medication", "content": "降压药", "time": time.time(), "status": "active"}]
        kept, reply = ai_services.forget_by_request(
            "把降压药的事忘了吧", app.user_memory["events"], app.memory_bank, "张奶奶")
        self.assertIsNotNone(reply)
        self.assertEqual(app.memory_bank.facts, [], "记忆库事实没删干净")
        self.assertEqual(kept, [], "旧事件表没删干净")
        self.assertIn("忘掉", reply)

    def test_unrelated_memory_survives(self):
        seed_fact("家庭", "孙子", "豆豆")
        seed_fact("物品", "降压药位置", "门口的抽屉里")
        kept, reply = ai_services.forget_by_request(
            "把降压药的事忘了吧", [], app.memory_bank, "张奶奶")
        self.assertEqual(len(app.memory_bank.facts), 1)
        self.assertEqual(app.memory_bank.facts[0]["key"], "孙子")

    def test_no_match_returns_none(self):
        seed_fact("家庭", "孙子", "豆豆")
        kept, reply = ai_services.forget_by_request(
            "把公园的事忘了吧", [], app.memory_bank, "张奶奶")
        self.assertIsNone(reply)
        self.assertEqual(len(app.memory_bank.facts), 1)

    def test_app_level_forget_flow(self):
        seed_fact("物品", "降压药位置", "门口的抽屉里")
        out = chat("把降压药的事忘了吧")
        reply = out[1][-1]["content"]
        self.assertIn("忘掉", reply)
        self.assertEqual(app.memory_bank.facts, [])

    def test_forget_cleans_pending_confirm(self):
        seed_fact("物品", "降压药位置", "门口的抽屉里")
        fid = app.memory_bank.facts[0]["id"]
        app.memory_bank.pending_confirms = [{"fact_id": fid, "ts": time.time()}]
        app.memory_bank.active_confirm = {"fact_id": fid, "ts": time.time()}
        ai_services.forget_by_request("把降压药的事忘了吧", [], app.memory_bank, "张奶奶")
        self.assertEqual(app.memory_bank.pending_confirms, [])
        self.assertIsNone(app.memory_bank.active_confirm)


# ---------------------------------------------------- 任务⑥：消息撤回真删记忆
class TestRecallMessage(unittest.TestCase):
    def setUp(self):
        reset_state()
        os.chdir(TMP)   # 数据文件全部落临时目录

    def tearDown(self):
        # chat_service 内部（backend.core.config import）会把 CWD 切到项目根，
        # 不恢复的话组合跑后续套件（proactive/bugfixes）会读写到真实数据目录
        os.chdir(TMP)

    def test_recall_removes_round_and_cleans_memory(self):
        from backend.services import chat_service
        conv_id = "recall-test"
        msg = "三点提醒我找老花镜，老花镜就放在门口抽屉"
        app.save_conversation(conv_id, [
            {"role": "user", "content": msg},
            {"role": "assistant", "content": "好嘞，三点提醒您找老花镜。"},
        ])
        # 该轮已抽进记忆库（quote 是原话片段）+ 关键词事件 + 该轮建的提醒
        app.memory_bank.facts.append({
            "id": "f1", "field": "物品", "key": "老花镜位置", "value": "门口抽屉",
            "quotes": ["老花镜就放在门口抽屉"], "ts": time.time(), "first_ts": time.time(),
            "confidence": 0.9, "importance": 6, "strength": 30.0,
            "recall_count": 0, "last_recall": time.time(), "history": [], "ask_count": 0})
        app.memory_bank.episodes.append({
            "id": "e1", "text": "老花镜位置是门口抽屉",
            "quote": msg, "field": "物品",
            "ts": time.time(), "importance": 6, "strength": 30.0,
            "recall_count": 0, "last_recall": time.time()})
        app.user_memory["events"] = [
            {"type": "health", "content": "高血压", "time": time.time(), "status": "active"},
            {"type": "item", "content": "老花镜", "time": time.time(), "status": "active"}]
        app.reminder_store.add("找老花镜", time.time() + 3600, source="chat")

        out = chat_service.recall(conv_id)
        self.assertTrue(out["ok"])
        self.assertEqual(out["message"], "好嘞，刚那句不算哈～")   # 固定回复，不调模型
        # 返回历史与对话文件里：这一轮都删掉，只剩撤回确认
        self.assertEqual([(m["role"], m["content"]) for m in out["chat_history"]],
                         [("assistant", "好嘞，刚那句不算哈～")])
        msgs, _ = app.get_conversation_messages_by_id(conv_id)
        self.assertEqual([m["content"] for m in msgs], ["好嘞，刚那句不算哈～"])
        # 撤掉的话不许留在记忆里：事实/情节/该轮事件/该轮提醒全清
        self.assertEqual(app.memory_bank.facts, [])
        self.assertEqual(app.memory_bank.episodes, [])
        self.assertEqual([e["content"] for e in app.user_memory["events"]], ["高血压"])
        self.assertEqual(app.reminder_store.pending(), [])

    def test_recall_empty_conversation(self):
        from backend.services import chat_service
        out = chat_service.recall("no-such-conv")
        self.assertFalse(out["ok"])
        self.assertEqual(out["chat_history"], [])

    def test_recall_cleans_reworded_reminder_and_keeps_old(self):
        """LLM 改写过的 thing（非连续子串）也能清；时间窗外的同类提醒不误删。
        真机联调发现：thing="我孙子叫豆豆，去社区医院拿药" 出自
        "我孙子叫豆豆，明天下午三点提醒我去社区医院拿药"——剥掉时间短语
        拼接前后文，纯 in 判定漏掉，提醒残留在待触发列表。"""
        msg = "我孙子叫豆豆，明天下午三点提醒我去社区医院拿药"
        app.reminder_store.add("我孙子叫豆豆，去社区医院拿药",   # 改写拼接（bug 现场）
                               time.time() + 7200, source="chat")
        app.reminder_store.add("去社区医院拿药",                  # 连续子串
                               time.time() + 3600, source="chat")
        old = app.reminder_store.add("去社区医院拿药",           # 同类但 2 小时前建的
                                     time.time() + 5400, source="chat")
        old["created"] = time.time() - 7200
        app.reminder_store._save()

        counts = app.recall_cleanup({"role": "user", "content": msg})

        self.assertEqual(counts["reminders"], 2)   # 改写的 + 子串的都清
        self.assertEqual(len(app.reminder_store.pending()), 1)   # 时间窗外的保留
        self.assertEqual(app.reminder_store.pending()[0]["thing"], "去社区医院拿药")

    def test_recall_reminder_short_thing_not_fuzzy_matched(self):
        """短 thing（<6字）不做模糊匹配，只认完全包含——防误删别人的提醒。"""
        app.reminder_store.add("量血压", time.time() + 3600, source="chat")
        counts = app.recall_cleanup({"role": "user", "content": "明天下午三点提醒我去社区医院拿药"})
        self.assertEqual(counts["reminders"], 0)   # "量血压"与原话无关，不清
        self.assertEqual(len(app.reminder_store.pending()), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

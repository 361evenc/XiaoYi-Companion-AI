# -*- coding: utf-8 -*-
"""B-3 找物流程验收测试（B-1 PRD §3 三级逻辑 + §4 接口契约）。

验收口径：
  - graph（视觉条目"我见过"）→ text（"您上次说"）→ none（诚实兜底）三级正确
  - 时间窗：graph ≤30 天直答 / 30–180 stale / >180 不主动展示；
    text ≤90 天 / >90 stale / >180 不主动引用
  - 编造 0 容忍：低置信/太老/无依据一律不引用，只说"我没瞅见"
  - "我见过"与"您说过"话术严格区分，不冒充
  - has_clean_crop 恒 False（H-2 裁剪图未供），crop_url None
"""
import os
import shutil
import tempfile
import unittest

from memory_system import MemoryBank


class FakeClock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t

    def tick(self, days=0, minutes=0):
        self.t += days * 86400 + minutes * 60
        return self.t


def _mk_bank(testcase):
    tmp = tempfile.mkdtemp()
    testcase.addCleanup(shutil.rmtree, tmp, True)
    clock = FakeClock()
    path = os.path.join(tmp, "memory_bank.json")
    return MemoryBank(path=path, llm=None, clock=clock), clock


def _fact(bank, key, value, quote="", ts_days_ago=0, importance=8, confidence=0.9):
    now = bank.clock()
    bank._add_fact(field="物品", key=key, value=value, quote=quote,
                   importance=importance, confidence=confidence,
                   now=now - ts_days_ago * 86400)
    bank._save()


# ================================================================ 意图提取与放行

class TestFindObjectIntent(unittest.TestCase):

    def test_intent_extraction(self):
        """各句式提取物品名正确（含称谓前缀剥离、尾部介词剥离）。"""
        bank, clock = _mk_bank(self)
        cases = [
            ("我降压药放哪了", "降压药"),
            ("降压药放哪了", "降压药"),
            ("老花镜在哪", "老花镜"),
            ("钥匙去哪了", "钥匙"),
            ("遥控器搁哪儿了", "遥控器"),
            ("降压药在什么地方", "降压药"),
        ]
        for q, obj in cases:
            res = bank.find_object(q)
            self.assertIsNotNone(res, q)
            self.assertEqual(res["object"], obj, q)

    def test_passthrough_non_find_queries(self):
        """代词提问与闲聊放行（None），绝不误接管。"""
        bank, clock = _mk_bank(self)
        for q in ["你在哪", "您在哪呢", "今天天气真好", "把电视声音调大点",
                  "咱家孙子叫啥", "我有点头晕"]:
            self.assertIsNone(bank.find_object(q), q)


# ================================================================ L1 图谱层（视觉条目）

class TestGraphTier(unittest.TestCase):

    def test_fresh_visual_entry(self):
        """30 天内高置信视觉条目 → graph 直答：位置+时间+话术（"我瞅见它了"）。"""
        bank, clock = _mk_bank(self)
        bank.ingest_visual_event("place", "降压药", "客厅", 0.87, furniture="茶几")
        res = bank.find_object("我降压药放哪了")
        self.assertEqual(res["tier"], "graph")
        self.assertEqual(res["source"], "graph")
        self.assertEqual(res["position"], "客厅的茶几")
        self.assertFalse(res["stale"])
        self.assertFalse(res["too_old"])
        self.assertFalse(res["has_clean_crop"])      # H-2 未供：F-1 不展示图
        self.assertIsNone(res["crop_url"])
        self.assertGreaterEqual(res["confidence"], 0.8)
        self.assertIn("瞅见", res["reply"])
        self.assertIn("茶几", res["reply"])

    def test_stale_visual_entry(self):
        """30–180 天的条目仍答位置，但 stale=True + "好久没见"提示。"""
        bank, clock = _mk_bank(self)
        bank.ingest_visual_event("appear", "拐杖", "玄关", 0.9,
                                 ts=clock.t - 40 * 86400)
        res = bank.find_object("拐杖在哪")
        self.assertEqual(res["tier"], "graph")
        self.assertTrue(res["stale"])
        self.assertIn("好久没见", res["reply"])
        self.assertIn("玄关", res["reply"])          # 位置仍给

    def test_too_old_visual_falls_through(self):
        """>180 天的视觉条目不主动展示（"去年11月见过"早已失真）→ 降级。"""
        bank, clock = _mk_bank(self)
        bank.ingest_visual_event("appear", "棉被", "阳台", 0.9,
                                 ts=clock.t - 200 * 86400)
        res = bank.find_object("棉被去哪了")
        self.assertEqual(res["tier"], "none")        # 唯一依据太老，不用即诚实

    def test_low_confidence_not_cited(self):
        """低置信（<0.8）只在短期缓存、检索不可见——引用它=编造风险。"""
        bank, clock = _mk_bank(self)
        bank.ingest_visual_event("appear", "钥匙", "客厅", 0.5)
        res = bank.find_object("钥匙在哪")
        self.assertEqual(res["tier"], "none")

    def test_sensitive_zone_never_recorded(self):
        """敏感区条目 B-4 拒写 → 找物查不到（护栏联动的下游验证）。"""
        bank, clock = _mk_bank(self)
        bank.ingest_visual_event("appear", "药膏", "卧室", 0.95)
        res = bank.find_object("药膏在哪")
        self.assertEqual(res["tier"], "none")


# ================================================================ L2 文字层（"您说过"）

class TestTextTier(unittest.TestCase):

    def test_text_fact(self):
        """记忆里"X位置"键 → text 层："您上次说"（不冒充"我见过"）。"""
        bank, clock = _mk_bank(self)
        _fact(bank, "老花镜位置", "电视柜", quote="老花镜放在电视柜")
        res = bank.find_object("老花镜在哪")
        self.assertEqual(res["tier"], "text")
        self.assertEqual(res["source"], "text")
        self.assertEqual(res["position"], "电视柜")
        self.assertFalse(res["stale"])
        self.assertIn("您上次说", res["reply"])
        self.assertNotIn("瞅见它了", res["reply"])    # 话术严格区分

    def test_text_stale(self):
        """>90 天的文字层条目 → stale=True + "好久以前说的"提示。"""
        bank, clock = _mk_bank(self)
        _fact(bank, "血压计位置", "客厅茶几", quote="血压计放在茶几", ts_days_ago=100)
        res = bank.find_object("血压计搁哪了")
        self.assertEqual(res["tier"], "text")
        self.assertTrue(res["stale"])
        self.assertIn("好久以前", res["reply"])

    def test_text_too_old_falls_through(self):
        """>180 天的文字层条目不主动引用（照答=带错路）→ 降级 none。"""
        bank, clock = _mk_bank(self)
        _fact(bank, "棉被位置", "阳台柜子", quote="棉被晒在阳台", ts_days_ago=200)
        res = bank.find_object("棉被在哪")
        self.assertEqual(res["tier"], "none")

    def test_non_position_fact_not_cited(self):
        """非位置事实（孙子=豆豆）不能被找物当位置依据引用。"""
        bank, clock = _mk_bank(self)
        bank._add_fact(field="家庭", key="孙子", value="豆豆", quote="孙子叫豆豆",
                       importance=7, confidence=0.9, now=clock.t)
        bank._save()
        res = bank.find_object("孙子在哪")
        self.assertEqual(res["tier"], "none")


# ================================================================ 优先级与 L3 诚实兜底

class TestPriorityAndFallback(unittest.TestCase):

    def test_graph_beats_text(self):
        """graph 与 text 同在 → graph 优先（最新视觉依据强于口头旧账）。"""
        bank, clock = _mk_bank(self)
        _fact(bank, "老花镜位置", "电视柜", quote="老花镜放在电视柜")
        bank.ingest_visual_event("place", "老花镜", "书房", 0.85, furniture="书桌")
        res = bank.find_object("老花镜放哪了")
        self.assertEqual(res["tier"], "graph")
        self.assertEqual(res["position"], "书房的书桌")

    def test_none_honest_reply(self):
        """无任何依据 → 诚实"我没瞅见"+ 失败后引导（记上/下次说一声）。
        编造 0 容忍：position=None、confidence=0，绝不给具体位置。"""
        bank, clock = _mk_bank(self)
        res = bank.find_object("钥匙在哪")
        self.assertEqual(res["tier"], "none")
        self.assertEqual(res["source"], "none")
        self.assertIsNone(res["position"])
        self.assertEqual(res["confidence"], 0)
        self.assertIn("我没瞅见", res["reply"])
        self.assertIn("记上", res["reply"])           # 失败后引导：H-3 标注机会

    def test_asr_fuzzy_object_name(self):
        """物品名轻微出入（"降压药"vs 条目"降压药瓶"）经 bigram 相似度仍可命中。"""
        bank, clock = _mk_bank(self)
        bank.ingest_visual_event("place", "降压药瓶", "客厅", 0.9, furniture="茶几")
        res = bank.find_object("降压药放哪了")
        self.assertEqual(res["tier"], "graph")
        self.assertIn("客厅", res["position"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

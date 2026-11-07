# -*- coding: utf-8 -*-
"""B-5 记忆系统增强（三维检索 + 显式更正）验收测试。

任务卡 B-5 验收标准：
  1. 按人/物/场景三种检索均返回正确结果（各 ≥10 条）；
  2. 更正即时生效、历史保留可查（旧值入 history，不是物理删）。
"""
import shutil
import tempfile
import unittest
import os

from memory_system import MemoryBank, SCENE_LEXICON


class FakeClock:
    """可控时间源：避开真实系统时间，测试淡忘/检索不受跑测试的时刻影响。"""

    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t

    def tick(self, days=1):
        self.t += days * 86400
        return self.t


def _mk_bank(testcase):
    tmp = tempfile.mkdtemp()
    testcase.addCleanup(shutil.rmtree, tmp, True)
    clock = FakeClock()
    path = os.path.join(tmp, "memory_bank.json")
    return MemoryBank(path=path, llm=None, clock=clock), clock


def _fact(bank, field, key, value, quote, importance=6, confidence=0.9):
    bank._add_fact(field=field, key=key, value=value, quote=quote,
                   importance=importance, confidence=confidence, now=bank.clock())
    bank._save()


def _episode(bank, text, field="其他", importance=5):
    now = bank.clock()
    bank.episodes.append({
        "id": f"ep{len(bank.episodes) + 1}", "text": text, "quote": text,
        "field": field, "ts": now, "importance": importance, "strength": 3.0,
        "recall_count": 0, "last_recall": now,
    })
    bank._save()


# ================================================================ B-5① 按物检索

class TestSearchObject(unittest.TestCase):

    def test_ten_objects_top1_correct(self):
        """验收：按物检索 10 条，每条 top1 都是该物的"X位置"事实、值正确。"""
        bank, clock = _mk_bank(self)
        objects = [
            ("老花镜", "电视柜"), ("降压药", "药箱"), ("遥控器", "沙发扶手"),
            ("拐杖", "门口"), ("助听器", "床头柜抽屉"), ("保温杯", "餐桌"),
            ("钥匙", "玄关挂钩"), ("血压计", "客厅茶几"), ("放大镜", "书房"),
            ("眼镜布", "客厅"),
        ]
        for obj, pos in objects:
            _fact(bank, "物品", f"{obj}位置", pos, f"{obj}放在{pos}", importance=8)
        # 现场噪声：别的事实在场，检索不许串台
        _fact(bank, "家庭", "孙子", "豆豆", "孙子叫豆豆", importance=7)
        _episode(bank, "今天心情不错，在阳台浇了花")

        for obj, pos in objects:
            out = bank.search(obj, by="object")
            self.assertEqual(out["by"], "object")
            self.assertTrue(out["results"], f"查「{obj}」应有结果")
            top = out["results"][0]
            self.assertEqual(top["type"], "fact", f"查「{obj}」top1 应是事实")
            self.assertEqual(top["key"], f"{obj}位置", f"查「{obj}」top1 键应为 {obj}位置")
            self.assertEqual(top["value"], pos, f"查「{obj}」top1 值应为 {pos}")
            self.assertIn("history", top, "fact 条目应带 history（旧值随检索可查）")

    def test_object_not_found_and_bad_by(self):
        """查无此物返回空列表（不编造）；by 非法给 message。"""
        bank, clock = _mk_bank(self)
        _fact(bank, "物品", "老花镜位置", "电视柜", "老花镜放在电视柜")
        out = bank.search("完全不认识的物件", by="object")
        self.assertEqual(out["results"], [])
        out = bank.search("老花镜", by="乱写")
        self.assertIn("message", out)
        self.assertEqual(out["results"], [])
        out = bank.search("", by="object")
        self.assertIn("message", out)


# ================================================================ B-5① 按人检索

class TestSearchPerson(unittest.TestCase):

    def _seed(self, bank):
        _fact(bank, "家庭", "孙子", "豆豆", "孙子叫豆豆", importance=7)
        bank.add_person_profile("豆豆", "孙子", quote="这是我孙子豆豆")
        _episode(bank, "豆豆考了一百分，可给我长脸了", field="家庭", importance=7)
        _episode(bank, "豆豆来看我，带了绿豆糕", field="家庭", importance=6)
        _episode(bank, "邻居王阿姨上门送了饺子", field="家庭")
        _episode(bank, "有客人来家里串门", field="家庭")
        _episode(bank, "保姆李姐帮忙打扫了卫生", field="家庭")
        _episode(bank, "家里的小猫在阳台晒太阳", field="其他")
        _episode(bank, "小狗旺财闹着要出门", field="其他")

    def test_ten_queries_resolved_kind(self):
        """验收：按人检索 10+ 条，档案优先、三类各归其位。"""
        bank, clock = _mk_bank(self)
        self._seed(bank)
        cases = [
            ("豆豆", "profile"), ("孙子", "profile"), ("豆豆的事", "profile"),
            ("客人", "visitor"), ("邻居", "visitor"), ("保姆", "visitor"),
            ("上门", "visitor"), ("串门", "visitor"),
            ("小猫", "pet"), ("小狗", "pet"), ("我自己", "self"),
        ]
        for q, kind in cases:
            out = bank.search(q, by="person")
            self.assertEqual(out["by"], "person")
            self.assertEqual(out["resolved"]["kind"], kind, f"查「{q}」应解析为 {kind}")
            self.assertTrue(out["results"], f"查「{q}」应有结果")

    def test_profile_results_only_about_person(self):
        """档案命中：结果里每条都提到该人（名字或称谓），不掺访客/宠物记忆。"""
        bank, clock = _mk_bank(self)
        self._seed(bank)
        out = bank.search("孙子", by="person")
        self.assertEqual(out["resolved"]["kind"], "profile")
        self.assertEqual(out["resolved"]["name"], "豆豆")
        for r in out["results"]:
            text = " ".join(str(r.get(k, "")) for k in ("text", "key", "value", "quote"))
            self.assertTrue(("豆豆" in text) or ("孙子" in text), f"串台：{text}")

    def test_fallback_visitor_never_shows_guess_name(self):
        """F-4 兜底：查无档案的具名人物（"孙子"无档案）→ 退访客类，
        绝不显示猜的人名"豆豆"。"""
        bank, clock = _mk_bank(self)
        self._seed(bank)
        bank.remove_person_profile("豆豆")
        self.assertEqual(bank.person_profiles, [])
        out = bank.search("孙女", by="person")   # 从没建过档案的具名人物
        self.assertEqual(out["resolved"]["kind"], "fallback_visitor")
        for r in out["results"]:
            text = " ".join(str(r.get(k, "")) for k in ("text", "key", "value", "quote"))
            self.assertNotIn("豆豆", text, "兜底结果里不许出现猜的人名")

    def test_profiles_persist_and_merge(self):
        """档案落盘可恢复；同名建档合并称谓；删除退回三类。"""
        bank, clock = _mk_bank(self)
        bank.add_person_profile("豆豆", "孙子", quote="这是我孙子豆豆")
        bank2 = MemoryBank(path=bank.path, llm=None, clock=clock)
        self.assertEqual(len(bank2.person_profiles), 1)
        self.assertEqual(bank2.person_profiles[0]["calls"], ["孙子"])
        # 同名再建档 → 合并称谓，不重复建档
        bank2.add_person_profile("豆豆", "大孙子")
        self.assertEqual(len(bank2.person_profiles), 1)
        self.assertIn("大孙子", bank2.person_profiles[0]["calls"])
        out = bank2.search("大孙子", by="person")   # 新称谓也能命中档案
        self.assertEqual(out["resolved"]["kind"], "profile")
        self.assertEqual(bank2.remove_person_profile("豆豆"), 1)
        self.assertEqual(bank2.person_profiles, [])


# ================================================================ B-5① 按场景检索

class TestSearchScene(unittest.TestCase):

    def _seed(self, bank):
        _episode(bank, "在厨房烧水泡茶")
        _episode(bank, "厨房的锅该换了")
        _episode(bank, "做饭时切了手，不碍事", importance=7)
        _episode(bank, "在客厅沙发上看电视睡着了")
        _episode(bank, "今天在阳台浇花")
        _episode(bank, "阳台上晒了被子")
        _episode(bank, "在卧室午睡了一会儿")
        _episode(bank, "床头柜上的台灯坏了")
        _episode(bank, "在卫生间洗澡滑了一下，没事", importance=8)
        _episode(bank, "在餐桌吃饭")

    def test_ten_queries_scene_resolved(self):
        """验收：按场景检索 10 条，词典归位正确（"做饭"→厨房），结果不串场景。"""
        bank, clock = _mk_bank(self)
        self._seed(bank)
        cases = [
            ("厨房", "厨房"), ("做饭", "厨房"), ("锅", "厨房"),
            ("客厅", "客厅"), ("沙发", "客厅"), ("看电视", "客厅"),
            ("阳台", "阳台"), ("卧室", "卧室"), ("卫生间", "卫生间"), ("餐桌", "餐厅"),
        ]
        for q, canon in cases:
            out = bank.search(q, by="scene")
            self.assertEqual(out["by"], "scene")
            self.assertEqual(out["resolved"].get("scene"), canon, f"查「{q}」应归 {canon}")
            self.assertTrue(out["results"], f"查「{q}」应有结果")
            words = SCENE_LEXICON[canon] + [canon]
            for r in out["results"][:3]:
                self.assertTrue(any(w in r.get("text", "") for w in words),
                                f"「{q}」结果串场景：{r.get('text')}")

    def test_scene_tag_from_h2_wins(self):
        """H-2 粗位置标签（mem['scene']）优先：有标签的记忆按标签归位。"""
        bank, clock = _mk_bank(self)
        ep = {"id": "ep_tag", "text": "这东西挺好", "quote": "这东西挺好",
              "field": "其他", "ts": bank.clock(), "importance": 5, "strength": 3.0,
              "recall_count": 0, "last_recall": bank.clock(), "scene": "厨房"}
        bank.episodes.append(ep)
        bank._save()
        out = bank.search("厨房", by="scene")
        texts = [r.get("text") for r in out["results"]]
        self.assertIn("这东西挺好", texts)   # 文本不含厨房词，靠标签命中


# ================================================================ B-5② 显式更正

class TestCorrection(unittest.TestCase):

    def _seed(self, bank):
        _fact(bank, "家庭", "孙子", "豆豆", "孙子叫豆豆", importance=7)
        _fact(bank, "物品", "降压药位置", "药箱", "降压药放在药箱", importance=8)

    def test_correct_immediate_and_history(self):
        """更正即时生效（值更新+一律用新值），旧值入 history 保留可查。"""
        bank, clock = _mk_bank(self)
        self._seed(bank)
        res = bank.correct_fact(key="孙子", new_value="毛毛", quote="不叫豆豆了")
        self.assertEqual(res["action"], "corrected")
        self.assertEqual(res["previous"], "豆豆")
        fact = next(f for f in bank.facts if f["key"] == "孙子")
        self.assertEqual(fact["value"], "毛毛")                       # 即时生效
        self.assertEqual(fact["history"][-1]["value"], "豆豆")         # 历史保留
        ctx = bank.build_chat_context("我孙子叫啥名字", now=clock())
        self.assertIn("毛毛", ctx)                                    # AI 回答一律用新值
        # 重启后依然：新值生效 + 历史可查（落盘不是物理删）
        bank2 = MemoryBank(path=bank.path, llm=None, clock=clock)
        fact2 = next(f for f in bank2.facts if f["key"] == "孙子")
        self.assertEqual(fact2["value"], "毛毛")
        self.assertEqual(fact2["history"][-1]["value"], "豆豆")

    def test_correct_to_same_value_is_noop(self):
        """更正成同一个值 → 只加固，不制造假历史。"""
        bank, clock = _mk_bank(self)
        self._seed(bank)
        res = bank.correct_fact(key="孙子", new_value="豆豆")
        self.assertEqual(res["action"], "noop")
        fact = next(f for f in bank.facts if f["key"] == "孙子")
        self.assertEqual(fact["history"], [])

    def test_explicit_correction_sentences(self):
        """"不叫豆豆叫毛毛" / "把豆豆改成毛毛" 确定性更正 + 旧值问句闭环。"""
        bank, clock = _mk_bank(self)
        self._seed(bank)
        reply = bank.handle_explicit_correction("我孙子不叫豆豆，叫毛毛", call_name="奶奶")
        self.assertIsNotNone(reply)
        self.assertIn("毛毛", reply)
        fact = next(f for f in bank.facts if f["key"] == "孙子")
        self.assertEqual(fact["value"], "毛毛")
        self.assertEqual(fact["history"][-1]["value"], "豆豆")
        # 旧值问句："我孙子之前叫啥来着" → 答旧值 + 补改后值（规则写死）
        reply2 = bank.handle_explicit_correction("我孙子之前叫啥来着", call_name="奶奶")
        self.assertIsNotNone(reply2)
        self.assertIn("豆豆", reply2)
        self.assertIn("毛毛", reply2)
        # 老人拿旧值反问："豆豆之前叫啥" → 旧值是以前的叫法
        reply3 = bank.handle_explicit_correction("豆豆之前叫啥来着", call_name="奶奶")
        self.assertIsNotNone(reply3)
        self.assertIn("毛毛", reply3)

        # 第二种句式："把豆豆改成毛毛"（按旧值定位）
        bank2, clock2 = _mk_bank(self)
        self._seed(bank2)
        reply4 = bank2.handle_explicit_correction("把豆豆改成毛毛", call_name="奶奶")
        self.assertIsNotNone(reply4)
        fact4 = next(f for f in bank2.facts if f["key"] == "孙子")
        self.assertEqual(fact4["value"], "毛毛")
        self.assertEqual(fact4["history"][-1]["value"], "豆豆")

    def test_passthrough_and_no_fabrication(self):
        """不是更正/旧值问句 → 放行（None）；没记过/没改过 → 不接管（不编造旧值）。"""
        bank, clock = _mk_bank(self)
        self._seed(bank)
        self.assertIsNone(bank.handle_explicit_correction("今天天气真好"))
        self.assertIsNone(bank.handle_explicit_correction("把电视声音调大点"))
        self.assertIsNone(bank.handle_explicit_correction("咱聊聊降压药的事"))
        # "邻居"从没记过 → 旧值问句不接管，交给正常聊天（绝不编"以前叫X"）
        self.assertIsNone(bank.handle_explicit_correction("邻居之前叫啥来着"))
        # 空 thing（"我之前叫啥来着"）→ 不接管
        self.assertIsNone(bank.handle_explicit_correction("我之前叫啥来着"))
        # 更正目标不存在但有明确 key → 记为新事实（老人亲口的最可靠）
        reply = bank.handle_explicit_correction("我孙子不叫豆豆，叫毛毛", call_name="奶奶")
        self.assertIsNotNone(reply)
        self.assertIn("毛毛", reply)

    def test_position_fact_guard(self):
        """模糊匹配护栏："把降压药改成饭后吃"不许覆写"降压药位置=药箱"。"""
        bank, clock = _mk_bank(self)
        self._seed(bank)
        reply = bank.handle_explicit_correction("把降压药改成饭后吃", call_name="奶奶")
        pos_fact = next(f for f in bank.facts if f["key"] == "降压药位置")
        self.assertEqual(pos_fact["value"], "药箱")       # 位置事实纹丝不动
        self.assertIsNotNone(reply)                        # 记成新事实（降压药=饭后吃）
        new_fact = next(f for f in bank.facts if f["key"] == "降压药")
        self.assertEqual(new_fact["value"], "饭后吃")


# ================================================================ 检索只读性

class TestSearchReadOnly(unittest.TestCase):

    def test_search_does_not_reinforce(self):
        """检索是只读的：翻时间线 ≠ 回忆强化，不许把记忆越翻越牢。"""
        bank, clock = _mk_bank(self)
        _fact(bank, "物品", "老花镜位置", "电视柜", "老花镜放在电视柜")
        before = {f["key"]: (f["recall_count"], f["last_recall"]) for f in bank.facts}
        bank.search("老花镜", by="object")
        bank.search("老花镜", by="object")
        after = {f["key"]: (f["recall_count"], f["last_recall"]) for f in bank.facts}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)

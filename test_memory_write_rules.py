# -*- coding: utf-8 -*-
"""B-4 记忆写入规则验收测试。

任务卡 B-4 验收标准（可在本仓库内验证的部分）：
  - 四类写入护栏可执行：敏感区/免看清单拒写、30 分钟去重、低置信只进短期缓存
  - 免看清单口头配置闭环（一句话增删，认不出不接管）
  - audit 自检：条目数/重复堆积/敏感词命中/拦截统计，规则出 bug 能被发现
  - 边界：护栏只管视觉写入路径，语音记忆（"卫生间摔了一跤"健康事件）不受影响
  （"模拟持续录制 24 小时"依赖 vision/ 数据源，归 H-2/B-2 线；本测试验证执行点规则）
"""
import os
import shutil
import tempfile
import unittest

from memory_system import (MemoryBank, SENSITIVE_SCENES, DEDUP_WINDOW_SECONDS,
                           VISUAL_HIGH_CONFIDENCE)


class FakeClock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t

    def tick(self, minutes=0, seconds=0):
        self.t += minutes * 60 + seconds
        return self.t


def _mk_bank(testcase):
    tmp = tempfile.mkdtemp()
    testcase.addCleanup(shutil.rmtree, tmp, True)
    clock = FakeClock()
    path = os.path.join(tmp, "memory_bank.json")
    return MemoryBank(path=path, llm=None, clock=clock), clock


def _episode(bank, text, field="其他", source="voice", scene="", importance=5):
    now = bank.clock()
    ep = {"id": f"ep{len(bank.episodes) + 1}", "text": text, "quote": text,
          "field": field, "ts": now, "importance": importance, "strength": 3.0,
          "recall_count": 0, "last_recall": now}
    if source:
        ep["source"] = source
    if scene:
        ep["scene"] = scene
    bank.episodes.append(ep)
    bank._save()
    return ep


# ================================================================ 免看清单：口头配置闭环

class TestIgnoreZoneChat(unittest.TestCase):

    def test_add_and_remove_sentences(self):
        """"卧室别看了"→加入；"卧室可以看了"→移除；后置句式"别看卧室了"也认。"""
        bank, clock = _mk_bank(self)
        reply = bank.handle_ignore_zone_request("卧室别看了", call_name="奶奶")
        self.assertIsNotNone(reply)
        self.assertIn("卧室", bank.ignore_zones)
        reply = bank.handle_ignore_zone_request("卧室可以看了", call_name="奶奶")
        self.assertIsNotNone(reply)
        self.assertNotIn("卧室", bank.ignore_zones)
        # 后置句式："别看卫生间了"
        reply = bank.handle_ignore_zone_request("别看卫生间了", call_name="奶奶")
        self.assertIsNotNone(reply)
        self.assertIn("卫生间", bank.ignore_zones)
        # 重复说"别看了"：不重复加入，仍给安心答复
        reply = bank.handle_ignore_zone_request("卫生间别看了", call_name="奶奶")
        self.assertIsNotNone(reply)
        self.assertEqual(bank.ignore_zones.count("卫生间"), 1)
        # 移除没关过的区域 → 提示本来就在看
        reply = bank.handle_ignore_zone_request("阳台可以看了", call_name="奶奶")
        self.assertIn("本来就在看", reply)

    def test_zone_name_normalization(self):
        """"卧室那屋别看了"归一到"卧室"；非敏感区（阳台/客厅）也可口头关闭。"""
        bank, clock = _mk_bank(self)
        reply = bank.handle_ignore_zone_request("卧室那屋别看了", call_name="奶奶")
        self.assertIsNotNone(reply)
        self.assertIn("卧室", bank.ignore_zones)
        bank.handle_ignore_zone_request("阳台别看了", call_name="奶奶")
        self.assertIn("阳台", bank.ignore_zones)
        bank.handle_ignore_zone_request("客厅别看了", call_name="奶奶")
        self.assertIn("客厅", bank.ignore_zones)

    def test_unrecognized_passthrough(self):
        """认不出的说法（"电视别看了"）不接管、不猜区域，放行正常聊天。"""
        bank, clock = _mk_bank(self)
        self.assertIsNone(bank.handle_ignore_zone_request("电视别看了"))
        self.assertIsNone(bank.handle_ignore_zone_request("今天天气不错"))
        self.assertIsNone(bank.handle_ignore_zone_request(
            "我跟你讲个特别长的事儿，从前有座山山里有座庙，庙里有个老爷爷"))
        self.assertEqual(bank.ignore_zones, [])


# ================================================================ 视觉写入护栏（四类规则执行点）

class TestVisualIngest(unittest.TestCase):

    def test_sensitive_scene_blocked(self):
        """敏感场景（卧室/卫生间/更衣…）视觉条目默认拒写——装机兜底防线。"""
        bank, clock = _mk_bank(self)
        for scene in ("卧室", "卫生间", "更衣区"):
            res = bank.ingest_visual_event("appear", "拐杖", scene, 0.95)
            self.assertEqual(res["action"], "blocked_by_zone", scene)
        self.assertEqual(bank.episodes, [])            # 一条都没进来
        self.assertEqual(bank.write_stats["blocked_by_zone"], 3)

    def test_ignore_zone_blocked_by_oral_config(self):
        """口头关闭的非敏感区（"阳台别看了"）→ 该区域视觉条目拒写。"""
        bank, clock = _mk_bank(self)
        bank.handle_ignore_zone_request("阳台别看了", call_name="奶奶")
        res = bank.ingest_visual_event("appear", "花盆", "阳台", 0.9)
        self.assertEqual(res["action"], "blocked_by_zone")
        # 其他区域不受影响
        res = bank.ingest_visual_event("appear", "降压药", "客厅", 0.9)
        self.assertEqual(res["action"], "written")

    def test_confidence_grading(self):
        """分级写入：≥0.8 进长期记忆（episodes）；<0.8 只进短期缓存（检索不可见）。"""
        bank, clock = _mk_bank(self)
        res = bank.ingest_visual_event("place", "降压药", "客厅", 0.87, furniture="茶几")
        self.assertEqual(res["action"], "written")
        ep = bank.episodes[-1]
        self.assertEqual(ep["source"], "visual")
        self.assertEqual(ep["scene"], "客厅")
        self.assertIn("降压药", ep["text"])
        # 高置信条目可被 B-5 检索到（长期记忆有效）
        out = bank.search("降压药", by="object")
        self.assertTrue(out["results"])
        # 低置信：进短期缓存，不进 episodes、检索不可见
        res = bank.ingest_visual_event("appear", "钥匙", "客厅", 0.5)
        self.assertEqual(res["action"], "buffered")
        self.assertEqual(len(bank.episodes), 1)
        self.assertEqual(len(bank.short_term_buffer), 1)
        self.assertEqual(bank.write_stats["low_conf_buffered"], 1)
        out = bank.search("钥匙", by="object")
        self.assertEqual(out["results"], [])

    def test_dedup_window_30_minutes(self):
        """同物品+同动作+同位置 30 分钟内只写一条；窗口外放行；不同位置不拦。"""
        bank, clock = _mk_bank(self)
        t0 = clock()
        res = bank.ingest_visual_event("appear", "老花镜", "客厅", 0.9, ts=t0)
        self.assertEqual(res["action"], "written")
        res = bank.ingest_visual_event("appear", "老花镜", "客厅", 0.9, ts=t0 + 60)
        self.assertEqual(res["action"], "blocked_by_dedup")
        res = bank.ingest_visual_event("appear", "老花镜", "客厅", 0.9,
                                        ts=t0 + DEDUP_WINDOW_SECONDS - 1)
        self.assertEqual(res["action"], "blocked_by_dedup")
        # 窗口外（30 分钟过线）→ 放行
        res = bank.ingest_visual_event("appear", "老花镜", "客厅", 0.9,
                                        ts=t0 + DEDUP_WINDOW_SECONDS + 1)
        self.assertEqual(res["action"], "written")
        # 不同位置/不同动作不受同窗口影响
        res = bank.ingest_visual_event("appear", "老花镜", "厨房", 0.9, ts=t0 + 60)
        self.assertEqual(res["action"], "written")
        res = bank.ingest_visual_event("take", "老花镜", "客厅", 0.9, ts=t0 + 60)
        self.assertEqual(res["action"], "written")
        self.assertEqual(len(bank.episodes), 4)
        self.assertEqual(bank.write_stats["blocked_by_dedup"], 2)

    def test_invalid_event(self):
        """缺物品名的帧条目拒收（脏数据不入库）。"""
        bank, clock = _mk_bank(self)
        res = bank.ingest_visual_event("appear", "", "客厅", 0.9)
        self.assertEqual(res["action"], "invalid")


# ================================================================ audit 自检 + 边界

class TestHealthAudit(unittest.TestCase):

    def test_healthy_and_blocked_stats(self):
        """正常运行：audit 返回 healthy；拦截/写入/缓存计数与实际一致。"""
        bank, clock = _mk_bank(self)
        bank.ingest_visual_event("appear", "降压药", "客厅", 0.9)
        bank.ingest_visual_event("appear", "降压药", "客厅", 0.9)   # 30 分钟内重复 → 拦
        bank.ingest_visual_event("appear", "钥匙", "客厅", 0.5)    # 低置信 → 缓存
        bank.ingest_visual_event("appear", "拐杖", "卧室", 0.95)   # 敏感区 → 拒
        audit = bank.health_audit()
        self.assertEqual(audit["status"], "healthy")
        self.assertEqual(audit["issues"], [])
        self.assertEqual(audit["counts"]["visual_episodes"], 1)
        self.assertEqual(audit["counts"]["short_term_buffer"], 1)
        self.assertEqual(audit["blocked"]["blocked_by_dedup"], 1)
        self.assertEqual(audit["blocked"]["blocked_by_zone"], 1)
        self.assertEqual(audit["blocked"]["low_conf_buffered"], 1)
        self.assertEqual(audit["blocked"]["visual_written"], 1)

    def test_sensitive_leak_detected(self):
        """漏网的敏感视觉条目（模拟规则失效）→ audit 报 sensitive_leak。"""
        bank, clock = _mk_bank(self)
        _episode(bank, "看见扫帚在卧室墙角", field="物品", source="visual", scene="卧室")
        audit = bank.health_audit()
        self.assertEqual(audit["status"], "attention")
        self.assertIn("sensitive_leak", audit["issues"])
        self.assertEqual(audit["sensitive_hits"], 1)

    def test_visual_duplicate_detected(self):
        """视觉条目 text 完全重复堆积（去重规则失效的信号）→ audit 报 visual_duplicate。"""
        bank, clock = _mk_bank(self)
        _episode(bank, "看见降压药在客厅", field="物品", source="visual", scene="客厅")
        _episode(bank, "看见降压药在客厅", field="物品", source="visual", scene="客厅")
        audit = bank.health_audit()
        self.assertEqual(audit["status"], "attention")
        self.assertIn("visual_duplicate", audit["issues"])
        self.assertEqual(audit["visual_duplicate_pairs"], 1)

    def test_voice_memories_not_scanned(self):
        """边界：语音记忆不经视觉护栏——老人说"我在卫生间摔了一跤"必须能记，
        且 audit 的敏感词命中只扫视觉条目。"""
        bank, clock = _mk_bank(self)
        _episode(bank, "在卫生间摔了一跤，不碍事", field="健康", source="voice")
        _episode(bank, "卧室的床单该换了", field="生活", source="voice")
        audit = bank.health_audit()
        self.assertEqual(audit["sensitive_hits"], 0)   # 语音条目不算敏感泄漏
        self.assertEqual(audit["status"], "healthy")

    def test_state_persistence(self):
        """免看清单/拦截统计/短期缓存/去重窗口落盘可恢复。"""
        bank, clock = _mk_bank(self)
        bank.handle_ignore_zone_request("阳台别看了", call_name="奶奶")
        bank.ingest_visual_event("appear", "钥匙", "客厅", 0.5)
        bank.ingest_visual_event("appear", "拐杖", "卧室", 0.95)
        bank2 = MemoryBank(path=bank.path, llm=None, clock=clock)
        self.assertEqual(bank2.ignore_zones, ["阳台"])
        self.assertEqual(bank2.write_stats["blocked_by_zone"], 1)
        self.assertEqual(len(bank2.short_term_buffer), 1)
        self.assertTrue(bank2.visual_dedup_log)


if __name__ == "__main__":
    unittest.main(verbosity=2)

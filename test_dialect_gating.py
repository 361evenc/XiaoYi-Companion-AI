# -*- coding: utf-8 -*-
"""F-8 方言识别闸门测试（任务卡验收口径，可第二人执行）。

跑法（项目根）：
    python test_dialect_gating.py
    python test_dialect_gating.py --runner 李四

覆盖：
  1. 没有分布统计 / 没有 WER → hold，一个方言都不上线（不猜、不硬上）
  2. 上线清单 = 分布 Top3 ∩ WER ≤ 基线×0.8 ∩ 有引擎参数（缺一不上）
  3. 未上线方言：ASR 过闸门后被拒绝、不调引擎，话术是「我还没学会听X方言，等我学好了」
  4. 上线方言：只带后端选型给的引擎参数识别（前端/引擎不自己猜）
  5. 声音复刻默认关、没有任何复刻接口（防顺手做）
  6. WER 计算与 Top3 占比口径（dialect_report 的纯函数）
"""
import argparse
import base64
import json
import os
import shutil
import sys
import tempfile
import unittest

from backend.services import dialect_service, voice_service
import dialect_report


def _write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _dist(top3, complete=True, total=10):
    return {
        "generated_at": "2026-11-08 10:00",
        "total_labeled": total,
        "top3": [{"name": n, "count": c, "share": round(c / total, 4)} for n, c in top3],
        "complete": complete,
        "channels": {},
        "missing": [] if complete else ["通道②缺失"],
    }


def _eval(entries):
    return {"generated_at": "2026-11-08 10:00", "dialects": entries, "missing": []}


class DialectGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._old = (dialect_service.DIST_FILE, dialect_service.EVAL_FILE,
                     dialect_service.PROFILE_FILE)
        dialect_service.DIST_FILE = os.path.join(self.tmp, "dist.json")
        dialect_service.EVAL_FILE = os.path.join(self.tmp, "eval.json")
        dialect_service.PROFILE_FILE = os.path.join(self.tmp, "profile.json")
        self._old_engine = voice_service.engine

    def tearDown(self):
        (dialect_service.DIST_FILE, dialect_service.EVAL_FILE,
         dialect_service.PROFILE_FILE) = self._old
        voice_service.engine = self._old_engine

    # -------------------------------------------------------------- 1
    def test_01_no_data_means_hold(self):
        """没有任何统计/实测 → hold，普通话照常，任何方言都诚实拒绝。"""
        cfg = dialect_service.config()
        self.assertTrue(cfg["hold"], "没有数据必须 hold")
        self.assertEqual(cfg["online"], [])
        self.assertEqual(cfg["top3"], [])
        self.assertTrue(all(not o["supported"] for o in cfg["options"]))

        gate = dialect_service.asr_gate("河南话")
        self.assertTrue(gate["refused"])
        self.assertEqual(gate["dialect"], "河南方言")
        self.assertEqual(gate["reply"], "我还没学会听河南方言，等我学好了。")
        self.assertIsNone(gate["hint"])

        self.assertFalse(dialect_service.asr_gate("普通话")["refused"],
                         "普通话不能被方言闸门误伤")

    # -------------------------------------------------------------- 2
    def test_02_online_is_intersection_with_wer_gate(self):
        """Top3 ∩ WER 达标 ∩ 有引擎参数；缺引擎参数的达标方言仍不上线。"""
        _write(dialect_service.DIST_FILE,
               _dist([("河南方言", 5), ("东北方言", 3), ("山东方言", 2)]))
        _write(dialect_service.EVAL_FILE, _eval([
            {"name": "河南方言", "baseline_wer": 0.20, "wer": 0.15,
             "corpus_size": 20, "asr": {"cluster": "TEST_CLUSTER", "language": "zh-CN"}},
            {"name": "东北方言", "baseline_wer": 0.20, "wer": 0.19,
             "corpus_size": 20, "asr": {"cluster": "TEST_CLUSTER"}},
            {"name": "山东方言", "baseline_wer": 0.20, "wer": 0.16,
             "corpus_size": 20},   # WER 达标，但后端没给引擎参数
        ]))

        cfg = dialect_service.config()
        self.assertEqual(cfg["top3"], ["河南方言", "东北方言", "山东方言"])
        self.assertEqual(cfg["wer_pass"], ["河南方言", "山东方言"])
        self.assertEqual(cfg["online"], ["河南方言"], "缺引擎参数不能上线")
        self.assertFalse(cfg["hold"])

        by_name = {o["name"]: o for o in cfg["options"]}
        self.assertTrue(by_name["河南方言"]["supported"])
        self.assertIn("WER", by_name["东北方言"]["reason"])
        self.assertIn("引擎参数", by_name["山东方言"]["reason"])

        gate = dialect_service.asr_gate("河南话")
        self.assertFalse(gate["refused"])
        self.assertEqual(gate["mode"], "dialect")
        self.assertEqual(gate["dialect"], "河南方言")

    # -------------------------------------------------------------- 3
    def test_03_empty_intersection_holds_and_refuses_honestly(self):
        """Top3 有、WER 全不达标 → 交集为空 → hold，不硬上。"""
        _write(dialect_service.DIST_FILE, _dist([("河南方言", 6), ("东北方言", 4)]))
        _write(dialect_service.EVAL_FILE, _eval([
            {"name": "河南方言", "baseline_wer": 0.20, "wer": 0.21, "corpus_size": 20},
            {"name": "东北方言", "baseline_wer": 0.20, "wer": 0.18,
             "asr": {"cluster": "TEST_CLUSTER"}},
        ]))
        cfg = dialect_service.config()
        self.assertTrue(cfg["hold"])
        self.assertEqual(cfg["online"], [])

        gate = dialect_service.asr_gate("东北方言")
        self.assertTrue(gate["refused"], "WER 未达标必须拒绝，不能硬上")
        self.assertEqual(gate["reply"], "我还没学会听东北方言，等我学好了。")

    # -------------------------------------------------------------- 4
    def test_04_asr_refusal_never_calls_engine(self):
        """拒绝路径不调 ASR 引擎；上线路径只带后端给的 hint。"""
        class FakeEngine:
            def __init__(self):
                self.calls = []
            def transcribe_audio(self, wav, dialect_hint=None):
                self.calls.append(dialect_hint)
                return "识别结果"

        fake = FakeEngine()
        voice_service.engine = fake
        audio = base64.b64encode(b"\x00" * 400).decode()

        # 没有数据 → 拒绝，引擎一次都不该被调
        res = voice_service.asr(audio, dialect="河南方言")
        self.assertTrue(res["refused"])
        self.assertEqual(res["text"], "")
        self.assertEqual(fake.calls, [], "未上线方言不能送去识别")

        # 打开一个上线方言 → 只带后端选型给的参数
        _write(dialect_service.DIST_FILE, _dist([("河南方言", 10)]))
        _write(dialect_service.EVAL_FILE, _eval([
            {"name": "河南方言", "baseline_wer": 0.2, "wer": 0.1, "corpus_size": 10,
             "asr": {"cluster": "TEST_CLUSTER", "language": "zh-CN"}},
        ]))
        res = voice_service.asr(audio, dialect="河南方言")
        self.assertFalse(res["refused"])
        self.assertEqual(res["text"], "识别结果")
        self.assertEqual(fake.calls, [{"cluster": "TEST_CLUSTER", "language": "zh-CN"}])

        # 普通话不带 hint（方言参数不能顺手塞给普通话）
        res = voice_service.asr(audio)
        self.assertEqual(fake.calls[-1], None)

    # -------------------------------------------------------------- 5
    def test_05_voice_clone_default_off_and_no_endpoint(self):
        """声音复刻默认关、没有任何复刻接口（本卡不做，防顺手做）。"""
        self.assertFalse(dialect_service.VOICE_CLONE["enabled"])
        self.assertIn("显式同意", "".join(dialect_service.VOICE_CLONE["requires"]))

        cfg = dialect_service.config()
        self.assertFalse(cfg["voice_clone"]["enabled"])

        from backend.api import dialect_api
        paths = " ".join(r.path for r in dialect_api.router.routes).lower()
        self.assertNotIn("clone", paths)
        self.assertNotIn("voice", paths)

    # -------------------------------------------------------------- 6
    def test_06_wer_and_top3_math(self):
        """WER = 字级编辑距离；Top3 占比只算带标注样本。"""
        self.assertEqual(dialect_report.wer("今天吃药", "今天吃药"), 0.0)
        self.assertAlmostEqual(dialect_report.wer("今天吃药", "今天吃约"), 0.25)
        self.assertAlmostEqual(dialect_report.wer("今天吃药", ""), 1.0)
        self.assertAlmostEqual(
            dialect_report.wer_lines(["今天吃药", "血压高"], ["今天吃约", "血压高"]), 1 / 7)

        counts = {"河南方言": 5, "东北方言": 3, "山东方言": 2, "湖北方言": 1}
        top3 = dialect_report.top3_from_counts(counts)
        self.assertEqual([t["name"] for t in top3], ["河南方言", "东北方言", "山东方言"])
        self.assertAlmostEqual(top3[0]["share"], 5 / 11, places=4)
        self.assertAlmostEqual(sum(t["share"] for t in top3), 10 / 11, places=3)  # 只取前三

    # -------------------------------------------------------------- 7
    def test_07_profile_field_is_collection_only(self):
        """通道③只进统计：填了也不会让方言直接上线。"""
        res = dialect_service.set_profile("河南方言", by="child")
        self.assertTrue(res["ok"])
        self.assertEqual(dialect_service.profile()["elder_dialect"], "河南方言")

        self.assertFalse(dialect_service.set_profile("闽南语")["ok"],
                         "表单里没有的方言要拒绝，不能猜着收")
        self.assertFalse(dialect_service.set_profile("其他方言", other="")["ok"],
                         "选了「其他方言」必须写清是哪种")

        cfg = dialect_service.config()
        self.assertTrue(cfg["hold"], "表单填了不等于上线：仍要看分布/WER")
        self.assertTrue(dialect_service.asr_gate("河南方言")["refused"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--runner", default="")
    args, passthrough = ap.parse_known_args()
    prog = unittest.main(argv=[sys.argv[0]] + passthrough, exit=False)
    print(f"\n本次执行人：{args.runner or '（未填）'}")
    print("第二人复核签字：____________（闸门验收不是自己测自己）")
    sys.exit(0 if prog.result.wasSuccessful() else 1)

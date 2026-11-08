# -*- coding: utf-8 -*-
"""F-7 子女端权限测试（任务卡验收：5 条用例，第二人执行）。

跑法（项目根）：
    python test_child_privacy.py
第二人复核时把名字带上，输出里会留签字行：
    python test_child_privacy.py --runner 张三

5 条用例（与任务卡逐条对应）：
  1. 摘要无聊天字段：summary 里没有任何 message/chat/conversation/history 字段，
     服务源码里也没有读取会话的能力（不是前端不画，是后端拿不到）
  2. 无会话路由：/api/child/* 的路径白名单里没有 conversations/chat/history
  3. 关闭即隐藏：安心开关一关，summary 只剩 enabled=false，不带任何概况
  4. 无老人记忆写入口：没有 DELETE，POST 只有开关/告知/回执/已读，且不调提醒写操作
  5. 回执不改提醒且生成通知：处理购药待办只记回执 + 给老人一条「买了药」通知

本测试用假数据源（不拉起引擎、不碰真实提醒/情绪文件），只看服务层的权限口径。
"""
import argparse
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest

from backend.services import child_service
from backend.api import child_api

NOW = 1_800_000_000.0   # 固定时钟，避免跨天导致「今日概况」缓存命中不稳定

FAKE_REMINDERS = {
    "awaiting": [{"id": "r_med_1", "desc": "每天早上8点 吃降压药", "repeat": {"type": "daily"}}],
    "pending": [{"id": "r_med_2", "desc": "下午三点 吃药", "eta": "2小时后"}],
    "stats": {"total": 0, "done": 0, "rate": 0},
    "notices": [],
}

FAKE_EMOTION = [
    {"time": NOW - 6 * 3600, "label": "low", "score": -0.6, "note": "情绪低落"},
    {"time": NOW - 30 * 3600, "label": "neutral", "score": 0.0, "note": ""},
]

FORBIDDEN_KEYS = {"chat", "chats", "message", "messages", "conversation",
                  "conversations", "history", "transcript", "chat_history", "conv_id"}
FORBIDDEN_PATH_WORDS = ("conversation", "conversations", "chat", "history", "message")


def _walk_keys(obj):
    keys = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(str(k))
            keys |= _walk_keys(v)
    elif isinstance(obj, list):
        for it in obj:
            keys |= _walk_keys(it)
    return keys


class ChildPrivacyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._old_file = child_service.CHILD_FILE
        self._old_providers = (child_service._reminder_list, child_service._emotion_log,
                               child_service._fall_state, child_service._elder_chart_url)
        child_service.CHILD_FILE = os.path.join(self.tmp, "child_portal.json")
        child_service._reminder_list = lambda: copy.deepcopy(FAKE_REMINDERS)
        child_service._emotion_log = lambda: copy.deepcopy(FAKE_EMOTION)
        child_service._fall_state = lambda: "idle"
        child_service._elder_chart_url = lambda: "/audio/emotion_test.png"

    def tearDown(self):
        child_service.CHILD_FILE = self._old_file
        (child_service._reminder_list, child_service._emotion_log,
         child_service._fall_state, child_service._elder_chart_url) = self._old_providers

    # -------------------------------------------------------------- 1
    def test_01_summary_has_no_chat_fields(self):
        """摘要里没有会话字段，源码也没有读写会话的能力。"""
        s = child_service.summary(now=NOW)
        self.assertTrue(s["enabled"])
        self.assertFalse(_walk_keys(s) & FORBIDDEN_KEYS,
                         f"摘要里出现了会话字段：{_walk_keys(s) & FORBIDDEN_KEYS}")

        with open(child_service.__file__, "r", encoding="utf-8") as f:
            body = f.read()
        body = body.split('"""')[2] if body.startswith('"""') else body   # 去掉模块文档
        for token in ("conversations", "chat_history", "load_conversation",
                      "conv_id", "get_conversation"):
            self.assertNotIn(token, body, f"child_service 不应出现 {token}")

    # -------------------------------------------------------------- 2
    def test_02_no_conversation_routes(self):
        """子女端只有白名单路由，没有会话/聊天路由。"""
        paths = {r.path for r in child_api.router.routes}
        expected = {
            "/api/child/summary",
            "/api/child/consent",
            "/api/child/consent/notified",
            "/api/child/purchase/{rid}/receipt",
            "/api/child/notices",
            "/api/child/notices/{nid}/ack",
        }
        self.assertEqual(paths, expected)
        for p in paths:
            for word in FORBIDDEN_PATH_WORDS:
                self.assertNotIn(word, p, f"路由 {p} 含会话词 {word}")

    # -------------------------------------------------------------- 3
    def test_03_consent_off_hides_everything(self):
        """默认开；关掉后只回 enabled=false，不泄露任何概况。"""
        self.assertTrue(child_service.consent()["enabled"], "安心开关必须默认开")

        child_service.set_consent(False)
        s = child_service.summary(now=NOW)
        self.assertFalse(s["enabled"])
        self.assertEqual(s["closed_text"], "已关闭")
        self.assertLessEqual(set(s.keys()),
                             {"ok", "enabled", "closed_text", "closed_hint", "consent"})
        for k in ("today", "health", "emotion", "purchase", "privacy"):
            self.assertNotIn(k, s, f"关闭后不应返回 {k}")

        child_service.set_consent(True)
        self.assertTrue(child_service.summary(now=NOW)["enabled"])

    # -------------------------------------------------------------- 4
    def test_04_no_elder_memory_write_entry(self):
        """没有写老人记忆的入口，也不调提醒的写操作。"""
        methods = {}
        for r in child_api.router.routes:
            methods.setdefault(r.path, set()).update(r.methods or [])
        self.assertNotIn("DELETE", set().union(*methods.values()), "子女端不应有 DELETE 入口")
        post_paths = {p for p, m in methods.items() if "POST" in m}
        self.assertEqual(post_paths, {
            "/api/child/consent",
            "/api/child/consent/notified",
            "/api/child/purchase/{rid}/receipt",
            "/api/child/notices/{nid}/ack",
        })

        with open(child_service.__file__, "r", encoding="utf-8") as f:
            body = f.read()
        for token in ("wipe_all_memory", "wipe", "forget(", "memory_bank", "save_memory",
                      "user_memory", "reminder_service.done", "reminder_service.cancel",
                      "reminder_store.add", "reminder_store.confirm"):
            self.assertNotIn(token, body, f"child_service 不应出现写操作 {token}")

    # -------------------------------------------------------------- 5
    def test_05_receipt_keeps_reminder_and_notifies_elder(self):
        """回执只记录 + 生成通知，提醒原样不动（老人用药自主权）。"""
        before = copy.deepcopy(FAKE_REMINDERS)
        res = child_service.purchase_receipt("r_med_1")
        self.assertTrue(res["ok"])
        self.assertEqual(child_service._reminder_list(), before, "回执不得改提醒")

        ns = child_service.notices()["notices"]
        self.assertEqual(len(ns), 1)
        self.assertFalse(ns[0]["acked"])
        self.assertIn("买了药", ns[0]["text"])
        self.assertIn("吃降压药", ns[0]["text"])

        self.assertTrue(child_service.ack_notice(ns[0]["id"])["ok"])
        self.assertTrue(child_service.notices()["notices"][0]["acked"])

        self.assertFalse(child_service.purchase_receipt("不存在")["ok"], "找不到的待办要诚实拒绝")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--runner", default="")
    args, passthrough = ap.parse_known_args()
    prog = unittest.main(argv=[sys.argv[0]] + passthrough, exit=False)
    print(f"\n本次执行人：{args.runner or '（未填）'}")
    print("第二人复核签字：____________（任务卡要求不是自己测自己）")
    sys.exit(0 if prog.result.wasSuccessful() else 1)

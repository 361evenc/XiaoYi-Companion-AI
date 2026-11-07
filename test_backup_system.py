# -*- coding: utf-8 -*-
"""B-6 备份加固验收测试（任务卡 B-6 数据安全部分，本机可落地子集）。

验收口径（B-6 验收标准第 3 条）：
  - 备份实际跑通 + 恢复演练一次（真恢复一份验证）
  - 备份监控自检可用（模拟备份失败/超 24h → 触发告警）
  - 访问日志有实际写入
  - 原子写（先写临时文件再 rename——恢复拿不到写一半的文件）
  - 保留最近 7 份
  - B-11 紧急联系人文件在备份清单内
"""
import json
import os
import shutil
import tempfile
import time
import unittest

import backup_system
from backup_system import BackupManager, DEFAULT_BACKUP_FILES


class FakeClock:
    def __init__(self, base=None):
        self.t = base or time.mktime((2026, 11, 6, 9, 0, 0, 0, 0, -1))

    def __call__(self):
        return self.t

    def tick(self, hours=0):
        self.t += hours * 3600
        return self.t


def _mk_env(testcase, keep=7):
    """独立数据目录 + 备份目录，互不污染生产 backups/。"""
    data = tempfile.mkdtemp()
    bkp = tempfile.mkdtemp()
    testcase.addCleanup(shutil.rmtree, data, True)
    testcase.addCleanup(shutil.rmtree, bkp, True)
    clock = FakeClock()
    mgr = BackupManager(data_dir=data, backup_dir=bkp, keep=keep, now=clock)
    return mgr, data, bkp, clock


def _write(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


class TestBackupAndKeep(unittest.TestCase):
    """备份跑通 + 滚动保留 7 份。"""

    def test_backup_ok(self):
        mgr, data, bkp, clock = _mk_env(self)
        _write(os.path.join(data, "memory_bank.json"), {"facts": [1]})
        res = mgr.backup_file("memory_bank.json")
        self.assertTrue(res["ok"])
        self.assertTrue(os.path.exists(os.path.join(bkp, res["backup"])))

    def test_missing_file_not_failure(self):
        mgr, data, _, _ = _mk_env(self)
        res = mgr.backup_file("不存在的.json")
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "not_found")

    def test_keep_rolling(self):
        """保留最近 7 份：第 8 份进来，最老的被清走。"""
        mgr, data, bkp, clock = _mk_env(self, keep=7)
        p = os.path.join(data, "memory_bank.json")
        for i in range(9):
            _write(p, {"i": i})
            mgr.backup_file("memory_bank.json", now=clock())
            clock.tick(hours=1)                     # 时间戳推进，文件名不撞
        kept = mgr._backups_of("memory_bank.json")
        self.assertEqual(len(kept), 7)              # B-6 写死：保留最近 7 份

    def test_emergency_contact_in_default_files(self):
        """B-11 紧急联系人（安全数据）必须在备份清单内。"""
        self.assertIn("emergency_contact.json", DEFAULT_BACKUP_FILES)

    def test_backup_all(self):
        mgr, data, bkp, clock = _mk_env(self)
        for name in DEFAULT_BACKUP_FILES:
            with open(os.path.join(data, name), "w", encoding="utf-8") as f:
                f.write("{}" if name.endswith(".json") else "profile")
        res = mgr.backup_all()
        self.assertEqual(len(res["backed"]), len(DEFAULT_BACKUP_FILES))
        self.assertEqual(res["missing"], [])


class TestRestoreDrill(unittest.TestCase):
    """恢复演练（B-6 验收：真恢复一份验证）。"""

    def test_restore_latest(self):
        mgr, data, bkp, clock = _mk_env(self)
        p = os.path.join(data, "memory_bank.json")
        _write(p, {"v": "旧数据"})
        mgr.backup_file("memory_bank.json")
        clock.tick(hours=1)
        _write(p, {"v": "新数据（模拟损坏/误删）"})     # 生产文件被改坏
        res = mgr.restore("memory_bank.json")
        self.assertTrue(res["ok"])
        with open(p, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["v"], "旧数据")   # 真恢复成功

    def test_restore_by_timestamp(self):
        mgr, data, bkp, clock = _mk_env(self)
        p = os.path.join(data, "conversations.json")
        _write(p, {"a": 1})
        first_day = time.strftime("%Y%m%d", time.localtime(clock()))
        mgr.backup_file("conversations.json")
        clock.tick(hours=25)                      # 跨天，文件名时间戳不同日
        _write(p, {"a": 2})
        mgr.backup_file("conversations.json")
        res = mgr.restore("conversations.json", timestamp=first_day)
        self.assertTrue(res["ok"])
        with open(p, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"a": 1})     # 恢复到指定那份

    def test_corrupt_backup_rejected(self):
        """损坏的备份拒绝恢复（坏备份不覆盖好数据）。"""
        mgr, data, bkp, clock = _mk_env(self)
        p = os.path.join(data, "memory_bank.json")
        _write(p, {"good": True})
        mgr.backup_file("memory_bank.json")
        bak = os.path.join(bkp, mgr._backups_of("memory_bank.json")[-1])
        with open(bak, "w", encoding="utf-8") as f:
            f.write("{写一半就断电了")                      # 模拟损坏
        res = mgr.restore("memory_bank.json")
        self.assertFalse(res["ok"])
        self.assertIn("backup_corrupt", res["reason"])
        with open(p, encoding="utf-8") as f:               # 生产文件没被动
            self.assertEqual(json.load(f), {"good": True})

    def test_restore_no_backup(self):
        mgr, _, _, _ = _mk_env(self)
        res = mgr.restore("never_backed.json")
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "no_backup")


class TestHealthMonitor(unittest.TestCase):
    """备份监控（静默失败=没备份）：>24h 告警。"""

    def test_never_backed_up_alerts(self):
        mgr, _, _, _ = _mk_env(self)
        h = mgr.health()
        self.assertEqual(h["status"], "alert")
        self.assertIn("never_backed_up", h["issues"])

    def test_fresh_backup_ok(self):
        mgr, data, _, clock = _mk_env(self)
        _write(os.path.join(data, "memory_bank.json"), {})
        mgr.backup_file("memory_bank.json")
        h = mgr.health()
        self.assertEqual(h["status"], "warn")      # ok 但无异地配置 → warn
        self.assertIn("no_offsite_target", h["issues"])
        self.assertNotIn("backup_stale_over_24h", h["issues"])

    def test_stale_over_24h_alerts(self):
        """模拟备份失败：上次成功 >24h → alert（B-6 验收：模拟失败触发告警）。"""
        mgr, data, _, clock = _mk_env(self)
        _write(os.path.join(data, "memory_bank.json"), {})
        mgr.backup_file("memory_bank.json")
        clock.tick(hours=25)                        # 25 小时没备份成功
        h = mgr.health()
        self.assertEqual(h["status"], "alert")
        self.assertIn("backup_stale_over_24h", h["issues"])

    def test_offsite_configured_removes_warn(self):
        mgr, data, _, _ = _mk_env(self)
        mgr.remote_target = "E:/xiaoyi_offsite"     # 配了异地（移动硬盘/云盘）
        _write(os.path.join(data, "memory_bank.json"), {})
        mgr.backup_file("memory_bank.json")
        h = mgr.health()
        self.assertEqual(h["status"], "ok")
        self.assertTrue(h["has_offsite"])


class TestAccessLogAndAtomic(unittest.TestCase):
    """访问日志（可审计）+ 原子写纪律。"""

    def test_access_log_written(self):
        mgr, data, _, _ = _mk_env(self)
        _write(os.path.join(data, "memory_bank.json"), {})
        mgr.backup_file("memory_bank.json")
        mgr.restore("memory_bank.json")
        with open(mgr.access_log_path, encoding="utf-8") as f:
            lines = [json.loads(x) for x in f if x.strip()]
        actions = [e["action"] for e in lines]
        self.assertIn("backup", actions)
        self.assertIn("restore", actions)
        for e in lines:
            self.assertIn("ts", e)                 # 谁在什么时候动过数据

    def test_state_json_parses(self):
        """state 原子写纪律：落盘的是完整 JSON（不是写一半的）。"""
        mgr, data, _, _ = _mk_env(self)
        _write(os.path.join(data, "memory_bank.json"), {})
        mgr.backup_file("memory_bank.json")
        with open(mgr.state_path, encoding="utf-8") as f:
            state = json.load(f)                    # 解析失败 = 原子写破了
        self.assertGreater(state["last_success_ts"], 0)

    def test_no_tmp_left_after_backup(self):
        """备份/状态写完后无 .tmp 残留（原子写：临时文件要么成功 rename 要么清理）。"""
        mgr, data, bkp, _ = _mk_env(self)
        _write(os.path.join(data, "memory_bank.json"), {})
        mgr.backup_file("memory_bank.json")
        leftovers = [n for n in os.listdir(bkp) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [])


class TestDefaultManager(unittest.TestCase):
    """进程级默认实例：路径与生产一致（项目根/backups）。"""

    def test_default_manager_singleton(self):
        m1 = backup_system.default_manager()
        m2 = backup_system.default_manager()
        self.assertIs(m1, m2)
        self.assertEqual(m1.backup_dir, backup_system.DEFAULT_BACKUP_DIR)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""
T-3 冷启动编排（Day1-7 脚本）：把"装完不会用"变成"一天学一件事"。

设计约束：
- 仅依赖标准库（可单元测试）
- 时间计算基于调用方注入的统一时钟，杜绝跨机时区歧义
- 冷启动状态原子落盘（防断电丢进度）
- Day 1 紧急联系人 = 安全关键（essential，不跳过）；其余任务可跳过
- 依赖 T-4（用药）+ T-5（紧急联系人）：Day 1 编排调用它们的 store（不重复实现）

设计决定：
    1. 一天学一件事，不堆功能 —— 老人一次记不住一堆，每天只推一个
    2. essential 任务不跳过 —— Day 1 紧急联系人是安全底线，跳了出事了没人接
    3. 超 7 天自动结束 —— 进日常使用，不再"教"，改"陪"
    4. 状态落盘 —— 重启不丢进度（老人 Day 3 了重启不该回 Day 1）

用法：
    python cold_start.py --self-test
"""
import json
import os
import threading
import time as _time


class ColdStartOrchestrator:
    """T-3 冷启动编排（Day1-7 脚本）。

    - 激活后前 7 天，每天学一件事（不堆功能）
    - Day 1 紧急联系人 = 安全关键（essential，不跳过）
    - 其余任务可跳过（老人说"不用了"）
    - 状态原子落盘（防断电丢进度）
    """

    # Day1-7 脚本（每天学一件事，不堆功能）
    # essential=True 的任务不允许跳过（安全关键）；depends 标注依赖哪张策略卡
    DAY_SCRIPT = {
        1: {
            "theme": "认识小忆 + 安全底线",
            "tasks": [
                {"id": "greeting", "type": "greeting", "essential": True,
                 "text": "您好，我是小忆，以后我陪着您。"},
                {"id": "emergency_contact", "type": "emergency_contact",
                 "essential": True, "depends": "T-5",
                 "text": "先记个紧急联系人，真有事我马上帮他打。您报个名字和电话。"},
                {"id": "medication", "type": "medication", "essential": False,
                 "depends": "T-4",
                 "text": "您平时吃什么药？跟我说一声，我帮您记着，快用完提醒您买。"},
            ],
        },
        2: {
            "theme": "记住一件事",
            "tasks": [
                {"id": "remember", "type": "memory", "essential": False,
                 "text": "跟我说一件您想让我记着的事，比如'我儿子周三来接我'。"},
            ],
        },
        3: {
            "theme": "定个提醒",
            "tasks": [
                {"id": "reminder", "type": "reminder", "essential": False,
                 "text": "跟我说个想让我提醒您做的事，比如'明天下午三点吃药'。"},
            ],
        },
        4: {
            "theme": "问个天气",
            "tasks": [
                {"id": "weather", "type": "weather", "essential": False,
                 "text": "您出门前可以问我'今天冷不冷'，我告诉您带不带伞。"},
            ],
        },
        5: {
            "theme": "语音聊天",
            "tasks": [
                {"id": "voice", "type": "voice", "essential": False,
                 "text": "您不用打字，直接跟我说就行，我听您说话。"},
            ],
        },
        6: {
            "theme": "翻翻旧事",
            "tasks": [
                {"id": "recall", "type": "memory_recall", "essential": False,
                 "text": "您忘了什么事，问我'我那个老花镜搁哪了'，我帮您想起来。"},
            ],
        },
        7: {
            "theme": "回顾一周",
            "tasks": [
                {"id": "review", "type": "review", "essential": False,
                 "text": "这一周我陪着您，您想让我以后多做什么，跟我说一声。"},
            ],
        },
    }

    def __init__(self, path="cold_start.json", clock=_time.time, activation_ts=None):
        self.path = path
        self.clock = clock
        self._lock = threading.RLock()
        self.activation_ts = activation_ts if activation_ts is not None else clock()
        self.completed = {}   # {day_str: [task_ids]}
        self.skipped = {}     # {day_str: [task_ids]}
        self._load()

    # ---- 持久化（原子写，防断电丢进度）----
    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.activation_ts = float(data.get("activation_ts", self.activation_ts))
            self.completed = data.get("completed", {})
            self.skipped = data.get("skipped", {})
        except Exception:
            pass

    def _save(self):
        data = {"activation_ts": self.activation_ts,
                "completed": self.completed, "skipped": self.skipped}
        tmp = f"{self.path}.{os.getpid()}_{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # ---- 天数计算 ----
    def current_day(self, now=None):
        """当前冷启动第几天（Day 1-7；超 7 天返回 8 = 冷启动结束）。"""
        now = now or self.clock()
        day = int((now - self.activation_ts) // 86400) + 1
        return min(day, 8)

    def in_cold_start(self, now=None):
        """是否还在冷启动期（Day 1-7）。"""
        return self.current_day(now) <= 7

    # ---- 任务编排 ----
    def today_tasks(self, now=None):
        """今天全部任务 + 状态。返回 [(task, done, skipped)]。"""
        day = self.current_day(now)
        if day > 7:
            return []
        done = self.completed.get(str(day), [])
        skipped = self.skipped.get(str(day), [])
        return [(t, t["id"] in done, t["id"] in skipped)
                for t in self.DAY_SCRIPT.get(day, {}).get("tasks", [])]

    def pending_tasks(self, now=None):
        """今天还没完成 + 没跳过的任务（essential 优先排序）。"""
        day = self.current_day(now)
        if day > 7:
            return []
        done = set(self.completed.get(str(day), []))
        skipped = set(self.skipped.get(str(day), []))
        out = [t for t in self.DAY_SCRIPT.get(day, {}).get("tasks", [])
               if t["id"] not in done and t["id"] not in skipped]
        out.sort(key=lambda t: (not t.get("essential", False),))
        return out

    def next_task(self, now=None):
        """今天下一个该做的任务（essential 优先）。返回 task dict 或 None。"""
        pending = self.pending_tasks(now)
        return pending[0] if pending else None

    def _find_task(self, task_id, day):
        for t in self.DAY_SCRIPT.get(day, {}).get("tasks", []):
            if t["id"] == task_id:
                return t
        return None

    def mark_done(self, task_id, day=None, now=None):
        """标记任务完成（幂等）。"""
        now = now or self.clock()
        day = day if day is not None else self.current_day(now)
        with self._lock:
            self.completed.setdefault(str(day), [])
            if task_id not in self.completed[str(day)]:
                self.completed[str(day)].append(task_id)
                self._save()
        return True

    def mark_skipped(self, task_id, day=None, now=None):
        """标记任务跳过。essential 任务不允许跳过（返回 False）。"""
        now = now or self.clock()
        day = day if day is not None else self.current_day(now)
        task = self._find_task(task_id, day)
        if task and task.get("essential"):
            return False  # essential 任务不跳过（安全底线）
        with self._lock:
            self.skipped.setdefault(str(day), [])
            if task_id not in self.skipped[str(day)]:
                self.skipped[str(day)].append(task_id)
                self._save()
        return True

    # ---- 状态查询 ----
    def status(self, now=None):
        """冷启动状态总览（供前端/子女端）。"""
        now = now or self.clock()
        day = self.current_day(now)
        in_cs = self.in_cold_start(now)
        return {
            "in_cold_start": in_cs,
            "current_day": day,
            "day_theme": self.DAY_SCRIPT.get(day, {}).get("theme", "") if in_cs else "日常使用",
            "next_task": self.next_task(now),
            "pending_count": len(self.pending_tasks(now)),
        }

    def all_done(self, now=None):
        """冷启动是否全部完成（Day1-7 所有非跳过任务都做了）。"""
        for day in range(1, 8):
            done = set(self.completed.get(str(day), []))
            skipped = set(self.skipped.get(str(day), []))
            for t in self.DAY_SCRIPT.get(day, {}).get("tasks", []):
                if t["id"] not in done and t["id"] not in skipped:
                    return False
        return True


def self_test():
    """自检：Day1-7 编排 + essential 不跳过 + 状态落盘，不依赖真环境。"""
    import tempfile, shutil
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "cs.json")
    base = 1_800_000_000.0
    clock_t = [base]
    clock = lambda: clock_t[0]
    store = ColdStartOrchestrator(path=path, clock=clock, activation_ts=base)

    # 1. Day 1（激活当天）
    assert store.current_day() == 1, "激活当天=Day1"
    assert store.in_cold_start(), "Day1 在冷启动期"
    print("  [1] 激活当天 = Day 1  ✓")

    # 2. Day 1 任务（3 个）+ essential 优先
    pending = store.pending_tasks()
    assert len(pending) == 3, f"Day1 应有 3 任务: {pending}"
    assert pending[0]["essential"] is True, f"essential 优先: {pending[0]}"
    print("  [2] Day 1 任务 + essential 优先  ✓")

    # 3. essential 任务不允许跳过
    assert store.mark_skipped("emergency_contact") is False, "essential 不该跳过"
    print("  [3] essential 任务不允许跳过  ✓")

    # 4. 非 essential 任务可跳过
    assert store.mark_skipped("medication") is True, "medication 该可跳过"
    assert all(t["id"] != "medication" for t in store.pending_tasks())
    print("  [4] 非 essential 任务可跳过  ✓")

    # 5. 完成任务
    store.mark_done("greeting")
    store.mark_done("emergency_contact")
    assert all(t["id"] not in ("greeting", "emergency_contact")
               for t in store.pending_tasks())
    print("  [5] 完成任务  ✓")

    # 6. 跨天（Day 2）
    clock_t[0] = base + 86400
    assert store.current_day() == 2, "+1 天=Day2"
    assert store.pending_tasks()[0]["id"] == "remember", "Day2=记住一件事"
    print("  [6] 跨天（Day 2 = 记住一件事）  ✓")

    # 7. Day 8（冷启动结束）
    clock_t[0] = base + 7 * 86400
    assert store.current_day() == 8, "+7 天=Day8（结束）"
    assert not store.in_cold_start(), "Day8 不在冷启动期"
    assert store.pending_tasks() == [], "Day8 无任务"
    print("  [7] Day 8 冷启动结束  ✓")

    # 8. 状态落盘（重启不丢进度）
    store2 = ColdStartOrchestrator(path=path, clock=clock, activation_ts=base)
    assert sorted(store2.completed.get("1", [])) == \
        sorted(["greeting", "emergency_contact"]), "重启后进度保留"
    assert "medication" in store2.skipped.get("1", []), "重启后跳过记录保留"
    print("  [8] 状态落盘（重启不丢进度）  ✓")

    shutil.rmtree(tmp, True)
    print("  PASS：T-3 冷启动编排 8 项自检全部通过")
    return 0


if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    print("用法：python cold_start.py --self-test")
    raise SystemExit(0)

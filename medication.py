# -*- coding: utf-8 -*-
"""
T-4 用药闭环核心：余量自报 + 购药待办 + 未回应通知子女

设计约束（与 reminder.py 一致）：
- 仅依赖标准库（可在无 torch/GPU 环境下单元测试）
- 所有时间计算基于调用方注入的统一时钟，杜绝跨机时区歧义
- 用药数据原子落盘（防断电丢记录），安全数据不走画像淡忘/剪枝
- 未回应通知子女：复用 reminder.ReminderStore 的升级逻辑（escalated_message），
  MedicationStore 只负责"该不该升级"的判定，不重复实现升级链路

用法（模拟跑）：
    python medication.py --self-test
"""
import json
import os
import re
import threading
import time as _time


def _norm(s):
    return re.sub(r"\s+", "", str(s or "")).strip()


class MedicationStore:
    """T-4 用药闭环（余量自报 + 购药待办 + 未回应通知子女判定）。

    - 余量自报：老人说"降压药还剩5片" → report_remaining() 记录余量 + 算够几天
    - 购药待办：余量 ≤ LOW_THRESHOLD_DAYS 天 → need_purchase_to_do() 返回该药品
      （上层创建购药提醒到 ReminderStore，防重复用 purchase_todo_created 标记）
    - 未回应通知子女：吃药提醒未确认 → 由 ReminderStore.advance_awaiting() 升级
      （本 store 提供 escalation_due() 判定，不重复实现升级链路）
    时钟注入可测试；meds 结构 {name: {name, unit, total, remaining, daily_dose,
    last_reported, purchase_todo_created, reminder_id}}。
    """

    LOW_THRESHOLD_DAYS = 3   # 余量 ≤ 3 天 → 触发购药待办
    DAILY_DOSE_DEFAULT = 1   # 默认每天 1 次（可后续更正）

    def __init__(self, path="medication.json", clock=_time.time):
        self.path = path
        self.clock = clock
        self._lock = threading.RLock()
        self.meds = {}
        self._load()

    # ---- 持久化（原子写，防断电损坏）----
    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.meds = data.get("meds", {}) if isinstance(data, dict) else {}
        except Exception:
            pass

    def _save(self):
        data = {"meds": self.meds}
        tmp = f"{self.path}.{os.getpid()}_{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # ---- 余量自报（老人说"降压药还剩5片"）----
    def report_remaining(self, med_name, remaining, unit="片", now=None):
        """记录余量自报。返回 {"ok", "days_left", "low", "message"}。
        新药品默认 daily_dose=1（可后续更正）；已有药品更新 remaining。"""
        now = now or self.clock()
        med_name = _norm(med_name)
        if not med_name or remaining is None:
            return {"ok": False, "message": "没听清哪个药、还剩多少，您再说一遍。"}
        try:
            remaining = int(remaining)
        except (TypeError, ValueError):
            return {"ok": False, "message": "还剩多少片没记清，您再说一遍数字。"}
        if remaining < 0:
            return {"ok": False, "message": "剩下的不能是负数，您再说一遍。"}
        with self._lock:
            med = self.meds.get(med_name)
            if not med:
                med = {"name": med_name, "unit": unit, "total": remaining,
                       "remaining": remaining,
                       "daily_dose": self.DAILY_DOSE_DEFAULT,
                       "last_reported": now, "purchase_todo_created": False}
                self.meds[med_name] = med
            else:
                med["remaining"] = remaining
                med["last_reported"] = now
                # 重新自报后重置购药待办标记（可能买过药了）
                med["purchase_todo_created"] = False
            days_left = self._days_left(med)
            low = days_left <= self.LOW_THRESHOLD_DAYS
            self._save()
            if low:
                msg = (f"{med_name}还剩{remaining}{unit}，大概够{days_left}天，"
                       f"您该去买药了。")
            else:
                msg = (f"记下了，{med_name}还剩{remaining}{unit}，"
                       f"大概够{days_left}天。")
            return {"ok": True, "days_left": days_left, "low": low,
                    "message": msg, "med_name": med_name}

    def set_daily_dose(self, med_name, daily_dose, now=None):
        """更正每天吃几次（老人说"降压药一天吃2次"）。"""
        now = now or self.clock()
        med_name = _norm(med_name)
        try:
            daily_dose = int(daily_dose)
        except (TypeError, ValueError):
            return {"ok": False, "message": "一天吃几次没记清，您再说一遍。"}
        if daily_dose <= 0:
            return {"ok": False, "message": "一天得吃至少 1 次。"}
        with self._lock:
            med = self.meds.get(med_name)
            if not med:
                return {"ok": False, "message": "这个药还没记下呢，您先说还剩多少。"}
            med["daily_dose"] = daily_dose
            self._save()
            days_left = self._days_left(med)
            return {"ok": True, "days_left": days_left,
                    "message": f"记下了，{med_name}一天吃{daily_dose}次，"
                               f"还剩的够{days_left}天。"}

    def _days_left(self, med):
        """余量够几天（remaining / daily_dose，向下取整）。"""
        dose = med.get("daily_dose", self.DAILY_DOSE_DEFAULT) or self.DAILY_DOSE_DEFAULT
        return max(0, int(med.get("remaining", 0) / dose))

    # ---- 购药待办（余量 ≤ N 天）----
    def low_meds(self, now=None):
        """余量低的药品列表（≤ LOW_THRESHOLD_DAYS 天）。"""
        now = now or self.clock()
        with self._lock:
            return [dict(m) for m in self.meds.values()
                    if self._days_left(m) <= self.LOW_THRESHOLD_DAYS]

    def need_purchase_to_do(self, now=None):
        """是否需要创建购药待办（有余量低 + 还没创建待办）。返回该药品 dict 或 None。"""
        now = now or self.clock()
        with self._lock:
            for m in self.meds.values():
                if self._days_left(m) <= self.LOW_THRESHOLD_DAYS \
                        and not m.get("purchase_todo_created"):
                    return dict(m)
        return None

    def mark_purchase_todo(self, med_name, reminder_id=None, now=None):
        """标记购药待办已创建（防重复）。"""
        now = now or self.clock()
        med_name = _norm(med_name)
        with self._lock:
            if med_name in self.meds:
                self.meds[med_name]["purchase_todo_created"] = True
                if reminder_id:
                    self.meds[med_name]["reminder_id"] = reminder_id
                self._save()

    # ---- 未回应通知子女（判定，升级链路复用 ReminderStore）----
    def escalation_due(self, reminder, now=None):
        """吃药提醒是否该升级通知子女（未确认 + 过了升级窗口）。
        reminder: ReminderStore 里的一条提醒（type 含 medication）。
        返回 True（该升级）/ False（不该）。判定逻辑与 reminder.advance_awaiting 对齐。"""
        now = now or self.clock()
        rtype = str(reminder.get("thing") or "")
        if "药" not in rtype and "吃药" not in rtype:
            return False
        # 未确认 + 已触发 + 过了升级窗口（2 次补催后）
        if reminder.get("confirmed"):
            return False
        if reminder.get("reremind_count", 0) < 2:
            return False
        return True

    # ---- 查询/状态 ----
    def get(self, med_name):
        """查某个药。"""
        med_name = _norm(med_name)
        with self._lock:
            m = self.meds.get(med_name)
            return dict(m) if m else None

    def status(self, now=None):
        """状态总览（供前端/子女端查询）。"""
        now = now or self.clock()
        with self._lock:
            return {
                "meds": {
                    name: {
                        "name": m["name"],
                        "remaining": m.get("remaining", 0),
                        "unit": m.get("unit", "片"),
                        "daily_dose": m.get("daily_dose", 1),
                        "days_left": self._days_left(m),
                        "low": self._days_left(m) <= self.LOW_THRESHOLD_DAYS,
                        "purchase_todo": m.get("purchase_todo_created", False),
                    }
                    for name, m in self.meds.items()
                },
                "low_meds": [m["name"] for m in self.meds.values()
                             if self._days_left(m) <= self.LOW_THRESHOLD_DAYS],
            }

    def clear(self, med_name, now=None):
        """删某个药（老人说"别记了"）。"""
        now = now or self.clock()
        med_name = _norm(med_name)
        with self._lock:
            removed = med_name in self.meds
            self.meds.pop(med_name, None)
            self._save()
        return removed


# ---- 口语提取（确定性，供 app.chat_response 调用）----

_MED_RE = re.compile(
    r"(?P<med>[一-龥A-Za-z]{2,12})"
    r"还剩\s*(?P<num>\d{1,3}|[一二两三四五六七八九十]{1,3})\s*(?P<unit>片|粒|胶囊|袋|支|瓶)?")

_MED_DOSE_RE = re.compile(
    r"(?P<med>[一-龥A-Za-z]{2,12})"
    r"一天(?:吃|服)\s*(?P<num>\d{1,2}|[一二两三四五六七八九十]{1,3})\s*次")


def cn_num_to_int(s):
    """中文/阿拉伯数字 → int（复用 reminder 的口径，简化版）。"""
    from reminder import cn_to_int
    return cn_to_int(s)


def extract_remaining(text):
    """从口语里提取余量自报。返回 {"med", "num", "unit"} 或 None。
    例："降压药还剩5片" → {"med":"降压药","num":5,"unit":"片"}"""
    t = _norm(text)
    m = _MED_RE.search(t)
    if not m:
        return None
    num = cn_num_to_int(m.group("num"))
    if num is None:
        return None
    return {"med": m.group("med"), "num": num, "unit": m.group("unit") or "片"}


def extract_daily_dose(text):
    """从口语里提取每天吃几次。返回 {"med", "num"} 或 None。
    例："降压药一天吃2次" → {"med":"降压药","num":2}"""
    t = _norm(text)
    m = _MED_DOSE_RE.search(t)
    if not m:
        return None
    num = cn_num_to_int(m.group("num"))
    if num is None:
        return None
    return {"med": m.group("med"), "num": num}


def self_test():
    """自检：余量自报 + 购药待办 + 升级判定 + 口语提取，不依赖真环境。"""
    import tempfile, shutil
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "med.json")
    clock_t = [1_800_000_000.0]
    clock = lambda: clock_t[0]
    store = MedicationStore(path=path, clock=clock)
    ok = True

    # 1. 余量自报（充足）
    r = store.report_remaining("降压药", 30, now=clock())
    assert r["ok"] and r["days_left"] == 30 and not r["low"], f"充足自报: {r}"
    print("  [1] 余量自报（充足 30 天）  ✓")

    # 2. 余量自报（偏低 → 触发购药待办）
    r = store.report_remaining("降压药", 2, now=clock())
    assert r["ok"] and r["days_left"] == 2 and r["low"], f"偏低自报: {r}"
    need = store.need_purchase_to_do(now=clock())
    assert need and need["name"] == "降压药", f"该建购药待办: {need}"
    print("  [2] 余量偏低 → 购药待办判定  ✓")

    # 3. 购药待办防重复（mark 后不再 need）
    store.mark_purchase_todo("降压药", now=clock())
    assert store.need_purchase_to_do(now=clock()) is None, "已建待办不该重复"
    print("  [3] 购药待办防重复  ✓")

    # 4. 更正每天吃几次（影响天数计算）
    r = store.set_daily_dose("降压药", 2, now=clock())
    assert r["ok"] and r["days_left"] == 1, f"每天2次后: {r}"  # 2 片 / 2 次 = 1 天
    print("  [4] 更正每天吃几次（天数重算）  ✓")

    # 5. 重新自报重置购药待办标记
    r = store.report_remaining("降压药", 20, now=clock())
    need = store.need_purchase_to_do(now=clock())
    assert need is None, "20 片充足不该建待办"
    print("  [5] 重新自报重置待办标记  ✓")

    # 6. 升级判定（未确认 + 2 次补催后）
    fake_reminder = {"thing": "吃降压药", "confirmed": False, "reremind_count": 2}
    assert store.escalation_due(fake_reminder) is True, "该升级通知子女"
    fake_reminder["confirmed"] = True
    assert store.escalation_due(fake_reminder) is False, "已确认不该升级"
    print("  [6] 未回应通知子女判定  ✓")

    # 7. 口语提取
    e = extract_remaining("降压药还剩5片")
    assert e and e["med"] == "降压药" and e["num"] == 5 and e["unit"] == "片", f"提取余量: {e}"
    e = extract_daily_dose("降压药一天吃2次")
    assert e and e["med"] == "降压药" and e["num"] == 2, f"提取剂量: {e}"
    e = extract_remaining("我没事")
    assert e is None, "非余量句式不该提取"
    print("  [7] 口语提取（余量/剂量/非句式）  ✓")

    # 8. 状态总览
    st = store.status(now=clock())
    assert "降压药" in st["meds"], f"状态总览: {st}"
    print("  [8] 状态总览  ✓")

    # 9. 非法输入
    assert store.report_remaining("降压药", "abc")["ok"] is False, "非数字拒收"
    assert store.report_remaining("降压药", -5)["ok"] is False, "负数拒收"
    assert store.report_remaining("", 5)["ok"] is False, "空药名拒收"
    print("  [9] 非法输入拒收  ✓")

    shutil.rmtree(tmp, True)
    print("  PASS：T-4 用药闭环 9 项自检全部通过")
    return 0


if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    print("用法：python medication.py --self-test")
    raise SystemExit(0)

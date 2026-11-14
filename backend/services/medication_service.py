"""用药闭环服务（T-4）：余量自报 + 购药待办 + 未回应通知子女 + 状态查询。

复用 app.medication_store（MedicationStore），供 API 与口语入口共用。
"""
from backend.core.state import engine


def _store():
    return engine.medication_store


def status():
    """状态总览（药品列表 + 余量低的药）。"""
    return _store().status()


def report_remaining(med_name, remaining, unit="片"):
    """余量自报（老人说"降压药还剩5片"）。"""
    return _store().report_remaining(str(med_name or ""), remaining, str(unit or "片"))


def set_daily_dose(med_name, daily_dose):
    """更正每天吃几次（老人说"降压药一天吃2次"）。"""
    return _store().set_daily_dose(str(med_name or ""), daily_dose)


def low_meds():
    """余量低的药品列表（≤ 3 天）。"""
    return _store().low_meds()


def need_purchase_todo():
    """是否需要创建购药待办（有余量低 + 还没创建）。"""
    return _store().need_purchase_to_do()


def mark_purchase_todo(med_name, reminder_id=None):
    """标记购药待办已创建（防重复）。"""
    _store().mark_purchase_todo(str(med_name or ""), reminder_id)
    return {"ok": True}


def get(med_name):
    """查某个药。"""
    return _store().get(str(med_name or ""))


def clear(med_name):
    """删某个药。"""
    return {"ok": True, "removed": _store().clear(str(med_name or ""))}

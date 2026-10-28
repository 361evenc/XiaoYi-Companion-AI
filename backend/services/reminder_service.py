"""提醒服务：列表 / 完成 / 取消。

复用旧版 reminder_store（confirm/cancel/pending/stats）与 describe_reminder，
数据结构与语音"喝了/办好了"走同一条后端逻辑。
"""
import time

import reminder as reminder_mod
from backend.core.state import engine

# 与旧版 app.CONFIRM_WINDOW 一致：触发后可确认"办好了"的窗口（6 小时）
CONFIRM_WINDOW = 6 * 3600


def list():
    """返回结构化提醒列表 {awaiting[], pending[], stats{}, notices[]}。"""
    now = time.time()
    store = engine.reminder_store

    # ① 已到点、正在被反复补催的 → 前端给「✅ 完成」按钮
    awaiting_ids = set()
    awaiting = []
    for r in store.awaiting_confirm(now, CONFIRM_WINDOW):
        awaiting_ids.add(r["id"])
        awaiting.append(_item(r, now, awaiting=True))

    # ② 未到点的待办 → 前端给「✖ 取消」按钮（排除等确认的，避免重复显示）
    pending = []
    for r in store.pending(now)[:8]:
        if r["id"] in awaiting_ids:
            continue
        pending.append(_item(r, now, awaiting=False))

    try:
        st = store.stats(now)
        stats = {"total": st["total"], "done": st["done"], "rate": st["rate"]}
    except Exception:
        stats = {"total": 0, "done": 0, "rate": 0}

    notices = [
        {"thing": n.get("thing", ""), "time": n.get("time", 0)}
        for n in (store.notices or [])[-3:]
    ]
    return {"awaiting": awaiting, "pending": pending, "stats": stats, "notices": notices}


def done(rid):
    """完成提醒（与语音"喝了/办好了"同一条 confirm 逻辑）。"""
    try:
        engine.reminder_store.confirm(str(rid), time.time())
        return {"ok": True, "message": "办妥啦，真棒！"}
    except Exception as e:
        print(f"[reminder_service] confirm 失败: {e}")
        return {"ok": False, "message": "没找到这条提醒，可能已经办妥啦。"}


def cancel(rid):
    """取消提醒（与语音"取消提醒"同一条 cancel 逻辑）。"""
    try:
        engine.reminder_store.cancel(str(rid))
        return {"ok": True, "message": "好嘞，这条提醒我撤掉了。"}
    except Exception as e:
        print(f"[reminder_service] cancel 失败: {e}")
        return {"ok": False, "message": "没找到这条提醒。"}


def _item(r, now, awaiting):
    """一条提醒 → 前端展示 dict：描述文案 + 相对时间。"""
    try:
        desc = reminder_mod.describe_reminder(r, now)
    except Exception:
        desc = str(r.get("thing", ""))
    item = {"id": r.get("id"), "desc": desc, "repeat": r.get("repeat")}
    if not awaiting and not r.get("repeat"):
        delta = int(r.get("time", now) - now)
        if delta < 60:
            item["eta"] = "马上"
        elif delta < 3600:
            item["eta"] = f"{delta // 60}分钟后"
        else:
            item["eta"] = f"{delta // 3600}小时{(delta % 3600) // 60}分后"
    return item

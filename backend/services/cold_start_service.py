"""冷启动编排服务（T-3）：Day1-7 脚本 + 状态 + 任务完成/跳过。

复用 app.cold_start（ColdStartOrchestrator），供 API 与口语入口共用。
essential 任务（Day 1 紧急联系人 = 安全底线）不允许跳过。
"""
from backend.core.state import engine


def _store():
    return engine.cold_start


def status():
    """冷启动状态总览（第几天/主题/下一个任务/待办数）。"""
    return _store().status()


def mark_done(task_id):
    """标记任务完成（老人说"弄好了"）。"""
    _store().mark_done(str(task_id or ""))
    return {"ok": True, "message": "弄好啦，真棒！"}


def mark_skipped(task_id):
    """标记任务跳过（essential 任务不跳过，返回安全底线话术）。"""
    ok = _store().mark_skipped(str(task_id or ""))
    if not ok:
        return {"ok": False,
                "message": "这个是安全底线，不能跳过呢，您跟我说个电话，我记着。"}
    return {"ok": True, "message": "好，这个先不弄，您想弄了跟我说。"}

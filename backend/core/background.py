"""后台任务。

旧版 Gradio 用 gr.Timer 承载：提醒调度轮询、主动关怀注入、音频清理、历史 prune。
其中「提醒调度轮询」由 app.py 模块底部的 daemon 线程 reminder_worker 在 import 时
已经启动，backend 直接复用（单机单人 + uvicorn workers=1）。

这里的 start() 补上两个纯维护类定时任务（无界面依赖）：
  - clean_old_audio_files：清理过期 TTS 音频临时文件（旧版 gr.Timer 300s）
  - prune_old_conversations：清理过期历史总结（旧版 gr.Timer 3600s）
主动关怀注入由前端轮询 POST /api/proactive 承载；提醒到点推送由 backend/api/ws.py
的 WebSocket 承载（均已上线，旧版 Gradio 界面段已于 2026-10-28 从 app.py 删除）。
"""
import threading
import time

from backend.core.state import engine


def _loop(fn, interval_sec, name):
    while True:
        try:
            fn()
        except Exception as e:  # noqa: BLE001 后台守护线程，吞掉异常避免退出
            print(f"[background:{name}] 异常: {e}")
        time.sleep(interval_sec)


def _daily_backup():
    """B-6 每日全量备份（本地滚动 7 份 + 访问日志）+ 备份监控告警。
    上次成功备份 >24h 时 health() 报 alert，并入 B-4 每日自检（memory/health）。"""
    import backup_system
    mgr = backup_system.default_manager()
    res = mgr.backup_all()
    h = mgr.health()
    if h.get("status") == "alert":
        print(f"[background:backup] ⚠️ 备份告警: {h.get('issues')}（静默失败=没备份）")


def start():
    """启动维护类后台线程（重复调用安全）。"""
    _daily_backup()   # 启动即备份一次（B-6：开机先保一手）
    for fn, interval, name in [
        (engine.clean_old_audio_files, 300, "clean-audio"),
        (engine.prune_old_conversations, 3600, "prune-history"),
        (_daily_backup, 6 * 3600, "backup"),   # 6h 一轮：>24h 告警线留足余量
    ]:
        threading.Thread(target=_loop, args=(fn, interval, name),
                         daemon=True, name=f"xy-{name}").start()
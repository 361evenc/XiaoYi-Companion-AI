"""记忆/隐私服务：忘掉一件事 + 清空全部记忆 + 记忆箱数据 + 心情曲线。

封装旧版 app.forget_box_event / app.wipe_all_memory / ai_services.forget_by_request，
只做参数规整，不重写底层逻辑。
"""
import os

import ai_services
from backend.core.state import engine
from backend.services import voice_service


def forget(text):
    """隐私页「忘掉一件事」：按文本语义匹配，真删记忆（关键词事件表 + 记忆库）。

    返回 {ok, message}。没匹配到内容时 ok=False，提示换说法。
    """
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "message": "您先说说要忘掉哪件事～"}

    events = engine.user_memory.get("events") or []
    kept, reply = ai_services.forget_by_request(
        text, events, bank=engine.memory_bank,
        call_name=engine.user_memory.get("call_name", "奶奶"),
    )
    if not reply:
        # forget_by_request 没匹配到任何记忆：reply 为 None
        return {"ok": False, "message": "没找到您说的这件事，换个说法再试试～"}

    # 事件表删完要落盘（记忆库 bank.forget 内部已自存）
    try:
        engine.user_memory["events"] = kept
        engine.save_memory_events(kept)
    except Exception as e:
        print(f"[memory_service] 事件表保存失败: {e}")
    return {"ok": True, "message": reply}


def wipe(is_muted=False):
    """清空全部记忆（被遗忘权）：记忆库/事件/画像/聊天记录/提醒/心情 全部删除。

    返回 {ok, message, audio_url}。
    """
    try:
        _history, msg, audio = engine.wipe_all_memory(bool(is_muted))
    except Exception as e:
        print(f"[memory_service] 清空失败: {e}")
        return {"ok": False, "message": "清理时出了点问题，请稍后再试。", "audio_url": None}
    return {"ok": True, "message": msg, "audio_url": voice_service.audio_url(audio)}


def box():
    """记忆箱数据：重要事件时间线 + 照片 + 小忆的理解（反思）。

    反馈11/任务⑦：用户画像与记忆彻底分开——画像只进 用户画像.txt（工作人员
    打开文件看），不再进记忆箱展示结构。返回 {events[], photos[], reflections[]}。
    """
    # 重要事件：importance>=7 的情节（与旧版面板 ⭐ 同一标准），最近 10 条
    try:
        events = [
            {"id": e.get("id"), "text": e.get("text", ""), "quote": e.get("quote", ""),
             "ts": e.get("ts", 0)}
            for e in engine._box_important_episodes()
        ]
    except Exception:
        events = []

    # 照片：photos/ 目录最近 20 张，经 /photos 静态路由暴露
    photos = []
    try:
        for p in engine.list_photos():
            photos.append("/photos/" + os.path.basename(p))
    except Exception:
        pass

    # 小忆的理解（反思）最近 5 条
    try:
        with engine.memory_bank._lock:
            refls = sorted(engine.memory_bank.reflections, key=lambda r: -r.get("ts", 0))[:5]
        reflections = [{"text": r.get("text", ""), "ts": r.get("ts", 0)} for r in refls]
    except Exception:
        reflections = []

    return {"events": events, "photos": photos, "reflections": reflections}


def emotion_chart():
    """心情曲线：情绪日志 → matplotlib 图，经 /audio 静态路由返回 URL。数据不足返回 null。"""
    try:
        path = engine.generate_emotion_chart()
    except Exception as e:
        print(f"[memory_service] 心情曲线失败: {e}")
        path = None
    return {"image_url": voice_service.audio_url(path)}

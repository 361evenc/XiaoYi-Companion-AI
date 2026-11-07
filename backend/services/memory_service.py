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


def search(q, by="object", k=10):
    """统一记忆检索（B-5）：按人 person / 按物 object / 按场景 scene 三维检索。
    供 F-4 时间线、B-8 盲盒等调用。返回 {"ok", by, query, resolved, results}。"""
    q = str(q or "").strip()
    by = str(by or "").strip().lower() or "object"
    if not q:
        return {"ok": False, "message": "您想翻翻啥呀？跟我说说——人、东西还是地方～"}
    try:
        out = engine.memory_bank.search(q, by=by, k=k)
    except Exception as e:
        print(f"[memory_service] 检索失败: {e}")
        return {"ok": False, "message": "检索时出了点问题，请稍后再试。"}
    out["ok"] = True
    return out


def correct(key, new_value, old_value="", quote=""):
    """显式更正（B-5）：值即时生效、旧值入 history 保留可查（不是物理删）。
    老人语音端的更正已由 chat_response 的确定性分支接住；本接口供程序化调用与测试。"""
    key, new_value = str(key or "").strip(), str(new_value or "").strip()
    if not key or not new_value:
        return {"ok": False, "message": "key 和 new_value 都不能为空。"}
    try:
        res = engine.memory_bank.correct_fact(
            key=key, new_value=new_value, old_value=str(old_value or "").strip() or None,
            quote=str(quote or "").strip())
    except Exception as e:
        print(f"[memory_service] 更正失败: {e}")
        return {"ok": False, "message": "更正时出了点问题，请稍后再试。"}
    if res is None:
        return {"ok": False, "message": "没找到要更正的事，也建不了新条目。"}
    res["ok"] = True
    return res


def health():
    """记忆箱健康自检（B-4 audit，B-10 报告"记忆箱健康"项数据源）：
    条目数 / 视觉重复堆积 / 敏感词命中 / 拦截统计 / 免看清单。
    B-6 并入"备份健康"（备份监控：上次成功 >24h 告警——静默失败=没备份）。"""
    try:
        out = engine.memory_bank.health_audit()
    except Exception as e:
        print(f"[memory_service] 健康自检失败: {e}")
        return {"ok": False, "message": "自检时出了点问题，请稍后再试。"}
    out["ok"] = True
    # B-6 备份健康项（并入每日自检，B-10 报告收口）
    try:
        import backup_system
        out["backup"] = backup_system.default_manager().health()
    except Exception as e:
        out["backup"] = {"status": "warn", "issues": [f"backup_check_failed: {e}"]}
    return out


def find_object(text):
    """找物三级回答（B-3，契约按 B-1 PRD §4）：graph（视觉条目）→ text（您说过）
    → none（诚实兜底）。不是找物话语 → matched=False（调用方转正常聊天）。"""
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "matched": False, "message": "您想找啥呀？跟我说说～"}
    try:
        res = engine.memory_bank.find_object(text)
    except Exception as e:
        print(f"[memory_service] 找物失败: {e}")
        return {"ok": False, "matched": False, "message": "找的时候出了点问题，请稍后再试。"}
    if res is None:
        return {"ok": False, "matched": False, "message": "不是找物提问。"}
    res["ok"] = True
    res["matched"] = True
    return res

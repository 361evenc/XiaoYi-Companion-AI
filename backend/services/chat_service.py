"""对话服务：对旧版 app.chat_response 做薄封装，去掉 gr.update 返回干净 dict。

「一次打包返回」：一次 /api/chat 同时返回
  - reply        回复文本
  - audio_url    整段 TTS 音频 URL（未静音且有内容时）
  - conv_id      会话 id
  - chat_history 更新后的完整历史（纯文本 dict 列表）
  - panels       更新后的面板快照（记忆 / 提醒 / 侧栏卡片），前端自行渲染
"""
import base64
import os
import tempfile
import time

from backend.core.state import engine
from backend.services import voice_service


def _last_reply(history):
    for msg in reversed(history or []):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            return engine.extract_text(msg.get("content", "")).strip()
    return ""


def chat(user_input, chat_history=None, surname="", gender="女", is_muted=False,
         personality="踏实务实", conv_id=None):
    user_input = user_input or ""
    chat_history = chat_history or []
    if not conv_id:
        conv_id = str(int(time.time() * 1000))

    # 旧版主流程（内部处理记忆抽取/情绪/压缩/遗忘/意图/模型），
    # 返回值第 5 项是 gr.update，这里直接丢弃。
    _, history, _audio, cid, _upd = engine.chat_response(
        user_input, chat_history, surname, gender, bool(is_muted),
        personality, conv_id, None)
    conv_id = cid or conv_id

    reply = _last_reply(history)

    # 整段 TTS：主流程返回后异步合成，避免阻塞文字展示。
    audio_url = None
    if not is_muted and history:
        path = engine.play_reply_audio(history, bool(is_muted))
        if path:
            audio_url = "/audio/" + os.path.basename(path)

    return {
        "reply": reply,
        "audio_url": audio_url,
        "conv_id": conv_id,
        "chat_history": history,
        "panels": {
            "memory": engine.get_memory_panel_html(),
            "reminders": engine.get_reminders_panel_html(),
            "sidebar_cards": engine.render_sidebar_cards(),
        },
    }


def setup(surname="", gender="女", personality="踏实务实", is_muted=False):
    """欢迎页提交：等价旧版 enter_chat，初始化会话并返回欢迎语。

    返回与 chat() 同构的打包（reply/audio_url/conv_id/chat_history/panels），
    前端可复用同一套渲染逻辑。欢迎语音留到 Phase 4 补（audio_url 暂置 None）。
    """
    call_name = engine.get_call_name(surname, gender)
    engine.user_memory["call_name"] = call_name
    engine.UI_STATE["personality"] = (
        personality if personality in ("踏实务实", "风趣幽默", "暖心知心")
        else "踏实务实"
    )
    engine.last_user_interaction_time = time.time()

    welcome_msg = f"您好{call_name}！我是小忆，很高兴能陪伴您～"
    greet = engine.ai_services.build_cross_session_greeting(
        engine.memory_bank, call_name)
    if greet:
        welcome_msg += "\n" + greet
    chat_history = [{"role": "assistant", "content": welcome_msg}]
    new_id = str(int(time.time() * 1000))

    return {
        "reply": welcome_msg,
        "audio_url": None,  # Phase 4 补欢迎语音
        "conv_id": new_id,
        "chat_history": chat_history,
        "panels": {
            "memory": engine.get_memory_panel_html(),
            "reminders": engine.get_reminders_panel_html(),
            "sidebar_cards": engine.render_sidebar_cards(),
        },
    }


def photo(photo_base64, mime="image/jpeg", chat_history=None, surname="", gender="女",
          is_muted=False, personality="踏实务实", conv_id=None):
    """拍照/上传图片：存进记忆箱 → 视觉识别 → 结果注入对话，走正常聊天流程。

    返回与 chat() 同构的打包（reply/audio_url/conv_id/chat_history/panels）。
    无 VISION key 时识别降级（识别结果为空，仅存图），回复走兜底话术。
    """
    photo_base64 = (photo_base64 or "").strip()
    user_text = "【我给您看张照片】"

    if photo_base64:
        raw = b""
        try:
            raw = base64.b64decode(photo_base64)
        except Exception:
            raw = b""
        if raw:
            ext = ".png" if "png" in (mime or "").lower() else ".jpg"
            tmp = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as f:
                    f.write(raw)
                    tmp = f.name
                saved = engine._save_photo_to_box(tmp)
                desc = engine.recognize_image(saved or tmp)
                if desc:
                    user_text = f"【我给您看张照片：{desc}】"
                elif saved:
                    user_text = "【我给您看张照片（已收进记忆箱，内容我暂时认不出来）】"
            finally:
                if tmp and os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except Exception:
                        pass

    return chat(user_text, chat_history, surname, gender, is_muted, personality, conv_id)


RECALL_ACK = "好嘞，刚那句不算哈～"   # 反馈2/任务⑥：撤回固定回复，不调模型


def recall(conv_id):
    """撤回当前对话的最后一轮（user + assistant）。

    - 对话文件与返回的 chat_history 同步删掉这一轮（下一轮模型上下文自然不含）
    - 调 engine.recall_cleanup 清掉该消息引发的记忆/意图痕迹（事件/记忆库/待抽取队列/该轮提醒）
    - 固定回一句 RECALL_ACK，不调模型
    """
    if not conv_id:
        return {"ok": False, "message": "还没有可撤回的消息。", "conv_id": conv_id,
                "chat_history": []}
    convs = engine.load_all_conversations() or []
    conv = next((c for c in convs if c.get("id") == conv_id), None)
    msgs = (conv or {}).get("messages") or []
    if not msgs:
        return {"ok": False, "message": "还没有可撤回的消息。", "conv_id": conv_id,
                "chat_history": []}

    # 从末尾摘掉一轮：先摘 assistant（若在末尾），再摘 user
    removed_user = None
    while msgs:
        last = msgs[-1]
        role = last.get("role") if isinstance(last, dict) else None
        if role == "assistant" and removed_user is None:
            msgs.pop()
        elif role == "user" and removed_user is None:
            removed_user = msgs.pop()
        else:
            break

    if conv is not None and removed_user is not None:
        # 任务⑥：同步清掉该消息引发的记忆/画像/意图痕迹
        try:
            engine.recall_cleanup(removed_user)
        except Exception as e:
            print(f"[chat_service] 撤回记忆清除失败: {e}")
        # 撤回确认作为小忆的话写进当前对话（固定文案，不调模型）
        msgs.append({"role": "assistant", "content": RECALL_ACK})
        conv["messages"] = msgs
        try:
            engine.save_json_safe(engine.HISTORY_FILE, convs)
        except Exception as e:
            print(f"[chat_service] 撤回保存失败: {e}")
    # 转成前端用的 [{role, content}]
    history = []
    for m in msgs:
        content = m.get("content", "") if isinstance(m, dict) else ""
        history.append({"role": m.get("role", "user") if isinstance(m, dict) else "user",
                         "content": engine.extract_text(content)})
    return {"ok": removed_user is not None,
            "message": RECALL_ACK if removed_user is not None else "没有可撤回的消息。",
            "conv_id": conv_id, "chat_history": history}


def proactive(is_muted=False, conv_id=None):
    """主动关怀：超过 15 秒没互动且触发信号成立（如天气要下雨）时，小忆主动说一句。

    复用旧版 inject_proactive_message（内含触发节流与 TTS），返回与 chat() 同构：
    {triggered, reply, audio_url, conv_id, chat_history, panels}。没触发时 triggered=False。
    """
    if not conv_id:
        return {"triggered": False}
    try:
        messages, _ = engine.get_conversation_messages_by_id(conv_id)
    except Exception:
        return {"triggered": False}
    history = [
        {"role": (m.get("role") if isinstance(m, dict) else None) or "user",
         "content": engine.extract_text(m.get("content", "") if isinstance(m, dict) else "")}
        for m in (messages or [])
    ]
    n_before = len(history)
    out_chat, audio_path = engine.inject_proactive_message(history, bool(is_muted), conv_id)
    out_chat = out_chat or history
    if len(out_chat) <= n_before:
        return {"triggered": False}

    reply = _last_reply(out_chat)
    return {
        "triggered": True,
        "reply": reply,
        "audio_url": voice_service.audio_url(audio_path),
        "conv_id": conv_id,
        "chat_history": out_chat,
        "panels": {
            "memory": engine.get_memory_panel_html(),
            "reminders": engine.get_reminders_panel_html(),
            "sidebar_cards": engine.render_sidebar_cards(),
        },
    }
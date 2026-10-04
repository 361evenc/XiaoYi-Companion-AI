# -*- coding: utf-8 -*-
"""第二优先：把 4+2 项功能接进 app.py（混合换行文件，字节级精确替换）。
每处替换要求唯一匹配（count==1），否则中止。"""
import sys

path = "app.py"
with open(path, "rb") as f:
    data = f.read().decode("utf-8")


def rep(old_block, new_block, tag):
    global data
    old = "\r\n".join(old_block.strip("\n").split("\n"))
    new = "\r\n".join(new_block.strip("\n").split("\n"))
    n = data.count(old)
    if n != 1:
        print(f"❌ {tag}: 匹配到 {n} 处（应为 1），中止")
        sys.exit(1)
    data = data.replace(old, new)
    print(f"✅ {tag}")


# ---------- 1. 引入服务层 + similarity（重复检测用） ----------
rep('''
from memory_system import MemoryBank, match_symptoms
''', '''
from memory_system import MemoryBank, match_symptoms, similarity
import ai_services
''', "1. 引入 ai_services")

# ---------- 2. 服务层注入 DeepSeek 通道 ----------
rep('''
    print(f"DeepSeek调用失败: {last_err}")
    return ""
''', '''
    print(f"DeepSeek调用失败: {last_err}")
    return ""

# 服务层（安全过滤/小传/跨会话问候等）注入 DeepSeek 通道：离线优先，LLM 增强
ai_services.init(deepseek_chat_msgs)
''', "2. ai_services.init")

# ---------- 3. 情绪日志持久化 ----------
rep('''
def save_memory_events(events):
    save_json_safe(MEMORY_EVENTS_FILE, events)
''', '''
def save_memory_events(events):
    save_json_safe(MEMORY_EVENTS_FILE, events)

# ========== 情绪日志持久化（任务9找回：analyze_emotion + 落盘 + 心情曲线） ==========
EMOTION_LOG_FILE = "emotion_log.json"

def load_emotion_log():
    log = load_json_safe(EMOTION_LOG_FILE, [])
    return log if isinstance(log, list) else []

def save_emotion_log(log):
    save_json_safe(EMOTION_LOG_FILE, log[-300:])   # 只留最近300条，防膨胀
''', "3. 情绪日志持久化")

# ---------- 4. chat_response：每轮情绪记录（离线关键词通道，零延迟） ----------
rep('''
    extract_memory(user_input)
    call_name = get_call_name(surname, gender)
    user_memory["call_name"] = call_name
''', '''
    extract_memory(user_input)
    call_name = get_call_name(surname, gender)
    user_memory["call_name"] = call_name

    # 任务9找回：每轮记录老人情绪（低落/平淡/愉悦）→ emotion_log.json，供心情曲线
    emo = ai_services.analyze_emotion(user_input, call_name=call_name)
    _emo_log = load_emotion_log()
    _emo_log.append({"time": time.time(), "label": emo["label"],
                     "score": emo["score"], "note": emo["note"]})
    save_emotion_log(_emo_log)
''', "4. 每轮情绪记录")

# ---------- 5. "忘了吧"真删记忆（提醒处理之前，直接回复不调模型） ----------
rep('''
    # 提醒事务优先：设置/取消/改期/查看/确认走确定性流程，不经过生成模型，
    # 保证时间等关键信息准确、响应快；返回 None 才走普通聊天
    rem_reply = try_handle_reminder(user_input)
''', '''
    # 顺带找回：「忘了吧」就真删记忆——关键词事件 + 记忆库（事实/情节）双层删除，
    # 直接回复不调模型（老人对自己的记忆有删除权，明确请求必须真删、立即生效）
    if ai_services.FORGET_INTENT_RE.search(user_input):
        kept, forget_reply = ai_services.forget_by_request(
            user_input, user_memory["events"], memory_bank, call_name)
        user_memory["events"] = kept
        save_memory_events(kept)
        if forget_reply is not None:
            save_history = internal_history + [{"role": "user", "content": user_input},
                                               {"role": "assistant", "content": forget_reply}]
            save_conversation(conv_id, save_history)
            return "", save_history, None, conv_id, gr.update(choices=get_conversation_list_display())

    # 提醒事务优先：设置/取消/改期/查看/确认走确定性流程，不经过生成模型，
    # 保证时间等关键信息准确、响应快；返回 None 才走普通聊天
    rem_reply = try_handle_reminder(user_input)
''', "5. 忘了吧真删记忆")

# ---------- 6. 冷场主动递话题（搜索/生成之前） ----------
rep('''
    # 联网搜索：时效性/事实性问题先搜 Bing，再让 DeepSeek 结合资料回答
    search_context = ""
''', '''
    # 任务5找回：冷场/不知道聊啥 → 基于已记住的信息主动递个轻松话题
    if ai_services.COLD_RE.search(user_input):
        topic = ai_services.suggest_topic_switch(memory_bank, user_memory["events"], call_name)
        save_history = internal_history + [{"role": "user", "content": user_input},
                                           {"role": "assistant", "content": topic}]
        save_conversation(conv_id, save_history)
        return "", save_history, None, conv_id, gr.update(choices=get_conversation_list_display())

    # 联网搜索：时效性/事实性问题先搜 Bing，再让 DeepSeek 结合资料回答
    search_context = ""
''', "6. 冷场递话题")

# ---------- 7. 输出安全过滤（称呼纠正之后、主动确认之前） ----------
rep('''
    # 性别称呼保险丝：模型若仍叫错（爷爷↔奶奶），按呼语规则确定性纠正
    bot_reply = fix_address(bot_reply, call_name, gender, surname)
''', '''
    # 性别称呼保险丝：模型若仍叫错（爷爷↔奶奶），按呼语规则确定性纠正
    bot_reply = fix_address(bot_reply, call_name, gender, surname)

    # 任务6找回：输出安全过滤——脏话/冷漠语气本地规则毫秒级拦截；
    # 医疗风险措辞才调 DeepSeek 审核（不拖慢普通回复，保住 Bug⑥ 成果）
    sf = ai_services.safety_filter(bot_reply, personality=UI_STATE["personality"],
                                   call_name=call_name)
    if not sf["safe"]:
        print(f"[安全过滤] {sf['reason']} | 原文: {bot_reply[:40]}")
        bot_reply = sf["safe_reply"]
''', "7. 输出安全过滤")

# ---------- 8. 重复检测：连说≥3轮同一件事 → 轻轻递新话题 ----------
rep('''
    # 记忆主动确认（冷启动节制：前几轮不问；全局30分钟冷却与每条最多2次在模块内控制）
    if len(internal_history) >= 2:
        conf_q = memory_bank.pop_confirm_question(UI_STATE["personality"], call_name)
        if conf_q:
            bot_reply += f"\\n{conf_q}"
''', '''
    # 记忆主动确认（冷启动节制：前几轮不问；全局30分钟冷却与每条最多2次在模块内控制）
    if len(internal_history) >= 2:
        conf_q = memory_bank.pop_confirm_question(UI_STATE["personality"], call_name)
        if conf_q:
            bot_reply += f"\\n{conf_q}"

    # 任务5找回（重复检测）：老人连着≥3轮说同一件事，轻轻递个新话题，不打断当前回复
    _recent_u = [m["content"] for m in internal_history if m["role"] == "user"][-2:] + [user_input]
    if len(_recent_u) >= 3 and all(similarity(user_input, m) >= 0.5 for m in _recent_u[:2]):
        bot_reply += "\\n" + ai_services.suggest_topic_switch(
            memory_bank, user_memory["events"], call_name)
''', "8. 重复检测递话题")

# ---------- 9. 新对话：跨会话主动问候 ----------
rep('''
    new_id = str(int(time.time() * 1000))
    welcome_msg = f"您好{user_memory['call_name']}！我是小忆，很高兴能陪伴您～"
    chat_history = [{"role": "assistant", "content": welcome_msg}]
    return chat_history, new_id, gr.update(choices=get_conversation_list_display(), value=None)
''', '''
    new_id = str(int(time.time() * 1000))
    welcome_msg = f"您好{user_memory['call_name']}！我是小忆，很高兴能陪伴您～"
    greet = ai_services.build_cross_session_greeting(memory_bank, user_memory["call_name"])
    if greet:
        welcome_msg += "\\n" + greet
    chat_history = [{"role": "assistant", "content": welcome_msg}]
    return chat_history, new_id, gr.update(choices=get_conversation_list_display(), value=None)
''', "9. 新对话跨会话问候")

# ---------- 10. 进入聊天：跨会话主动问候 ----------
rep('''
        welcome_msg = f"您好{call_name}！我是小忆，很高兴能陪伴您～"
        chat_history = [{"role": "assistant", "content": welcome_msg}]
        new_id = str(int(time.time() * 1000))
''', '''
        welcome_msg = f"您好{call_name}！我是小忆，很高兴能陪伴您～"
        greet = ai_services.build_cross_session_greeting(memory_bank, call_name)
        if greet:
            welcome_msg += "\\n" + greet
        chat_history = [{"role": "assistant", "content": welcome_msg}]
        new_id = str(int(time.time() * 1000))
''', "10. 进入聊天跨会话问候")

# ---------- 11. 心情曲线生成函数（Gradio 界面前） ----------
rep('''
# ========== Gradio 界面 ==========
''', '''
# ========== 心情曲线（任务9找回） ==========
def generate_emotion_chart():
    """根据情绪日志生成曲线图，返回临时图片路径；数据不足返回 None。"""
    log = [e for e in load_emotion_log() if e.get("score") is not None]
    if len(log) < 2:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        # 中文标题防乱码：按平台常见字体依次回退
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "PingFang SC",
                                           "Noto Sans CJK SC", "Arial Unicode MS"]
        plt.rcParams["axes.unicode_minus"] = False
        log.sort(key=lambda x: x["time"])
        xs = [datetime.fromtimestamp(e["time"]) for e in log]
        ys = [float(e["score"]) for e in log]
        fig, ax = plt.subplots(figsize=(4.2, 2.0), dpi=120)
        ax.plot(xs, ys, color="#E06060", marker="o", markersize=3, linewidth=1.8)
        ax.axhline(0, color="#999", linewidth=0.8, linestyle="--")
        ax.fill_between(xs, ys, 0, where=[v >= 0 for v in ys], color="#F8C8C8", alpha=0.5)
        ax.fill_between(xs, ys, 0, where=[v < 0 for v in ys], color="#BBD3F0", alpha=0.5)
        ax.set_ylim(-1.1, 1.1)
        ax.tick_params(axis='x', labelsize=7, rotation=30)
        ax.tick_params(axis='y', labelsize=7)
        ax.set_title("心情曲线", fontsize=10, color="#4A3420")
        fig.tight_layout()
        path = os.path.join(TEMP_AUDIO_DIR, f"emotion_{int(time.time())}.png")
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        return path
    except Exception as e:
        print(f"生成情绪曲线失败: {e}")
        return None

# ========== Gradio 界面 ==========
''', "11. 心情曲线函数")

# ---------- 12. 侧边栏：心情曲线 + 我的小传 ----------
rep('''
                gr.Markdown("⏰ **提醒事项**")
                reminders_display = gr.HTML(value=get_reminders_panel_html(), elem_classes="event-box")
                gr.HTML('</div>')
''', '''
                gr.Markdown("⏰ **提醒事项**")
                reminders_display = gr.HTML(value=get_reminders_panel_html(), elem_classes="event-box")
                # 任务9找回：心情曲线
                gr.Markdown("📈 **心情曲线**")
                emotion_btn = gr.Button("查看心情曲线", size="sm", variant="secondary")
                emotion_img = gr.Image(label="", height=180, interactive=False,
                                       elem_classes="event-box", show_label=False)
                # 任务10找回：我的小传
                gr.Markdown("📖 **我的小传**")
                bio_btn = gr.Button("生成我的小传", size="sm", variant="secondary")
                bio_out = gr.Markdown(elem_classes="event-box")
                gr.HTML('</div>')
''', "12. 侧边栏按钮")

# ---------- 13. 事件绑定：心情曲线 / 我的小传 ----------
rep('''
    # 搜索过滤
    search_input.change(search_conversations, [search_input], [history_dropdown])
''', '''
    # 搜索过滤
    search_input.change(search_conversations, [search_input], [history_dropdown])

    # 任务9找回：心情曲线
    def show_emotion_chart():
        path = generate_emotion_chart()
        if path:
            return gr.update(value=path, visible=True)
        return gr.update(value=None, visible=False)
    emotion_btn.click(show_emotion_chart, [], [emotion_img])

    # 任务10找回：我的小传
    def show_biography():
        text = ai_services.build_biography(memory_bank, user_memory["call_name"])
        return gr.update(value=text)
    bio_btn.click(show_biography, [], [bio_out])
''', "13. 按钮事件绑定")

with open(path, "wb") as f:
    f.write(data.encode("utf-8"))
print("全部接线完成")

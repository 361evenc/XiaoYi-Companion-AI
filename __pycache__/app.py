import gradio as gr
import time
import random
from datetime import datetime
import tempfile
import os
import json
import wave
import numpy as np
import requests
import base64
import io
import shutil
from threading import Thread
import ai_services  # AI 大脑服务层（实体抽取等，DeepSeek 驱动 + 关键词回退）

# ========== 本地模型配置（训练好的小忆 3B 模型） ==========
import torch, ssl
ssl._create_default_https_context = ssl._create_unverified_context
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_output", "merged_16bit")
print("⏳ 加载本地模型...")
_model_dtype = torch.float16 if torch.cuda.is_available() else torch.float32
_model = AutoModelForCausalLM.from_pretrained(
    MODEL_PATH, device_map="auto", torch_dtype=_model_dtype, trust_remote_code=True,
)
_tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=True)
if _tokenizer.pad_token is None:
    _tokenizer.pad_token = _tokenizer.eos_token
print(f"✅ 模型加载完成")

# ========== 火山引擎语音配置 ==========
VOLC_ACCESS_TOKEN = os.getenv("VOLC_ACCESS_TOKEN", "")
VOLC_APP_ID = os.getenv("VOLC_APP_ID", "")

ASR_URL = "https://openspeech.bytedance.com/api/v1/asr"
TTS_URL = "https://openspeech.bytedance.com/api/v1/tts"

# ========== 文件路径 ==========
HISTORY_FILE = "conversations.json"
BACKUP_DIR = "backups"
MEMORY_EVENTS_FILE = "memory_events.json"
EMOTION_LOG_FILE = "emotion_log.json"  # 任务9：老人情绪日志

# 初始化备份目录
if not os.path.exists(BACKUP_DIR):
    os.makedirs(BACKUP_DIR, exist_ok=True)

# ========== 数据操作工具 ==========
def load_json_safe(file_path, default=None):
    if not os.path.exists(file_path):
        return default
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return default

def save_json_safe(file_path, data):
    """原子写入：先写临时文件，再替换"""
    tmp_path = file_path + ".tmp"
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, file_path)

def backup_file(file_path):
    if os.path.exists(file_path):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(BACKUP_DIR, f"{os.path.basename(file_path)}.{timestamp}.bak")
        shutil.copy2(file_path, backup_path)
        # 清理旧备份（保留最近5个）
        backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith(os.path.basename(file_path))])
        while len(backups) > 5:
            os.remove(os.path.join(BACKUP_DIR, backups.pop(0)))

# ========== 情绪日志持久化（任务9） ==========
def load_emotion_log():
    return load_json_safe(EMOTION_LOG_FILE, [])

def save_emotion_log(log):
    save_json_safe(EMOTION_LOG_FILE, log[-500:])  # 最多保留500条

# ========== 全局记忆事件持久化 ==========
def load_memory_events():
    events = load_json_safe(MEMORY_EVENTS_FILE, [])
    # 清除过时事件（保留最近7天）
    cutoff = time.time() - 7 * 24 * 3600
    events = [e for e in events if e.get("time", 0) > cutoff]
    return events

def save_memory_events(events):
    save_json_safe(MEMORY_EVENTS_FILE, events)

# 初始化内存变量
user_memory = {
    "events": load_memory_events(),
    "last_medication_time": 0.0,
    "call_name": "奶奶"
}

# ========== 对话历史管理 ==========
def load_all_conversations():
    convs = load_json_safe(HISTORY_FILE, [])
    convs.sort(key=lambda x: x.get('updated', 0), reverse=True)
    return convs

def save_conversation(conv_id, messages, title=None, update_title=False):
    convs = load_all_conversations()
    now_ts = time.time()
    found = False
    for i, c in enumerate(convs):
        if c['id'] == conv_id:
            convs[i]['messages'] = messages
            convs[i]['updated'] = now_ts
            if update_title and title:
                convs[i]['title'] = title
            found = True
            break
    if not found:
        if not title:
            # 自动提取标题（先给个默认值，后续可异步更新）
            first_msg = next((m['content'] for m in messages if m['role'] == 'user'), "新对话")
            title = first_msg[:20] + ("..." if len(first_msg) > 20 else "")
        new_conv = {
            'id': conv_id,
            'title': title,
            'messages': messages,
            'created': now_ts,
            'updated': now_ts
        }
        convs.append(new_conv)
    convs.sort(key=lambda x: x.get('updated', 0), reverse=True)
    save_json_safe(HISTORY_FILE, convs)

def delete_conversation_by_id(conv_id):
    convs = load_all_conversations()
    convs = [c for c in convs if c['id'] != conv_id]
    save_json_safe(HISTORY_FILE, convs)

def get_conversation_list_display(search_text=None):
    convs = load_all_conversations()
    # 限制显示最近50条，搜索时显示匹配的前20条
    display_convs = convs[:50] if not search_text else [c for c in convs if search_text in c['title']][:20]
    items = []
    for c in display_convs:
        time_str = datetime.fromtimestamp(c['updated']).strftime('%m-%d %H:%M')
        display_text = f"{c['title']} ({time_str})"
        items.append((display_text, c['id']))
    return items

def get_conversation_messages_by_id(conv_id):
    convs = load_all_conversations()
    for c in convs:
        if c['id'] == conv_id:
            return c['messages'], conv_id
    return None, None

def generate_title_async(conv_id, messages):
    """后台生成标题并更新"""
    try:
        # 取第一句用户消息作为标题
        user_msgs = [m['content'] for m in messages if m['role'] == 'user']
        if not user_msgs:
            return
        title = user_msgs[0][:15] if user_msgs[0] else "新对话"
        save_conversation(conv_id, messages, title=title, update_title=True)
    except Exception as e:
        print(f"生成标题失败: {e}")

def extract_memory(user_input):
    """调用 AI 实体抽取（DeepSeek 驱动，无 key 时自动回退关键词方案）"""
    events = ai_services.extract_entities(user_input)
    # 用药事件同步触发主动提醒计时
    for e in events:
        if e.get("type") == "medication":
            user_memory["last_medication_time"] = e.get("time", time.time())
    # 持久化
    if events:
        user_memory["events"].extend(events)
        user_memory["events"] = user_memory["events"][-200:]  # 限制内存条数
        save_memory_events(user_memory["events"])
    return events

# 关键词回退方案已迁移至 ai_services._keyword_extract（由 extract_memory 自动调用）

def get_memory_context():
    recent = [e for e in user_memory["events"] if e.get("status") == "active" and (time.time() - e["time"]) < 600]
    if not recent:
        return "目前没有任何需要你记住的具体事件。"
    lines = []
    for e in recent:
        minutes_ago = int((time.time() - e["time"]) / 60)
        time_str = "刚刚" if minutes_ago == 0 else f"{minutes_ago}分钟前"
        lines.append(f"{time_str}老人提到：{e['content']}。")
    return "\n".join(lines)

def get_recent_events_display():
    events = user_memory["events"]
    if not events:
        return "<div style='color:#8B5E34;padding:8px;text-align:center;'>暂无记录</div>"

    type_info = {
        "health": {"icon": "🏥", "label": "健康", "color": "#E8F5E9", "border": "#A5D6A7"},
        "medication": {"icon": "💊", "label": "用药", "color": "#FFF3E0", "border": "#FFCC80"},
        "item": {"icon": "🔑", "label": "物品", "color": "#E3F2FD", "border": "#90CAF9"},
        "family": {"icon": "👨‍👩‍👧‍👦", "label": "家人", "color": "#FCE4EC", "border": "#F48FB1"},
        "habit": {"icon": "🌱", "label": "习惯", "color": "#F1F8E9", "border": "#AED581"},
        "emotion": {"icon": "❤️", "label": "情绪", "color": "#FBE9E7", "border": "#FFAB91"},
        "allergy": {"icon": "⚠️", "label": "过敏", "color": "#FFEBEE", "border": "#EF9A9A"},
        "contact": {"icon": "📞", "label": "联系方式", "color": "#EDE7F6", "border": "#B39DDB"},
        "todo": {"icon": "📌", "label": "待办", "color": "#FFF8E1", "border": "#FFD54F"},
    }

    grouped = {}
    for e in events:
        t = e.get("type", "other")
        if t not in grouped:
            grouped[t] = []
        grouped[t].append(e)

    html_parts = []
    for idx, t_key in enumerate(["health", "medication", "item", "family", "habit", "emotion", "allergy", "contact", "todo"]):
        items = grouped.get(t_key, [])
        if not items:
            continue
        info = type_info.get(t_key, {"icon": "📌", "label": "其他", "color": "#F5F5F5", "border": "#BDBDBD"})

        # 取最近5条
        recent = items[-5:]
        count = len(items)

        entries_html = '<div class="timeline" style="margin-top:4px;">'
        for e in reversed(recent):
            time_str = datetime.fromtimestamp(e["time"]).strftime('%m-%d %H:%M')
            importance = e.get("importance", "normal")
            imp_star = "⭐" if importance == "high" else ""
            dot_color = "#E06060" if importance == "high" else info["border"]
            entries_html += f"""
            <div class="t-item" style="padding:4px 0 4px 16px;font-size:15px;position:relative;border-left:2px solid {dot_color};margin-bottom:2px;">
                <span style="position:absolute;left:-5px;top:9px;width:8px;height:8px;border-radius:50%;background:{dot_color};"></span>
                {imp_star} {e['content']}
                <span style="color:#aaa;font-size:12px;margin-left:4px;">{time_str}</span>
            </div>"""
        entries_html += '</div>'

        # 折叠面板
        cid = f"mt{idx}"
        html_parts.append(f"""
        <div style='background:{info["color"]};border:1px solid {info["border"]};border-radius:10px;margin-bottom:8px;overflow:hidden;'>
            <input type="checkbox" id="{cid}" checked style="display:none;">
            <label for="{cid}" style="display:flex;align-items:center;justify-content:space-between;padding:8px 10px;cursor:pointer;font-size:15px;font-weight:bold;user-select:none;">
                <span>{info["icon"]} {info["label"]} <span style="font-size:12px;color:#999;">{count}条</span></span>
                <span class="fold-icon" style="font-size:12px;color:#999;transition:transform 0.2s;">▼</span>
            </label>
            <div class="mc" style="padding:0 8px 8px;">
                {entries_html}
            </div>
        </div>
        <style>
            /* 折叠逻辑 */
            #{cid}:not(:checked) ~ .mc {{ display: none; }}
            #{cid}:checked ~ label .fold-icon {{ transform: rotate(180deg); }}
        </style>
        """)

    return "".join(html_parts)

# ========== 语音识别与合成（火山引擎）==========
def resample_audio(audio_data, orig_sr, target_sr=16000):
    duration = len(audio_data) / orig_sr
    new_length = int(duration * target_sr)
    old_indices = np.linspace(0, len(audio_data) - 1, len(audio_data))
    new_indices = np.linspace(0, len(audio_data) - 1, new_length)
    return np.interp(new_indices, old_indices, audio_data).astype(np.int16)

def audio_numpy_to_wav_bytes(audio_tuple):
    sample_rate, audio_array = audio_tuple
    if len(audio_array.shape) > 1:
        audio_array = audio_array[:, 0]
    if audio_array.dtype != np.int16:
        if np.max(np.abs(audio_array)) <= 1.0:
            audio_array = (audio_array * 32767).astype(np.int16)
        else:
            audio_array = audio_array.astype(np.int16)
    if sample_rate != 16000:
        audio_array = resample_audio(audio_array, sample_rate, 16000)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(audio_array.tobytes())
    buf.seek(0)
    return buf.read()

def text_to_speech(text):
    if not text or text.strip() == "":
        return None
    try:
        headers = {"Authorization": f"Bearer; {VOLC_ACCESS_TOKEN}"}
        data = {
            "app": {"appid": VOLC_APP_ID, "token": VOLC_ACCESS_TOKEN, "cluster": "volcano_tts"},
            "user": {"uid": "xiaoyi_user"},
            "audio": {"voice_type": "BV001_streaming", "encoding": "wav", "speed_ratio": 0.8, "volume_ratio": 1.3, "pitch_ratio": 1.0, "rate": 16000},
            "request": {"reqid": str(int(time.time() * 1000)), "text": text, "text_type": "plain", "operation": "query"}
        }
        resp = requests.post(TTS_URL, headers=headers, json=data, timeout=15)
        result = resp.json()
        if result.get("code") == 3000 and "data" in result:
            audio_bytes = base64.b64decode(result["data"])
            audio_filename = f"audio_{int(time.time())}_{random.randint(1000,9999)}.wav"
            audio_path = os.path.join(TEMP_AUDIO_DIR, audio_filename)
            with open(audio_path, "wb") as f:
                f.write(audio_bytes)
            return audio_path
    except Exception as e:
        print(f"TTS异常: {e}")
    return None

def transcribe_audio(audio_input):
    if audio_input is None:
        return ""
    try:
        if isinstance(audio_input, tuple):
            wav_bytes = audio_numpy_to_wav_bytes(audio_input)
        else:
            with open(audio_input, 'rb') as f:
                wav_bytes = f.read()
        if len(wav_bytes) < 200:
            return ""
        audio_base64 = base64.b64encode(wav_bytes).decode("utf-8")
        headers = {"Authorization": f"Bearer; {VOLC_ACCESS_TOKEN}", "Content-Type": "application/json"}
        json_body = {
            "app": {"appid": VOLC_APP_ID, "token": VOLC_ACCESS_TOKEN, "cluster": "volcengine_input_common"},
            "user": {"uid": "xiaoyi_user"},
            "audio": {"format": "wav", "rate": 16000, "bits": 16, "channel": 1, "language": "zh-CN", "data": audio_base64, "enable_itn": True, "enable_punc": True},
            "request": {"reqid": str(int(time.time() * 1000)), "sequence": 1}
        }
        resp = requests.post(ASR_URL, headers=headers, json=json_body, timeout=30)
        result = resp.json()
        if result.get("code") == 1000 and "result" in result:
            return result["result"][0]["text"]
    except:
        pass
    return ""

# ========== 对话核心逻辑 ==========
def chat_response(user_input, chat_history, surname, gender, is_muted, personality, conv_id, dropdown):
    global last_user_interaction_time
    if user_input and str(user_input).strip():
        last_user_interaction_time = time.time()
    if not chat_history:
        chat_history = []
    if not user_input or str(user_input).strip() == "":
        bot_reply = f"{user_memory['call_name']}，您说什么我没听清呢～"
        chat_history.append({"role": "assistant", "content": bot_reply})
        save_conversation(conv_id, chat_history)
        return "", chat_history, None, conv_id, gr.update(choices=get_conversation_list_display())

    extract_memory(user_input)
    call_name = get_call_name(surname, gender)
    user_memory["call_name"] = call_name

    # 任务9：老人情绪分析并记入日志
    emo = ai_services.analyze_emotion(user_input, call_name=call_name)
    _emo_log = load_emotion_log()
    _emo_log.append({"time": time.time(), "label": emo["label"], "score": emo["score"], "note": emo["note"]})
    save_emotion_log(_emo_log)

    # 前置意图：先判断是否需要「短路」回复，避免无意义调用模型
    preempt_reply = None
    # 任务7：遗忘请求（"忘了吧"等）→ 直接回复，不调模型
    forget_kw = ["忘了吧", "忘了它", "删掉", "别记了", "不记得了", "甭记", "去掉吧", "甭记了"]
    if any(k in user_input for k in forget_kw):
        kept, reply = ai_services.forget_by_request(user_input, user_memory["events"], call_name)
        user_memory["events"] = kept
        save_memory_events(kept)
        if reply:
            preempt_reply = reply
    # 任务5：冷场/不知聊啥 → 主动给个轻松话题
    if preempt_reply is None:
        cold_kw = ["没啥聊", "不知道说啥", "聊啥", "没话", "冷场", "不想说", "没意思", "干啥呢", "说点啥", "没得聊"]
        if any(k in user_input for k in cold_kw):
            topic = ai_services.suggest_topic_switch(user_memory["events"], call_name)
            if topic:
                preempt_reply = topic

    if personality == "踏实务实":
        base_prompt = PROMPT_PRACTICAL
    elif personality == "风趣幽默":
        base_prompt = PROMPT_HUMOR
    elif personality == "暖心知心":
        base_prompt = PROMPT_CARING
    else:
        base_prompt = PROMPT_PRACTICAL

    # 任务3：检索与注入相关长期记忆，让小忆基于往事自然接话
    memory_injection = ai_services.build_memory_injection(user_input, user_memory["events"])
    system_content = base_prompt + f"\n当前称呼：{call_name}" + memory_injection

    # 提取纯文本（Gradio 可能嵌套 {'text': ...}）
    def extract_text(content):
        if isinstance(content, str): return content
        if isinstance(content, dict): return extract_text(content.get("text", ""))
        if isinstance(content, list): return " ".join(extract_text(c) for c in content if c)
        return str(content)

    # 统一 chat_history 为纯文本 dict
    internal_history = []
    for msg in chat_history:
        if isinstance(msg, (list, tuple)) and len(msg) == 2:
            u, b = extract_text(msg[0]), extract_text(msg[1])
            if u: internal_history.append({"role": "user", "content": u.strip()})
            if b: internal_history.append({"role": "assistant", "content": b.strip()})
        elif isinstance(msg, dict):
            r, c = msg.get("role","user"), extract_text(msg.get("content",""))
            if r in ("user","assistant") and c.strip():
                internal_history.append({"role": r, "content": c.strip()})

    try:
        if preempt_reply is not None:
            bot_reply = preempt_reply
        else:
            prompt = f"<|im_start|>system\n{system_content}<|im_end|>"
            for m in internal_history[-6:]:  # 任务3：短期上下文窗口放宽到6轮，缓解"聊到第5句忘第1句"
                prompt += f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>"
            prompt += f"<|im_start|>user\n{user_input}<|im_end|><|im_start|>assistant\n"

            inputs = _tokenizer(prompt, return_tensors="pt").to(_model.device)
            with torch.no_grad():
                outputs = _model.generate(**inputs, max_new_tokens=300, temperature=0.9,
                    top_p=0.95, do_sample=True, repetition_penalty=1.2,
                    no_repeat_ngram_size=4)
            bot_reply = _tokenizer.decode(outputs[0][inputs.input_ids.shape[1]:], skip_special_tokens=True)
            for junk in ["<|im_start|>","<|im_end|>","assistant","user","system"]:
                bot_reply = bot_reply.replace(junk, "")
            bot_reply = bot_reply.strip().strip("[]").strip("{}").strip(",").strip()
            if not bot_reply or len(bot_reply) < 5:
                bot_reply = f"{call_name}，我听着呢，您说。"
            # 释放显存
            torch.cuda.empty_cache()
    except Exception as e:
        print(f"推理错误: {e}")
        bot_reply = f"{call_name}，我有点听不清楚，您再说一遍好吗？"

    # 任务6：输出安全过滤（小忆回复先过安全审核，不合适则用安全替换）
    sf = ai_services.safety_filter(bot_reply, personality=personality, call_name=call_name)
    if not sf["safe"]:
        bot_reply = sf["safe_reply"]

    is_first_msg = len(chat_history) <= 1
    save_history = internal_history + [{"role":"user","content":user_input},{"role":"assistant","content":bot_reply}]
    save_conversation(conv_id, save_history)
    if is_first_msg:
        Thread(target=generate_title_async, args=(conv_id, save_history), daemon=True).start()
    audio_path = None if is_muted else text_to_speech(bot_reply)
    return "", save_history, audio_path, conv_id, gr.update(choices=get_conversation_list_display())

def process_mic(audio_input, chat_history, surname, gender, is_muted, personality, conv_id, dropdown):
    global last_user_interaction_time
    last_user_interaction_time = time.time()
    if not audio_input:
        bot_reply = "我没听清，请再说一遍"
        chat_history.append({"role": "assistant", "content": bot_reply})
        save_conversation(conv_id, chat_history)
        return "", chat_history, None, conv_id, gr.update(choices=get_conversation_list_display())
    text = transcribe_audio(audio_input)
    if not text:
        bot_reply = "我没听清您说的话，能再说一遍吗？"
        chat_history.append({"role": "assistant", "content": bot_reply})
        save_conversation(conv_id, chat_history)
        return "", chat_history, None, conv_id, gr.update(choices=get_conversation_list_display())
    return chat_response(text, chat_history, surname, gender, is_muted, personality, conv_id, dropdown)

def new_conversation():
    global last_user_interaction_time
    last_user_interaction_time = time.time()
    new_id = str(int(time.time() * 1000))
    call_name = user_memory["call_name"]
    welcome_msg = f"您好{call_name}！我是小忆，很高兴能陪伴您～"
    # 任务4：跨会话记忆问候（有跨天记忆时主动提起）
    greet = ai_services.build_cross_session_greeting(user_memory["events"], call_name)
    if greet:
        welcome_msg += "\n" + greet
    chat_history = [{"role": "assistant", "content": welcome_msg}]
    return chat_history, new_id, gr.update(choices=get_conversation_list_display(), value=None)

def load_conversation_by_id(conv_id):
    if not conv_id:
        return [], conv_id
    msgs, _ = get_conversation_messages_by_id(conv_id)
    return msgs if msgs else [], conv_id

def delete_selected_conversation(selected_value, dropdown_choices):
    """根据选中的value (display_text, conv_id) 删除对话"""
    if not selected_value:
        return gr.update(choices=dropdown_choices), gr.update(value=None)
    conv_id = selected_value
    delete_conversation_by_id(conv_id)
    new_choices = get_conversation_list_display()
    return gr.update(choices=new_choices, value=None), gr.update(value=None)

def search_conversations(search_text):
    return gr.update(choices=get_conversation_list_display(search_text))

def inject_proactive_message(chat_history, is_muted, conv_id):
    global last_user_interaction_time, last_proactive_msg_time
    if not chat_history:
        chat_history = []
    # 任务8：全局冷却，避免短时间内反复打扰老人
    if ai_services.PROACTIVE_GLOBAL_COOLDOWN > 0 and (time.time() - last_proactive_msg_time) < ai_services.PROACTIVE_GLOBAL_COOLDOWN:
        return chat_history, None
    msg = check_active_trigger()
    if msg and (time.time() - last_user_interaction_time) > 15:
        chat_history.append({"role": "assistant", "content": msg})
        save_conversation(conv_id, chat_history)
        audio_path = None if is_muted else text_to_speech(msg)
        return chat_history, audio_path
    return chat_history, None

def toggle_mute(current_mute):
    new_mute = not current_mute
    btn_text = "🔇 静音" if new_mute else "🔊 取消静音"
    audio_path = None
    if current_mute and not new_mute:
        audio_path = text_to_speech("小忆语音已开启")
    return new_mute, gr.update(value=btn_text), audio_path

# ========== 界面样式（不变） ==========
custom_css = """
    * { font-family: "Microsoft YaHei", sans-serif !important; color: #2D1B0E !important; }
    body { background-color: #FEF8ED !important; }
    .gradio-container { background-color: #FEF8ED !important; max-width: 100% !important; padding: 0 !important; }
    .top-bar { background-color: #F5E5CC !important; padding: 14px 20px !important; border-radius: 0 0 20px 20px !important; display: flex !important; justify-content: space-between !important; align-items: center !important; font-size: 22px !important; font-weight: bold !important; color: #2D1B0E !important; margin-bottom: 10px !important; }
    .status-online { display: flex !important; align-items: center !important; gap: 8px !important; font-size: 20px !important; }
    .green-dot { width: 14px !important; height: 14px !important; background-color: #52c41a !important; border-radius: 50% !important; }
    .input-area { background-color: #F5E5CC !important; padding: 16px !important; border-radius: 20px 20px 0 0 !important; margin-top: 10px !important; display: flex !important; align-items: center !important; gap: 6px !important; }
    .chat-input { flex: 1 !important; }
    .chat-input textarea { border-radius: 30px !important; padding: 14px 24px !important; font-size: 20px !important; background: #fff !important; border: 1px solid #ddd !important; min-height: 76px !important; }
    .big-btn { height: 52px !important; font-size: 20px !important; border-radius: 30px !important; background: #E3B87C !important; color: #2D1B0E !important; font-weight: bold !important; border: none !important; outline: none !important; box-shadow: none !important; min-width: 60px !important; }
    .big-btn:focus, .big-btn:active { outline: none !important; box-shadow: none !important; }
    .quick-btn { height: 48px !important; font-size: 18px !important; border-radius: 25px !important; background: #ffffff !important; color: #2D1B0E !important; border: 1px solid #D4A86A !important; margin: 0 4px !important; flex: 1 !important; cursor: pointer !important; }
    .quick-btn:hover { background-color: #F5E5CC !important; border-color: #C88A52 !important; }
    .footer-tip { text-align: center !important; color: #8B5E34 !important; font-size: 16px !important; padding: 10px !important; }
    .center-container { margin: 0 auto !important; width: 80% !important; max-width: 400px !important; }
    /* ===== 聊天气泡（老年人友好） ===== */
    .gradio-chatbot .message {
        max-width: 82% !important;
        width: fit-content !important;
        padding: 16px 22px !important;
        font-size: 20px !important;
        line-height: 1.7 !important;
        border-radius: 20px !important;
        margin-bottom: 12px !important;
        box-shadow: 0 2px 6px rgba(0,0,0,0.08) !important;
    }
    .gradio-chatbot .user-message {
        margin-left: auto !important;
        background-color: #E3B87C !important;
        color: #2D1B0E !important;
        border-bottom-right-radius: 4px !important;
        border: none !important;
    }
    .gradio-chatbot .bot-message {
        margin-right: auto !important;
        background-color: #FFFFFF !important;
        color: #2D1B0E !important;
        border: 1px solid #E0D3C0 !important;
        border-bottom-left-radius: 4px !important;
    }
    .quick-btn-row { padding: 0 10px !important; display: flex !important; gap: 8px !important; align-items: stretch !important; }
    .history-sidebar { background-color: #F9F2E6 !important; border-radius: 16px !important; padding: 12px !important; margin: 10px !important; height: 85% !important; overflow-y: auto !important; }
    .history-title { font-size: 20px !important; font-weight: bold !important; margin-bottom: 12px !important; text-align: center !important; color: #2D1B0E !important; }
    .event-box { background: #fff; border-radius: 12px; padding: 12px; margin-top: 10px; font-size: 16px; color: #2D1B0E; max-height: 250px; overflow-y: auto; }
    /* ===== 麦克风按钮 ===== */
    .mic-btn {
        min-width: 52px !important;
        width: 52px !important;
        height: 52px !important;
        border-radius: 50% !important;
        background: #E3B87C !important;
        border: none !important;
        outline: none !important;
        box-shadow: none !important;
        font-size: 24px !important;
        cursor: pointer !important;
        transition: all 0.2s ease !important;
        padding: 0 !important;
        line-height: 52px !important;
        text-align: center !important;
    }
    .mic-btn:hover {
        background: #D4A86A !important;
        transform: scale(1.08) !important;
    }
    .mic-btn:focus, .mic-btn:active { outline: none !important; box-shadow: none !important; }
    /* ===== 录音状态文字 ===== */
    .mic-status {
        text-align: center !important;
        font-size: 18px !important;
        color: #8B5E34 !important;
        padding: 2px 0 !important;
        margin: 0 !important;
        min-height: 28px !important;
    }
    .audio-hidden { height: 0 !important; overflow: hidden !important; margin: 0 !important; padding: 0 !important; position: absolute !important; opacity: 0 !important; pointer-events: none !important; }
"""

# ========== 全局变量 ==========
last_user_interaction_time = time.time()
last_proactive_msg_time = 0.0  # 任务8：上次主动消息时间（用于全局冷却）
TEMP_AUDIO_DIR = tempfile.mkdtemp(prefix="xiaoyi_audio_")
os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)

# 启动时备份历史文件
backup_file(HISTORY_FILE)
backup_file(MEMORY_EVENTS_FILE)

# 引入 prompt 定义（简短版，与训练数据一致）
PROMPT_PRACTICAL = "你是小忆，一位踏实稳重、真诚靠谱的晚辈。说话温和耐心，用简短自然的口语。"
PROMPT_HUMOR = "你是小忆，一位活泼开朗、风趣俏皮的晚辈。语气轻松欢快，说话带一点小俏皮。"
PROMPT_CARING = "你是小忆，一位温柔细腻、共情暖心的晚辈。语气温柔舒缓，善于倾听与安抚。"

def get_call_name(surname, gender):
    s = surname.strip() if surname else ""
    if s:
        return f"{s}{'奶奶' if gender == '女' else '爷爷'}"
    return "奶奶" if gender == '女' else "爷爷"

def check_active_trigger():
    """按 ai_services 的主动触发规则表评估，返回应主动说的一句话（或 None）。
    记忆持久化与全局状态仍在 app 内处理，规则逻辑交给 ai_services。"""
    ctx = {
        "events": user_memory["events"],
        "last_medication_time": user_memory["last_medication_time"],
        "call_name": user_memory["call_name"],
        "now": time.time(),
        "cared_ids": [],
    }
    msg = ai_services.evaluate_proactive_trigger(ctx)
    if ctx["cared_ids"]:
        save_memory_events(user_memory["events"])
    user_memory["last_medication_time"] = ctx["last_medication_time"]
    if msg:
        global last_proactive_msg_time
        last_proactive_msg_time = time.time()
    return msg

def clean_old_audio_files():
    now = time.time()
    for f in os.listdir(TEMP_AUDIO_DIR):
        file_path = os.path.join(TEMP_AUDIO_DIR, f)
        if os.path.isfile(file_path) and (now - os.path.getmtime(file_path) > 600):
            try:
                os.remove(file_path)
            except:
                pass

# ========== 任务9：情绪曲线 ==========
def generate_emotion_chart():
    """根据情绪日志生成曲线图，返回临时图片路径；数据不足返回 None。"""
    log = load_emotion_log()
    log = [e for e in log if e.get("score") is not None]
    if len(log) < 2:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
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

# ========== 任务7：记忆定时新陈代谢 ==========
def prune_old_memories():
    """定时清理过期日常琐事（保留 permanent / 近期）。"""
    kept, removed = ai_services.prune_memories(user_memory["events"])
    if removed:
        user_memory["events"] = kept
        save_memory_events(user_memory["events"])
        return removed
    return 0

# ========== Gradio 界面 ==========
with gr.Blocks(title="小忆陪伴助手") as demo:
    ready = gr.State(False)
    surname = gr.State("")
    gender = gr.State("女")
    is_muted = gr.State(True)
    personality = gr.State("踏实务实")
    current_conv_id = gr.State(None)
    audio_output = gr.Audio(autoplay=True, visible=True, elem_classes="audio-hidden")

    # 欢迎页
    with gr.Column(visible=True) as setup_view:
        gr.Markdown("""<div style="text-align:center; font-size:24px; color:#4A3420; padding:40px 20px;">🌸 欢迎使用小忆陪伴助手</div>""")
        with gr.Column(scale=1, min_width=300, elem_classes="center-container"):
            s_ipt = gr.Textbox(label="请输入您的姓氏", placeholder="例如：张、李、王")
            g_ipt = gr.Radio(["女", "男"], label="请选择称呼性别", value="女")
            p_ipt = gr.Radio(["踏实务实", "风趣幽默", "暖心知心"], label="请选择我的性格", value="踏实务实")
            btn = gr.Button("✅ 进入聊天", variant="primary", size="lg")

    # 主界面
    with gr.Column(visible=False) as main_view:
        with gr.Row():
            with gr.Column(scale=1, min_width=240):
                gr.HTML('<div class="history-sidebar">')
                gr.Markdown("📚 **记忆箱**")
                # 搜索框
                search_input = gr.Textbox(placeholder="🔍 搜索对话...", show_label=False, container=False)
                # 对话列表
                history_dropdown = gr.Dropdown(choices=get_conversation_list_display(), label="历史对话", interactive=True, allow_custom_value=False)
                with gr.Row():
                    delete_btn = gr.Button("🗑️ 删除", variant="stop", size="sm")
                    new_conv_btn = gr.Button("➕ 新对话", variant="secondary", size="sm")
                # 重要事件展示
                gr.Markdown("📝 **重要事件**")
                events_display = gr.HTML(value=get_recent_events_display(), elem_classes="event-box")
                gr.Markdown("📈 **心情曲线**")
                emotion_btn = gr.Button("查看心情曲线", size="sm", variant="secondary")
                emotion_img = gr.Image(label="", height=180, interactive=False, elem_classes="event-box", show_label=False)
                gr.Markdown("📖 **我的小传**")
                bio_btn = gr.Button("生成我的小传", size="sm", variant="secondary")
                bio_out = gr.Markdown(elem_classes="event-box")
                gr.HTML('</div>')
            with gr.Column(scale=4):
                gr.HTML('''<div class="top-bar"><div>🌸 小忆</div><div class="status-online"><div class="green-dot"></div> 陪伴中</div></div>''')
                chat = gr.Chatbot(value=[], height=520, show_label=False)
                mic_status = gr.Markdown("", visible=True, elem_classes="mic-status")
                with gr.Row(elem_classes="quick-btn-row"):
                    q1 = gr.Button("我今天挺好的", elem_classes="quick-btn")
                    q2 = gr.Button("有点想你了", elem_classes="quick-btn")
                    q3 = gr.Button("身体有点不舒服", elem_classes="quick-btn")
                    q4 = gr.Button("讲讲以前的事", elem_classes="quick-btn")
                with gr.Row(elem_classes="input-area"):
                    mic_btn = gr.Button("🎙️", elem_classes="mic-btn")
                    txt = gr.Textbox(placeholder="点这里跟小忆说话...", show_label=False, container=False, elem_classes="chat-input")
                    send = gr.Button("📨 发送", elem_classes="big-btn")
                    mute_btn = gr.Button("🔇 静音", elem_classes="big-btn")
                mic_input = gr.Audio(sources="microphone", type="numpy", visible=False, show_label=False)
                gr.HTML('<div class="footer-tip">💖 小忆会记住你说过的每一句话</div>')

    # 事件绑定
    def enter_chat(s, g, p, muted):
        surname_val = s.strip() if s else ""
        gender_val = g
        call_name = get_call_name(surname_val, gender_val)
        user_memory["call_name"] = call_name
        welcome_msg = f"您好{call_name}！我是小忆，很高兴能陪伴您～"
        # 任务4：跨会话记忆问候
        greet = ai_services.build_cross_session_greeting(user_memory["events"], call_name)
        if greet:
            welcome_msg += "\n" + greet
        chat_history = [{"role": "assistant", "content": welcome_msg}]
        new_id = str(int(time.time() * 1000))
        audio_path = None if muted else text_to_speech(welcome_msg)
        global last_user_interaction_time
        last_user_interaction_time = time.time()
        return True, surname_val, gender_val, p, gr.update(visible=False), gr.update(visible=True), chat_history, new_id, audio_path, gr.update(choices=get_conversation_list_display()), get_recent_events_display()

    btn.click(enter_chat, [s_ipt, g_ipt, p_ipt, is_muted],
              [ready, surname, gender, personality, setup_view, main_view, chat, current_conv_id, audio_output, history_dropdown, events_display])

    # 文字/语音交互后刷新事件展示
    def refresh_events_display(*args):
        return get_recent_events_display()

    # 文字提交
    txt.submit(chat_response, [txt, chat, surname, gender, is_muted, personality, current_conv_id, history_dropdown],
               [txt, chat, audio_output, current_conv_id, history_dropdown]).then(refresh_events_display, outputs=events_display)
    send.click(chat_response, [txt, chat, surname, gender, is_muted, personality, current_conv_id, history_dropdown],
               [txt, chat, audio_output, current_conv_id, history_dropdown]).then(refresh_events_display, outputs=events_display)

    # 快捷按钮
    for btn_q, q_text in [(q1, "我今天挺好的"), (q2, "有点想你了"), (q3, "身体有点不舒服"), (q4, "讲讲以前的事")]:
        btn_q.click(lambda t=q_text: t, None, txt).then(
            chat_response, [txt, chat, surname, gender, is_muted, personality, current_conv_id, history_dropdown],
            [txt, chat, audio_output, current_conv_id, history_dropdown]
        ).then(refresh_events_display, outputs=events_display)

    # 麦克风 - 点按钮弹出录音器
    mic_btn.click(
        lambda: [gr.update(visible=True), gr.update(value="🎤 **点击麦克风图标开始录音，再点停止**")],
        outputs=[mic_input, mic_status]
    )
    mic_input.stop_recording(process_mic, [mic_input, chat, surname, gender, is_muted, personality, current_conv_id, history_dropdown],
                             [txt, chat, audio_output, current_conv_id, history_dropdown]).then(
        lambda: [gr.update(visible=False), gr.update(value="")],
        outputs=[mic_input, mic_status]
    ).then(refresh_events_display, outputs=events_display)

    # 静音
    mute_btn.click(toggle_mute, [is_muted], [is_muted, mute_btn, audio_output])

    # 新建对话
    new_conv_btn.click(new_conversation, [], [chat, current_conv_id, history_dropdown]).then(refresh_events_display, outputs=events_display)

    # 加载历史
    history_dropdown.change(load_conversation_by_id, [history_dropdown], [chat, current_conv_id])

    # 删除对话
    delete_btn.click(delete_selected_conversation, [history_dropdown, history_dropdown], [history_dropdown, history_dropdown])

    # 搜索过滤
    search_input.change(search_conversations, [search_input], [history_dropdown])

    # 任务9：心情曲线
    def show_emotion_chart():
        path = generate_emotion_chart()
        if path:
            return gr.update(value=path, visible=True)
        return gr.update(value=None, visible=False)
    emotion_btn.click(show_emotion_chart, [], [emotion_img])

    # 任务10：我的小传
    def show_biography():
        text = ai_services.build_biography(user_memory["events"], user_memory["call_name"])
        return gr.update(value=text)
    bio_btn.click(show_biography, [], [bio_out])

    # 定时器
    gr.Timer(30).tick(inject_proactive_message, [chat, is_muted, current_conv_id], [chat, audio_output])
    gr.Timer(30).tick(clean_old_audio_files, [])
    gr.Timer(120).tick(prune_old_memories, [])  # 任务7：定时清理过期记忆（2分钟一次，温和）

if __name__ == "__main__":
    demo.launch(server_port=7861, share=False, css=custom_css)
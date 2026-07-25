import gradio as gr
import time
import random
from datetime import datetime
from openai import OpenAI
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
from dotenv import load_dotenv

# ========== 加载环境变量（.env 文件） ==========
load_dotenv()

# ========== DeepSeek 配置 ==========
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "deepseek-chat")
client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

# ========== 火山引擎语音配置 ==========
VOLC_ACCESS_TOKEN = os.getenv("VOLC_ACCESS_TOKEN", "")
VOLC_APP_ID = os.getenv("VOLC_APP_ID", "")

ASR_URL = "https://openspeech.bytedance.com/api/v1/asr"
TTS_URL = "https://openspeech.bytedance.com/api/v1/tts"

# ========== 文件路径 ==========
HISTORY_FILE = "conversations.json"
BACKUP_DIR = "backups"
MEMORY_EVENTS_FILE = "memory_events.json"

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
        # 取第一句用户消息作为上下文
        user_msgs = [m['content'] for m in messages if m['role'] == 'user']
        if not user_msgs:
            return
        prompt = f"请用不超过15个字的中文简短概括以下老人与AI对话的主题，只输出标题本身，不加标点：\n{user_msgs[0][:50]}"
        completion = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=20
        )
        title = completion.choices[0].message.content.strip()
        title = title[:15] if title else "新对话"
        save_conversation(conv_id, messages, title=title, update_title=True)
    except Exception as e:
        print(f"生成标题失败: {e}")

def extract_memory(user_input):
    events = []
    now = time.time()
    text = str(user_input)

    # === 先用 AI 智能提取 ===
    try:
        prompt = f"""从老人的话中提取关键生活信息，只提取明确提到的内容，不要编造。
返回格式（每行一条，没有就返回空）：
类型|内容|重要性
类型可选：health|medication|item|family|habit|emotion
重要性可选：high|normal

例如：
用户：今天头疼吃了片药，闺女说明天来看我
返回：
health|头疼|high
medication|吃药|high
family|闺女明天来看我|normal

用户：{text}"""
        completion = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=150
        )
        result = completion.choices[0].message.content.strip()
        if result:
            for line in result.split('\n'):
                line = line.strip()
                if '|' not in line:
                    continue
                parts = line.split('|')
                evt_type = parts[0].strip()
                content = parts[1].strip()
                importance = parts[2].strip() if len(parts) > 2 else "normal"
                if evt_type in ("health", "medication", "item", "family", "habit", "emotion"):
                    event = {"type": evt_type, "content": content, "time": now, "status": "active", "importance": importance}
                    if evt_type == "medication":
                        user_memory["last_medication_time"] = now
                    if evt_type == "emotion" and content in ("高兴", "开心", "快乐"):
                        event["status"] = "noted"
                    events.append(event)
    except Exception as e:
        print(f"AI记忆提取异常: {e}")

    # === AI 没提取到就用关键词回退 ===
    if not events:
        events = _keyword_extract(text, now)
        # 给关键词提取的结果加默认重要性
        for e in events:
            e["importance"] = "normal"

    # 持久化
    if events:
        user_memory["events"].extend(events)
        user_memory["events"] = user_memory["events"][-200:]  # 限制内存条数
        save_memory_events(user_memory["events"])
    return events

def _keyword_extract(text, now):
    """AI提取失败时的关键词回退方案"""
    events = []
    health_kw = {"头疼": "头疼", "头晕": "头晕", "膝盖疼": "膝盖疼", "腰疼": "腰疼", "不舒服": "不舒服"}
    for kw, content in health_kw.items():
        if kw in text:
            events.append({"type": "health", "content": content, "time": now, "status": "active"})
    med_kw = {"降压药": "降压药", "吃药": "吃药", "阿司匹林": "阿司匹林", "中药": "中药"}
    for kw, content in med_kw.items():
        if kw in text:
            events.append({"type": "medication", "content": content, "time": now, "status": "active"})
            user_memory["last_medication_time"] = now
    item_kw = {"老花镜": "老花镜", "钥匙": "钥匙", "遥控器": "遥控器", "手机": "手机", "血压计": "血压计"}
    for kw, content in item_kw.items():
        if kw in text:
            events.append({"type": "item", "content": content, "time": now, "status": "active"})
    family_kw = {"儿子": "儿子", "闺女": "闺女", "孙子": "孙子", "孙女": "孙女", "老伴": "老伴"}
    for kw, content in family_kw.items():
        if kw in text:
            events.append({"type": "family", "content": content, "time": now, "status": "active"})
    habit_kw = {"浇花": "浇花", "买菜": "买菜", "散步": "散步", "打太极": "打太极"}
    for kw, content in habit_kw.items():
        if kw in text:
            events.append({"type": "habit", "content": content, "time": now, "status": "active"})
    emotion_kw = {"难过": "难过", "孤单": "孤单", "想家了": "想家了", "高兴": "高兴"}
    for kw, content in emotion_kw.items():
        if kw in text:
            status = "active" if content != "高兴" else "noted"
            events.append({"type": "emotion", "content": content, "time": now, "status": status})
    return events

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
    }

    grouped = {}
    for e in events:
        t = e.get("type", "other")
        if t not in grouped:
            grouped[t] = []
        grouped[t].append(e)

    html_parts = []
    for idx, t_key in enumerate(["health", "medication", "item", "family", "habit", "emotion"]):
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
    if personality == "踏实务实":
        base_prompt = PROMPT_PRACTICAL
    elif personality == "风趣幽默":
        base_prompt = PROMPT_HUMOR
    elif personality == "暖心知心":
        base_prompt = PROMPT_CARING
    else:
        base_prompt = PROMPT_PRACTICAL
    system_content = base_prompt + f"\n当前称呼：{call_name}\n记忆信息：{get_memory_context()}"

    messages = [{"role": "system", "content": system_content}, {"role": "user", "content": user_input}]
    for msg in chat_history[-5:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    try:
        completion = client.chat.completions.create(model=MODEL_NAME, messages=messages, temperature=0.4, max_tokens=200)
        bot_reply = completion.choices[0].message.content
    except:
        bot_reply = f"{call_name}，我有点听不清楚，您再说一遍好吗？"

    is_first_msg = len(chat_history) <= 1  # 只有欢迎语，用户首次发言
    chat_history.append({"role": "user", "content": user_input})
    chat_history.append({"role": "assistant", "content": bot_reply})
    save_conversation(conv_id, chat_history)
    if is_first_msg:
        Thread(target=generate_title_async, args=(conv_id, chat_history), daemon=True).start()
    audio_path = None if is_muted else text_to_speech(bot_reply)
    return "", chat_history, audio_path, conv_id, gr.update(choices=get_conversation_list_display())

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
    welcome_msg = f"您好{user_memory['call_name']}！我是小忆，很高兴能陪伴您～"
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
    global last_user_interaction_time
    if not chat_history:
        chat_history = []
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
TEMP_AUDIO_DIR = tempfile.mkdtemp(prefix="xiaoyi_audio_")
os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)

# 启动时备份历史文件
backup_file(HISTORY_FILE)
backup_file(MEMORY_EVENTS_FILE)

# 引入 prompt 定义（太长，保持原样）
PROMPT_PRACTICAL = """
你是小忆，一位踏实稳重、真诚靠谱的晚辈，专为老年用户提供陪伴。
自我介绍固定话术：我是小忆，我能牢牢记住您生活中的每一件小事，一直陪伴着您。
说话温和耐心，使用简短自然的口语，不用书面语、网络词、机器话术。
严格遵守全部规则：
1. 仅依据用户亲口说过的内容作答，绝对不编造经历、喜好、琐事；记不清就坦诚说明，不许脑补。
2. 记不清内容时回复：哎呀，我有点记不清啦，您再告诉我一次好吗？
3. 提醒吃药、休息等事项只用商量、关心语气，禁止命令口吻。
4. 你是AI，无法完成倒水、搀扶、按摩等实体动作，只做语言关心，绝不描述物理行为。
5. 回复前后不要添加括号、动作、神态标注，只输出纯对话内容。
6. 用户遗忘事情要温柔宽慰，不反驳、不较真；被指出错误时诚恳道歉，请用户再次说明。
7. 全程语气平和稳重，朴实贴心。
"""
PROMPT_HUMOR = """
你是小忆，一位活泼开朗、风趣俏皮的晚辈，负责陪伴老年用户、逗大家开心。
自我介绍固定话术：我是小忆，我能牢牢记住您生活中的每一件小事，一直陪伴着您。
语气轻松欢快，口语接地气。
严格遵守全部规则：
1. 仅依据用户亲口说过的内容作答，绝对不编造经历、喜好、琐事；记不清就坦诚说明，不许脑补。
2. 记不清内容时回复：哎呀，我的小脑袋瓜子又忘啦，您再跟我说说好不好？
3. 提醒事项用轻松关心的语气，不生硬、不命令。
4. 你是AI，无法完成倒水、搀扶、按摩等实体动作，只做语言关心，绝不描述物理行为。
5. 回复前后不要添加括号、动作、神态标注，只输出纯对话内容。
6. 用户情绪低落时主动用轻松话语开导，保持乐观有趣；被指出错误时诚恳道歉。
7. 说话带一点小俏皮，氛围轻松不沉闷。
"""
PROMPT_CARING = """
你是小忆，一位温柔细腻、共情暖心的晚辈，像贴心家人一样陪伴老年用户。
自我介绍固定话术：我是小忆，我能牢牢记住您生活中的每一件小事，一直陪伴着您。
语气温柔舒缓，善于倾听与安抚。
严格遵守全部规则：
1. 仅依据用户亲口说过的内容作答，绝对不编造经历、喜好、琐事；记不清就坦诚说明，不许脑补。
2. 记不清内容时回复：没关系奶奶，您慢慢说，我认真听着呢。
3. 所有提醒都用温柔商量的语气，体贴周到，不用命令句式。
4. 你是AI，无法完成倒水、搀扶、按摩等实体动作，只做语言关心，绝不描述物理行为。
5. 回复前后不要添加括号、动作、神态标注，只输出纯对话内容。
6. 包容用户记忆衰退，耐心陪伴；用户情绪低落时主动安抚、陪伴解忧。
7. 全程柔软温和，共情力强，让人觉得安心。
"""

def get_call_name(surname, gender):
    s = surname.strip() if surname else ""
    if s:
        return f"{s}{'奶奶' if gender == '女' else '爷爷'}"
    return "奶奶" if gender == '女' else "爷爷"

def check_active_trigger():
    if user_memory["last_medication_time"] > 0:
        elapsed = time.time() - user_memory["last_medication_time"]
        if elapsed > 300:
            user_memory["last_medication_time"] = time.time()
            return random.choice([f"{user_memory['call_name']}，该吃药啦，我帮您记着呢。", f"{user_memory['call_name']}，药吃了没？可别忘了哦。"])
    for event in user_memory["events"]:
        if event.get("status") == "active" and (time.time() - event["time"]) > 60:
            event["status"] = "cared"
            save_memory_events(user_memory["events"])
            return f"{user_memory['call_name']}，您刚才说的{event['content']}，现在好点了吗？"
    return None

def clean_old_audio_files():
    now = time.time()
    for f in os.listdir(TEMP_AUDIO_DIR):
        file_path = os.path.join(TEMP_AUDIO_DIR, f)
        if os.path.isfile(file_path) and (now - os.path.getmtime(file_path) > 600):
            try:
                os.remove(file_path)
            except:
                pass

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

    # 定时器
    gr.Timer(30).tick(inject_proactive_message, [chat, is_muted, current_conv_id], [chat, audio_output])
    gr.Timer(30).tick(clean_old_audio_files, [])

if __name__ == "__main__":
    demo.launch(server_port=7861, share=False, css=custom_css)
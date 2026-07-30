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

# ========== 本地模型配置（训练好的小忆 3B 模型） ==========
import torch, ssl
ssl._create_default_https_context = ssl._create_unverified_context
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_output", "merged_16bit")
FALLBACK_MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qwen3b_local")

# 自动回退：优先加载训练模型，不存在则用基座模型
if not os.path.exists(os.path.join(MODEL_PATH, "config.json")):
    print(f"⚠️ 未找到训练模型: {MODEL_PATH}")
    print(f"⏳ 自动回退到基座模型: {FALLBACK_MODEL_PATH}")
    MODEL_PATH = FALLBACK_MODEL_PATH

print(f"⏳ 加载本地模型: {MODEL_PATH}")
_model = AutoModelForCausalLM.from_pretrained(
    MODEL_PATH, device_map="auto", torch_dtype=torch.float16, trust_remote_code=True,
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
REMINDERS_FILE = "reminders.json"

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

# ========== 超忆便签——日常事务记忆与提醒 ==========
# 意图触发关键词
REMINDER_INTENT_KW = [
    "帮我记着", "记一下", "记住", "别忘了", "别忘",
    "提醒我", "到点提醒", "记得提醒", "到时间提醒",
    "放哪了", "在哪里", "在哪", "放到哪", "放在哪",
    "提醒", "记着",
]

# 星期映射
_WEEKDAY_MAP = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}

def load_reminders():
    """加载便签列表"""
    rs = load_json_safe(REMINDERS_FILE, [])
    # 清除过期的已触发便签（超过3天）
    now = time.time()
    cutoff = now - 3 * 86400
    rs = [r for r in rs if r.get("status") in ("pending",) or r.get("created_at", 0) > cutoff]
    return rs

def save_reminders(reminders):
    save_json_safe(REMINDERS_FILE, reminders)

def _has_reminder_intent(text):
    """检测用户输入是否包含便签/提醒意图"""
    for kw in REMINDER_INTENT_KW:
        if kw in text:
            return True
    return False

def _parse_time_expression(text, now):
    """从文本中解析时间，返回 (trigger_time, time_desc) 或 (None, None)"""
    import re
    text = text.strip()
    now_ts = now
    now_dt = datetime.fromtimestamp(now_ts)

    # ---- 相对时间 ----
    # "X分钟后"
    m = re.search(r'(\d+)\s*分钟\s*后', text)
    if m:
        minutes = int(m.group(1))
        return now_ts + minutes * 60, f"{minutes}分钟后"

    # "X小时后" / "X个半小时后" / "半小时后"
    m = re.search(r'(\d+)\s*个?\s*半?\s*小时\s*后', text)
    if m:
        hours = int(m.group(1))
        return now_ts + hours * 3600, f"{hours}小时后"
    if "半小时后" in text:
        return now_ts + 1800, "半小时后"

    # ---- 绝对值日期 ----
    target_date = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    date_desc = "今天"

    if "明天" in text:
        target_date = target_date.replace(day=target_date.day + 1)
        date_desc = "明天"
    elif "后天" in text:
        target_date = target_date.replace(day=target_date.day + 2)
        date_desc = "后天"
    elif "大后天" in text:
        target_date = target_date.replace(day=target_date.day + 3)
        date_desc = "大后天"
    elif "昨天" in text or "前天" in text:
        return None, None  # 过去时间不设提醒

    # "星期X" — 找最近的该星期
    for wk_name, wk_idx in _WEEKDAY_MAP.items():
        if f"星期{wk_name}" in text or f"周{wk_name}" in text:
            diff = (wk_idx - target_date.weekday()) % 7
            if diff == 0:
                diff = 7  # 同一周→下周
            target_date = target_date.replace(day=target_date.day + diff)
            date_desc = f"下星期{wk_name}"
            break

    # ---- 时间点 ----
    hour, minute = None, None
    m = re.search(r'([上下午晚早]+)?\s*(\d+)\s*[：:]\s*(\d+)', text)
    if m:
        ampm = m.group(1) if m.group(1) else ""
        hour = int(m.group(2))
        minute = int(m.group(3))
        if "下" in ampm or "晚" in ampm:
            if hour < 12:
                hour += 12
        elif "早" in ampm and hour >= 12:
            hour -= 12
    else:
        m = re.search(r'([上下午晚早]+)\s*(\d+)\s*点', text)
        if m:
            ampm = m.group(1)
            hour = int(m.group(2))
            if "下" in ampm or "晚" in ampm:
                if hour < 12:
                    hour += 12
            elif "早" in ampm and hour >= 12:
                hour -= 12
            minute = 0
        else:
            m = re.search(r'(\d+)\s*点', text)
            if m:
                hour = int(m.group(1))
                minute = 0
                # 无上下午区分时不自动调整

    if hour is not None:
        if hour >= 24:
            hour = None
        else:
            target_date = target_date.replace(hour=hour, minute=minute or 0)

    trigger_ts = target_date.timestamp()
    # 如果时间已过且是今天，推到明天
    if date_desc == "今天" and trigger_ts <= now_ts:
        target_date = target_date.replace(day=target_date.day + 1)
        trigger_ts = target_date.timestamp()
        date_desc = "明天"

    time_desc = f"{date_desc}"
    if hour is not None:
        hour_12 = hour % 12 or 12
        ampm_str = "上午" if hour < 12 else "下午" if hour < 18 else "晚上"
        time_desc += f"{ampm_str}{hour_12}点"
        if minute:
            time_desc += f"{minute}分"

    return trigger_ts, time_desc


def _extract_reminder_entities(text):
    """从文本中抽取便签实体：待办内容、物品、位置"""
    import re
    entities = {"todo": "", "item": "", "location": "", "time_desc": ""}

    # 提取物品位置：XX在/放 在 YY
    m = re.search(r'([\u4e00-\u9fa5]{2,8})\s*(?:放在|在|放到|放)\s*了?\s*([\u4e00-\u9fa5\w]{2,20})', text)
    if m:
        entities["item"] = m.group(1).strip()
        entities["location"] = m.group(2).strip()

    # 提取待办内容：别忘了XX / 记得XX / 要XX / 该XX了
    patterns = [
        r'(?:别忘了|别忘|记得|记着)\s*(.{2,30}?)(?:[。，！？]|$)',
        r'(?:要|该)\s*(.{2,30}?)(?:了\s*[。，！？]|了$|哦|哈|呀|吧|[。，！？]|$)',
        r'(?:去|要去做|要去)\s*(.{2,30}?)(?:[。，！？]|$)',
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            entities["todo"] = m.group(1).strip()
            break

    # 提取"XX放哪了" → 物品查询
    m = re.search(r'([\u4e00-\u9fa5]{2,8})\s*(?:放哪了|在哪|在哪里)', text)
    if m:
        entities["item"] = m.group(1).strip()
        entities["location"] = "?"

    # 时间抽取
    trigger_ts, time_desc = _parse_time_expression(text, time.time())
    entities["trigger_ts"] = trigger_ts
    entities["time_desc"] = time_desc or ""

    return entities


def process_reminder_intent(user_input, call_name):
    """主入口：识别意图 → 抽取实体 → 存入便签 → 返回回复提示"""
    if not _has_reminder_intent(user_input):
        return None, None

    entities = _extract_reminder_entities(user_input)
    now = time.time()

    reminder = {
        "id": f"rem_{int(now * 1000)}_{random.randint(100,999)}",
        "type": "reminder",
        "content": "",
        "item": entities["item"],
        "location": entities["location"],
        "todo": entities["todo"],
        "time_desc": entities["time_desc"],
        "trigger_time": entities.get("trigger_ts"),
        "mentioned_at": now,
        "user_input": user_input.strip(),
        "created_at": now,
        "status": "pending",
        "acknowledged": False,
    }

    replies = []

    # ---- 物品位置记忆 ----
    if entities["item"] and entities["location"] and entities["location"] != "?":
        reminder["type"] = "location"
        reminder["content"] = f"{entities['item']}放在{entities['location']}"
        replies.append(f"好嘞，我记住了，{entities['item']}在{entities['location']}。")

    # ---- 物品位置查询 ----
    elif entities["item"] and entities["location"] == "?":
        # 查已有便签
        existing = find_item_location(entities["item"])
        if existing:
            replies.append(f"{entities['item']}我记得在{existing}。")
        else:
            replies.append(f"您还没跟我说过{entities['item']}放哪了呢，您告诉我我记着。")
        return reminder if entities["location"] == "?" else None, replies

    # ---- 有时间触发的待办提醒 ----
    if entities["trigger_ts"]:
        todo_text = entities["todo"] or re.sub(r'(帮我记着|别忘了|记得提醒我|到点提醒我|提醒我)', '', user_input).strip()[:30]
        reminder["type"] = "reminder"
        reminder["content"] = todo_text
        time_str = entities["time_desc"]
        replies.append(f"好的{call_name}，{time_str}我会提醒您{' ' + todo_text if todo_text else '的'}。")

    # ---- 无时间但有待办 ----
    elif entities["todo"] and not entities["trigger_ts"]:
        reminder["type"] = "reminder"
        reminder["content"] = entities["todo"]
        replies.append(f"好的{call_name}，我记住了：{entities['todo']}。")
        # 没时间就默认2小时后提醒
        reminder["trigger_time"] = now + 7200
        reminder["time_desc"] = "2小时后"

    # ---- 兜底：存为待办便签 ----
    if reminder["content"] or reminder["item"]:
        rs = load_reminders()
        rs.append(reminder)
        save_reminders(rs)
        return reminder, replies

    return None, None


def find_item_location(item_name):
    """查找之前记过的物品位置"""
    rs = load_reminders()
    for r in reversed(rs):
        if r.get("type") == "location" and r.get("item") == item_name:
            return r.get("location", "")
    # 也查 memory_events
    for e in user_memory.get("events", []):
        if e.get("type") == "item" and item_name in e.get("content", ""):
            return "(之前提到过)"
    return ""


def check_due_reminders():
    """定时检查：返回到点的待提醒消息列表"""
    now = time.time()
    rs = load_reminders()
    due_messages = []
    updated = False
    for r in rs:
        if r.get("status") != "pending":
            continue
        trigger = r.get("trigger_time")
        if trigger and 0 < trigger <= now:
            r["status"] = "triggered"
            updated = True
            rtype = r.get("type", "reminder")
            content = r.get("content", "")
            item = r.get("item", "")
            location = r.get("location", "")
            time_desc = r.get("time_desc", "")

            if rtype == "location":
                due_messages.append(f"对了，{item}在{location}。")
            elif content:
                if item and location:
                    due_messages.append(f"{time_desc}到了，该{content}了。对了，{item}在{location}。")
                else:
                    due_messages.append(f"{time_desc}到了，该{content}了。")
            else:
                due_messages.append(f"您之前说的{time_desc}的事，到时间了。")

    if updated:
        save_reminders(rs)

    return due_messages


# ========== 隐私设置——数据查看与删除 ==========
DELETE_INTENT_KW = [
    "忘了吧", "忘掉", "忘了", "删掉", "删除", "清除", "清空",
    "不要了", "不要记", "别记了", "别存了",
]

DATA_SAVE_EXPLANATION = (
    "💡 **小忆是怎么保存您的数据的？**\n\n"
    "• 您跟小忆说的每一句话，都会保存在这台电脑上，"
    "存在一个叫「conversations.json」的文件里。\n"
    "• 小忆记住的重要事情（比如身体不舒服、物品放哪了），"
    "存在「memory_events.json」和「reminders.json」里。\n"
    "• **这些数据只存在您自己的电脑上，不会上传到任何地方。**\n"
    "• 您随时可以把它们全部删除，小忆不会偷偷保留。\n\n"
    "如果您不放心，可以定期点「一键删除」清理数据。"
)

def _has_delete_intent(text):
    """检测用户是否表达删除/忘记数据的意图"""
    for kw in DELETE_INTENT_KW:
        if kw in text:
            return True
    return False

def delete_conversations_by_time_range(cutoff_ts):
    """删除指定时间之前的所有对话"""
    convs = load_all_conversations()
    before = len(convs)
    convs = [c for c in convs if c.get('updated', 0) > cutoff_ts]
    save_json_safe(HISTORY_FILE, convs)
    return before - len(convs)

def delete_all_conversations():
    """一键删除所有对话"""
    save_json_safe(HISTORY_FILE, [])
    return True

def delete_memory_events_by_time_range(cutoff_ts):
    """删除指定时间之前的记忆事件"""
    events = load_memory_events()
    before = len(events)
    events = [e for e in events if e.get("time", 0) > cutoff_ts]
    save_memory_events(events)
    user_memory["events"] = events
    return before - len(events)

def delete_all_memory_events():
    """一键删除所有记忆事件（含便签）"""
    save_memory_events([])
    user_memory["events"] = []
    save_reminders([])

def process_delete_intent(user_input, call_name):
    """主入口：识别删除意图 → 执行删除 → 返回回复"""
    if not _has_delete_intent(user_input):
        return None

    text = user_input.strip()
    now = time.time()
    today_start = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    yesterday_start = today_start - 86400

    # "全部忘掉" / "全删了" → 清空所有
    if any(kw in text for kw in ("全部", "所有", "全删", "全清", "都删")):
        delete_all_conversations()
        delete_all_memory_events()
        return f"好的{call_name}，我已经把所有的聊天记录和记的事情都删干净了。您放心，这些数据只在这台电脑上，删了就没了。"

    # "今天的忘了吧" / "今天的话删了"
    if "今天" in text:
        n = delete_conversations_by_time_range(today_start)
        m = delete_memory_events_by_time_range(today_start)
        if n or m:
            return f"好的{call_name}，今天的聊天记录（{n}条对话）和记的事情（{m}条）都已经删除了。"
        else:
            return f"{call_name}，今天好像还没有什么记录需要删除的。"

    # "昨天的忘了吧" / "昨天的话删了"
    if "昨天" in text:
        n = delete_conversations_by_time_range(yesterday_start)
        # 同时删除昨天的记忆事件
        cutoff = yesterday_start
        events = load_memory_events()
        before = len(events)
        events = [e for e in events if e.get("time", 0) > cutoff]
        save_memory_events(events)
        user_memory["events"] = events
        return f"好的{call_name}，昨天的聊天记录（{n}条对话）和相关记忆都删除了。"

    # "把X的话忘了吧" / 默认删除全部
    delete_all_conversations()
    delete_all_memory_events()
    return f"好的{call_name}，我已经把您的记录都清理干净了。您随时可以再找我聊天，小忆一直都在。"


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
            'dialogue_summary': '',
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
    events = []
    now = time.time()
    text = str(user_input)

    # 关键词提取（本地运行，无需 API）
    events = _keyword_extract(text, now)
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


# ========== 短期对话记忆（对话摘要 + 滑动窗口） ==========
RECENT_ROUNDS = 20          # 保留最近20轮对话原文（每轮=1条user+1条assistant）
SUMMARY_TRIGGER = 25        # 超过25轮时触发摘要更新

def get_dialogue_summary(conv_id):
    """获取当前对话的摘要"""
    convs = load_all_conversations()
    for c in convs:
        if c['id'] == conv_id:
            return c.get('dialogue_summary', '')
    return ''

def _do_summarize_async(conv_id, messages):
    """后台线程：将超出窗口的对话压缩为摘要"""
    try:
        total_rounds = len(messages) // 2
        if total_rounds <= RECENT_ROUNDS:
            return  # 还不够长，无需摘要

        # 需要摘要的部分 = 除最近 RECENT_ROUNDS 轮之外的全部
        cutoff = total_rounds - RECENT_ROUNDS
        old_msgs = messages[:cutoff * 2]

        # 构建摘要用的对话文本
        dialog_text = ""
        for m in old_msgs:
            role = "用户" if m["role"] == "user" else "小忆"
            dialog_text += f"{role}: {m['content']}\n"

        # 已有的摘要
        existing = get_dialogue_summary(conv_id)

        summary_prompt = f"""下面是一段对话中较早的部分。请将其核心信息压缩成一段中文摘要（50字以内），保留重要细节（健康状况、身体不适、物品需求、家人信息、情绪变化等）。如果已有摘要，请合并更新。

已有摘要：{existing if existing else "无"}

需要摘要的对话：
{dialog_text}

简洁摘要（50字以内）："""

        prompt = f"<|im_start|>system\n你是一个简洁的对话摘要助手，只输出摘要本身。<|im_end|><|im_start|>user\n{summary_prompt}<|im_end|><|im_start|>assistant\n"
        inputs = _tokenizer(prompt, return_tensors="pt").to(_model.device)
        with torch.no_grad():
            outputs = _model.generate(**inputs, max_new_tokens=200, temperature=0.3,
                top_p=0.9, do_sample=False, repetition_penalty=1.1)
        summary = _tokenizer.decode(outputs[0][inputs.input_ids.shape[1]:], skip_special_tokens=True)
        for junk in ["<|im_end|>","<|im_start|>","assistant","user","system"]:
            summary = summary.replace(junk, "")
        summary = summary.strip().strip("[]").strip("{}").strip('"').strip("'")

        if summary:
            convs = load_all_conversations()
            for c in convs:
                if c['id'] == conv_id:
                    c['dialogue_summary'] = summary
                    break
            save_json_safe(HISTORY_FILE, convs)
            print(f"✅ 对话摘要已更新: {summary}")
    except Exception as e:
        print(f"❌ 对话摘要生成失败: {e}")

def update_dialogue_summary_async(conv_id, messages):
    """后台启动摘要更新"""
    Thread(target=_do_summarize_async, args=(conv_id, messages), daemon=True).start()


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

# 三种性格对应的发音人配置
VOICE_CONFIG = {
    "踏实务实": {
        "voice_type": "BV003_streaming",   # 沉稳男声
        "speed_ratio": 0.78,
        "pitch_ratio": 0.95,
        "volume_ratio": 1.3,
    },
    "风趣幽默": {
        "voice_type": "BV004_streaming",   # 阳光男声
        "speed_ratio": 0.88,
        "pitch_ratio": 1.05,
        "volume_ratio": 1.35,
    },
    "暖心知心": {
        "voice_type": "BV001_streaming",   # 温柔女声
        "speed_ratio": 0.72,
        "pitch_ratio": 1.0,
        "volume_ratio": 1.3,
    },
}

# 情感/场景 → 语速微调
SPEED_MODIFIERS = {
    "happy": 0.08,
    "caring": -0.06,
    "reminder": 0.03,
    "default": 0.0,
}

def _detect_speech_context(text):
    """根据文本内容判断说话场景"""
    if not text:
        return "default"
    if any(kw in text for kw in ("提醒", "别忘了", "到时间", "该", "记得")):
        return "reminder"
    if any(kw in text for kw in ("好点了吗", "怎么了", "别担心", "没事", "好好休息", "注意身体")):
        return "caring"
    if any(kw in text for kw in ("哈哈", "开心", "高兴", "真棒", "太好了", "有意思")):
        return "happy"
    return "default"


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

def text_to_speech(text, personality="暖心知心"):
    """TTS 合成，支持性格发音人 + 场景语速调节"""
    if not text or text.strip() == "":
        return None
    voice_cfg = VOICE_CONFIG.get(personality, VOICE_CONFIG["暖心知心"])
    context = _detect_speech_context(text)
    speed_mod = SPEED_MODIFIERS.get(context, 0.0)
    final_speed = max(0.5, min(1.5, voice_cfg["speed_ratio"] + speed_mod))
    try:
        headers = {"Authorization": f"Bearer; {VOLC_ACCESS_TOKEN}"}
        data = {
            "app": {"appid": VOLC_APP_ID, "token": VOLC_ACCESS_TOKEN, "cluster": "volcano_tts"},
            "user": {"uid": "xiaoyi_user"},
            "audio": {
                "voice_type": voice_cfg["voice_type"],
                "encoding": "wav",
                "speed_ratio": round(final_speed, 2),
                "volume_ratio": voice_cfg["volume_ratio"],
                "pitch_ratio": voice_cfg["pitch_ratio"],
                "rate": 16000,
            },
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


# ========== 全网搜索（Bing 主 + DuckDuckGo 备胎） ==========
# 环境变量 BING_SEARCH_KEY → 优先用 Bing；未配置则自动降级到 DuckDuckGo
BING_SEARCH_KEY = os.getenv("BING_SEARCH_KEY", "")
SEARCH_CACHE = {}           # 简单内存缓存：query → (results, timestamp)
SEARCH_CACHE_TTL = 300       # 缓存有效期 5 分钟

# 需要搜实时信息的关键词触发
SEARCH_TRIGGER_KEYWORDS = [
    "天气", "气温", "下雨", "下雪", "台风", "雾霾",
    "新闻", "最新", "今天", "明天", "昨天", "最近",
    "怎么做", "做法", "怎么弄", "怎么煮", "怎么烧", "教程",
    "多少钱", "价格", "多少", "几块", "几元",
    "谁", "什么时候", "在哪", "为什么",
    "股票", "汇率", "油价", "金价",
    "是什么", "是什么意思", "怎么回事",
]

# 医疗关键词 — 触发后附加免责声明
MEDICAL_KEYWORDS = [
    "头疼", "头晕", "发烧", "感冒", "咳嗽", "拉肚子", "肚子疼",
    "胃疼", "膝盖疼", "腰疼", "腿疼", "胳膊疼", "肩膀疼",
    "高血压", "糖尿病", "心脏病", "药", "吃药", "中药", "西药",
    "手术", "住院", "检查", "化验", "CT", "核磁",
    "医生", "医院", "诊所", "看病",
]


def _search_bing(query):
    """Bing Web Search API（主方案）"""
    if not BING_SEARCH_KEY:
        return None  # 未配置密钥，跳过
    headers = {"Ocp-Apim-Subscription-Key": BING_SEARCH_KEY}
    params = {"q": query, "count": 5, "mkt": "zh-CN"}
    try:
        r = requests.get("https://api.bing.microsoft.com/v7.0/search",
                          headers=headers, params=params, timeout=8)
        r.raise_for_status()
        data = r.json()
        results = []
        for item in data.get("webPages", {}).get("value", []):
            results.append({
                "title": item.get("name", ""),
                "snippet": item.get("snippet", ""),
                "url": item.get("url", ""),
            })
        if results:
            print(f"🔍 Bing 搜索成功: {query} → {len(results)} 条结果")
            return results
        return None
    except Exception as e:
        print(f"⚠️ Bing 搜索失败: {e}")
        return None  # 触发降级


def _search_duckduckgo(query):
    """DuckDuckGo Instant Answer API（备胎方案，无需密钥）"""
    try:
        r = requests.get("https://api.duckduckgo.com/",
                          params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
                          timeout=8)
        r.raise_for_status()
        data = r.json()
        results = []

        # Abstract 结果
        if data.get("AbstractText"):
            results.append({
                "title": data.get("Heading", ""),
                "snippet": data.get("AbstractText", ""),
                "url": data.get("AbstractURL", ""),
            })

        # RelatedTopics
        for topic in data.get("RelatedTopics", []):
            if "Text" in topic:
                results.append({
                    "title": topic.get("Text", "").split(" - ")[0] if " - " in topic.get("Text", "") else "",
                    "snippet": topic.get("Text", ""),
                    "url": topic.get("FirstURL", ""),
                })
            if len(results) >= 5:
                break

        if results:
            print(f"🔍 DuckDuckGo 搜索成功: {query} → {len(results)} 条结果")
            return results
        return None
    except Exception as e:
        print(f"⚠️ DuckDuckGo 搜索失败: {e}")
        return None


def _search_fallback(query):
    """最终兜底：用 requests + 简易爬取搜狗搜索摘要（纯文本提取）"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        r = requests.get(f"https://www.sogou.com/web?query={requests.utils.quote(query)}",
                          headers=headers, timeout=8)
        r.raise_for_status()
        r.encoding = "utf-8"
        html = r.text
        results = []
        # 简单提取摘要片段
        import re
        # 匹配搜狗结果片段
        snippets = re.findall(r'<div class="str-text"[^>]*>(.*?)</div>', html, re.DOTALL)
        titles = re.findall(r'<h3[^>]*>.*?<a[^>]*>(.*?)</a>', html, re.DOTALL)
        for i, snip in enumerate(snippets[:5]):
            clean = re.sub(r'<[^>]+>', '', snip).strip()
            title = ""
            if i < len(titles):
                title = re.sub(r'<[^>]+>', '', titles[i]).strip()
            if clean:
                results.append({"title": title, "snippet": clean[:200], "url": ""})
        if results:
            print(f"🔍 搜狗兜底搜索成功: {query} → {len(results)} 条结果")
            return results
        return None
    except Exception as e:
        print(f"⚠️ 搜狗兜底搜索失败: {e}")
        return None


def search_web(query):
    """搜索入口：Bing → DuckDuckGo → Sogou 逐一降级，带缓存"""
    cache_key = query.strip().lower()
    now = time.time()

    # 检查缓存
    if cache_key in SEARCH_CACHE:
        cached_results, cached_time = SEARCH_CACHE[cache_key]
        if now - cached_time < SEARCH_CACHE_TTL:
            print(f"🔍 搜索缓存命中: {query}")
            return cached_results

    # 轮询三个方案
    results = _search_bing(query)
    if results is None:
        results = _search_duckduckgo(query)
    if results is None:
        results = _search_fallback(query)

    # 缓存结果
    if results:
        SEARCH_CACHE[cache_key] = (results, now)

    return results


def should_search(user_input):
    """判断用户输入是否需要触发搜索"""
    text = user_input.strip()
    if len(text) < 4:
        return False  # 太短不搜
    for kw in SEARCH_TRIGGER_KEYWORDS:
        if kw in text:
            return True
    return False


def is_medical_query(user_input):
    """判断是否属于医疗相关问题"""
    for kw in MEDICAL_KEYWORDS:
        if kw in user_input:
            return True
    return False


def format_search_context(results, user_query):
    """把搜索结果过滤、压缩成一段适合喂给模型的上下文"""
    if not results:
        return ""

    lines = []
    for i, r in enumerate(results[:3]):  # 最多取前 3 条
        snippet = r.get("snippet", "").strip()
        if snippet:
            # 过滤无用片段
            if len(snippet) < 10:
                continue
            lines.append(f"  搜索结果{i+1}：{snippet}")

    if not lines:
        return ""

    context = "以下是从网上查到的相关信息：\n" + "\n".join(lines)

    # 医疗类附加免责声明
    if is_medical_query(user_query):
        context += "\n\n（温馨提示：以上信息仅供参考，身体不适请以医生的专业意见为准。）"

    return context


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

    # 超忆便签：检测提醒/记东西意图
    reminder_obj, reminder_replies = process_reminder_intent(user_input, user_memory.get("call_name", "您"))

    # 隐私删除：检测"删掉""忘了吧"等意图
    delete_reply = process_delete_intent(user_input, user_memory.get("call_name", "您"))

    # 提取纯文本（Gradio 可能嵌套 {'text': ...}）
    def extract_text(content):
        if isinstance(content, str): return content
        if isinstance(content, dict): return extract_text(content.get("text", ""))
        if isinstance(content, list): return " ".join(extract_text(c) for c in content if c)
        return str(content)

    # 统一 chat_history 为纯文本 dict（提前构建，便签提前返回也要用）
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
    system_content = base_prompt + f"\n当前称呼：{call_name}"

    # 注入对话摘要（短期记忆：让模型记住早期聊过的内容）
    dialogue_summary = get_dialogue_summary(conv_id)
    if dialogue_summary:
        system_content += f"\n\n对话背景（之前聊过的内容）：{dialogue_summary}"

    # 全网搜索：检测是否需要上网查信息
    search_context = ""
    if should_search(user_input):
        print(f"🔍 触发搜索: {user_input}")
        search_results = search_web(user_input)
        if search_results:
            search_context = format_search_context(search_results, user_input)
        else:
            print("⚠️ 搜索无结果或全部失败")
    if search_context:
        system_content += f"\n\n{search_context}"

    # 如果便签系统已生成回复，直接用它（跳过模型推理）
    if reminder_replies:
        bot_reply = " ".join(reminder_replies)
        is_first_msg = len(chat_history) <= 1
        save_history = internal_history + [{"role":"user","content":user_input},{"role":"assistant","content":bot_reply}]
        save_conversation(conv_id, save_history)
        if is_first_msg:
            Thread(target=generate_title_async, args=(conv_id, save_history), daemon=True).start()
        if len(save_history) // 2 > RECENT_ROUNDS:
            Thread(target=update_dialogue_summary_async, args=(conv_id, save_history), daemon=True).start()
        audio_path = None if is_muted else text_to_speech(bot_reply, personality)
        audio_wrap_visible = not is_muted and audio_path is not None
        return "", save_history, audio_path, conv_id, gr.update(choices=get_conversation_list_display()), gr.update(visible=audio_wrap_visible)

    # 如果触发了删除意图，直接返回回复（跳过模型推理）
    if delete_reply:
        bot_reply = delete_reply
        is_first_msg = len(chat_history) <= 1
        save_history = internal_history + [{"role":"user","content":user_input},{"role":"assistant","content":bot_reply}]
        save_conversation(conv_id, save_history)
        if is_first_msg:
            Thread(target=generate_title_async, args=(conv_id, save_history), daemon=True).start()
        audio_path = None if is_muted else text_to_speech(bot_reply, personality)
        audio_wrap_visible = not is_muted and audio_path is not None
        return "", save_history, audio_path, conv_id, gr.update(choices=get_conversation_list_display()), gr.update(visible=audio_wrap_visible)

    try:
        prompt = f"<|im_start|>system\n{system_content}<|im_end|>"
        # 保留最近 RECENT_ROUNDS 轮对话原文（滑动窗口）
        recent_msgs = internal_history[-(RECENT_ROUNDS * 2):] if internal_history else []
        for m in recent_msgs:
            prompt += f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>"
        prompt += f"<|im_start|>user\n{user_input}<|im_end|><|im_start|>assistant\n"

        inputs = _tokenizer(prompt, return_tensors="pt").to(_model.device)
        with torch.no_grad():
            outputs = _model.generate(**inputs, max_new_tokens=300, temperature=0.8,
                top_p=0.92, do_sample=True, repetition_penalty=1.15)
        bot_reply = _tokenizer.decode(outputs[0][inputs.input_ids.shape[1]:], skip_special_tokens=True)
        for junk in ["<|im_end|>","<|im_start|>","assistant","user","system"]:
            bot_reply = bot_reply.replace(junk, "")
        bot_reply = bot_reply.strip().strip("[]").strip("{}").strip(",").strip()
        if not bot_reply or len(bot_reply) < 5:
            bot_reply = f"{call_name}，我听着呢，您说。"
    except Exception as e:
        print(f"推理错误: {e}")
        bot_reply = f"{call_name}，我有点听不清楚，您再说一遍好吗？"

    is_first_msg = len(chat_history) <= 1
    save_history = internal_history + [{"role":"user","content":user_input},{"role":"assistant","content":bot_reply}]
    save_conversation(conv_id, save_history)
    # 如果对话轮数超出窗口，后台启动摘要压缩（让模型记住早期内容）
    if len(save_history) // 2 > RECENT_ROUNDS:
        Thread(target=update_dialogue_summary_async, args=(conv_id, save_history), daemon=True).start()
    if is_first_msg:
        Thread(target=generate_title_async, args=(conv_id, save_history), daemon=True).start()
    audio_path = None if is_muted else text_to_speech(bot_reply, personality)
    audio_wrap_visible = not is_muted and audio_path is not None
    return "", save_history, audio_path, conv_id, gr.update(choices=get_conversation_list_display()), gr.update(visible=audio_wrap_visible)

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

def inject_proactive_message(chat_history, is_muted, conv_id, personality):
    global last_user_interaction_time
    if not chat_history:
        chat_history = []
    msg = check_active_trigger()
    if msg and (time.time() - last_user_interaction_time) > 15:
        chat_history.append({"role": "assistant", "content": msg})
        save_conversation(conv_id, chat_history)
        audio_path = None if is_muted else text_to_speech(msg, personality)
        return chat_history, audio_path
    return chat_history, None

def toggle_mute(current_mute, personality):
    new_mute = not current_mute
    btn_text = "🔇 静音" if new_mute else "🔊 取消静音"
    audio_path = None
    audio_wrap_visible = False
    if current_mute and not new_mute:
        audio_path = text_to_speech("小忆语音已开启", personality)
        audio_wrap_visible = True
    return new_mute, gr.update(value=btn_text), audio_path, gr.update(visible=audio_wrap_visible)

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
    /* ===== 音频播放条 ===== */
    .audio-player-wrap {
        background: #F5E5CC;
        border-radius: 12px;
        padding: 6px 12px;
        margin: 4px 10px 0 10px;
    }
"""

# ========== 全局变量 ==========
last_user_interaction_time = time.time()
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
    # 1) 超忆便签：到点提醒（优先级最高）
    due_msgs = check_due_reminders()
    if due_msgs:
        return due_msgs[0]

    # 2) 用药提醒
    if user_memory["last_medication_time"] > 0:
        elapsed = time.time() - user_memory["last_medication_time"]
        if elapsed > 300:
            user_memory["last_medication_time"] = time.time()
            return random.choice([f"{user_memory['call_name']}，该吃药啦，我帮您记着呢。", f"{user_memory['call_name']}，药吃了没？可别忘了哦。"])
    # 3) 关心回访
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
    # audio_output 现在在主界面内定义

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
                # 隐私设置按钮
                privacy_btn = gr.Button("🔒 隐私设置", size="sm")
                # 重要事件展示
                gr.Markdown("📝 **重要事件**")
                events_display = gr.HTML(value=get_recent_events_display(), elem_classes="event-box")
                gr.HTML('</div>')
            with gr.Column(scale=4):
                gr.HTML('''<div class="top-bar"><div>🌸 小忆</div><div class="status-online"><div class="green-dot"></div> 陪伴中</div></div>''')
                chat = gr.Chatbot(value=[], height=520, show_label=False)
                # 隐私设置面板（默认隐藏）
                with gr.Column(visible=False) as privacy_view:
                    gr.HTML("""
                    <div style="background:#fff;border-radius:16px;padding:24px;margin:10px 0;border:1px solid #E0D3C0;">
                        <h3 style="color:#2D1B0E;margin-bottom:16px;">🔒 隐私设置</h3>
                        <div style="font-size:18px;line-height:1.8;color:#4A3420;">
                            <p>💡 小忆是怎么保存您的数据的？</p>
                            <ul style="padding-left:20px;">
                                <li>您跟小忆说的每一句话，都保存在这台电脑的聊天记录文件里。</li>
                                <li>小忆记住的重要事情（身体哪里不舒服、物品放哪了等），保存在记忆文件里。</li>
                                <li><b>这些数据只存在您自己的电脑上，不会上传到任何地方。</b></li>
                                <li>您随时可以把它们删除，小忆不会偷偷保留。</li>
                            </ul>
                        </div>
                        <div style="margin-top:16px;display:flex;gap:10px;flex-wrap:wrap;">
                            <button id="privacy-del-today" class="big-btn" style="flex:1;min-width:120px;background:#F5E5CC;" onclick="document.querySelector('#privacy-del-today-input').click()">🗑️ 删除今天的对话</button>
                            <button id="privacy-del-all" class="big-btn" style="flex:1;min-width:120px;background:#F5E5CC;" onclick="document.querySelector('#privacy-del-all-input').click()">🗑️ 一键删除所有</button>
                        </div>
                    </div>
                    """)
                    # 隐藏的真实按钮（用于 Gradio 事件绑定）
                    privacy_del_today_btn = gr.Button("删除今天的数据", elem_id="privacy-del-today-input", visible=True, size="sm")
                    privacy_del_all_btn = gr.Button("一键删除所有数据", elem_id="privacy-del-all-input", visible=True, size="sm")
                    privacy_back_btn = gr.Button("← 返回聊天", size="lg")
                # 删除结果提示
                privacy_result = gr.HTML(value="", visible=True)
                # 音频播放器（可见，带进度条 + 暂停/继续）
                with gr.Column(elem_classes="audio-player-wrap", visible=False) as audio_wrap:
                    audio_output = gr.Audio(autoplay=True, show_label=False, container=False)
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
        audio_path = None if muted else text_to_speech(welcome_msg, p)
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
               [txt, chat, audio_output, current_conv_id, history_dropdown, audio_wrap]).then(refresh_events_display, outputs=events_display)
    send.click(chat_response, [txt, chat, surname, gender, is_muted, personality, current_conv_id, history_dropdown],
               [txt, chat, audio_output, current_conv_id, history_dropdown, audio_wrap]).then(refresh_events_display, outputs=events_display)

    # 快捷按钮
    for btn_q, q_text in [(q1, "我今天挺好的"), (q2, "有点想你了"), (q3, "身体有点不舒服"), (q4, "讲讲以前的事")]:
        btn_q.click(lambda t=q_text: t, None, txt).then(
            chat_response, [txt, chat, surname, gender, is_muted, personality, current_conv_id, history_dropdown],
            [txt, chat, audio_output, current_conv_id, history_dropdown, audio_wrap]
        ).then(refresh_events_display, outputs=events_display)

    # 麦克风 - 点按钮弹出录音器
    mic_btn.click(
        lambda: [gr.update(visible=True), gr.update(value="🎤 **点击麦克风图标开始录音，再点停止**")],
        outputs=[mic_input, mic_status]
    )
    mic_input.stop_recording(process_mic, [mic_input, chat, surname, gender, is_muted, personality, current_conv_id, history_dropdown],
                             [txt, chat, audio_output, current_conv_id, history_dropdown, audio_wrap]).then(
        lambda: [gr.update(visible=False), gr.update(value="")],
        outputs=[mic_input, mic_status]
    ).then(refresh_events_display, outputs=events_display)

    # 静音
    mute_btn.click(toggle_mute, [is_muted, personality], [is_muted, mute_btn, audio_output, audio_wrap])

    # 新建对话
    new_conv_btn.click(new_conversation, [], [chat, current_conv_id, history_dropdown]).then(refresh_events_display, outputs=events_display)

    # 加载历史
    history_dropdown.change(load_conversation_by_id, [history_dropdown], [chat, current_conv_id])

    # 删除对话
    delete_btn.click(delete_selected_conversation, [history_dropdown, history_dropdown], [history_dropdown, history_dropdown])

    # 搜索过滤
    search_input.change(search_conversations, [search_input], [history_dropdown])

    # 隐私设置：打开面板（隐藏聊天区，显示隐私面板）
    privacy_btn.click(
        lambda: [gr.update(visible=False), gr.update(visible=True), gr.update(value="")],
        outputs=[chat, privacy_view, privacy_result]
    )

    # 隐私设置：一键删除今天的数据
    def privacy_delete_today():
        now = time.time()
        today_start = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        n_convs = delete_conversations_by_time_range(today_start)
        n_events = delete_memory_events_by_time_range(today_start)
        msg = f"✅ 已删除今天的对话记录（{n_convs}条）和记忆（{n_events}条）。"
        return gr.update(value=msg)

    privacy_del_today_btn.click(privacy_delete_today, outputs=[privacy_result])

    # 隐私设置：一键删除所有数据
    def privacy_delete_all():
        delete_all_conversations()
        delete_all_memory_events()
        return gr.update(value="✅ 已删除所有聊天记录和记忆数据。小忆不会偷偷保留任何信息。")

    privacy_del_all_btn.click(privacy_delete_all, outputs=[privacy_result])

    # 隐私设置：返回聊天
    privacy_back_btn.click(
        lambda: [gr.update(visible=True), gr.update(visible=False), gr.update(value="")],
        outputs=[chat, privacy_view, privacy_result]
    )

    # 定时器
    gr.Timer(30).tick(inject_proactive_message, [chat, is_muted, current_conv_id, personality], [chat, audio_output])
    gr.Timer(30).tick(clean_old_audio_files, [])

if __name__ == "__main__":
    demo.launch(server_port=7861, share=False, css=custom_css)
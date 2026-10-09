import gradio as gr
import time
import random
import re
import uuid
from datetime import datetime, timedelta
import tempfile
import os
import json
import wave
import numpy as np
import requests
import base64
import io
import shutil
from threading import Thread, current_thread as _current_thread
import threading
from collections import deque
import proactive

# ========== 本地模型配置（训练好的小忆 3B 模型，只做推理，无需训练） ==========
# "模型不需要本地训练，只需要能调用"：模型文件缺失 / 未装 torch / 加载失败时
# 优雅降级——聊天走 DeepSeek API，意图解析与记忆抽取走规则回退，应用照常可用。
import ssl
ssl._create_default_https_context = ssl._create_unverified_context
try:
    import torch
except Exception:           # 没装 torch 也能启动（DeepSeek 兜底）
    torch = None

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_output", "merged_16bit")
MODEL_INT8_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_output", "merged_int8.pt")
ON_GPU = False
_model = None
_tokenizer = None

def _try_load_local_model():
    """加载本地小忆 3B（纯推理，不训练）。成功返回 True；任何失败都降级 DeepSeek，应用照常启动。"""
    global _model, _tokenizer, ON_GPU
    if torch is None:
        print("⚠️ 未安装 torch，跳过本地模型，聊天走 DeepSeek API")
        return False
    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer
        ON_GPU = torch.cuda.is_available()
        if ON_GPU:
            # 有显卡：加载 4bit 量化版（需 bitsandbytes）
            if not os.path.exists(os.path.join(MODEL_PATH, "model.safetensors")):
                print(f"⚠️ 未找到本地模型文件（{MODEL_PATH}），聊天走 DeepSeek API")
                return False
            print("⏳ 加载本地模型（GPU）...")
            _model = AutoModelForCausalLM.from_pretrained(
                MODEL_PATH, device_map="auto", dtype=torch.float16, trust_remote_code=True,
            )
        else:
            # 无显卡：加载烘焙好的 int8 动态量化模型——CPU 实测约 4.3 tok/s，
            # 是 bnb-4bit（0.9 tok/s）的 5 倍；烘焙脚本见 _bake_int8.py
            #（4bit 反量化为 fp32 后做 int8 动态量化，embedding 保持 fp32）
            if not os.path.exists(MODEL_INT8_PATH):
                print(f"⚠️ 未找到 int8 模型文件（{MODEL_INT8_PATH}），聊天走 DeepSeek API")
                return False
            print("⏳ 加载本地模型（CPU int8 推理模式，无需训练）...")
            _model = torch.load(MODEL_INT8_PATH, map_location="cpu", weights_only=False)
            _model.eval()
        _tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=True)
        if _tokenizer.pad_token is None:
            _tokenizer.pad_token = _tokenizer.eos_token
        print("✅ 模型加载完成")
        return True
    except Exception as e:
        _model, _tokenizer = None, None
        print(f"⚠️ 本地模型加载失败（{e}），聊天走 DeepSeek API")
        return False

# 后台加载本地模型：Gradio 立即起服务（预览卡片 15 秒就绪预算），

# 模型加载的 ~15 秒里聊天暂走 DeepSeek，就绪后自动切回本地模型。

LOCAL_MODEL_OK = False

def _load_model_bg():

    global LOCAL_MODEL_OK

    LOCAL_MODEL_OK = _try_load_local_model()

Thread(target=_load_model_bg, daemon=True, name='local-model-loader').start()

# 本地模型全局锁：聊天推理 / 提醒意图解析 / 记忆抽取共用，避免 CPU 上并发 generate
MODEL_LOCK = threading.Lock()

def _load_secret(name):
    """读取密钥：优先环境变量，其次本地 local_secrets.json（不入库，防止公开仓库泄露）"""
    val = os.getenv(name)
    if val:
        return val
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_secrets.json"), encoding="utf-8") as f:
            return json.load(f).get(name, "")
    except Exception:
        return ""

# ========== 火山引擎语音配置 ==========
VOLC_ACCESS_TOKEN = _load_secret("VOLC_ACCESS_TOKEN")
VOLC_APP_ID = _load_secret("VOLC_APP_ID")

# ========== DeepSeek 配置（联网搜索时结合搜索结果回答） ==========
DEEPSEEK_API_KEY = _load_secret("DEEPSEEK_API_KEY")
# 备用 key：主 key 余额不足/失效时自动切换
DEEPSEEK_API_KEYS = [k for k in [DEEPSEEK_API_KEY, _load_secret("DEEPSEEK_API_KEY_BACKUP")] if k]
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

# ========== 联网搜索配置（博查 BochaAI API，Bing 抓取作回退） ==========
BING_SEARCH_KEY = _load_secret("BING_SEARCH_KEY")

ASR_URL = "https://openspeech.bytedance.com/api/v1/asr"
TTS_URL = "https://openspeech.bytedance.com/api/v1/tts"

# 复用 HTTP 连接，省去每次请求的 TLS 握手
http_session = requests.Session()

# TTS 音色（可在 voice_samples/ 试听后更换）：
#   BV705 炀炀-自然对话 | BV406 梓梓-超自然 | BV007 亲切女声 | BV157 慈爱姥姥
VOICE_TYPE = "BV705_streaming"

# 任务5：三种人格配三种音色（火山引擎 TTS voice_type，可在控制台音色列表试听后替换）
#   踏实务实 → BV705 炀炀（自然对话风，原默认男声）
#   风趣幽默 → BV123 阳光青年（活泼男声，起伏丰富，适合幽默场景）
#   暖心知心 → BV405 甜美小源（甜美可爱女声，智能助手系列，陪伴感强）
PERSONA_VOICE = {
    "踏实务实": "BV705_streaming",
    "风趣幽默": "BV123_streaming",
    "暖心知心": "BV405_streaming",
}

# 任务5：说话节奏随场景变化——情绪 → 语速映射（speed_ratio 范围 [0.2, 3.0]）
#   愉悦快一点、平淡保持默认 0.8、低落/关心慢一点
EMOTION_SPEED = {"愉悦": 0.95, "平淡": 0.8, "低落": 0.7}

# 提醒播报专用参数：清晰有力（语速稍快、音调略抬、音量加大）
REMINDER_TTS_PARAMS = {"speed": 0.85, "pitch": 1.05, "volume": 1.5}
# 主动关怀专用参数：放慢放柔（关心慢一点）
CARING_TTS_PARAMS = {"speed": 0.7, "pitch": 1.0, "volume": 1.3}

from reminder import (ReminderStore, cn_to_int, parse_time_expr, humanize_ts,
                      humanize_repeat, describe_reminder, looks_like_reminder,
                      fallback_extract, match_confirmation, repeat_desc,
                      ack_set_message, trigger_message, reremind_message,
                      escalated_message, missed_message, confirm_ack_message,
                      later_ack_message, clarify_time_message, list_message,
                      select_message, confirm_cancel_message, modify_ask_message,
                      cancel_ack_message, abort_message, capability_message,
                      is_complaint_about_reminder, split_thing_note)
from memory_system import MemoryBank, match_symptoms, similarity
import ai_services

ASR_URL = "https://openspeech.bytedance.com/api/v1/asr"
TTS_URL = "https://openspeech.bytedance.com/api/v1/tts"

# 一键录音 JS：点一下麦克风按钮开始录音，再点一下停止并回传 16k 单声道 WAV base64
# （绕过 Gradio 录音组件需二次点击的问题；浏览器要求麦克风必须在用户手势中启动）
MIC_RECORD_JS = """
async () => {
    const SR = 16000;
    const setBtn = (t) => {
        const btn = document.querySelector('.mic-btn button') || document.querySelector('.mic-btn');
        if (btn) btn.textContent = t;
    };
    // 正在录音 -> 停止、编码、回传
    if (window.__xyRecording) {
        window.__xyRecording = false;
        setBtn('🎙️');
        try { window.__xyProc && window.__xyProc.disconnect(); } catch (e) {}
        try { window.__xySrc && window.__xySrc.disconnect(); } catch (e) {}
        try { window.__xyStream && window.__xyStream.getTracks().forEach(t => t.stop()); } catch (e) {}
        const origSR = window.__xyCtx.sampleRate;
        const chunks = window.__xyChunks || [];
        try { await window.__xyCtx.close(); } catch (e) {}
        let total = 0; chunks.forEach(c => total += c.length);
        if (total < origSR / 5) {  // 不足 0.2 秒视为误触
            return ["", ""];
        }
        // 合并分块
        const merged = new Float32Array(total);
        let off = 0;
        for (const c of chunks) { merged.set(c, off); off += c.length; }
        // 分块平均降采样到 16k（等效低通+抽取），再转 int16
        const ratio = origSR / SR;
        const n = Math.floor(merged.length / ratio);
        const pcm = new Int16Array(n);
        for (let i = 0; i < n; i++) {
            let s = 0, cnt = 0;
            const st = Math.floor(i * ratio), en = Math.min(Math.floor((i + 1) * ratio), merged.length);
            for (let j = st; j < en; j++) { s += merged[j]; cnt++; }
            const v = cnt ? s / cnt : 0;
            pcm[i] = Math.max(-32768, Math.min(32767, Math.round(v * 32767)));
        }
        // 组装 WAV 头
        const buf = new ArrayBuffer(44 + pcm.length * 2);
        const v = new DataView(buf);
        const ws = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
        ws(0, 'RIFF'); v.setUint32(4, 36 + pcm.length * 2, true); ws(8, 'WAVE');
        ws(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
        v.setUint32(24, SR, true); v.setUint32(28, SR * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
        ws(36, 'data'); v.setUint32(40, pcm.length * 2, true);
        new Int16Array(buf, 44).set(pcm);
        // base64 编码
        const bytes = new Uint8Array(buf);
        let bin = '';
        const CH = 0x8000;
        for (let i = 0; i < bytes.length; i += CH) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
        return ["", btoa(bin)];
    }
    // 未录音 -> 开始录音
    let stream;
    try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
        return ["❌ 无法访问麦克风，请检查浏览器权限", ""];
    }
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    try { await ctx.resume(); } catch (e) {}
    const src = ctx.createMediaStreamSource(stream);
    const proc = ctx.createScriptProcessor(4096, 1, 1);
    const silent = ctx.createGain(); silent.gain.value = 0;  // 静音输出，防止音箱回声
    window.__xyChunks = [];
    proc.onaudioprocess = (e) => {
        const d = e.inputBuffer.getChannelData(0);
        if (window.__xyRecording) window.__xyChunks.push(new Float32Array(d));
    };
    src.connect(proc); proc.connect(silent); silent.connect(ctx.destination);
    window.__xyStream = stream; window.__xyCtx = ctx; window.__xySrc = src; window.__xyProc = proc;
    window.__xyRecording = true;
    setBtn('🔴');
    return ["🎤 正在录音，说完后再点一下发送", ""];
}
"""

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
    """原子写入：先写临时文件，再替换。临时文件名带进程/线程标识，
    避免主流程与异步标题线程并发写同一 tmp 文件时 WinError 32 撞车"""
    tmp_path = f"{file_path}.{os.getpid()}_{threading.get_ident()}.tmp"
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

# ========== 情绪日志持久化（任务9找回：analyze_emotion + 落盘 + 心情曲线） ==========
EMOTION_LOG_FILE = "emotion_log.json"

def load_emotion_log():
    log = load_json_safe(EMOTION_LOG_FILE, [])
    return log if isinstance(log, list) else []

def save_emotion_log(log):
    save_json_safe(EMOTION_LOG_FILE, log[-300:])   # 只留最近300条，防膨胀

# 初始化内存变量
user_memory = {
    "events": load_memory_events(),
    "last_medication_time": 0.0,
    "call_name": "奶奶"
}

# ========== 提醒系统（意图解析/调度/确认闭环见 reminder.py 与下方相关函数） ==========
REMINDERS_FILE = "reminders.json"
RE_REMIND_INTERVAL = 120     # 到点未确认，隔多久补提醒（秒）
MAX_RE_REMIND = 2            # 最多补提醒2次，仍未确认则升级记录"请家人留意"
PENDING_TTL = 150            # 澄清/选择等待老人答复的有效期（秒），超时作废不追问第二次
CONFIRM_WINDOW = 6 * 3600    # 触发后可确认"办好了"的窗口

reminder_store = ReminderStore(REMINDERS_FILE)
UI_STATE = {"personality": "踏实务实", "last_emotion": "平淡"}
pending_reminder = {"kind": None, "data": {}, "ts": 0.0}
reminder_queue = deque(maxlen=20)

# ========== 对话历史管理 ==========
RECENT_TURNS = 20                 # 保留原文轮数
RECENT_MSGS = RECENT_TURNS * 2     # 40 条消息（user+assistant 各一）

def load_all_conversations():
    convs = load_json_safe(HISTORY_FILE, [])
    for c in convs:
        c.setdefault('summary', '')
        c.setdefault('summary_until_index', 0)
        c.setdefault('last_summarized_ts', 0.0)
    convs.sort(key=lambda x: x.get('updated', 0), reverse=True)
    return convs

def save_conversation(conv_id, messages, title=None, update_title=False,
                       summary=None, summary_until_index=None):
    convs = load_all_conversations()
    now_ts = time.time()
    found = False
    for i, c in enumerate(convs):
        if c['id'] == conv_id:
            convs[i]['messages'] = messages
            convs[i]['updated'] = now_ts
            if update_title and title:
                convs[i]['title'] = title
            if summary is not None:
                convs[i]['summary'] = summary
                # summary_until_index 由调用方显式传入更稳健；
                # 未传时按"已超出阈值则裁剪掉溢出部分"语义估算
                if summary_until_index is not None:
                    convs[i]['summary_until_index'] = summary_until_index
                elif len(messages) > RECENT_MSGS:
                    convs[i]['summary_until_index'] = len(messages) - RECENT_MSGS
                # 否则保留原值，防止回退为 0
                convs[i]['last_summarized_ts'] = now_ts
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
            'updated': now_ts,
            'summary': summary or '',
            'summary_until_index': summary_until_index or 0,
            'last_summarized_ts': now_ts if summary is not None else 0.0,
        }
        convs.append(new_conv)
    convs.sort(key=lambda x: x.get('updated', 0), reverse=True)
    save_json_safe(HISTORY_FILE, convs)

def _maybe_compress_history(conv_id, internal_history, call_name):
    """溢出时增量摘要并裁剪 internal_history。
    返回 (裁剪后的 internal_history, summary_str)。
    失败时返回 (原 internal_history, "")，不修改 conv。
    触发阈值 RECENT_MSGS+2：留出当前轮 user+assistant 的写入空间，
    避免 save 后又因 +2 越界导致每轮重复触发摘要。"""
    if len(internal_history) <= RECENT_MSGS + 2:
        return internal_history, ""
    convs = load_all_conversations()
    conv = next((c for c in convs if c['id'] == conv_id), None)
    if conv is None:
        return internal_history, ""
    old_summary = conv.get('summary', '')
    old_sui = conv.get('summary_until_index', 0)
    overflow = internal_history[:-RECENT_MSGS]
    new_summary = ai_services.summarize_conversation_segment(
        old_summary, overflow, call_name)
    if not new_summary:
        print(f"[summarize] 失败，保留原文 conv={conv_id}")
        return internal_history, ""
    trimmed = internal_history[-RECENT_MSGS:]
    # summary_until_index 反映"messages 中已被摘要覆盖的下一条索引"
    # = 旧值 + 本轮裁剪掉的条数
    new_sui = old_sui + len(overflow)
    # 立即写回，避免后续 save_conversation 覆盖回滚
    save_conversation(conv_id, trimmed, summary=new_summary,
                      summary_until_index=new_sui)
    return trimmed, new_summary

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
    """关键词事件抽取：驱动主动关怀（吃药提醒/健康回访）。
    语义层的画像/检索/反思由 memory_bank（LLM结构化抽取）负责，两者互补。"""
    events = []
    now = time.time()
    text = str(user_input)

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
    # Bug②：健康触发接入 memory_system 的症状变体表（头痛/头昏/偏头风 → 归一规范名），
    # 单一数据源，与记忆系统共用，杜绝两表漂移
    for content in match_symptoms(text):
        events.append({"type": "health", "content": content, "time": now, "status": "active"})
    if not any(e["type"] == "health" for e in events):
        for kw in ("不舒服", "失眠", "睡不着", "血压高", "血糖高", "感冒", "牙疼"):
            if kw in text:
                events.append({"type": "health", "content": kw, "time": now, "status": "active"})
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


# ========== 语音识别与合成（火山引擎）==========
def resample_audio(audio_data, orig_sr, target_sr=16000):
    """带抗混叠的重采样：先低通（均值滤波），再插值，避免高频混叠成噪声影响 ASR"""
    ratio = orig_sr / target_sr
    if ratio <= 1:
        return audio_data.astype(np.int16)
    # 整数倍降采样（如 48k->16k）直接分块平均，等效低通+抽取
    if abs(ratio - round(ratio)) < 1e-6:
        n = int(round(ratio))
        trim = len(audio_data) - len(audio_data) % n
        return audio_data[:trim].reshape(-1, n).mean(axis=1).astype(np.int16)
    # 非整数倍：先做窗口=n 的滑动平均低通，再线性插值
    win = int(np.ceil(ratio))
    if win > 1 and len(audio_data) > win:
        kernel = np.ones(win) / win
        audio_data = np.convolve(audio_data, kernel, mode='same')
    old_indices = np.linspace(0, len(audio_data) - 1, len(audio_data))
    new_length = int(len(audio_data) / ratio)
    new_indices = np.linspace(0, len(audio_data) - 1, new_length)
    return np.interp(new_indices, old_indices, audio_data).astype(np.int16)

def normalize_gain(audio_array, target_peak=30000.0):
    """峰值归一化：老人说话音量偏小时提升信号强度，提高 ASR 识别率"""
    peak = np.max(np.abs(audio_array))
    if peak < 100 or peak >= target_peak:
        return audio_array
    return (audio_array.astype(np.float64) * (target_peak / peak)).astype(np.int16)

def audio_numpy_to_wav_bytes(audio_tuple, tail_silence_sec=0.3):
    sample_rate, audio_array = audio_tuple
    if len(audio_array.shape) > 1:
        audio_array = audio_array[:, 0]
    audio_array = np.asarray(audio_array).reshape(-1)
    if audio_array.size == 0:
        return b""
    if audio_array.dtype != np.int16:
        if np.max(np.abs(audio_array)) <= 1.0:
            audio_array = (audio_array * 32767).astype(np.int16)
        else:
            audio_array = audio_array.astype(np.int16)
    if sample_rate != 16000:
        audio_array = resample_audio(audio_array, sample_rate, 16000)
    audio_array = normalize_gain(audio_array)
    # 结尾补静音：避免停止录音时尾字被截掉，ASR 识别不全
    audio_array = np.concatenate([audio_array, np.zeros(int(16000 * tail_silence_sec), dtype=np.int16)])
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(audio_array.tobytes())
    buf.seek(0)
    return buf.read()

def split_sentences(text):
    """按标点切分长文本：避免单次 TTS 请求文本过长被截断；过短的片段合并，避免请求碎片化"""
    parts = re.split(r'(?<=[。！？!?；;\n])', text)
    sentences, buf = [], ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        buf += p
        if len(buf) >= 10:
            sentences.append(buf)
            buf = ""
    if buf:
        sentences.append(buf)
    return sentences if sentences else [text]

def tts_request(text, voice_type=None, speed=0.8, pitch=1.0, volume=1.3):
    """单次调用火山 TTS，返回 wav 字节。
    任务5：voice_type/speed/pitch/volume 参数化，支持人格音色与场景节奏"""
    headers = {"Authorization": f"Bearer; {VOLC_ACCESS_TOKEN}"}
    data = {
        "app": {"appid": VOLC_APP_ID, "token": VOLC_ACCESS_TOKEN, "cluster": "volcano_tts"},
        "user": {"uid": "xiaoyi_user"},
        "audio": {"voice_type": voice_type or VOICE_TYPE, "encoding": "wav",
                  "speed_ratio": speed, "volume_ratio": volume,
                  "pitch_ratio": pitch, "rate": 16000},
        "request": {"reqid": str(uuid.uuid4()), "text": text, "text_type": "plain", "operation": "query"}
    }
    resp = http_session.post(TTS_URL, headers=headers, json=data, timeout=15)
    result = resp.json()
    if result.get("code") == 3000 and "data" in result:
        return base64.b64decode(result["data"])
    print(f"TTS返回异常: {result}")
    return None

def concat_wav_bytes(wav_list, gap_sec=0.25):
    """拼接多段同格式 wav，句间插入停顿"""
    out = io.BytesIO()
    with wave.open(out, 'wb') as wf_out:
        params_set = False
        for i, wav_bytes in enumerate(wav_list):
            with wave.open(io.BytesIO(wav_bytes), 'rb') as wf_in:
                if not params_set:
                    wf_out.setparams(wf_in.getparams())
                    params_set = True
                wf_out.writeframes(wf_in.readframes(wf_in.getnframes()))
                if i < len(wav_list) - 1:
                    # 16k 采样率、16bit 单声道：每秒 16000 个采样点，每点 2 字节
                    wf_out.writeframes(b'\x00\x00' * int(16000 * gap_sec))
    return out.getvalue()

def text_to_speech(text, voice_type=None, speed=0.8, pitch=1.0, volume=1.3):
    if not text or text.strip() == "":
        return None
    try:
        sentences = split_sentences(text)
        wav_list = []
        for s in sentences:
            wav_bytes = tts_request(s, voice_type=voice_type,
                                    speed=speed, pitch=pitch, volume=volume)
            if wav_bytes:
                wav_list.append(wav_bytes)
        if not wav_list:
            return None
        audio_bytes = wav_list[0] if len(wav_list) == 1 else concat_wav_bytes(wav_list)
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
        elif isinstance(audio_input, (bytes, bytearray)):
            wav_bytes = bytes(audio_input)
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
            "request": {"reqid": str(uuid.uuid4()), "sequence": 1}
        }
        resp = http_session.post(ASR_URL, headers=headers, json=json_body, timeout=30)
        result = resp.json()
        if result.get("code") == 1000 and "result" in result:
            return result["result"][0]["text"]
        print(f"ASR返回异常: {result}")
    except Exception as e:
        print(f"ASR异常: {e}")
    return ""

def enhance_wav_bytes(wav_bytes):
    """对 wav 字节做归一化增益 + 结尾补静音（与麦克风 numpy 路径一致）"""
    try:
        with wave.open(io.BytesIO(wav_bytes), 'rb') as wf:
            sr, ch = wf.getframerate(), wf.getnchannels()
            frames = wf.readframes(wf.getnframes())
        arr = np.frombuffer(frames, dtype=np.int16)
        if ch > 1:
            arr = arr.reshape(-1, ch)[:, 0].copy()
        if sr != 16000:
            arr = resample_audio(arr, sr, 16000)
        arr = normalize_gain(arr)
        arr = np.concatenate([arr, np.zeros(int(16000 * 0.3), dtype=np.int16)])
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(arr.tobytes())
        return buf.getvalue()
    except Exception as e:
        print(f"音频增强失败: {e}")
        return wav_bytes

def _clean_search_query(text):
    """把老人的口语问题清洗成更利于搜索引擎的查询词：去疑问后缀和标点"""
    q = re.sub(r"[?？。！，,.!~～\s]+", " ", str(text)).strip()
    q = re.sub(r"(怎么样|怎么办|好不好|可不可以|行不行|呢|吗|啊|吧|请问)", "", q)
    return q.strip() or str(text)

# ========== 联网搜索（Bing 免费抓取 + DeepSeek 总结） ==========
from html import unescape as _html_unescape

# 触发联网搜索的关键词（时效性/事实性问题）
SEARCH_TRIGGERS = ["今天", "最新", "新闻", "天气", "现在", "最近", "目前",
                   "股价", "汇率", "热搜", "奥运会", "世界杯", "几号", "什么时候",
                   "是谁", "谁是", "多少钱", "放假", "台风", "春晚"]

def needs_search(text):
    return any(k in text for k in SEARCH_TRIGGERS)

def _bocha_search(query, top_k=4):
    """博查搜索API（结构化结果，带摘要），失败返回空列表"""
    if not BING_SEARCH_KEY:
        return []
    try:
        r = http_session.post("https://api.bochaai.com/v1/web-search",
                              headers={"Authorization": f"Bearer {BING_SEARCH_KEY}",
                                       "Content-Type": "application/json"},
                              json={"query": query, "summary": True, "count": top_k},
                              timeout=10)
        data = r.json().get("data") or {}
        out = []
        for v in (data.get("webPages") or {}).get("value", [])[:top_k]:
            strip = lambda s: _html_unescape(re.sub(r'<[^>]+>', '', s or '')).strip()
            name, snip = strip(v.get("name")), strip(v.get("summary") or v.get("snippet"))
            if name:
                out.append((name, snip, v.get("url", "")))
        if out:
            print(f"🔍 博查搜索: {query}（{len(out)} 条结果）")
        return out
    except Exception as e:
        print(f"博查搜索异常: {e}")
        return []

def web_search(query, top_k=4):
    """联网搜索：优先博查API（key可用、结果更稳），失败回退 Bing 网页抓取，返回 [(标题, 摘要, 链接)]"""
    results = _bocha_search(query, top_k)
    if results:
        return results
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        resp = http_session.get("https://www.bing.com/search",
                                params={"q": query, "setlang": "zh-hans"},
                                headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"搜索 HTTP {resp.status_code}")
            return []
        page = resp.text
        results = []
        # 每条结果：<li class="b_algo">...<h2><a href="链接">标题</a></h2>...<p>摘要</p>...</li>
        for m in re.finditer(r'<li class="b_algo".*?</li>', page, re.S):
            block = m.group(0)
            t = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
            if not t:
                continue
            url_ = t.group(1)
            title = _html_unescape(re.sub(r'<[^>]+>', '', t.group(2))).strip()
            snip = re.search(r'<p[^>]*>(.*?)</p>', block, re.S)
            snippet = _html_unescape(re.sub(r'<[^>]+>', '', snip.group(1))).strip() if snip else ""
            if title:
                results.append((title, snippet, url_))
            if len(results) >= top_k:
                break
        return results
    except Exception as e:
        print(f"搜索异常: {e}")
        return []

def fetch_page_text(url, max_chars=1200):
    """抓取网页正文（去掉标签/脚本），取前 max_chars 字符，失败返回空串"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        resp = http_session.get(url, headers=headers, timeout=8)
        if resp.status_code != 200 or len(resp.text) < 100:
            return ""
        text = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', resp.text, flags=re.S | re.I)
        text = _html_unescape(re.sub(r'<[^>]+>', ' ', text))
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:max_chars]
    except Exception:
        return ""

def search_and_summarize(query, top_k=4):
    """搜索 + 抓正文，返回拼好的参考资料文本（空串表示没搜到）"""
    results = web_search(query, top_k)
    if not results:
        return ""
    lines = []
    for i, (title, snippet, url_) in enumerate(results):
        page_text = fetch_page_text(url_) if i == 0 else ""  # 只抓第一条的正文，控制耗时
        entry = f"{i+1}. {title}：{snippet}"
        if page_text:
            entry += f"（正文节选：{page_text}）"
        lines.append(entry)
    print(f"🔍 已联网搜索: {query}（{len(results)} 条结果）")
    return "\n".join(lines)

def deepseek_chat_msgs(messages, max_tokens=300, timeout=60):
    """调用 DeepSeek 对话接口，返回回复文本（失败返回空串）；主 key 失败自动换备用 key"""
    last_err = ""
    for key in DEEPSEEK_API_KEYS:
        try:
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            data = {"model": "deepseek-chat", "messages": messages,
                    "max_tokens": max_tokens, "temperature": 0.7}
            resp = http_session.post(DEEPSEEK_URL, headers=headers, json=data, timeout=timeout)
            res = resp.json()
            if res.get("choices"):
                return res["choices"][0]["message"]["content"].strip()
            last_err = str(res.get("error", res))[:150]
        except Exception as e:
            last_err = str(e)[:150]
    print(f"DeepSeek调用失败: {last_err}")
    return ""

# 服务层（安全过滤/小传/跨会话问候等）注入 DeepSeek 通道：离线优先，LLM 增强
ai_services.init(deepseek_chat_msgs)

# ========== 记忆系统（事件流→记忆库→画像，见 memory_system.py） ==========
MEMORY_BANK_FILE = "memory_bank.json"
PROFILE_TXT_FILE = "用户画像.txt"   # 任务3：画像后台导出文件（界面不再展示画像卡片）

def memory_llm(prompt):
    """记忆抽取/反思用 LLM：优先 DeepSeek（快、JSON 稳），失败回退本地 3B 贪心解码。
    记忆抽取在后台线程异步跑，不阻塞聊天。"""
    txt = deepseek_chat_msgs([{"role": "user", "content": prompt}], max_tokens=500)
    if txt:
        return txt
    if _model is None:
        return ""            # 本地模型缺失：调用方自动降级关键词抽取
    try:
        with MODEL_LOCK:
            full = f"<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n"
            inputs = _tokenizer(full, return_tensors="pt").to(_model.device)
            with torch.no_grad():
                out = _model.generate(**inputs, max_new_tokens=160, do_sample=False)
        return _tokenizer.decode(out[0][inputs.input_ids.shape[1]:], skip_special_tokens=True)
    except Exception as e:
        print(f"记忆LLM本地回退失败: {e}")
        return ""

memory_bank = MemoryBank(MEMORY_BANK_FILE, llm=memory_llm)
_n_migrated = memory_bank.import_legacy_events(MEMORY_EVENTS_FILE)
if _n_migrated:
    print(f"📥 已从旧版关键词事件导入 {_n_migrated} 条情节记忆")

# 记忆抽取队列：聊天返回后再后台抽取，不拖慢回复速度
_memory_queue = deque()

def _memory_worker():
    while True:
        try:
            if _memory_queue:
                u, a = _memory_queue.popleft()
                memory_bank.observe(u, a)
                # 任务3：画像写进 用户画像.txt（越用越全，后台静默更新）
                memory_bank.export_profile_txt(
                    PROFILE_TXT_FILE, user_memory.get("call_name", ""))
            else:
                time.sleep(2)
        except Exception as e:
            print(f"记忆抽取异常: {e}")
            time.sleep(1)

Thread(target=_memory_worker, daemon=True).start()

def get_memory_panel_html():
    return memory_bank.panel_html()

# ========== 对话核心逻辑 ==========
def extract_text(content):
    """提取纯文本（兼容 Gradio 的 str / {'text': ...} / [{'type': 'text', ...}, ...] 等嵌套格式）"""
    if isinstance(content, str): return content
    if isinstance(content, dict): return extract_text(content.get("text", ""))
    if isinstance(content, (list, tuple)): return "".join(extract_text(c) for c in content if c)
    return ""

def chat_response(user_input, chat_history, surname, gender, is_muted, personality, conv_id, dropdown):
    global last_user_interaction_time
    if user_input and str(user_input).strip():
        last_user_interaction_time = time.time()
        proactive.note_user_activity(user_input)   # 反馈闭环：熔断/拒绝/接受分类
    if not chat_history:
        chat_history = []
    if not user_input or str(user_input).strip() == "":
        # 提示语只展示，不写入对话历史文件
        chat_history = list(chat_history or []) + [{"role": "assistant", "content": f"{user_memory['call_name']}，您说什么我没听清呢～"}]
        return "", chat_history, None, conv_id, gr.update(choices=get_conversation_list_display())

    extract_memory(user_input)
    call_name = get_call_name(surname, gender)
    user_memory["call_name"] = call_name

    # 任务9找回：每轮记录老人情绪（低落/平淡/愉悦）→ emotion_log.json，供心情曲线
    emo = ai_services.analyze_emotion(user_input, call_name=call_name)
    _emo_log = load_emotion_log()
    _emo_log.append({"time": time.time(), "label": emo["label"],
                     "score": emo["score"], "note": emo["note"]})
    save_emotion_log(_emo_log)
    UI_STATE["personality"] = personality if personality in ("踏实务实", "风趣幽默", "暖心知心") else "踏实务实"
    UI_STATE["last_emotion"] = emo["label"]      # 任务5：语音节奏随情绪（愉悦快/低落慢）
    if personality == "踏实务实":
        base_prompt = PROMPT_PRACTICAL
    elif personality == "风趣幽默":
        base_prompt = PROMPT_HUMOR
    elif personality == "暖心知心":
        base_prompt = PROMPT_CARING
    else:
        base_prompt = PROMPT_PRACTICAL
    # 称呼强约束：小模型聊到"孙子"等词时易顺嘴叫错性别（爷爷↔奶奶），
    # 显式给出性别+正例+禁令；本地/DeepSeek 两条生成路径共用此 system
    wrong_call = "奶奶" if gender == "女" else "爷爷"
    system_content = (base_prompt +
                      f"\n【称呼规则】你陪伴的是一位{'女' if gender == '女' else '男'}性老人，"
                      f"全程只能称呼TA「{call_name}」"
                      f"（例如：{call_name}，您今天气色真好），"
                      f"绝对不能把TA叫成「{wrong_call}」。")

    # 记忆检索：三因子（新近性×重要性×相关性）取相关记忆注入上下文——
    # "上次您说膝盖疼，这几天好点没？"的"被记住"体验由此而来
    mem_ctx = memory_bank.build_chat_context(user_input)
    if mem_ctx:
        system_content += f"\n{mem_ctx}"

    # 统一 chat_history 为纯文本 dict（extract_text 为模块级函数）
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

    # 任务1：短期对话记忆——溢出时增量摘要并裁剪，所有出口共享压缩后历史
    internal_history, history_summary = _maybe_compress_history(
        conv_id, internal_history, call_name)

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
    if rem_reply is not None:
        save_history = internal_history + [{"role": "user", "content": user_input},
                                           {"role": "assistant", "content": rem_reply}]
        save_conversation(conv_id, save_history)
        is_first_msg = len(chat_history) <= 1
        if is_first_msg:
            Thread(target=generate_title_async, args=(conv_id, save_history), daemon=True).start()
        # 语音播报放到 .then 后置事件（play_reply_audio），不阻塞文字显示
        return "", save_history, None, conv_id, gr.update(choices=get_conversation_list_display())

    # 记忆主动确认的答复（上一轮末尾小忆问过"您之前说X是Y，是吧？"）：
    # 分类"对/不是/叫新值"走确定性闭环，不相关则放行给正常聊天
    conf_reply = memory_bank.resolve_confirm_reply(
        user_input, UI_STATE["personality"], user_memory["call_name"])
    if conf_reply is not None:
        save_history = internal_history + [{"role": "user", "content": user_input},
                                           {"role": "assistant", "content": conf_reply}]
        save_conversation(conv_id, save_history)
        return "", save_history, None, conv_id, gr.update(choices=get_conversation_list_display())

    # 任务5找回：冷场/不知道聊啥 → 基于已记住的信息主动递个轻松话题
    if ai_services.COLD_RE.search(user_input):
        topic = ai_services.suggest_topic_switch(memory_bank, user_memory["events"], call_name)
        save_history = internal_history + [{"role": "user", "content": user_input},
                                           {"role": "assistant", "content": topic}]
        save_conversation(conv_id, save_history)
        return "", save_history, None, conv_id, gr.update(choices=get_conversation_list_display())

    # 联网搜索：时效性/事实性问题先搜 Bing，再让 DeepSeek 结合资料回答
    search_context = ""
    if needs_search(user_input):
        refs = search_and_summarize(_clean_search_query(user_input))
        if refs:
            search_context = (
                "\n\n[网络搜索参考资料（回答要求：1.资料里有的具体信息——温度、日期、数字、人名等——"
                "必须直接转述给老人，不许说查不到；2.用口语化中文、一两句话说重点；"
                "3.资料确实没提到才可以说不知道，并给贴心的通用建议）]\n" + refs
            )

    bot_reply = ""
    if search_context or _model is None:
        # DeepSeek 结合搜索资料回答（本地 3B 难以可靠利用搜索结果）；
        # 本地模型缺失/加载失败时整体降级 DeepSeek——"模型不需要本地训练，能调用就行"
        try:
            msgs = [{"role": "system", "content": system_content}]
            if history_summary:
                msgs.append({"role": "system",
                             "content": f"【之前对话摘要】\n{history_summary}"})
            msgs += internal_history[-RECENT_MSGS:] + \
                   [{"role": "user", "content": user_input + search_context}]
            bot_reply = deepseek_chat_msgs(msgs)
        except Exception as e:
            print(f"DeepSeek调用失败: {e}")

    if not bot_reply and _model is not None:
        try:
            local_system = system_content + search_context
            if history_summary:
                local_system += f"\n【之前对话摘要】\n{history_summary}"
            prompt = f"<|im_start|>system\n{local_system}<|im_end|>"
            for m in internal_history[-RECENT_MSGS:]:
                prompt += f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>"
            prompt += f"<|im_start|>user\n{user_input}<|im_end|><|im_start|>assistant\n"

            inputs = _tokenizer(prompt, return_tensors="pt").to(_model.device)
            with MODEL_LOCK, torch.no_grad():
                outputs = _model.generate(**inputs,
                    max_new_tokens=300 if ON_GPU else 160,  # int8 CPU 实测 4+ tok/s，放宽长度避免回复过短
                    temperature=0.9, top_p=0.95, do_sample=True,
                    repetition_penalty=1.2, no_repeat_ngram_size=4)
            bot_reply = _tokenizer.decode(outputs[0][inputs.input_ids.shape[1]:], skip_special_tokens=True)
            for junk in ["<|im_end|>","<|im_start|>","assistant","user","system"]:
                bot_reply = bot_reply.replace(junk, "")
            bot_reply = bot_reply.strip().strip("[]").strip("{}").strip(",").strip()
            if not bot_reply or len(bot_reply) < 5:
                bot_reply = f"{call_name}，我听着呢，您说。"
            # Bug⑥a修复：不再每轮 torch.cuda.empty_cache()。依据 vLLM/PagedAttention
            # (Kwon et al., SOSP 2023)——稳态单进程推理显存占用稳定，缓存分配器
            # 本就在进程内复用已释放块；empty_cache 会向驱动归还并同步设备，
            # 实测每轮空耗 1-3 秒。OOM 风险由异常分支兜底，不做预防式清理。
        except Exception as e:
            print(f"推理错误: {e}")
            bot_reply = ""

    if not bot_reply:
        bot_reply = f"{call_name}，我有点听不清楚，您再说一遍好吗？"

    # 性别称呼保险丝：模型若仍叫错（爷爷↔奶奶），按呼语规则确定性纠正
    bot_reply = fix_address(bot_reply, call_name, gender, surname)

    # 任务6找回：输出安全过滤——脏话/冷漠语气本地规则毫秒级拦截；
    # 医疗风险措辞才调 DeepSeek 审核（不拖慢普通回复，保住 Bug⑥ 成果）
    sf = ai_services.safety_filter(bot_reply, personality=UI_STATE["personality"],
                                   call_name=call_name)
    if not sf["safe"]:
        print(f"[安全过滤] {sf['reason']} | 原文: {bot_reply[:40]}")
        bot_reply = sf["safe_reply"]

    # 记忆主动确认（冷启动节制：前几轮不问；全局30分钟冷却与每条最多2次在模块内控制）
    if len(internal_history) >= 2:
        conf_q = memory_bank.pop_confirm_question(UI_STATE["personality"], call_name)
        if conf_q:
            bot_reply += f"\n{conf_q}"

    # 任务5找回（重复检测）：老人连着≥3轮说同一件事，轻轻递个新话题，不打断当前回复
    _recent_u = [m["content"] for m in internal_history if m["role"] == "user"][-2:] + [user_input]
    if len(_recent_u) >= 3 and all(similarity(user_input, m) >= 0.5 for m in _recent_u[:2]):
        bot_reply += "\n" + ai_services.suggest_topic_switch(
            memory_bank, user_memory["events"], call_name)

    # 本轮对话入记忆抽取队列，后台 LLM 结构化抽取原子事实（不阻塞回复）
    _memory_queue.append((user_input, bot_reply))

    is_first_msg = len(chat_history) <= 1
    save_history = internal_history + [{"role":"user","content":user_input},{"role":"assistant","content":bot_reply}]
    save_conversation(conv_id, save_history)
    if is_first_msg:
        Thread(target=generate_title_async, args=(conv_id, save_history), daemon=True).start()
    # 语音播报放到 .then 后置事件，让文字先显示出来，不被 TTS 合成阻塞
    return "", save_history, None, conv_id, gr.update(choices=get_conversation_list_display())

def play_reply_audio(chat_history, is_muted):
    """主流程返回后再合成语音（读取最后一条助手消息），避免 TTS 阻塞文字显示。
    任务5：按当前人格选音色、按当轮情绪调语速（愉悦快一点、低落慢一点）"""
    if is_muted or not chat_history:
        return None
    last = chat_history[-1]
    if isinstance(last, dict) and last.get("role") == "assistant":
        # 用 extract_text 取纯文本：content 可能是 str / {'text':...} / [{'type':'text',...}]
        # 直接 str(content) 会把英文结构（type/text 等）读出来
        content = extract_text(last.get("content", "")).strip()
        if content:
            voice = PERSONA_VOICE.get(UI_STATE["personality"])
            speed = EMOTION_SPEED.get(UI_STATE["last_emotion"], 0.8)
            return text_to_speech(content, voice_type=voice, speed=speed)
    return None

def process_mic_data(audio_b64, chat_history, surname, gender, is_muted, personality, conv_id, dropdown):
    """处理一键录音回传的 base64 WAV：空值表示『开始录音』触发的空事件，直接跳过"""
    global last_user_interaction_time
    if not audio_b64:
        # 不改变任何界面状态
        return "", chat_history, None, conv_id, gr.update(choices=get_conversation_list_display())
    last_user_interaction_time = time.time()
    try:
        wav_bytes = enhance_wav_bytes(base64.b64decode(audio_b64))
    except Exception as e:
        print(f"录音数据解码失败: {e}")
        wav_bytes = b""
    text = transcribe_audio(wav_bytes) if len(wav_bytes) > 200 else ""
    if not text:
        # 提示语只展示在界面上，不写入对话历史文件
        chat_history = list(chat_history or []) + [{"role": "assistant", "content": "我没听清您说的话，能再说一遍吗？"}]
        return "", chat_history, None, conv_id, gr.update(choices=get_conversation_list_display())
    return chat_response(text, chat_history, surname, gender, is_muted, personality, conv_id, dropdown)

def play_mic_reply_audio(chat_history, is_muted, audio_b64):
    """麦克风链路：仅在本轮确实有语音输入时播报（『开始录音』的空事件不重复播报）"""
    if not audio_b64:
        return None
    return play_reply_audio(chat_history, is_muted)

def new_conversation():
    global last_user_interaction_time
    last_user_interaction_time = time.time()
    new_id = str(int(time.time() * 1000))
    welcome_msg = f"您好{user_memory['call_name']}！我是小忆，很高兴能陪伴您～"
    greet = ai_services.build_cross_session_greeting(memory_bank, user_memory["call_name"])
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
    global last_user_interaction_time
    if not chat_history:
        chat_history = []
    msg = check_active_trigger()
    if msg and (time.time() - last_user_interaction_time) > 15:
        chat_history.append({"role": "assistant", "content": msg})
        save_conversation(conv_id, chat_history)
        # 任务5：主动关怀放慢放柔（关心慢一点），音色仍随人格
        audio_path = None if is_muted else text_to_speech(
            msg, voice_type=PERSONA_VOICE.get(UI_STATE["personality"]), **CARING_TTS_PARAMS)
        return chat_history, audio_path
    return chat_history, None

def toggle_mute(current_mute):
    new_mute = not current_mute
    btn_text = "🔇 静音" if new_mute else "🔊 取消静音"
    audio_path = None
    if current_mute and not new_mute:
        audio_path = text_to_speech("小忆语音已开启",
                                    voice_type=PERSONA_VOICE.get(UI_STATE["personality"]))
    return new_mute, gr.update(value=btn_text), audio_path

# ========== 提醒系统：意图解析 + 交互状态机 + 调度 ==========
INTENT_PROMPT = (
    "你是提醒指令解析器。判断老人对小忆说的话属于哪种：set(设提醒)、cancel(取消提醒)、"
    "modify(改提醒时间)、list(查看提醒)、complain(抱怨小忆忘了提醒，不是设新提醒)、none(其他)。"
    "输出一行JSON："
    '{"intent":"set","time":"时间原文，没有填空串","thing":"要办的事，不超过12个字，'
    '只留核心动作，如：吃降压药","note":"其余说明如药品放在哪，没有填空串",'
    '"target":"取消或修改的对象，没有填空串"}'
    "。不是提醒指令就输出{\"intent\":\"none\"}。只输出JSON，不要解释。"
)

def llm_extract_intent(user_input):
    """本地模型做意图+槽位结构化输出（贪心解码）；失败返回 None 走规则回退。
    Bug⑥b 后仅作断网兜底（DeepSeek 优先），避免与聊天生成抢本地模型锁。"""
    if _model is None:
        return None  # 本地模型缺失：DeepSeek/规则已覆盖，放弃本地兜底
    try:
        prompt = (f"<|im_start|>system\n{INTENT_PROMPT}<|im_end|>"
                  f"<|im_start|>user\n{user_input}<|im_end|>"
                  f"<|im_start|>assistant\n")
        inputs = _tokenizer(prompt, return_tensors="pt").to(_model.device)
        with MODEL_LOCK, torch.no_grad():
            out = _model.generate(**inputs, max_new_tokens=48, do_sample=False)
        txt = _tokenizer.decode(out[0][inputs.input_ids.shape[1]:], skip_special_tokens=True)
        m = re.search(r"\{.*\}", txt, re.S)
        if not m:
            return None
        data = json.loads(m.group(0))
        if data.get("intent") in ("set", "cancel", "modify", "list", "complain"):
            return data
    except Exception as e:
        print(f"意图解析失败，走规则回退: {e}")
    return None

def deepseek_extract_intent(user_input):
    """Bug⑥b：提醒意图+槽位解析走 DeepSeek API——不占本地 MODEL_LOCK，
    延迟从本地 3B 的 30 秒级降到 1-3 秒；与 memory_llm 的"DeepSeek 优先、
    本地兜底"架构一致（依据 Orca/vLLM 服务化思想：结构化子任务不与主生成
    争抢同一推理资源）。失败返回 None 落到本地兜底，维持离线可用。"""
    txt = deepseek_chat_msgs(
        [{"role": "system", "content": INTENT_PROMPT},
         {"role": "user", "content": user_input}], max_tokens=100, timeout=10)
    if not txt:
        return None
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    if data.get("intent") in ("set", "cancel", "modify", "list", "complain"):
        return data
    return None

def handle_reminder_complaint(user_input, now, call, persona):
    """Bug③：抱怨/质问（"你还不提醒我"）→ 道歉并补提醒，绝不反问"什么时候提醒您"。
    优先补响已触发未确认的提醒（3 秒内响铃）；有唯一待办则核对；都没有则道歉后
    请老人说一声要提醒什么，一句话补上。"""
    overdue = reminder_store.awaiting_confirm(now, CONFIRM_WINDOW)
    if overdue:
        r = overdue[0]
        reminder_store.snooze(r["id"], 3, now)   # 立即补响（调度线程 5 秒内触发）
        return (f"哎呀{call}，是我疏忽了，对不住——「{r['thing']}」我这就喊您，"
                f"往后我一定上心。")
    pend = reminder_store.pending(now)
    if len(pend) == 1:
        return (f"对不起{call}，是我没记牢。您说的是「{pend[0]['thing']}」这件事吧？"
                f"要我现在就提醒您吗？")
    if pend:
        return select_message(call, [describe_reminder(r, datetime.now()) for r in pend],
                              "让我提醒")
    return (f"对不起{call}，这回是我没记住，您别急。"
            f"您跟我说一声要提醒哪件事，我马上给您补上。")

def _filter_by_target(pend, target):
    """'把明天的提醒取消' -> 按对象描述过滤候选提醒；过滤为空则返回全部"""
    target = (target or "").strip()
    if not target:
        return pend
    key = target[:2]
    hits = [r for r in pend if key in r.get("thing", "") or key in describe_reminder(r)]
    return hits or pend

def _pick_option(text, options):
    """老人答复里挑提醒：支持序号（'第2个'/'2'）、事项关键词、日期词（'明天那个'）"""
    t = str(text or "")
    m = re.search(r"第?\s*([0-9]+|[一二两三四五六七八九十]+)\s*(?:个|条|号)?", t)
    if m:
        n = cn_to_int(m.group(1))
        if n and 1 <= n <= len(options):
            return options[n - 1]
    for o in options:
        thing = o.get("thing", "")
        if thing and len(thing) >= 2 and thing[:2] in t:
            return o
    today = datetime.now().date()
    for o in options:
        delta = (datetime.fromtimestamp(o["time"]).date() - today).days
        word = {0: "今天", 1: "明天", 2: "后天"}.get(delta)
        if word and word in t:
            return o
    return None

def _resolve_pending(user_input, now, call, persona):
    """处理上一轮澄清/选择等老人答复；返回回复文案或 None（不接管这条消息）"""
    kind = pending_reminder["kind"]
    d = pending_reminder["data"]

    if kind == "clarify_time":
        parsed = parse_time_expr(user_input, datetime.now())
        if parsed and parsed["kind"] == "fixed" and parsed.get("hour") is not None:
            ts = parsed["fire_ts"]
            repeat = parsed.get("repeat")  # 答复自带的周期（如补答"每天早上八点"）
            # 上一轮说了日期/周期但没说几点，本轮补了时刻 -> 对齐
            if d.get("repeat_type") == "daily":
                repeat = {"type": "daily", "hour": parsed["hour"] % 24,
                          "minute": parsed.get("minute") or 0}
            elif d.get("repeat_type") == "weekly":
                w = d.get("weekday")
                w = w if w is not None else datetime.now().weekday()
                repeat = {"type": "weekly", "weekday": w,
                          "hour": parsed["hour"] % 24, "minute": parsed.get("minute") or 0}
            elif d.get("date_offset") is not None:
                base = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) \
                       + timedelta(days=d["date_offset"], hours=parsed["hour"],
                                   minutes=parsed.get("minute") or 0)
                if base <= datetime.now():
                    base += timedelta(days=1)
                ts = base.timestamp()
            elif d.get("weekday") is not None:
                base = datetime.now() + timedelta(days=(d["weekday"] - datetime.now().weekday()) % 7)
                base = base.replace(hour=parsed["hour"], minute=parsed.get("minute") or 0,
                                    second=0, microsecond=0)
                if base <= datetime.now():
                    base += timedelta(days=7)
                ts = base.timestamp()
            reminder_store.add(d["thing"], ts, repeat, note=d.get("note", ""))
            pending_reminder["kind"] = None
            return ack_set_message(persona, call, d["thing"],
                                   humanize_ts(ts, datetime.now()), humanize_repeat(repeat))
        if parsed and parsed["kind"] == "fixed":
            reminder_store.add(d["thing"], parsed["fire_ts"], note=d.get("note", ""))
            pending_reminder["kind"] = None
            return ack_set_message(persona, call, d["thing"],
                                   humanize_ts(parsed["fire_ts"], datetime.now()))
        # 答复本身是个模糊锚点（如"睡醒后"）-> 直接采用其默认时长，不追问第二次
        if parsed and parsed["kind"] == "clarify" and parsed.get("suggestion"):
            ts = now + parsed["suggestion"]
            reminder_store.add(d["thing"], ts, note=d.get("note", ""))
            pending_reminder["kind"] = None
            return ack_set_message(persona, call, d["thing"], humanize_ts(ts, datetime.now()))
        # 同意默认时长
        if d.get("suggestion") and (match_confirmation(user_input) == "done"
                                    or re.search(r"^(好|行|可以|嗯+|要|就这样|就那样|随便)[吧呀啊哈。！!]*$", user_input.strip())):
            ts = now + d["suggestion"]
            reminder_store.add(d["thing"], ts, note=d.get("note", ""))
            pending_reminder["kind"] = None
            return ack_set_message(persona, call, d["thing"], humanize_ts(ts, datetime.now()))
        pending_reminder["kind"] = None
        if re.search(r"不用|算了|取消|不要了|先不", user_input):
            return abort_message(call)
        # 只问一次，答不上来就作罢，不追问第二次
        return f"好{call}，那这次先不设了。想提醒的时候随时跟我说“X分钟后提醒我XX”就行。"

    if kind == "confirm_cancel":
        pending_reminder["kind"] = None
        if re.search(r"不是|先不|算了|别", user_input):
            return abort_message(call)
        if re.search(r"是|对|嗯|好|没错|取消吧", user_input):
            reminder_store.cancel(d["rid"])
            return cancel_ack_message(persona, call, d.get("desc", ""))
        return abort_message(call)

    if kind in ("select_cancel", "select_modify"):
        action = "取消" if kind == "select_cancel" else "改时间"
        options = [reminder_store.get(rid) for rid in d.get("ids", [])]
        options = [o for o in options if o]
        if re.search(r"不了|算了|先不|别取消|别改", user_input):
            pending_reminder["kind"] = None
            return abort_message(call)
        if kind == "select_cancel" and re.search(r"全部|都取消|所有的|统统", user_input):
            n = reminder_store.cancel_all()
            pending_reminder["kind"] = None
            return f"好，{n}个提醒都取消了。"
        pick = _pick_option(user_input, options)
        pending_reminder["kind"] = None
        if pick is None:
            return f"{call}，没关系，您也可以看左边的“提醒事项”面板。"
        if kind == "select_cancel":
            desc = describe_reminder(pick, datetime.now())
            reminder_store.cancel(pick["id"])
            return cancel_ack_message(persona, call, desc)
        # 选择改期：答复里已带明确时间（"就明天那个，下午四点半"）-> 一步到位
        parsed = parse_time_expr(user_input, datetime.now())
        desc = describe_reminder(pick, datetime.now())
        if parsed and parsed["kind"] == "fixed":
            reminder_store.modify(pick["id"], parsed["fire_ts"], parsed.get("repeat"))
            return ack_set_message(persona, call, "",
                                   humanize_ts(parsed["fire_ts"], datetime.now()),
                                   humanize_repeat(parsed.get("repeat")))
        pending_reminder.update({"kind": "modify_time",
                                 "data": {"rid": pick["id"], "desc": desc}, "ts": now})
        return modify_ask_message(call, desc)

    if kind == "modify_time":
        pending_reminder["kind"] = None
        parsed = parse_time_expr(user_input, datetime.now())
        if parsed and parsed["kind"] == "fixed":
            reminder_store.modify(d["rid"], parsed["fire_ts"], parsed.get("repeat"))
            return ack_set_message(persona, call, "",
                                   humanize_ts(parsed["fire_ts"], datetime.now()),
                                   humanize_repeat(parsed.get("repeat")))
        return f"{call}，没听清时间。您也可以重新说一个，比如“下午三点提醒我{d.get('desc', '')}”。"
    return None

def try_handle_reminder(user_input):
    """提醒事务总入口。返回回复文案；返回 None 表示与提醒无关，走正常聊天。"""
    now = time.time()
    call = user_memory["call_name"]
    persona = UI_STATE["personality"]

    # 0) 过期的待澄清/待选择直接失效
    if pending_reminder["kind"] and now - pending_reminder["ts"] > PENDING_TTL:
        pending_reminder["kind"] = None

    # 0') Bug③补强：抱怨/质问优先于一切状态机——"你怎么还没提醒我"不是"等会儿"。
    #    旧顺序里它会被第2步待确认拦截的"还没"误判为顺延10分钟（Searle 言语行为理论：
    #    表达类责备 ≠ 指令类答复）。抱怨句式永远不会是澄清/确认/选择的合法答复，
    #    故提到最前：一律道歉+立即补提醒，绝不反问时间、绝不顺延。
    if is_complaint_about_reminder(user_input):
        return handle_reminder_complaint(user_input, now, call, persona)

    # 1) 上一轮澄清/选择的答复
    if pending_reminder["kind"]:
        reply = _resolve_pending(user_input, now, call, persona)
        if reply is not None:
            return reply

    # 2) 已触发提醒的确认/稍后（只拦截短句，避免误伤正常聊天）
    awaiting = reminder_store.awaiting_confirm(now, CONFIRM_WINDOW)
    if awaiting and len(str(user_input).strip()) <= 20:
        act = match_confirmation(user_input)
        if act == "done":
            r = awaiting[0]
            reminder_store.confirm(r["id"], now)
            return confirm_ack_message(persona, call, r["thing"])
        if act == "later":
            reminder_store.snooze(awaiting[0]["id"], 600, now)
            return later_ack_message(call, 10)

    # 3) 新意图：关键词预过滤，命中才解析（普通聊天零额外开销）。
    #    Bug③：先拦截抱怨/质问句式（"你还不提醒我"是责备不是设提醒指令），道歉并补提醒；
    #    Bug⑥b：解析链 规则(毫秒级) -> DeepSeek API(1-3秒，不占本地锁) -> 本地3B(断网兜底)。
    if not looks_like_reminder(user_input):
        return None
    if is_complaint_about_reminder(user_input):
        return handle_reminder_complaint(user_input, now, call, persona)
    data = (fallback_extract(user_input) or deepseek_extract_intent(user_input)
            or llm_extract_intent(user_input))
    if not data or data.get("intent") not in ("set", "cancel", "modify", "list", "complain"):
        return None
    intent = data["intent"]
    if intent == "complain":   # LLM 路径的第二道防线（正则未拦住时）
        return handle_reminder_complaint(user_input, now, call, persona)

    if intent == "list":
        return list_message(call, [describe_reminder(r, datetime.now())
                                   for r in reminder_store.pending(now)])

    if intent == "cancel":
        pend = reminder_store.pending(now)
        if not pend:
            return list_message(call, [])
        cands = _filter_by_target(pend, data.get("target"))
        if len(cands) == 1:
            desc = describe_reminder(cands[0], datetime.now())
            pending_reminder.update({"kind": "confirm_cancel",
                                     "data": {"rid": cands[0]["id"], "desc": desc}, "ts": now})
            return confirm_cancel_message(call, desc)
        pending_reminder.update({"kind": "select_cancel",
                                 "data": {"ids": [r["id"] for r in cands]}, "ts": now})
        return select_message(call, [describe_reminder(r, datetime.now()) for r in cands], "取消")

    if intent == "modify":
        pend = reminder_store.pending(now)
        if not pend:
            return list_message(call, [])
        cands = _filter_by_target(pend, data.get("target"))
        if len(cands) == 1:
            desc = describe_reminder(cands[0], datetime.now())
            pending_reminder.update({"kind": "modify_time",
                                     "data": {"rid": cands[0]["id"], "desc": desc}, "ts": now})
            return modify_ask_message(call, desc)
        pending_reminder.update({"kind": "select_modify",
                                 "data": {"ids": [r["id"] for r in cands]}, "ts": now})
        return select_message(call, [describe_reminder(r, datetime.now()) for r in cands], "改时间")

    # intent == "set"
    if re.search(r"能不能|可不可以|会不会|可以吗|行吗", user_input) \
            and not re.search(r"[0-9一二两三四五六七八九十]+分|[点半刻秒小时]", user_input):
        return capability_message(call)
    # Bug④：超长事项拆分主事项+备注——到点只念主事项，备注（位置等）入面板括号、不上 TTS
    thing, note = split_thing_note((data.get("thing") or "").strip())
    thing = thing or "您交代的事"
    note = note or str(data.get("note") or "").strip()
    time_src = (data.get("time") or "").strip()
    parsed = parse_time_expr(time_src, datetime.now()) if time_src else None
    if parsed is None:
        parsed = parse_time_expr(user_input, datetime.now())
    if parsed and parsed["kind"] == "fixed":
        reminder_store.add(thing, parsed["fire_ts"], parsed.get("repeat"), note=note)
        return ack_set_message(persona, call, thing,
                               humanize_ts(parsed["fire_ts"], datetime.now()),
                               humanize_repeat(parsed.get("repeat")))
    # 模糊锚点/缺时间 -> 一次澄清（带默认时长），不追问第二次
    pending_reminder.update({"kind": "clarify_time",
                             "data": {"thing": thing, "note": note,
                                      "suggestion": parsed.get("suggestion") if parsed else None,
                                      "repeat_type": parsed.get("repeat_type") if parsed else None,
                                      "weekday": parsed.get("weekday") if parsed else None,
                                      "date_offset": parsed.get("date_offset") if parsed else None},
                             "ts": now})
    return clarify_time_message(call, thing, parsed.get("suggestion") if parsed else None)

def reminder_worker():
    """提醒调度线程：持久化任务、到点触发、未确认补提醒、超限升级（防断电丢任务）"""
    for r in reminder_store.missed_on_startup(time.time()):
        reminder_queue.append({"message": missed_message(UI_STATE["personality"],
                                  user_memory["call_name"], r["thing"])})
    while True:
        try:
            now = time.time()
            for r in reminder_store.due(now):
                thing = r["thing"]
                reminder_store.mark_fired(r["id"], now)
                msg = trigger_message(UI_STATE["personality"], user_memory["call_name"], thing)
                reminder_queue.append({"message": msg})
            for r, kind in reminder_store.advance_awaiting(now, RE_REMIND_INTERVAL, MAX_RE_REMIND):
                if kind == "reremind":
                    msg = reremind_message(UI_STATE["personality"], user_memory["call_name"], r["thing"])
                else:
                    msg = escalated_message(user_memory["call_name"], r["thing"])
                reminder_queue.append({"message": msg})
        except Exception as e:
            print(f"提醒调度异常: {e}")
        time.sleep(5)

# ========== 提醒音频：提示音 + 人设播报 ==========
_chime_cache = None

def _make_chime():
    """到点提示音：三个中低频音符（392/523/659Hz），避开老年性耳背敏感的高频区"""
    sr = 16000
    seg, gap = int(sr * 0.28), int(sr * 0.06)
    parts = []
    for f in (392.0, 523.25, 659.25):
        t = np.arange(seg) / sr
        tone = 0.32 * np.sin(2 * np.pi * f * t)
        env = np.minimum(1.0, np.linspace(0, 12, seg)) * np.minimum(1.0, np.linspace(12, 0, seg))
        parts.append(tone * env)
        parts.append(np.zeros(gap))
    return (np.concatenate(parts) * 32767).astype(np.int16)

def _chime():
    global _chime_cache
    if _chime_cache is None:
        _chime_cache = _make_chime()
    return _chime_cache

def _wav_to_numpy(path):
    with wave.open(path, 'rb') as wf:
        sr = wf.getframerate()
        data = wf.readframes(wf.getnframes())
    return sr, np.frombuffer(data, dtype=np.int16)

def build_reminder_audio(text, is_muted):
    """提醒音频 = 提示音 + 播报。静音时保留提示音，保证到点一定有反馈。"""
    try:
        sr = 16000
        pieces = [_chime()]
        if not is_muted:
            # 任务5：提醒播报清晰有力（语速稍快、音调略抬、音量加大）
            path = text_to_speech(text, voice_type=PERSONA_VOICE.get(UI_STATE["personality"]),
                                  **REMINDER_TTS_PARAMS)
            if path:
                tsr, arr = _wav_to_numpy(path)
                if tsr != sr and len(arr):
                    n = int(len(arr) * sr / tsr)
                    arr = np.interp(np.linspace(0, len(arr) - 1, n),
                                    np.arange(len(arr)), arr).astype(np.int16)
                pieces.append(arr)
        return (sr, np.concatenate(pieces))
    except Exception as e:
        print(f"提醒音频生成失败: {e}")
        return None

def poll_reminder_events(chat, is_muted, conv_id):
    """轮询调度队列：把到点的提醒推进聊天窗，播放提示音/语音，刷新提醒与记忆面板"""
    if not reminder_queue:
        # 空转时也顺带刷新记忆面板（后台记忆抽取是异步的，面板靠这里追平）
        return gr.update(), gr.update(), gr.update(), gr.update(value=get_memory_panel_html())
    fired = []
    while reminder_queue:
        fired.append(reminder_queue.popleft())
    if not chat:
        chat = []
    for item in fired:
        chat = chat + [{"role": "assistant", "content": item["message"]}]
    save_conversation(conv_id, chat)
    audio = build_reminder_audio(fired[-1]["message"], is_muted)
    return chat, audio, gr.update(value=get_reminders_panel_html()), gr.update(value=get_memory_panel_html())

def get_reminders_panel_html():
    now = time.time()
    now_dt = datetime.fromtimestamp(now)
    rows = []
    for r in reminder_store.pending(now)[:8]:
        desc = describe_reminder(r, now_dt)
        if r.get("repeat"):
            extra = f"🔁 {repeat_desc(r['repeat'])}"
        else:
            delta = int(r["time"] - now)
            extra = "马上" if delta < 60 else (
                f"{delta // 60}分钟后" if delta < 3600
                else f"{delta // 3600}小时{(delta % 3600) // 60}分后")
        rows.append(f"""
        <div style="padding:4px 0 4px 16px;position:relative;border-left:2px solid #FFB74D;margin-bottom:2px;font-size:15px;">
            <span style="position:absolute;left:-5px;top:9px;width:8px;height:8px;border-radius:50%;background:#FFB74D;"></span>
            ⏰ {desc}<span style="color:#aaa;font-size:12px;margin-left:6px;">{extra}</span>
        </div>""")
    stats = reminder_store.stats(now)
    stats_line = ""
    if stats["total"]:
        stats_line = (f"<div style='margin-top:6px;font-size:13px;color:#8B5E34;'>"
                      f"最近30天：提醒{stats['total']}次 · 办妥{stats['done']}次 · 完成率{stats['rate']}%</div>")
    notices = ""
    if reminder_store.notices:
        items = "".join(
            f"<div style='font-size:13px;color:#C62828;'>📣 {n['thing']}"
            f"（{datetime.fromtimestamp(n['time']).strftime('%m-%d %H:%M')}）还没办，已请家人留意</div>"
            for n in reversed(reminder_store.notices[-3:]))
        notices = f"<div style='margin-top:6px;'>{items}</div>"
    if not rows and not notices:
        return "<div style='color:#8B5E34;padding:8px;text-align:center;'>暂无提醒，可以说“五分钟后提醒我喝水”</div>"
    return "".join(rows) + stats_line + notices

# 启动调度线程（daemon：随主进程退出）
Thread(target=reminder_worker, daemon=True).start()

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
    /* 任务5：原全隐藏样式保留（备用），新增小型语音条样式——
       Gradio Audio 原生自带播放/暂停按钮 + 可拖动进度条 + 时长显示 */
    .audio-hidden { height: 0 !important; overflow: hidden !important; margin: 0 !important; padding: 0 !important; position: absolute !important; opacity: 0 !important; pointer-events: none !important; }
    .audio-bar { margin: 4px 0 6px !important; padding: 2px 6px !important;
                 border: 1px solid #E0D3C0 !important; border-radius: 10px !important;
                 background: #FFF9F0 !important; }
    .audio-bar audio { height: 40px !important; }
    .audio-bar .download-button { display: none !important; }
"""

# ========== 全局变量 ==========
last_user_interaction_time = time.time()
TEMP_AUDIO_DIR = tempfile.mkdtemp(prefix="xiaoyi_audio_")
os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)

# 启动时备份历史文件
backup_file(HISTORY_FILE)
backup_file(MEMORY_EVENTS_FILE)
backup_file(MEMORY_BANK_FILE)

# 引入 prompt 定义（简短版，与训练数据一致）
PROMPT_PRACTICAL = "你是小忆，一位踏实稳重、真诚靠谱的晚辈。说话温和耐心，用简短自然的口语。"
PROMPT_HUMOR = "你是小忆，一位活泼开朗、风趣俏皮的晚辈。语气轻松欢快，说话带一点小俏皮。"
PROMPT_CARING = "你是小忆，一位温柔细腻、共情暖心的晚辈。语气温柔舒缓，善于倾听与安抚。"

# ========== 主动关怀引擎（五信号→三道闸仲裁→记忆话题→人格话术→反馈闭环，见 proactive.py） ==========
# 与 reminder.py 的分工：reminder 管"到点必响、确认闭环"的确定性事务；
# proactive 管"可开可不开"的弹性陪伴开口，两者不重复打扰。
proactive.init(
    llm=deepseek_chat_msgs,
    safety=ai_services.safety_filter,
    persona_prompts={"踏实务实": PROMPT_PRACTICAL, "风趣幽默": PROMPT_HUMOR, "暖心知心": PROMPT_CARING},
    get_persona=lambda: UI_STATE["personality"],
    get_call_name=lambda: user_memory["call_name"],
    last_interaction=lambda: last_user_interaction_time,
    get_profile=lambda: memory_bank.profile_snapshot(),
    get_keyword_events=lambda: user_memory["events"],
    save_keyword_events=lambda evts: save_memory_events(evts),
    get_emotion_log=load_emotion_log,
    get_pending_reminders=lambda: reminder_store.pending(),
    get_awaiting_reminders=lambda: reminder_store.awaiting_confirm(),
    get_last_medication_time=lambda: user_memory["last_medication_time"],
    touch_medication_time=lambda: user_memory.__setitem__("last_medication_time", time.time()),
    weather_provider=None,   # 钩子保留：接入天气接口后，信号5的降温/雨雪×慢病关怀生效
)

def get_call_name(surname, gender):
    s = surname.strip() if surname else ""
    if s:
        return f"{s}{'奶奶' if gender == '女' else '爷爷'}"
    return "奶奶" if gender == '女' else "爷爷"

def fix_address(reply, call_name, gender, surname=""):
    """性别称呼保险丝：模型叫错（爷爷↔奶奶）时确定性纠正。
    只处理"呼语"用法（错称紧跟您/你/标点、姓+错称），负向断言保护
    "您爷爷""老爷爷""邻居李爷爷"等指代他人的用法。"""
    if not reply:
        return reply
    wrong = "爷爷" if gender == "女" else "奶奶"
    if wrong not in reply or call_name == wrong:
        return reply
    # 1) 错称+您/你："爷爷您歇着" → "李奶奶您歇着"
    reply = re.sub(rf"(?<![您我他她它]){wrong}(?=[您你])", call_name, reply)
    # 2) 姓+错称+呼号："李爷爷，" → "李奶奶，"（指代用法已被断言排除）
    s = (surname or "").strip()
    if s:
        reply = re.sub(rf"(?<![您我他她它邻位说念老]){s}{wrong}(?=[，。！？!?])",
                       call_name, reply)
    # 3) 句界后的独立呼语："...！爷爷，您..." → "...！李奶奶，您..."
    #    前瞻标点保证只匹配纯呼语，"从前，爷爷和孙子"这类叙述不误伤
    reply = re.sub(rf"((?:^|[。！？～，\n])\s*){wrong}(?=[，。！？!?])",
                   rf"\g<1>{call_name}", reply)
    return reply

def check_active_trigger():
    """30s 轮询入口：委托 proactive 引擎。旧版关键词猜测/健康跟进逻辑已迁入
    proactive.py 信号层（_sig_health_followup），并按理念文档补齐冷却、
    免打扰、动机阈值与反馈闭环。"""
    try:
        return proactive.tick()
    except Exception as e:
        print(f"主动关怀引擎异常: {e}")
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
with gr.Blocks(title="小忆陪伴助手") as demo:
    ready = gr.State(False)
    surname = gr.State("")
    gender = gr.State("女")
    is_muted = gr.State(True)
    personality = gr.State("踏实务实")
    current_conv_id = gr.State(None)

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
                # 记忆（事件流→记忆库；画像已改为后台导出 用户画像.txt，任务3）
                gr.Markdown("🧠 **记忆**")
                events_display = gr.HTML(value=get_memory_panel_html(), elem_classes="event-box")
                # 提醒事项
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
            with gr.Column(scale=4):
                gr.HTML('''<div class="top-bar"><div>🌸 小忆</div><div class="status-online"><div class="green-dot"></div> 陪伴中</div></div>''')
                # 任务5补充：聊天中随时切换人格（同时切换音色），免刷新页面
                persona_switch = gr.Dropdown(choices=["踏实务实", "风趣幽默", "暖心知心"],
                                             value="踏实务实", label="小忆的性格（音色随之切换）",
                                             container=False, interactive=True)
                chat = gr.Chatbot(value=[], height=520, show_label=False)
                # 语音播放器（隐藏：所有语音输出只播放声音，不显示可见组件）
                audio_output = gr.Audio(autoplay=True, visible=True, show_label=False,
                                        elem_classes="audio-hidden")
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
                mic_input = gr.Textbox(visible=False, elem_id="mic-data", show_label=False)  # 录音数据回传通道（base64 WAV）
                gr.HTML('<div class="footer-tip">💖 小忆会记住你说过的每一句话</div>')

    # 事件绑定
    def enter_chat(s, g, p, muted):
        surname_val = s.strip() if s else ""
        gender_val = g
        call_name = get_call_name(surname_val, gender_val)
        user_memory["call_name"] = call_name
        UI_STATE["personality"] = p if p in ("踏实务实", "风趣幽默", "暖心知心") else "踏实务实"
        welcome_msg = f"您好{call_name}！我是小忆，很高兴能陪伴您～"
        greet = ai_services.build_cross_session_greeting(memory_bank, call_name)
        if greet:
            welcome_msg += "\n" + greet
        chat_history = [{"role": "assistant", "content": welcome_msg}]
        new_id = str(int(time.time() * 1000))
        # 任务5修复：欢迎语音也用当前人格音色（原来固定默认音色，导致选不同人格听感相同）
        audio_path = None if muted else text_to_speech(
            welcome_msg, voice_type=PERSONA_VOICE.get(UI_STATE["personality"]))
        global last_user_interaction_time
        last_user_interaction_time = time.time()
        return True, surname_val, gender_val, p, gr.update(visible=False), gr.update(visible=True), chat_history, new_id, audio_path, gr.update(choices=get_conversation_list_display()), get_memory_panel_html(), get_reminders_panel_html(), gr.update(value=UI_STATE["personality"])

    btn.click(enter_chat, [s_ipt, g_ipt, p_ipt, is_muted],
              [ready, surname, gender, personality, setup_view, main_view, chat, current_conv_id, audio_output, history_dropdown, events_display, reminders_display, persona_switch])

    # 任务5补充：聊天中切换人格 → 立即生效（下一句回复的提示词与音色同步切换）
    def switch_personality(p):
        UI_STATE["personality"] = p if p in ("踏实务实", "风趣幽默", "暖心知心") else "踏实务实"
        return UI_STATE["personality"]
    persona_switch.change(switch_personality, [persona_switch], [personality])

    # 文字/语音交互后刷新事件与提醒面板
    def refresh_panels(*args):
        return get_memory_panel_html(), get_reminders_panel_html()

    # 文字提交（语音播报放最后：文字先显示，再合成音频）
    txt.submit(chat_response, [txt, chat, surname, gender, is_muted, personality, current_conv_id, history_dropdown],
               [txt, chat, audio_output, current_conv_id, history_dropdown]).then(refresh_panels, outputs=[events_display, reminders_display]).then(play_reply_audio, [chat, is_muted], [audio_output])
    send.click(chat_response, [txt, chat, surname, gender, is_muted, personality, current_conv_id, history_dropdown],
               [txt, chat, audio_output, current_conv_id, history_dropdown]).then(refresh_panels, outputs=[events_display, reminders_display]).then(play_reply_audio, [chat, is_muted], [audio_output])

    # 快捷按钮
    for btn_q, q_text in [(q1, "我今天挺好的"), (q2, "有点想你了"), (q3, "身体有点不舒服"), (q4, "讲讲以前的事")]:
        btn_q.click(lambda t=q_text: t, None, txt).then(
            chat_response, [txt, chat, surname, gender, is_muted, personality, current_conv_id, history_dropdown],
            [txt, chat, audio_output, current_conv_id, history_dropdown]
        ).then(refresh_panels, outputs=[events_display, reminders_display]).then(play_reply_audio, [chat, is_muted], [audio_output])

    # 麦克风 - 一键录音：点一下开始，再点一下结束并发送（JS 直接采集，不弹录音组件）
    mic_btn.click(fn=None, inputs=None, outputs=[mic_status, mic_input], js=MIC_RECORD_JS)
    mic_input.change(process_mic_data, [mic_input, chat, surname, gender, is_muted, personality, current_conv_id, history_dropdown],
                     [txt, chat, audio_output, current_conv_id, history_dropdown]).then(
        refresh_panels, outputs=[events_display, reminders_display]
    ).then(play_mic_reply_audio, [chat, is_muted, mic_input], [audio_output])

    # 静音
    mute_btn.click(toggle_mute, [is_muted], [is_muted, mute_btn, audio_output])

    # 新建对话
    new_conv_btn.click(new_conversation, [], [chat, current_conv_id, history_dropdown]).then(refresh_panels, outputs=[events_display, reminders_display])

    # 加载历史
    history_dropdown.change(load_conversation_by_id, [history_dropdown], [chat, current_conv_id])

    # 删除对话
    delete_btn.click(delete_selected_conversation, [history_dropdown, history_dropdown], [history_dropdown, history_dropdown])

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

    # 定时器
    gr.Timer(30).tick(inject_proactive_message, [chat, is_muted, current_conv_id], [chat, audio_output])
    gr.Timer(30).tick(clean_old_audio_files, [])
    # 提醒调度轮询：到点的提醒推进聊天窗 + 播报 + 刷新提醒/记忆面板
    gr.Timer(15).tick(poll_reminder_events, [chat, is_muted, current_conv_id],
                      [chat, audio_output, reminders_display, events_display])

if __name__ == "__main__":
    # 预览/外部托管时可经 --port / --host 或 PORT/HOST 环境变量覆盖端口与监听地址
    import argparse
    _ap = argparse.ArgumentParser()
    _ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 7861)))
    _ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    _args = _ap.parse_args()
    demo.launch(server_name=_args.host, server_port=_args.port, share=False, css=custom_css)
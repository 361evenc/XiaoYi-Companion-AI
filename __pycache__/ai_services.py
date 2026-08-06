# -*- coding: utf-8 -*-
"""
ai_services.py —— 小忆「AI 大脑」服务层

集中封装需要调用大模型的能力，目前包含：
  - extract_entities()   实体抽取（任务1，已实现）
  - _keyword_extract()   关键词回退方案（无 API key 时自动启用）

后续任务（2~10）会在这里逐步补充：
  - 主动触发规则评估
  - 记忆检索与注入
  - 跨会话记忆加载
  - 话题切换判断
  - 输出安全过滤
  - 记忆新陈代谢（过期/永久保留判断）
  - 情绪分析
  - 回忆录生成

设计原则：
  1. 所有函数都不依赖本地 Qwen3B 模型，可独立测试、可离线回退。
  2. DeepSeek 调用失败 / 无 key 时，自动降级到本地规则，保证主程序不崩。
  3. 统一事件结构，向后兼容现有 UI（get_recent_events_display）。
"""

import os
import json
import time
import random

# ========== DeepSeek 配置（通过环境变量注入，不在代码里写密钥） ==========
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
# 默认开启 AI 抽取；设为 0 可强制走关键词回退（离线测试/省钱）
AI_EXTRACT_ENABLED = os.getenv("XIAOYI_AI_EXTRACT", "1") == "1" and bool(DEEPSEEK_API_KEY)

# ========== 事件类型与永久记忆规则 ==========
EVENT_TYPES = {
    "health", "medication", "item", "family", "habit",
    "emotion", "allergy", "contact", "todo", "other",
}
# 这些类型属于「必须永远记住」：过敏 / 家人联系方式 / 常用药
PERMANENT_TYPES = {"allergy", "contact", "medication"}

# ========== DeepSeek 调用封装 ==========
def _call_deepseek(system_prompt, user_prompt, expect_json=True, temperature=0.2):
    """调用 DeepSeek Chat（OpenAI 兼容）。失败返回 None。"""
    if not DEEPSEEK_API_KEY:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        kwargs = dict(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
        )
        if expect_json:
            kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content
    except Exception as e:
        print(f"[ai_services] DeepSeek 调用失败: {e}")
        return None


_EXTRACT_SYSTEM = """你是一个为老年陪伴AI「小忆」服务的实体抽取器。
任务：从用户（老人）说的话里，提取值得长期记住的结构化信息。

只输出 JSON，格式严格为：
{"events":[{"type": "...", "content": "...", "importance": "...", "attrs": {...}}]}

type 取值（只能选一个）：
  health    健康不适（如高血压、膝盖疼、头晕）
  medication 用药（如降压药、阿司匹林、中药）
  item      物品位置（如老花镜放哪了、降压药在床头柜）
  family    家人（儿子、闺女、孙子、老伴等）
  habit     生活习惯（浇花、散步、打太极）
  emotion   情绪（难过、孤单、高兴、心烦）
  allergy   过敏（对什么过敏，必须永久记住）
  contact   联系方式（家人电话、手机号，必须永久记住）
  todo      待办/提醒（attrs 可含 time 字段，如"明天下午三点体检"）
  other     其他值得记的

importance：
  "permanent" 必须永远记住（过敏、家人电话、常用药）
  "normal"    普通记忆

content：用老人原话里的简短短语，不要改写、不要编造。
attrs：能提取到时间/地点时填入，没有就空对象 {}。
没有可提取信息时，events 为空数组 []。
"""


def extract_entities(text):
    """从一句话中抽取结构化记忆事件。

    返回 list[dict]，每个事件含：
      id, type, content, time, status, importance, attrs
    优先走 DeepSeek；失败/未配置时回退关键词方案。
    """
    text = str(text).strip()
    now = time.time()
    events = []

    if AI_EXTRACT_ENABLED and text:
        raw = _call_deepseek(_EXTRACT_SYSTEM, f"请从下面这段话中提取关键信息：\n{text}")
        if raw:
            events = _parse_extract_json(raw, now)

    if not events:
        events = _keyword_extract(text, now)

    # 统一后处理：补全字段、强制永久记忆类型
    for e in events:
        e["type"] = e.get("type", "other")
        if e["type"] not in EVENT_TYPES:
            e["type"] = "other"
        e.setdefault("content", "")
        e.setdefault("attrs", {})
        e.setdefault("time", now)
        e.setdefault("status", "active")
        if e["type"] in PERMANENT_TYPES:
            e["importance"] = "permanent"
        else:
            e.setdefault("importance", "normal")
        e["id"] = f"{int(now * 1000)}_{abs(hash(e['type'] + e['content']))}"
    return [e for e in events if e["content"]]


def _parse_extract_json(raw, now):
    """解析 DeepSeek 返回的 JSON，失败返回空列表。"""
    try:
        data = json.loads(raw)
        items = data.get("events", []) if isinstance(data, dict) else data
        if not isinstance(items, list):
            return []
        out = []
        for it in items:
            if not isinstance(it, dict):
                continue
            out.append({
                "type": it.get("type", "other"),
                "content": str(it.get("content", "")).strip(),
                "importance": it.get("importance", "normal"),
                "attrs": it.get("attrs", {}) or {},
                "time": now,
                "status": "active",
            })
        return out
    except Exception as e:
        print(f"[ai_services] 解析抽取结果失败: {e}")
        return []


# ========== 关键词回退方案（与原有逻辑等价，并补充过敏/联系方式） ==========
_KEYWORD_MAP = {
    "health": ["头疼", "头晕", "膝盖疼", "膝盖", "腰疼", "高血压", "不舒服", "难受", "乏力", "胸闷"],
    "medication": ["降压药", "吃药", "阿司匹林", "中药", "降糖药", "止疼药"],
    "item": ["老花镜", "钥匙", "遥控器", "手机", "血压计", "医保卡"],
    "family": ["儿子", "闺女", "孙子", "孙女", "老伴", "外孙"],
    "habit": ["浇花", "买菜", "散步", "打太极", "下棋", "遛弯"],
    "emotion": ["难过", "孤单", "想家了", "高兴", "心烦", "憋屈", "心里闷"],
    "allergy": ["过敏"],
    "contact": ["电话", "手机号", "号码"],
}


def _keyword_extract(text, now):
    """无 API key 时的关键词回退抽取。"""
    events = []
    for t, kws in _KEYWORD_MAP.items():
        for kw in kws:
            if kw in text:
                events.append({
                    "type": t,
                    "content": kw,
                    "time": now,
                    "status": "active",
                    "importance": "permanent" if t in PERMANENT_TYPES else "normal",
                    "attrs": {},
                })
    return events


# ========== 主动触发规则表（任务2） ==========
# 把写死的 if/else 改为可扩展的规则表。每条规则是一个谓词，接收 ctx、返回消息或 None。
# ctx 字段：events(记忆事件list), last_medication_time, call_name, now, cared_ids(list)
# 规则可在此自由增删；后续「天气变冷」「情绪低落」等规则都可按同一结构接入。
PROACTIVE_GLOBAL_COOLDOWN = 600  # 全局冷却（秒）：两次主动消息最小间隔，任务8 频率控制。默认10分钟避免打扰老人；设为0可关闭。

PROACTIVE_RULES = [
    {
        "id": "medication_reminder",
        "desc": "用药后约5分钟主动提醒吃药",
        "evaluate": lambda c: _rule_medication(c),
    },
    {
        "id": "health_followup",
        "desc": "健康不适提及约1分钟后主动回访",
        "evaluate": lambda c: _rule_health_followup(c),
    },
]


def _rule_medication(c):
    if c["last_medication_time"] > 0 and (c["now"] - c["last_medication_time"]) > 300:
        c["last_medication_time"] = c["now"]
        return random.choice([
            f"{c['call_name']}，该吃药啦，我帮您记着呢。",
            f"{c['call_name']}，药吃了没？可别忘了哦。",
        ])
    return None


def _rule_health_followup(c):
    for e in c["events"]:
        if e.get("type") in ("health", "medication") and e.get("status") == "active" \
                and (c["now"] - e["time"]) > 60:
            e["status"] = "cared"
            if e.get("id") is not None:
                c["cared_ids"].append(e["id"])
            return f"{c['call_name']}，您刚才说的{e['content']}，现在好点了吗？"
    return None


def evaluate_proactive_trigger(ctx):
    """按规则表顺序评估，返回第一条命中的消息；否则 None。
    会就地修改 ctx（last_medication_time / 事件 status / cared_ids）。
    """
    for rule in PROACTIVE_RULES:
        msg = rule["evaluate"](ctx)
        if msg:
            return msg
    return None


# ========== 任务3：记忆检索与注入 ==========
EVENT_LABELS = {
    "health": "健康", "medication": "用药", "item": "物品", "family": "家人",
    "habit": "习惯", "emotion": "情绪", "allergy": "过敏", "contact": "联系方式",
    "todo": "待办", "other": "其他",
}
# 各类型在用户话里的触发词，用于离线相关性初筛
TYPE_TRIGGERS = {
    "health": ["疼", "痛", "不舒服", "难受", "身体", "病", "晕", "闷", "乏", "不好受"],
    "medication": ["药", "吃", "喝", "剂", "片"],
    "item": ["放", "哪", "找", "丢", "在哪", "位置"],
    "family": ["儿", "女", "孙", "老伴", "家", "闺女", "儿子"],
    "habit": ["惯", "常", "每", "喜", "爱", "喜欢"],
    "emotion": ["心", "闷", "难过", "孤单", "高兴", "烦", "乐", "开心"],
}


def _retrieve_via_deepseek(query, events, k):
    """用 DeepSeek 做语义检索，返回相关事件列表（仅启用时）。"""
    compact = [{"id": e.get("id"), "type": e.get("type"), "content": e.get("content")}
               for e in events if e.get("status") != "archived"]
    if not compact:
        return None
    prompt = (f"用户刚说：{query}\n下面是小忆记住的往事列表，"
              f"请返回最相关的至多{k}条的 id。无关则返回空数组。")
    raw = _call_deepseek(
        "你是记忆检索器。只从给定列表挑选与用户当前话题相关的记忆 id，返回 JSON: {\"ids\":[...]}。",
        prompt,
    )
    if not raw:
        return None
    try:
        ids = set(json.loads(raw).get("ids", []))
        return [e for e in events if e.get("id") in ids][:k]
    except Exception:
        return None


def retrieve_relevant_memory(query, events, k=4, now=None):
    """从全部记忆里挑出与当前话题相关的事件。
    优先 DeepSeek 语义检索；离线时用字符重叠 + 类型触发词 + 重要度打分。
    """
    now = now or time.time()
    if AI_EXTRACT_ENABLED:
        deep = _retrieve_via_deepseek(query, events, k)
        if deep is not None:
            return deep
    qchars = set(str(query))
    scored = []
    for e in events:
        if e.get("status") == "archived":
            continue
        content = e.get("content", "")
        score = len(qchars & set(content)) * 3
        if any(t in str(query) for t in TYPE_TRIGGERS.get(e.get("type", ""), [])):
            score += 3
        imp = e.get("importance", "normal")
        if imp == "permanent":
            score += 4
        elif imp == "high":
            score += 2
        age = now - e.get("time", now)
        if 0 <= age < 7 * 86400:
            score += max(0, 1 - age / (7 * 86400))
        if score > 0:
            scored.append((score, e))
    scored.sort(key=lambda x: -x[0])
    return [e for _, e in scored[:k]]


def build_memory_injection(query, events, k=4):
    """生成可注入 system prompt 的「相关往事」文本块。"""
    rel = retrieve_relevant_memory(query, events, k=k)
    if not rel:
        return ""
    lines = "\n".join(f"- {e['content']}（{EVENT_LABELS.get(e['type'], '其他')}）" for e in rel)
    return f"\n【你可能记得的往事，可自然接话】\n{lines}"


# ========== 任务6：输出安全过滤 ==========
PROFANITY = ["妈的", "傻逼", "去你的", "滚蛋", "废物", "混蛋"]
COLD_PHRASES = ["关我什么事", "不关我事", "别问我", "烦死了", "滚", "关你屁事"]

_SAFETY_SYSTEM = (
    "你是小忆的「说话安全审核员」。小忆是陪伴老人的温柔晚辈，人设：{personality}。\n"
    "判断下面这段话作为小忆的回复是否合适：\n"
    "1) 是否含脏话/不文明用语；2) 是否给出错误/危险的医疗建议（应强调以医生为准）；"
    "3) 语气是否符合温柔晚辈人设。\n"
    "只返回 JSON：{{\"safe\": true/false, \"reason\": \"简短原因\", "
    "\"safe_reply\": \"若不合适，给一句符合人设的安全替换回复\"}}"
)


def safety_filter(text, personality="踏实务实", call_name="奶奶"):
    """检查小忆的回复是否安全合适；不安全则返回安全替换。"""
    text = str(text)
    if any(w in text for w in PROFANITY):
        return {"safe": False, "reason": "含不文明用语",
                "safe_reply": f"{call_name}，我说话不注意，您别往心里去，咱换个话题吧。"}
    if any(w in text for w in COLD_PHRASES):
        return {"safe": False, "reason": "语气不符合温柔晚辈人设",
                "safe_reply": f"{call_name}，这话我说得不合适，您别往心里去。咱换个话题吧。"}
    if AI_EXTRACT_ENABLED:
        raw = _call_deepseek(_SAFETY_SYSTEM.format(personality=personality),
                             f"请判断下面这段话是否合适以小忆（{personality}的晚辈）的身份说给老人听：\n{text}")
        if raw:
            try:
                r = json.loads(raw)
                if not r.get("safe", True):
                    return {"safe": False,
                            "reason": r.get("reason", ""),
                            "safe_reply": r.get("safe_reply",
                                               f"{call_name}，这话我说得不合适，您别往心里去。咱换个话题吧。")}
                return {"safe": True, "reason": "", "safe_reply": text}
            except Exception:
                pass
    return {"safe": True, "reason": "", "safe_reply": text}


# ========== 任务9：情绪分析 ==========
_EMOTION_SYSTEM = (
    "你是小忆的「情绪识别员」。分析老人这句话里的情绪，返回 JSON："
    "{\"label\": \"low/neutral/high\", \"score\": -1到1之间的数, \"note\": \"简短说明\"}。"
    "low=低落/孤单/难过，high=开心/愉快，neutral=平淡。"
)


def analyze_emotion(text, call_name="奶奶"):
    """分析老人一句话的情绪，返回 {label, score, note}。"""
    text = str(text)
    if AI_EXTRACT_ENABLED:
        raw = _call_deepseek(_EMOTION_SYSTEM, f"分析这句话里老人的情绪：\n{text}")
        if raw:
            try:
                r = json.loads(raw)
                return {"label": r.get("label", "neutral"),
                        "score": float(r.get("score", 0.0)),
                        "note": r.get("note", "")}
            except Exception:
                pass
    low_kw = ["难过", "孤单", "想家", "心烦", "闷", "憋屈", "难受", "伤心", "委屈", "心里不痛快"]
    high_kw = ["高兴", "开心", "乐", "欢喜", "舒心", "甜", "美滋滋"]
    if any(w in text for w in low_kw):
        return {"label": "low", "score": -0.6, "note": "情绪低落"}
    if any(w in text for w in high_kw):
        return {"label": "high", "score": 0.6, "note": "情绪愉悦"}
    return {"label": "neutral", "score": 0.0, "note": ""}


# ========== 任务5：话题切换 ==========
def suggest_topic_switch(events, call_name="奶奶"):
    """当老人反复说同一件事或冷场时，基于兴趣自然换话题。"""
    types_present = {e.get("type") for e in events}
    if "family" in types_present:
        return f"{call_name}，您上次说起家里人，最近他们咋样呀？有啥新鲜事没？"
    if "habit" in types_present:
        return f"{call_name}，您平时爱干的那些个事，最近还想着不？跟我念叨念叨。"
    if "emotion" in types_present:
        return f"{call_name}，咱聊点开心的，您小时候有啥好玩的事儿不？"
    return f"{call_name}，您平时喜欢听戏还是逛公园呀？咱聊点轻松的。"


# ========== 任务4：跨会话记忆加载 ==========
def build_cross_session_greeting(events, call_name="奶奶", now=None):
    """新会话/隔天打开时，基于昨天的记忆主动问候。无跨天记忆则返回 None。"""
    now = now or time.time()
    from datetime import datetime as _dt
    today_str = _dt.fromtimestamp(now).date().isoformat()
    prev = [e for e in events
            if e.get("status") != "archived"
            and _dt.fromtimestamp(e.get("time", 0)).date().isoformat() < today_str
            and e.get("type") in ("health", "medication", "family", "habit", "emotion", "allergy", "contact")]
    if not prev:
        return None
    prev.sort(key=lambda e: 0 if e.get("type") in ("health", "medication") else 1)
    picks = prev[:2]
    parts = []
    for e in picks:
        if e.get("type") == "medication":
            parts.append(f"{EVENT_LABELS.get(e['type'])}的事——{e['content']}，可得按时吃呀")
        elif e.get("type") == "health":
            parts.append(f"您前阵子说的{e['content']}，现在好点没")
        else:
            parts.append(f"您之前提的{e['content']}")
    body = "，".join(parts)
    return f"{call_name}，您来啦。{body}？"


# ========== 任务7：记忆新陈代谢 ==========
def prune_memories(events, now=None, trivia_ttl_days=7):
    """清理过期的日常琐事（保留 permanent / 近期 / 已归档不算）。返回 (保留后列表, 清理数)。"""
    now = now or time.time()
    kept, removed = [], 0
    for e in events:
        if e.get("importance") == "permanent":
            kept.append(e)
            continue
        age = now - e.get("time", now)
        if age > trivia_ttl_days * 86400 and e.get("status") in ("active", "noted", "cared"):
            removed += 1
            continue
        kept.append(e)
    return kept, removed


def forget_by_request(user_input, events, call_name="奶奶"):
    """老人说「忘了吧」时，删除匹配的记忆。返回 (保留后列表, 回复或None)。"""
    if AI_EXTRACT_ENABLED:
        compact = [{"id": e.get("id"), "type": e.get("type"), "content": e.get("content")}
                   for e in events if e.get("status") != "archived"]
        raw = _call_deepseek(
            "你是遗忘助手。用户想忘掉某些记忆，请从列表里挑出要删的 id。返回 JSON: {\"ids\":[...]}。",
            f"用户说：{user_input}\n记忆列表：{json.dumps(compact, ensure_ascii=False)}")
        if raw:
            try:
                ids = set(json.loads(raw).get("ids", []))
                kept = [e for e in events if e.get("id") not in ids]
                if len(kept) < len(events):
                    return kept, f"{call_name}，您说的不记得了，我已经帮您忘掉啦。"
            except Exception:
                pass
    # 离线兜底：含遗忘意图且事件内容与输入有重叠则删（permanent 不删）
    if any(k in str(user_input) for k in ("忘", "删", "别记", "不记", "去掉", "甭记")):
        qchars = set(str(user_input))
        kept, removed = [], 0
        for e in events:
            if e.get("status") == "archived" or e.get("importance") == "permanent":
                kept.append(e)
                continue
            if len(qchars & set(e.get("content", ""))) >= 1:
                removed += 1
                continue
            kept.append(e)
        if removed:
            return kept, f"{call_name}，您说的不记得了，我已经帮您忘掉啦。"
    return events, None


# ========== 任务10：我的小传回忆录 ==========
def build_biography(events, call_name="奶奶"):
    """把长期记忆整合成一段温暖的小传/回忆录文字。优先 DeepSeek 整合，离线拼装。"""
    evs = [e for e in events if e.get("status") != "archived"]
    if not evs:
        return f"{call_name}，咱们相处时间还短，等您多跟我说说自己的事，我就能给您写小传啦～"
    if AI_EXTRACT_ENABLED:
        compact = [{"type": e.get("type"), "content": e.get("content"), "importance": e.get("importance")}
                   for e in evs]
        raw = _call_deepseek(
            "你是小忆的「回忆录撰写员」。请用温暖、口语化、像晚辈唠嗑的语气，把下面这些关于老人的记忆，"
            "写成一段 150 字以内的『我的小传』，突出家人、习惯、健康、重要时刻。不要编造，只输出正文。",
            f"记忆列表：{json.dumps(compact, ensure_ascii=False)}")
        if raw:
            return raw.strip()
    # 离线拼装
    groups = {}
    for e in evs:
        groups.setdefault(e.get("type"), []).append(e.get("content"))
    parts = []
    for t in ["family", "habit", "health", "medication", "item", "emotion", "contact", "allergy", "todo"]:
        if groups.get(t):
            items = "、".join(groups[t][:3])
            parts.append(f"{EVENT_LABELS.get(t, t)}：{items}")
    if not parts:
        return f"{call_name}，您跟我说的话还不多，等以后多聊聊，我给您写段小传～"
    return f"{call_name}的小传：\n" + "\n".join(parts)


if __name__ == "__main__":
    # 简单自测（无 key 时走关键词回退）
    sample = "我有高血压，每天吃降压药，对阿司匹林过敏，儿子电话13800138000，今天膝盖疼"
    print(json.dumps(extract_entities(sample), ensure_ascii=False, indent=2))

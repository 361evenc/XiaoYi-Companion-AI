# -*- coding: utf-8 -*-
"""
ai_services.py —— 小忆「AI 大脑」服务层（第二优先找回版）
==========================================================
从 8 月版本（origin/main，commit 33a8811）搬回并适配 9 月新架构的 4+2 项能力：

  1. safety_filter()                 输出安全过滤（任务6）
  2. suggest_topic_switch()          重复/冷场时的话题切换（任务5）
  3. build_biography()               "我的小传"回忆录（任务10）
  4. analyze_emotion()               情绪分析（任务9；日志落盘/心情曲线在 app.py）
  5. build_cross_session_greeting()  跨会话主动问候（任务4，顺带找回）
  6. forget_by_request()             "忘了吧"就真删记忆（任务7，顺带找回）

与 8 月版的差异（适配 9 月重构后的架构）：
  - 数据源从"关键词事件表"升级为 memory_bank（事件流→记忆库→画像三级），
    旧事件表仅作兜底；记忆检索注入、主动触发规则、记忆新陈代谢已由
    memory_bank / reminder.py / 遗忘曲线接管，不再搬回旧实现。
  - DeepSeek 统一走 app.py 注入的 llm callable（主备 key 轮询、超时受控），
    不再单独维护 OpenAI client；无 key / 调用失败一律离线规则回退，主程序不崩。
  - safety_filter 不再每轮调 LLM（Bug⑥"回复慢"的教训）：
    脏话/冷漠语气走本地规则（毫秒级）；仅当回复含医疗风险措辞时才调 LLM 审核。
  - analyze_emotion 默认离线关键词通道（零延迟记日志），LLM 通道保留可选。

设计约束：仅依赖标准库；LLM 以 init(llm_callable) 注入，无 LLM 也能跑。
"""

import json
import re
import time

# ---------------------------------------------------------------- LLM 注入

_llm = None  # callable(messages:list[dict], max_tokens:int, timeout:int) -> str


def init(llm_callable):
    """由 app.py 注入 deepseek_chat_msgs（主备 key 轮询版）。"""
    global _llm
    _llm = llm_callable


def _ask_llm(system_prompt, user_prompt, max_tokens=200):
    """统一 LLM 调用：失败/未注入返回空串。"""
    if _llm is None:
        return ""
    try:
        return _llm([{"role": "system", "content": system_prompt},
                     {"role": "user", "content": user_prompt}],
                    max_tokens=max_tokens, timeout=10) or ""
    except Exception as e:
        print(f"[ai_services] LLM 调用失败: {e}")
        return ""


def _parse_json_obj(raw):
    m = re.search(r"\{.*\}", str(raw or ""), re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


# ---------------------------------------------------------------- 任务6：输出安全过滤

PROFANITY = ["妈的", "傻逼", "去你的", "滚蛋", "废物", "混蛋"]
COLD_PHRASES = ["关我什么事", "不关我事", "别问我", "烦死了", "关你屁事", "爱听不听"]

# 医疗风险措辞：小忆的回复命中才调 LLM 审核（普通聊天零额外延迟，保住 Bug⑥ 的成果）
MEDICAL_RISK_RE = re.compile(
    r"停药|别吃药|不用吃药|不用吃.{0,3}药|加量|减量|多吃[两几1-9]?片|少吃点药"
    r"|偏方|秘方|根治|包好|包治|不用去医院|别去医院|不用看医生|我帮你诊断|你这是得了")

_SAFETY_SYSTEM = (
    "你是小忆的「说话安全审核员」。小忆是陪伴老人的温柔晚辈，人设：{personality}。\n"
    "判断下面这段话作为小忆对老人说的回复是否合适：\n"
    "1) 是否含脏话/不文明/冷漠用语；2) 是否给出错误/危险的医疗建议"
    "（任何调整药量、替代就医、打包票的说法都不合适，应强调以医生为准）；\n"
    "3) 语气是否符合温柔晚辈人设。\n"
    "只返回 JSON：{{\"safe\": true/false, \"reason\": \"简短原因\", "
    "\"safe_reply\": \"若不合适，给一句符合人设的安全替换回复\"}}"
)


def safety_filter(text, personality="踏实务实", call_name="奶奶"):
    """检查小忆的回复是否安全合适；不安全返回安全替换。返回 {"safe","reason","safe_reply"}"""
    text = str(text or "")
    fallback = f"{call_name}，这话我说得不合适，您别往心里去，咱换个话题吧。"
    # 1) 本地规则（毫秒级，每轮都跑）
    if any(w in text for w in PROFANITY):
        return {"safe": False, "reason": "含不文明用语", "safe_reply": fallback}
    if any(w in text for w in COLD_PHRASES):
        return {"safe": False, "reason": "语气不符合温柔晚辈人设", "safe_reply": fallback}
    # 2) 医疗风险措辞才走 LLM 审核（关键安全场景，一次调用值得）
    if _llm is not None and MEDICAL_RISK_RE.search(text):
        raw = _ask_llm(_SAFETY_SYSTEM.format(personality=personality),
                       f"请判断这段话是否适合小忆（{personality}的晚辈）说给老人听：\n{text}",
                       max_tokens=200)
        r = _parse_json_obj(raw)
        if r and not r.get("safe", True):
            return {"safe": False,
                    "reason": str(r.get("reason", "医疗建议风险"))[:60],
                    "safe_reply": str(r.get("safe_reply") or fallback)}
    return {"safe": True, "reason": "", "safe_reply": text}


# ---------------------------------------------------------------- 任务9：情绪分析

_EMOTION_SYSTEM = (
    "你是小忆的「情绪识别员」。分析老人这句话里的情绪，返回 JSON："
    "{\"label\": \"low/neutral/high\", \"score\": -1到1之间的数, \"note\": \"简短说明\"}。"
    "low=低落/孤单/难过，high=开心/愉快，neutral=平淡。"
)

# 老年人情绪表达特点：躯体化/口语化（"心里堵得慌""睡不着""不想吃饭"常是情绪信号）
_LOW_KW = ["难过", "孤单", "想家", "心烦", "憋屈", "难受", "伤心", "委屈", "不痛快",
           "心里堵", "心里闷", "闷得慌", "睡不着", "睡不好", "吃不下", "没胃口",
           "没精神", "没意思", "唉声叹气", "掉眼泪", "想哭"]
_HIGH_KW = ["高兴", "开心", "欢喜", "舒心", "美滋滋", "乐呵", "痛快", "精神好", "踏实"]


def analyze_emotion(text, call_name="奶奶", use_llm=False):
    """分析老人一句话的情绪，返回 {label, score, note}。
    默认离线关键词通道（每轮零延迟记日志）；use_llm=True 时走 DeepSeek 细判。"""
    text = str(text or "")
    if use_llm and _llm is not None:
        raw = _ask_llm(_EMOTION_SYSTEM, f"分析这句话里老人的情绪：\n{text}", max_tokens=80)
        r = _parse_json_obj(raw)
        if r:
            try:
                return {"label": r.get("label", "neutral"),
                        "score": max(-1.0, min(1.0, float(r.get("score", 0.0)))),
                        "note": str(r.get("note", ""))[:40]}
            except (TypeError, ValueError):
                pass
    if any(w in text for w in _LOW_KW):
        return {"label": "low", "score": -0.6, "note": "情绪低落"}
    if any(w in text for w in _HIGH_KW):
        return {"label": "high", "score": 0.6, "note": "情绪愉悦"}
    return {"label": "neutral", "score": 0.0, "note": ""}


# ---------------------------------------------------------------- 任务5：话题切换

# 冷场信号（老人主动说不知道聊啥）
COLD_RE = re.compile(r"没啥聊|没得聊|不知道说啥|不知道聊啥|聊啥呢|聊点什么|没话说|冷场|说点啥|好无聊|没意思")


def suggest_topic_switch(bank=None, events=None, call_name="奶奶"):
    """当老人反复说同一件事或冷场时，基于已记住的信息自然换话题。
    优先用 memory_bank 画像字段；旧关键词事件表兜底。"""
    fields = set()
    if bank is not None:
        try:
            fields = set(bank.profile_snapshot().keys())
        except Exception:
            fields = set()
    if not fields and events:
        tmap = {"family": "家庭", "habit": "习惯", "emotion": "情绪", "health": "健康",
                "medication": "用药", "item": "物品"}
        fields = {tmap.get(e.get("type")) for e in events} - {None}
    if "家庭" in fields:
        return f"{call_name}，您上次说起家里人，最近他们怎么样呀？有啥新鲜事没？"
    if "兴趣" in fields or "习惯" in fields:
        return f"{call_name}，您平时爱干的那些事，最近还做着不？跟我念叨念叨。"
    if "情绪" in fields:
        return f"{call_name}，咱聊点开心的——您年轻时候有啥好玩的事儿不？"
    return f"{call_name}，您平时喜欢听戏还是逛公园呀？咱聊点轻松的。"


# ---------------------------------------------------------------- 任务4：跨会话主动问候

def build_cross_session_greeting(bank, call_name="奶奶", now=None):
    """新会话/隔天打开时，基于昨天的记忆主动问候。无跨天记忆返回 None。
    健康/用药优先（先有用再有温度），最多提两条，不查户口。"""
    now = now or time.time()
    from datetime import datetime as _dt
    today0 = _dt.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    try:
        snap = bank.profile_snapshot(now)
    except Exception:
        return None
    prev = []
    for field, items in snap.items():
        for it in items:
            if it.get("ts", now) < today0:
                prev.append((field, it))
    if not prev:
        return None
    prev.sort(key=lambda x: (0 if x[0] in ("健康", "用药") else 1, -x[1].get("ts", 0)))
    parts = []
    for field, it in prev[:2]:
        value = it.get("value", "")
        if field == "健康":
            parts.append(f"您之前说的{value}，现在好点没")
        elif field == "用药":
            parts.append(f"{value}，可得按时吃呀")
        elif field == "家庭":
            parts.append(f"您之前提的{it.get('key', '')}的事，我一直记着")
        else:
            parts.append(f"您之前提的{value}")
    return f"{call_name}，您来啦。" + "，".join(parts) + "？"


# ---------------------------------------------------------------- 任务7：忘了吧（真删记忆）

# 遗忘意图：注意"忘不掉/忘不了"不含"忘掉吧/忘了吧"，不误伤
FORGET_INTENT_RE = re.compile(r"忘了吧|忘了它|忘掉吧|别记|甭记|不记了|不要记|删掉|去掉吧|清除")
# 清洗输入用的意图词（删掉后再取语义 bigram，避免"了/吧"类虚字造成误匹配）
_FORGET_STRIP_RE = re.compile(r"忘了吧|忘了它|忘掉吧|忘掉|别记|甭记|不记了|不要记|删掉|去掉吧|清除"
                              r"|把|的|了|吧|啊|呢|事|事情|关于|那些|这些|帮我|给我|你|我")


def _bigrams(text):
    text = _FORGET_STRIP_RE.sub("", str(text or ""))
    if not text:
        return set()
    if len(text) == 1:
        return {text}
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _text_grams(text):
    text = str(text or "")
    if len(text) < 2:
        return {text} if text else set()
    return {text[i:i + 2] for i in range(len(text) - 1)}


def forget_by_request(user_input, events, bank=None, call_name="奶奶"):
    """老人说「忘了吧」时，真删匹配的记忆（关键词事件 + memory_bank 事实/情节双层）。
    返回 (保留后的旧事件列表, 回复文案或None)。没匹配到内容返回 None（交正常聊天）。"""
    user_input = str(user_input or "")
    qgrams = _bigrams(user_input)
    if not qgrams:
        return events, None
    removed_total = 0

    # 1) memory_bank：LLM 挑 id（更准）优先，离线 bigram 兜底
    if bank is not None:
        def _hit(f):
            text = str(f.get("key", "")) + str(f.get("value", "")) + str(f.get("text", ""))
            return bool(_text_grams(text) & qgrams)
        try:
            removed_total += bank.forget(_hit)
        except Exception as e:
            print(f"[ai_services] 记忆库删除失败: {e}")

    # 2) 旧关键词事件表：bigram 匹配即删
    kept = []
    for e in events:
        if _text_grams(str(e.get("content", ""))) & qgrams:
            removed_total += 1
            continue
        kept.append(e)

    if removed_total:
        return kept, f"{call_name}，好，您说的这些我都忘掉啦，往后不提了。"
    return events, None


# ---------------------------------------------------------------- 任务10：我的小传回忆录

_FIELD_ORDER = ["家庭", "健康", "用药", "作息", "饮食", "兴趣", "习惯", "重要日期",
                "情绪", "物品", "其他"]


def build_biography(bank, call_name="奶奶"):
    """把长期记忆整合成一段温暖的小传。优先 DeepSeek 整合，离线按类别拼装。"""
    try:
        snap = bank.profile_snapshot() if bank else {}
        eps = bank.timeline(20) if bank else []
        refls = [r["text"] for r in (bank.reflections[:5] if bank else [])]
    except Exception:
        snap, eps, refls = {}, [], []
    if not snap and not eps:
        return f"{call_name}，咱们相处时间还短，等您多跟我说说自己的事，我就能给您写小传啦～"

    if _llm is not None:
        lines = []
        for field in _FIELD_ORDER:
            for it in (snap.get(field) or [])[:6]:
                v = it.get("value", "")
                k = it.get("key", "")
                lines.append(f"{field}-{k}：{v}" if k != v else f"{field}：{v}")
        for r in refls:
            lines.append(f"小忆的理解：{r}")
        raw = _ask_llm(
            "你是小忆的「回忆录撰写员」。请用温暖、口语化、像晚辈唠嗑的语气，把下面这些关于老人的记忆，"
            "写成一段 150 字以内的『我的小传』，突出家人、习惯、健康、重要时刻。不要编造，只输出正文。",
            "记忆列表：\n" + "\n".join(lines[:40]), max_tokens=300)
        if raw.strip():
            return raw.strip()

    # 离线拼装
    parts = []
    for field in _FIELD_ORDER:
        items = snap.get(field) or []
        if items:
            vals = [it.get("value", "") for it in items[:3] if it.get("value")]
            if vals:
                parts.append(f"{field}：{'、'.join(vals)}")
    if refls:
        parts.append("小忆的理解：" + "；".join(refls[:2]))
    if not parts:
        return f"{call_name}，您跟我说的话还不多，等以后多聊聊，我给您写段小传～"
    return f"📖 {call_name}的小传：\n" + "\n".join(parts)


# ---------------------------------------------------------------- 任务1：短期对话记忆摘要

MAX_SUMMARY_LEN = 800

_SUMMARY_SYSTEM = (
    "你是小忆的对话摘要员。把「旧摘要 + 新对话片段」合并成一份新摘要。\n"
    "必须保留：健康状况、用药、家人、情绪、关键事件、用户提到的人物/时间/地点。\n"
    "必须删除：寒暄、重复、无信息量内容。\n"
    "用第三人称、简洁中文，不超过 800 字。直接输出摘要正文，不要前缀标题。"
)


def summarize_conversation_segment(old_summary, new_segment, call_name="奶奶"):
    """增量合并摘要。new_segment: [{"role","content"},...]
    返回新摘要字符串；失败返回 ""（调用方据此保留原文不删除）"""
    if not new_segment:
        return old_summary or ""
    seg_text = "\n".join(
        f"{m['role']}：{m['content']}" for m in new_segment if m.get("content")
    )
    user_prompt = f"【旧摘要】\n{old_summary or '（无）'}\n\n【新对话片段】\n{seg_text}"
    raw = _ask_llm(_SUMMARY_SYSTEM, user_prompt, max_tokens=600)
    if not raw:
        return ""
    out = raw.strip()
    if len(out) > MAX_SUMMARY_LEN:
        out = out[:MAX_SUMMARY_LEN]
    return out

# -*- coding: utf-8 -*-
"""
proactive.py —— 小忆「主动关怀触发引擎」
=========================================
依据《主动对话理念.md》与《主动对话方案.md》实现，五层流水线：

  L1 信号层  五类触发信号（沉默太久/时间到了/有事没做完/情绪不对/外面变了）
             + 承继旧版 check_active_trigger 的健康/用药/情绪跟进；
  L2 仲裁层  三道闸——硬抑制（免打扰时段/"想静静"熔断/聊得正欢）、
             冷却（仅高优先级可破）、动机阈值（Inner Thoughts 机制的简化移植，
             见 Liu et al., CHI 2025, arXiv:2501.00383）；
  L3 话题层  P1 待办 → P2 健康 → P3 情感 → P4 闲聊；铁律：无记忆证据不开口、
             24h 不重复同类话题（ElliQ 部署教训：无画像驱动的主动=打扰，
             Broadbent et al. 2024, DOI:10.14283/jarlife.2024.2）；
  L4 生成层  人格 prompt + 记忆原话现场生成（ProCoT 实证 LLM 可直接生成
             主动话术，Deng et al., EMNLP-F 2023, arXiv:2305.13626），
             模板仅作离线兜底；与被动回复过同一道 safety_filter；
  L5 反馈层  用户 60s 内反应分为 接受/无视/拒绝/熔断，动态调整冷却系数 k
             与动机阈值（接受/拒绝标注驱动，方法同构于 ProactiveBench，
             Lu et al., arXiv:2410.12361）；频率个性化依据：老年人对主动性
             态度因人而异（UMD, CSCW 2021）。

设计约束：仅依赖标准库；LLM / 安全过滤 / 各数据源全部经 init() 注入，
未注入或调用失败时自动降级（模板话术 / 跳过该信号），引擎自身不崩。
"""

import json
import os
import re
import copy
import threading
import time
from datetime import datetime

# ---------------------------------------------------------------- 可调参数

SILENCE_THRESHOLD = 300        # 信号1：沉默多久算"太久"（秒）
EMO_SILENCE_THRESHOLD = 180    # 信号4：负面情绪后沉默多久主动关心（秒）
FOLLOWUP_MIN_AGE = 60          # 健康/情绪事件说完多久后跟进（秒，承继旧版）
TODO_STALE_AGE = 1800          # 提醒触发后多久仍未确认，算作"有事没做完"（秒）
USER_ACTIVE_GAP = 60           # 用户刚说过话的判定窗口：聊得正欢不插嘴（秒）
FEEDBACK_WINDOW = 60           # 主动开口后观察用户反应的窗口（秒）

BASE_COOLDOWN = 1800           # 基础冷却 30 分钟（理念文档）
QUIET_START, QUIET_END = 22, 8  # 免打扰时段 22:00–08:00（紧急提醒由 reminder.py 直走，不经本引擎）
DAILY_CAP = 8                  # 每日主动开口硬上限

K_MIN, K_MAX = 0.5, 2.0        # 冷却个性化系数 k 的上下限
THRESH_DEFAULT = 3.0           # 动机阈值（平时）
THRESH_COOLDOWN = 4.0          # 动机阈值（冷却期内，仅 P1/P2 可达）
THRESH_MAX = 4.5

# 作息锚点（信号2：时间到了）：(key, 时, 分, 话题类型, 餐名)
DAILY_ANCHORS = [
    ("breakfast", 7, 30, "meal", "早饭"),
    ("lunch", 11, 30, "meal", "午饭"),
    ("dinner", 17, 30, "meal", "晚饭"),
    ("bedtime", 21, 0, "bedtime", "睡觉"),
]
ANCHOR_WINDOW = 1800           # 锚点后 30 分钟内有效

# 内置阳历节日（农历节日放进 festival_dates.json，格式 {"09-25": "中秋节"} 按年维护）
SOLAR_FESTIVALS = {"01-01": "元旦", "05-01": "劳动节", "10-01": "国庆节", "12-22": "冬至"}

# 反馈分类词表
FUSE_RE = re.compile(r"想静静|想安静|让我静静|让我安静|别烦我|别吵我|别打扰我|今天别说话")
NEGATIVE_RE = re.compile(r"别烦|行了行|闭嘴|吵死|烦死|别说了|够了|别唠叨|啰嗦|真烦")

# 健康关键词（天气×慢病交叉用）
_HEALTH_BODY_RE = re.compile(r"膝盖|腿|腰|关节|颈椎|头|血压|心脏")

# 优先级 → 动机分（urgency）；新鲜度 ∈ {1.0, 0.3}
URGENCY = {"P1": 5.0, "P2": 4.0, "P3": 3.5, "P4": 3.0}

# 对话内跟进类信号：由事件状态机（active→cared）保证只说一次，
# 因此豁免"聊得正欢不插嘴"、24h 防重罚分与冷却提阈（Bug① 语义回归要求）
FOLLOWUP_SIGNALS = {"health_followup", "medication_guess"}

# ---------------------------------------------------------------- 注入点

_llm = None                # callable(messages, max_tokens, timeout) -> str
_safety = None             # callable(text, personality, call_name) -> {"safe","safe_reply"}
_P = {}                    # 数据提供者 callables
_PERSONA_PROMPTS = {}      # 人格 → system prompt
_STATE_FILE = "proactive_state.json"
_LOG_FILE = "proactive_log.json"
_FESTIVAL_FILE = "festival_dates.json"

_lock = threading.RLock()


def init(llm=None, safety=None, persona_prompts=None, state_file=None, log_file=None,
         festival_file=None, **providers):
    """注入 LLM、安全过滤与各数据源。providers 约定键：
    get_persona / get_call_name / last_interaction / get_profile /
    get_keyword_events / save_keyword_events / get_emotion_log /
    get_pending_reminders / get_awaiting_reminders /
    get_last_medication_time / touch_medication_time / weather_provider(可空)
    """
    global _llm, _safety, _PERSONA_PROMPTS, _STATE_FILE, _LOG_FILE, _FESTIVAL_FILE
    _llm = llm
    _safety = safety
    _PERSONA_PROMPTS = dict(persona_prompts or {})
    _P.update(providers)
    if state_file:
        _STATE_FILE = state_file
    if log_file:
        _LOG_FILE = log_file
    if festival_file:
        _FESTIVAL_FILE = festival_file


def _prov(name, default=None):
    fn = _P.get(name)
    if not callable(fn):
        return default
    try:
        v = fn()
        return default if v is None else v
    except Exception as e:
        print(f"[proactive] 数据源 {name} 异常: {e}")
        return default

# ---------------------------------------------------------------- 状态与日志

_DEFAULT_STATE = {
    "cooldown_k": 1.0,             # 冷却个性化系数（overt proactivity）
    "motivation_threshold": THRESH_DEFAULT,  # 动机阈值（covert proactivity）
    "last_proactive_ts": 0.0,
    "quiet_until": 0.0,            # "想静静"熔断截止
    "day": "",                     # 日计数归属日
    "fired_today": 0,
    "anchors_fired": [],           # 今日已响的作息锚点
    "env_fired": [],               # 今日已说的节日/天气/生日话题键
    "disable_p4_day": "",          # 被拒绝后当日关闭 P4
    "ignored_streak": 0,
    "awaiting_feedback": None,     # {"ts","log_idx","topic_type"}
    "topic_history": {},           # 话题键 → 上次说出时间（24h 防重复）
}


def _load_state():
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            st = json.load(f)
        out = copy.deepcopy(_DEFAULT_STATE)   # 深拷贝：嵌套 dict/list 不得与默认值共享引用
        out.update({k: v for k, v in st.items() if k in _DEFAULT_STATE})
        return out
    except Exception:
        return copy.deepcopy(_DEFAULT_STATE)


def _save_json(path, data):
    tmp = f"{path}.{os.getpid()}_{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _save_state(st):
    try:
        _save_json(_STATE_FILE, st)
    except Exception as e:
        print(f"[proactive] 状态落盘失败: {e}")


def _load_log():
    try:
        with open(_LOG_FILE, "r", encoding="utf-8") as f:
            log = json.load(f)
        return log if isinstance(log, list) else []
    except Exception:
        return []


def _append_log(entry):
    try:
        log = _load_log()
        log.append(entry)
        _save_json(_LOG_FILE, log[-500:])
        return len(log) - 1
    except Exception as e:
        print(f"[proactive] 日志落盘失败: {e}")
        return -1


def _set_log_reaction(idx, reaction):
    if idx is None or idx < 0:
        return
    try:
        log = _load_log()
        if 0 <= idx < len(log):
            log[idx]["reaction"] = reaction
            _save_json(_LOG_FILE, log[-500:])
    except Exception:
        pass

# ---------------------------------------------------------------- 小工具

def _day_key(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _roll_day(st, now):
    """跨天时重置日计数、锚点、环境话题与 P4 封禁。"""
    d = _day_key(now)
    if st["day"] != d:
        st["day"] = d
        st["fired_today"] = 0
        st["anchors_fired"] = []
        st["env_fired"] = []
        st["disable_p4_day"] = ""
    return st


def _end_of_day(ts):
    dt = datetime.fromtimestamp(ts)
    return dt.replace(hour=23, minute=59, second=59).timestamp()


def _in_quiet_hours(now):
    h = datetime.fromtimestamp(now).hour
    return h >= QUIET_START or h < QUIET_END


def _call_name():
    return str(_prov("get_call_name", "奶奶"))


def _persona():
    p = str(_prov("get_persona", "踏实务实"))
    return p if p in _PERSONA_PROMPTS else "踏实务实"

# ---------------------------------------------------------------- L1 信号层
# 每个信号产出候选：{"signal","priority","topic_type","evidence":{"source","text"},
#                  "scene":{...}, "topic_key":str}；无记忆证据的候选直接不产出。

def _sig_health_followup(now):
    """承继旧 check_active_trigger：健康/用药事件 60s 后问"好点了吗"（P2），
    情绪事件共情跟进（P3）；家人/习惯/物品类只标记不追问（Bug① 规则）。"""
    events = _prov("get_keyword_events", []) or []
    cands = []
    changed = False
    for e in events:
        if e.get("status") != "active" or now - e.get("time", now) < FOLLOWUP_MIN_AGE:
            continue
        etype, content = e.get("type"), str(e.get("content", ""))
        if etype in ("health", "medication") and content:
            e["status"] = "cared"
            changed = True
            cands.append({"signal": "health_followup", "priority": "P2",
                          "topic_type": "health_followup",
                          "evidence": {"source": "keyword_event", "text": content},
                          "scene": {"content": content},
                          "topic_key": f"health_followup:{content[:16]}"})
        elif etype == "emotion" and content:
            e["status"] = "cared"
            changed = True
            cands.append({"signal": "health_followup", "priority": "P3",
                          "topic_type": "emotion_followup",
                          "evidence": {"source": "keyword_event", "text": content},
                          "scene": {"content": content},
                          "topic_key": f"emotion_followup:{content[:16]}"})
        else:
            e["status"] = "noted"   # family/habit/item：不主动追问
            changed = True
    # 旧版用药猜测：老人提过吃药且没设正式提醒
    last_med = float(_prov("get_last_medication_time", 0.0) or 0.0)
    if last_med > 0 and now - last_med > 300:
        pend = _prov("get_pending_reminders", []) or []
        if not any("药" in str(r.get("thing", "")) for r in pend):
            _prov("touch_medication_time")  # 重置计时，等同旧版
            cands.append({"signal": "medication_guess", "priority": "P2",
                          "topic_type": "medication_guess",
                          "evidence": {"source": "keyword_event", "text": "老人提过吃药"},
                          "scene": {}, "topic_key": "medication_guess"})
    if changed:
        save = _P.get("save_keyword_events")
        if callable(save):
            try:
                save(events)
            except Exception:
                pass
    return cands


def _sig_todo_stale(now):
    """信号3（P1）：提醒已触发但 30 分钟仍没确认的"没做完的事"，带上位置备注轻轻再问。"""
    out = []
    for r in (_prov("get_awaiting_reminders", []) or []):
        fired_at = float(r.get("last_fired_at") or r.get("time") or now)
        if now - fired_at < TODO_STALE_AGE:
            continue
        thing = str(r.get("thing", "")).strip()
        if not thing:
            continue
        note = str(r.get("note", "")).strip()
        out.append({"signal": "todo_stale", "priority": "P1", "topic_type": "todo_stale",
                    "evidence": {"source": "reminder", "text": thing + (f"（{note}）" if note else "")},
                    "scene": {"thing": thing, "note": note},
                    "topic_key": f"todo_stale:{r.get('id', thing[:16])}"})
    return out


def _sig_anchors(now):
    """信号2（P4）：内置作息锚点——饭点关心饮食、睡前道晚安。
    用药/待办的"到点必说"由 reminder.py 承载，本引擎不重复。"""
    dt = datetime.fromtimestamp(now)
    fired = set(_load_state()["anchors_fired"])
    out = []
    for key, hh, mm, ttype, name in DAILY_ANCHORS:
        anchor_ts = dt.replace(hour=hh, minute=mm, second=0, microsecond=0).timestamp()
        if 0 <= now - anchor_ts <= ANCHOR_WINDOW and key not in fired:
            out.append({"signal": "schedule_anchor", "priority": "P4", "topic_type": ttype,
                        "evidence": {"source": "anchor", "text": f"{hh:02d}:{mm:02d} {name}时间"},
                        "scene": {"meal": name, "anchor": key},
                        "topic_key": f"anchor:{key}"})
    return out


def _sig_emotion(now):
    """信号4（P3）：最近一次情绪=低落，且之后沉默超过 3 分钟。"""
    log = _prov("get_emotion_log", []) or []
    lows = [e for e in log if e.get("label") == "low"]
    if not lows:
        return []
    last = lows[-1]
    ts = float(last.get("time", 0))
    if now - ts > 3600:      # 一小时前的低落不追
        return []
    last_user = float(_prov("last_interaction", now) or now)
    if now - last_user < EMO_SILENCE_THRESHOLD:
        return []
    note = str(last.get("note") or "情绪低落")
    return [{"signal": "emotion_low", "priority": "P3", "topic_type": "emotion_care",
             "evidence": {"source": "emotion_log", "text": note},
             "scene": {"note": note}, "topic_key": f"emotion_care:{_day_key(ts)}"}]


def _sig_silence(now):
    """信号1（P4）：沉默超过 5 分钟 → 从画像里找兴趣/习惯/家人开启话题。
    铁律：检索不到记忆证据就不开口（不记得的事不乱说）。"""
    last_user = float(_prov("last_interaction", now) or now)
    if now - last_user < SILENCE_THRESHOLD:
        return []
    profile = _prov("get_profile", {}) or {}
    for field, ttype in (("兴趣", "chat_interest"), ("习惯", "chat_interest"),
                         ("家庭", "chat_family"), ("物品", "chat_item")):
        items = profile.get(field) or []
        if items:
            v = str(items[0].get("value", "")).strip()
            if v:
                return [{"signal": "silence", "priority": "P4", "topic_type": ttype,
                         "evidence": {"source": f"profile:{field}", "text": v},
                         "scene": {"field": field, "value": v},
                         "topic_key": f"{ttype}:{v[:16]}"}]
    return []


def _festival_name(now, st):
    """今日节日：内置阳历 + festival_dates.json（按 MM-DD 匹配，每年维护）。"""
    md = datetime.fromtimestamp(now).strftime("%m-%d")
    name = SOLAR_FESTIVALS.get(md)
    if not name:
        try:
            with open(_FESTIVAL_FILE, "r", encoding="utf-8") as f:
                name = (json.load(f) or {}).get(md)
        except Exception:
            name = None
    if name and f"festival:{md}" not in st["env_fired"]:
        return md, name
    return None


def _birthday_today(now, st):
    """画像「重要日期」里 month-day 命中今天 → 生日/纪念日（P3）。"""
    profile = _prov("get_profile", {}) or {}
    md = datetime.fromtimestamp(now).strftime("%m-%d")
    for it in (profile.get("重要日期") or []):
        text = str(it.get("key", "")) + " " + str(it.get("value", ""))
        m = re.search(r"(\d{1,2})月(\d{1,2})[日号]", text) or re.search(r"(\d{1,2})-(\d{1,2})", text)
        if not m:
            continue
        if f"{int(m.group(1)):02d}-{int(m.group(2)):02d}" == md and f"date:{md}" not in st["env_fired"]:
            return {"is_birthday": "生日" in text, "text": text.strip(), "md": md}
    return None


def _sig_env(now, st):
    """信号5：节日/生日（本地日历+画像）；天气走注入的 weather_provider（默认关闭）。
    天气×慢病交叉（降温+膝盖怕凉 → 提醒护膝）是 P2 健康关怀。"""
    out = []
    hit = _festival_name(now, st)
    if hit:
        md, name = hit
        out.append({"signal": "env", "priority": "P3", "topic_type": "festival",
                    "evidence": {"source": "calendar", "text": name},
                    "scene": {"festival": name, "md": md},
                    "topic_key": f"festival:{md}"})
    bd = _birthday_today(now, st)
    if bd:
        out.append({"signal": "env", "priority": "P3", "topic_type": "birthday",
                    "evidence": {"source": "profile:重要日期", "text": bd["text"]},
                    "scene": {"is_birthday": bd["is_birthday"], "text": bd["text"], "md": bd["md"]},
                    "topic_key": f"date:{bd['md']}"})
    wp = _P.get("weather_provider")
    if callable(wp):
        try:
            w = wp()
        except Exception:
            w = None
        if w and w.get("kind") and f"weather:{w['kind']}" not in st["env_fired"]:
            profile = _prov("get_profile", {}) or {}
            hint = ""
            for it in (profile.get("健康") or []):
                v = str(it.get("value", ""))
                if _HEALTH_BODY_RE.search(v):
                    hint = v
                    break
            out.append({"signal": "env", "priority": "P2", "topic_type": "weather_care",
                        "evidence": {"source": "weather", "text": w.get("desc", w["kind"]) + (f" + {hint}" if hint else "")},
                        "scene": {"weather_desc": w.get("desc", ""), "kind": w["kind"], "health_hint": hint},
                        "topic_key": f"weather:{w['kind']}"})
    return out

# ---------------------------------------------------------------- L4 生成层

def _llm_phrase(cand, persona, call):
    """人格 prompt + 记忆原话现场生成；失败返回空串走模板兜底。"""
    if _llm is None:
        return ""
    sys_p = _PERSONA_PROMPTS.get(persona, "")
    ev = cand["evidence"]["text"]
    rules = (
        f"\n【主动开口任务】你要主动对老人开口说一句话。场景：{_scene_desc(cand)}。"
        f"\n必须遵守：1) 一两句话，不超过60字；2) 自然带上这件事：{ev}，"
        "不要背诵字段名，不编造没提过的事；3) 不给任何医疗建议（药量/换药等），"
        "不提「信号」「引擎」这类内部词；4) 结尾留一个轻松的话头（问句或提议），不纠缠；"
        f"5) 只能称呼老人「{call}」。只输出要说的话本身。"
    )
    try:
        return (_llm([{"role": "system", "content": sys_p + rules},
                      {"role": "user", "content": f"现在请对{call}主动开口。"}],
                     max_tokens=120, timeout=10) or "").strip().strip('"“”')
    except Exception as e:
        print(f"[proactive] LLM 话术生成失败: {e}")
        return ""


def _scene_desc(cand):
    t, s = cand["topic_type"], cand.get("scene", {})
    return {
        "health_followup": f"老人刚才说{s.get('content', '')}，你惦记着，问一句现在好点没",
        "emotion_followup": "老人刚才心里不太得劲，你轻轻再关心一下",
        "medication_guess": "老人之前提过吃药，估计到点了，提醒一句",
        "todo_stale": f"该办的事「{s.get('thing', '')}」到现在没确认办妥" + (f"，备注：{s.get('note')}" if s.get("note") else ""),
        "meal": f"到{s.get('meal', '饭')}点了，关心老人吃饭",
        "bedtime": "到睡觉时间了，道晚安、叮嘱休息",
        "emotion_care": "老人之前情绪低落，沉默有一会儿了，你主动陪陪",
        "chat_interest": f"老人安静有一阵了，用TA的{s.get('field', '')}「{s.get('value', '')}」起个话头",
        "chat_family": f"老人安静有一阵了，从TA家里人「{s.get('value', '')}」聊起",
        "chat_item": f"老人安静有一阵了，从TA说过的「{s.get('value', '')}」聊起",
        "festival": f"今天是{s.get('festival', '')}，主动问候，陪老人说说话",
        "birthday": ("今天是老人的生日，送上祝福" if s.get("is_birthday") else f"今天是个日子：{s.get('text', '')}，主动提起"),
        "weather_care": f"{s.get('weather_desc', '天气变了')}" + (f"，老人有「{s.get('health_hint')}」的老毛病，针对性叮嘱" if s.get("health_hint") else "，提醒老人注意添衣"),
    }.get(t, "主动关心老人")


def _template_phrase(cand, persona, call):
    """离线兜底模板（不写死原则下的最后退路：LLM 不可用时才用）。"""
    t, s = cand["topic_type"], cand.get("scene", {})
    humor, caring = persona == "风趣幽默", persona == "暖心知心"
    if t == "health_followup":
        return f"{call}，您刚才说的{s.get('content', '')}，现在好点了吗？"
    if t == "emotion_followup":
        return (f"{call}，我一直惦记着呢，现在心里舒坦点了吗？" if not humor else
                f"{call}，刚那事儿别搁心里头啊，跟我唠唠？")
    if t == "medication_guess":
        return f"{call}，该吃药啦，我帮您记着呢。"
    if t == "todo_stale":
        note = f"，{s.get('note')}" if s.get("note") else ""
        return f"{call}，{s.get('thing', '')}的事儿办妥了吗{note}？我替您记着呢。"
    if t == "meal":
        return (f"{call}，到点儿啦，{s.get('meal', '饭')}吃了没？别饿着肚子。" if not humor else
                f"{call}，肚子咕咕叫了吧？{s.get('meal', '饭')}安排上没？")
    if t == "bedtime":
        return (f"{call}，不早啦，泡泡脚早点歇着吧，晚安。" if not caring else
                f"{call}，该休息啦，我陪着您呢，晚安，做个好梦。")
    if t == "emotion_care":
        return (f"{call}，您今儿好像有点安静呢，是不是心里头藏着事？跟我说说。" if not humor else
                f"{call}，哎呦，今儿气氛不对呀，谁惹您啦？跟我说说呗。")
    if t == "chat_interest":
        return f"{call}，您上次说的{s.get('value', '')}，后来咋样啦？跟我念叨念叨。"
    if t == "chat_family":
        return f"{call}，这会儿安静，想起您说的{s.get('value', '')}了，最近他们怎么样呀？"
    if t == "chat_item":
        return f"{call}，您说的{s.get('value', '')}还在老地方放着吧？用不用我帮您记着？"
    if t == "festival":
        return (f"{call}，今儿{s.get('festival', '')}呢，我陪您说说话。" if not humor else
                f"{call}，今儿{s.get('festival', '')}！咱也得热闹热闹，聊两毛钱的？")
    if t == "birthday":
        return (f"{call}，今儿可是您的生日，祝您身子骨硬朗、天天开心！" if s.get("is_birthday") else
                f"{call}，今儿是个特别的日子呢，{s.get('text', '')}，我记着呐。")
    if t == "weather_care":
        if s.get("health_hint"):
            return f"{call}，{s.get('weather_desc', '外边变天了')}，您{s.get('health_hint')}，可得注意着点。"
        return f"{call}，{s.get('weather_desc', '外边变天了')}，出门记得添件衣裳。"
    return f"{call}，我在这儿呢，陪您唠两句？"

# ---------------------------------------------------------------- L2 仲裁 + L5 反馈 + 主入口

def _settle_awaiting(st, now):
    """开口后 60s 无回应 → 记 ignored；连续 2 次抬高动机阈值（降话痨）。"""
    aw = st.get("awaiting_feedback")
    if not aw:
        return st
    last_user = float(_prov("last_interaction", 0.0) or 0.0)
    if now - aw["ts"] > FEEDBACK_WINDOW and last_user < aw["ts"]:
        _set_log_reaction(aw.get("log_idx"), "ignored")
        st["ignored_streak"] += 1
        if st["ignored_streak"] >= 2:
            st["motivation_threshold"] = min(THRESH_MAX, st["motivation_threshold"] + 0.5)
            st["ignored_streak"] = 0
        st["awaiting_feedback"] = None
    return st


def note_user_activity(user_text, now=None):
    """每条用户消息调用一次：熔断检测 + 对上一条主动开口的反馈分类。"""
    now = now or time.time()
    text = str(user_text or "").strip()
    if not text:
        return
    with _lock:
        st = _roll_day(_load_state(), now)
        if FUSE_RE.search(text):
            st["quiet_until"] = _end_of_day(now)
            aw = st.get("awaiting_feedback")
            if aw:
                _set_log_reaction(aw.get("log_idx"), "fuse")
                st["awaiting_feedback"] = None
            _save_state(st)
            return
        aw = st.get("awaiting_feedback")
        if aw and now - aw["ts"] <= FEEDBACK_WINDOW:
            if NEGATIVE_RE.search(text):
                _set_log_reaction(aw.get("log_idx"), "rejected")
                st["cooldown_k"] = min(K_MAX, st["cooldown_k"] * 1.5)
                st["disable_p4_day"] = st["day"]
            else:
                _set_log_reaction(aw.get("log_idx"), "accepted")
                st["cooldown_k"] = max(K_MIN, st["cooldown_k"] * 0.9)
            st["ignored_streak"] = 0
            st["awaiting_feedback"] = None
            _save_state(st)


def tick(now=None):
    """30s 轮询主入口：返回要主动说出的话（str）或 None。"""
    now = now or time.time()
    with _lock:
        st = _roll_day(_load_state(), now)
        st = _settle_awaiting(st, now)

        # —— 闸1：硬抑制 ——
        if _in_quiet_hours(now) or now < st.get("quiet_until", 0.0):
            _save_state(st)
            return None
        if st["fired_today"] >= DAILY_CAP:
            _save_state(st)
            return None

        # —— L1：采集候选 ——
        cands = []
        for fn in (_sig_todo_stale, _sig_health_followup, _sig_emotion,
                   _sig_anchors, _sig_silence, _sig_env):
            try:
                cands.extend(fn(now) if fn is not _sig_env else fn(now, st))
            except Exception as e:
                print(f"[proactive] 信号 {fn.__name__} 异常: {e}")
        if st["disable_p4_day"] == st["day"]:
            cands = [c for c in cands if c["priority"] != "P4"]

        # 闸1b：聊得正欢不插嘴——但对话内跟进（"好点了吗"/催吃药）不受此限
        last_user = float(_prov("last_interaction", now) or now)
        if now - last_user < USER_ACTIVE_GAP:
            cands = [c for c in cands if c["signal"] in FOLLOWUP_SIGNALS]

        # —— 闸2/3：冷却 + 动机阈值 ——
        # 跟进类由事件状态机保证只说一次，豁免 24h 防重罚分与冷却提阈；
        # 其余话题冷却期内阈值提到 4.0，仅 P1/P2 可破闸。
        in_cooldown = (now - st["last_proactive_ts"]) < BASE_COOLDOWN * st["cooldown_k"]
        hist = st["topic_history"]
        for c in cands:
            if c["signal"] in FOLLOWUP_SIGNALS:
                c["motivation"] = URGENCY.get(c["priority"], 3.0)
                c["threshold"] = st["motivation_threshold"]
            else:
                fresh = 0.3 if now - float(hist.get(c["topic_key"], 0)) < 24 * 3600 else 1.0
                c["motivation"] = URGENCY.get(c["priority"], 3.0) * fresh
                c["threshold"] = THRESH_COOLDOWN if in_cooldown else st["motivation_threshold"]
        cands = [c for c in cands if c["motivation"] >= c["threshold"] and c.get("evidence", {}).get("text")]
        if not cands:
            _save_state(st)
            return None
        cands.sort(key=lambda c: (c["priority"], -c["motivation"]))
        cand = cands[0]

        # —— L4：人格话术生成 + 安全过滤 ——
        persona, call = _persona(), _call_name()
        msg = _llm_phrase(cand, persona, call) or _template_phrase(cand, persona, call)
        if _safety is not None:
            try:
                r = _safety(msg, persona, call)
                if r and not r.get("safe", True):
                    msg = str(r.get("safe_reply") or msg)
            except Exception:
                pass

        # —— 落账：状态 + 日志 + 待反馈 ——
        st["last_proactive_ts"] = now
        st["fired_today"] += 1
        hist[cand["topic_key"]] = now
        st["topic_history"] = {k: v for k, v in hist.items() if now - v < 48 * 3600}
        if cand["signal"] == "schedule_anchor":
            st["anchors_fired"].append(cand["scene"].get("anchor", cand["topic_key"]))
        if cand["signal"] == "env":
            st["env_fired"].append(cand["topic_key"])
        log_idx = _append_log({
            "ts": now, "signal": cand["signal"], "priority": cand["priority"],
            "topic_type": cand["topic_type"], "evidence": cand["evidence"]["text"],
            "persona": persona, "message": msg, "motivation": round(cand["motivation"], 2),
            "in_cooldown": in_cooldown, "reaction": None,
        })
        st["awaiting_feedback"] = {"ts": now, "log_idx": log_idx, "topic_type": cand["topic_type"]}
        _save_state(st)
        return msg

# ---------------------------------------------------------------- 评估数据出口

def get_stats(now=None):
    """评估面板数据：接受率/打扰率/今日次数/当前 k 与阈值（论文 5.1 指标）。"""
    now = now or time.time()
    st = _roll_day(_load_state(), now)
    log = _load_log()
    reacted = [e for e in log if e.get("reaction")]
    acc = sum(1 for e in reacted if e["reaction"] == "accepted")
    rej = sum(1 for e in reacted if e["reaction"] in ("rejected", "fuse"))
    return {
        "fired_total": len(log), "fired_today": st["fired_today"],
        "accepted": acc, "ignored": sum(1 for e in reacted if e["reaction"] == "ignored"),
        "rejected": rej,
        "acceptance_rate": round(acc / len(reacted), 3) if reacted else None,
        "annoyance_rate": round(rej / len(reacted), 3) if reacted else None,
        "cooldown_k": st["cooldown_k"], "motivation_threshold": st["motivation_threshold"],
        "quiet_until": st["quiet_until"],
    }

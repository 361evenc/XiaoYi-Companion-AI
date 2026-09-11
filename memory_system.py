# -*- coding: utf-8 -*-
"""
小忆记忆系统：事件流 → 记忆库 → 画像（三级架构）
=====================================================

设计参照（编号同《行动方案.md》）：
  [09] Generative Agents：记忆流 + 三因子检索（新近性×重要性×相关性）+ 反思升华
  [10] MemoryBank：情节/语义双记忆库 + 艾宾浩斯遗忘曲线 R=e^(-t/S) + 回忆强化

数据流：
  每轮对话 --LLM结构化抽取--> 原子事实 {字段, 键, 值, 原话, 重要性, 置信度}
          --> 情节记忆（对话事件时间线） + 语义记忆（画像，冲突时近期覆盖远期）
          --> 累计重要性超阈值时反思，生成高层画像结论
  回复前  --三因子检索--> 相关记忆注入对话上下文（"被记住"的体验）

适老化与伦理约束：
  - 冷启动节制：主动确认限频（全局冷却30分钟、每条事实最多问2次），
    绝不"查户口式"连续提问
  - 隐私：全部数据本地持久化（memory_bank.json），模块本身不联网
  - 无 LLM 时自动降级为关键词抽取（离线可用），与 reminder.py 同一设计哲学

工程约束：
  - 仅依赖标准库，LLM 以 callable(llm(prompt)->str) 注入，无 LLM 也能跑
  - 时钟注入（可测试）；所有读写持锁（后台抽取线程与检索并发安全）
  - 原子落盘（tmp + os.replace，防断电损坏）

核心公式（论文实验章可直接引用）：
  三因子检索   score = α·recency + β·importance + γ·relevance
               recency  = 0.995 ^ 距上次活动小时数   （GA [09] 衰减率）
               relevance= 字符bigram余弦相似度        （embedding 的轻量替代，可插拔）
  遗忘曲线     R = e^(-t/S)                         （MemoryBank [10]）
               t = 距上次回忆秒数；S = 记忆强度（天），初始值由重要性分档，
               每次被回忆 S <- S*1.5+1（间隔重复效应），R < 0.2 视为淡忘
"""

import json
import math
import os
import re
import threading
import time as _time
from datetime import datetime

__all__ = [
    "MemoryBank", "FIELDS", "FIELD_INFO", "keyword_extract", "similarity",
    "retrievability", "parse_llm_facts", "parse_json_strings",
    "humanize_ago", "classify_confirm_reply",
]

# ---------------------------------------------------------------- 字段表（老年场景定制）

FIELDS = ["家庭", "健康", "用药", "作息", "饮食", "兴趣", "重要日期", "情绪", "习惯", "物品", "其他"]

FIELD_ALIASES = {
    "家人": "家庭", "家庭成员": "家庭", "亲属": "家庭",
    "健康状况": "健康", "慢病": "健康", "慢性病": "健康", "症状": "健康", "身体": "健康",
    "药物": "用药", "服药": "用药",
    "睡眠": "作息", "日常作息": "作息",
    "忌口": "饮食", "饮食习惯": "饮食",
    "爱好": "兴趣", "兴趣爱好": "兴趣",
    "日期": "重要日期", "纪念日": "重要日期", "生日": "重要日期",
    "情绪敏感点": "情绪", "情感": "情绪",
    "生活习惯": "习惯", "方言": "习惯", "方言习惯": "习惯",
}

# UI 展示：图标 / 背景色 / 边框色
FIELD_INFO = {
    "家庭": ("👨‍👩‍👧‍👦", "#FCE4EC", "#F48FB1"),
    "健康": ("🏥", "#E8F5E9", "#A5D6A7"),
    "用药": ("💊", "#FFF3E0", "#FFCC80"),
    "作息": ("🕐", "#EDE7F6", "#B39DDB"),
    "饮食": ("🍚", "#FFF8E1", "#FFE082"),
    "兴趣": ("🎨", "#F3E5F5", "#CE93D8"),
    "重要日期": ("📅", "#E1F5FE", "#81D4FA"),
    "情绪": ("❤️", "#FBE9E7", "#FFAB91"),
    "习惯": ("🌱", "#F1F8E9", "#AED581"),
    "物品": ("🔑", "#E3F2FD", "#90CAF9"),
    "其他": ("📌", "#F5F5F5", "#BDBDBD"),
}

# ---------------------------------------------------------------- LLM 提示词

EXTRACT_PROMPT = """你是老年陪伴系统的记忆抽取器。从【老人这轮说的话】里抽取关于老人本人的关键事实。

要求：
1. 只抽取老人明确说过的信息，绝不推测；小忆的回复仅供理解上下语，不要从中抽取。
2. 每条事实包含：field(类别)、key(字段名，2-6字，如"孙子"、"常用药"、"生日")、value(值)、quote(老人原话片段)、importance(1-10整数)、confidence(0-1小数)。
3. field 必须是：家庭/健康/用药/作息/饮食/兴趣/重要日期/情绪/习惯/物品/其他 之一。
4. importance 量表：1-2琐事(打扫卫生)；3-4日常(买菜散步)；5-6重要(身体不适、家人来电)；7-8很重要(孙子过生日、老伴忌日)；9-10极重要(急救、住院)。
5. confidence：老人说得越明确越高，含糊或转述给低分。
6. 同一轮最多抽3条，没有可抽的就输出 []。
7. 只输出JSON数组，不要任何解释文字。

【老人这轮说的话】
{user_text}

【小忆的回复（仅供理解上下文）】
{assistant_text}"""

REFLECT_PROMPT = """你在帮陪伴AI"小忆"反思关于一位老人的记忆。

下面是小忆最近记下的对话事件和已知画像。请归纳1-3条更高层的理解结论：
第三人称、每条不超过25字、必须只基于给定信息、不编造。
示例："张奶奶最近常念叨孙子豆豆"、"李爷爷很在意血糖控制"。

只输出JSON数组（字符串列表），没有值得归纳的就输出 []。

【最近的对话事件】
{episodes}

【已知画像】
{profile}"""

# ---------------------------------------------------------------- 相似度（相关性因子）

# 适老场景高频同义词归一（embedding 的轻量替代；中文 embedding 可直接替换 similarity()）
_SYNONYMS = [("睡不着", "失眠"), ("睡不好", "失眠"), ("高血压", "血压高"),
             ("高血糖", "血糖高"), ("孙娃", "孙子"),
             ("忌口", "不能吃"), ("忌嘴", "不能吃")]

def _char_ngrams(text, n=2):
    text = re.sub(r"[\s，。！？、,.!?；;：:\"'（）()\[\]{}·…]", "", str(text or ""))
    if not text:
        return set()
    if len(text) < n:
        return {text}
    return {text[i:i + n] for i in range(len(text) - n + 1)}

# 相关性计算中剔除的高频虚字（避免"我/的/了"类字面重合虚高）
_STOP_CHARS = set("我你他她它的了着吧呢吗呀啊在是有不这人么")

def similarity(a, b):
    """中文相关性基线：0.6·字符bigram余弦 + 0.4·去虚字unigram Jaccard
    （先做同义词归一；可整体替换为 embedding 余弦）"""
    a, b = str(a or ""), str(b or "")
    for s, t in _SYNONYMS:
        a = a.replace(s, t)
        b = b.replace(s, t)
    ga, gb = _char_ngrams(a), _char_ngrams(b)
    ua = _char_ngrams(a, 1) - _STOP_CHARS
    ub = _char_ngrams(b, 1) - _STOP_CHARS
    bigram = uni = 0.0
    if ga and gb:
        inter = len(ga & gb)
        if inter:
            bigram = inter / math.sqrt(len(ga) * len(gb))
    if ua and ub:
        uni = len(ua & ub) / len(ua | ub)
    return 0.6 * bigram + 0.4 * uni

# ---------------------------------------------------------------- 遗忘曲线（MemoryBank [10]）

FORGET_THRESHOLD = 0.2      # R 低于此值视为淡忘：退出检索池与画像，但保留在磁盘
STRENGTH_CAP_DAYS = 3650.0  # 强度上限（约10年：被反复确认的记忆永不淡忘）

_IMP_BASE_DAYS = {1: 1, 2: 1, 3: 3, 4: 3, 5: 8, 6: 8, 7: 25, 8: 25, 9: 90, 10: 90}

def _strength_days(importance, is_fact):
    """初始记忆强度（天）：重要性分档；语义事实比情节踪迹更持久（×2）"""
    imp = int(max(1, min(10, importance)))
    base = _IMP_BASE_DAYS[imp]
    return float(base * (2.0 if is_fact else 1.0))

def retrievability(mem, now):
    """遗忘曲线 R = e^(-t/S)：t=距上次回忆秒数，S=记忆强度（天）"""
    t = max(0.0, now - float(mem.get("last_recall", mem.get("ts", now))))
    s_days = max(0.5, float(mem.get("strength", 1.0)))
    return math.exp(-t / (s_days * 86400.0))

def _reinforce(mem, now, factor=1.5):
    """被回忆一次：强度几何增长（间隔重复效应），并刷新回忆时间"""
    s = float(mem.get("strength", 1.0))
    mem["strength"] = min(STRENGTH_CAP_DAYS, s * factor + 1.0)
    mem["recall_count"] = int(mem.get("recall_count", 0)) + 1
    mem["last_recall"] = now

# ---------------------------------------------------------------- 规范化

def _norm_field(field):
    f = str(field or "").strip()
    f = FIELD_ALIASES.get(f, f)
    if f in FIELDS:
        return f
    for cand in FIELDS:            # 模糊兜底："家人情况" -> 家庭
        if cand in f:
            return cand
    return "其他"

def _norm_key(key):
    k = re.sub(r"[\s，。,.！!？?：:、]+", "", str(key or ""))
    k = re.sub(r"(名字|姓名)$", "", k)     # "孙子的名字" -> "孙子的"
    k = k.rstrip("的")                     # "孙子的" -> "孙子"
    return (k or "信息")[:12]

def _norm_value(value):
    v = re.sub(r"[\s，。,.！!？?；;]+$", "", str(value or "").strip())
    v = v.strip("“”\"'")
    return v[:40]

def _clamp_imp(v, default=5):
    try:
        return int(max(1, min(10, round(float(v)))))
    except (TypeError, ValueError):
        return default

def _clamp_conf(v, default=0.8):
    try:
        return float(max(0.0, min(1.0, float(v))))
    except (TypeError, ValueError):
        return default

def _pick(d, *names):
    for n in names:
        if d.get(n) not in (None, ""):
            return d[n]
    return None

# ---------------------------------------------------------------- LLM 输出的鲁棒解析

_FENCE_RE = re.compile(r"```(?:json)?|```")

def _json_candidates(text):
    t = _FENCE_RE.sub("", str(text or "")).strip()
    if not t:
        return []
    cands = [t]
    i, j = t.find("["), t.rfind("]")
    if i != -1 and j > i:
        cands.append(t[i:j + 1])
    i, j = t.find("{"), t.rfind("}")
    if i != -1 and j > i:
        cands.append(t[i:j + 1])
    return cands

def parse_llm_facts(text):
    """LLM 原始输出 -> 规范化事实列表。容忍围栏/前后废话/中英字段名，坏条目跳过"""
    for cand in _json_candidates(text):
        try:
            data = json.loads(cand)
        except (ValueError, TypeError):
            continue
        items = data
        if isinstance(data, dict):
            items = _pick(data, "facts", "列表", "results", "data")
            if isinstance(data, dict) and not isinstance(items, list):
                if _pick(data, "value", "值"):
                    items = [data]          # 单条对象直接当事实
                else:
                    continue
        if not isinstance(items, list):
            continue
        out = []
        for it in items:
            if not isinstance(it, dict):
                continue
            value = _pick(it, "value", "值", "内容")
            if value is None:
                continue
            out.append({
                "field": _norm_field(_pick(it, "field", "类别", "category", "分类")),
                "key": _norm_key(_pick(it, "key", "字段名", "字段", "name")),
                "value": _norm_value(value),
                "quote": str(_pick(it, "quote", "原话") or "")[:60],
                "importance": _clamp_imp(_pick(it, "importance", "重要性"), 5),
                "confidence": _clamp_conf(_pick(it, "confidence", "置信度"), 0.8),
            })
        if out:
            return out
    return []

def parse_json_strings(text, limit=3):
    """LLM 原始输出 -> 字符串列表（反思结论用）"""
    for cand in _json_candidates(text):
        try:
            data = json.loads(cand)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            data = _pick(data, "conclusions", "结论", "reflections") or []
        if isinstance(data, list):
            out = [str(s).strip() for s in data
                   if isinstance(s, (str, int, float)) and str(s).strip()]
            return out[:limit]
    return []

# ---------------------------------------------------------------- 关键词回退抽取（离线可用）

KW_FAMILY_NAME_RE = re.compile(
    r"(孙子|孙女|儿子|闺女|女儿|老伴|儿媳妇|女婿|弟弟|妹妹|哥哥|姐姐)"
    r"(?:的)?(?:名字|名字叫|名字是|小名叫|名叫|叫|是)([^\s，。,.！!？?、；;的]{1,8})")
KW_DATE_RE = re.compile(r"(\d{1,2}月\d{1,2}[号日]?|生日|忌日|结婚纪念日|复查)")
KW_HEALTH_RE = re.compile(
    r"头疼|头晕|心口闷|牙疼|失眠|睡不着|血压高|血糖高|不舒服|感冒"
    r"|(?:膝盖|腰|腿|肚子|胃|胸口|肩膀|后背)(?:也|又|总|老|最近)?(?:有点儿?|很|非常|十分)?(?:疼|酸|胀|难受)")
KW_MED = ["降压药", "阿司匹林", "降糖药", "二甲双胍", "中药", "止痛药", "感冒药", "胰岛素"]
KW_HOBBY = ["养花", "浇花", "下棋", "打太极", "散步", "遛弯", "买菜", "听戏",
            "看戏", "京剧", "钓鱼", "跳广场舞", "看电视"]
KW_ROUTINE = ["早起", "午睡", "早睡", "晨练"]
KW_DIET = ["不吃辣", "不能吃甜", "忌口", "少吃糖", "不吃糖", "吃得清淡", "喝粥"]

def keyword_extract(text, now=None):
    """LLM 不可用时的关键词抽取回退（覆盖老年陪伴高频场景）。
    键=匹配词本身，同一类别多条信息互不覆盖（"膝盖疼"与"血压高"共存）；
    原话=整句（检索时的措辞线索）。"""
    t = str(text or "")
    quote = t[:60]
    out = []

    m = KW_FAMILY_NAME_RE.search(t)
    if m:
        out.append({"field": "家庭", "key": m.group(1), "value": _norm_value(m.group(2)),
                    "quote": quote, "importance": 7, "confidence": 0.9})

    for kw in dict.fromkeys(KW_HEALTH_RE.findall(t)):   # 去重保序
        out.append({"field": "健康", "key": kw, "value": kw,
                    "quote": quote, "importance": 5, "confidence": 0.85})
    for kw in KW_MED:
        if kw in t:
            out.append({"field": "用药", "key": kw, "value": kw,
                        "quote": quote, "importance": 6, "confidence": 0.9})
    for kw in KW_HOBBY:
        if kw in t:
            out.append({"field": "兴趣", "key": kw, "value": kw,
                        "quote": quote, "importance": 4, "confidence": 0.8})
    for kw in KW_ROUTINE:
        if kw in t:
            out.append({"field": "作息", "key": kw, "value": kw,
                        "quote": quote, "importance": 4, "confidence": 0.8})
    for kw in KW_DIET:
        if kw in t:
            out.append({"field": "饮食", "key": kw, "value": kw,
                        "quote": quote, "importance": 5, "confidence": 0.8})
    dm = KW_DATE_RE.search(t)
    if dm:
        out.append({"field": "重要日期", "key": _norm_key(dm.group(1)),  # 键=日期本身，多个日期互不覆盖
                    "value": dm.group(1),
                    "quote": dm.group(0), "importance": 6, "confidence": 0.75})

    return out[:3]

# ---------------------------------------------------------------- 主动确认（人设话术）

CONFIRM_COOLDOWN = 1800.0   # 全局冷却：两次主动确认至少间隔30分钟
CONFIRM_TTL = 300.0          # 提问后等待老人答复的窗口（秒）
CONFIRM_CONF = 0.6           # 置信度低于此值 → 进入待确认队列
CONFIRM_MAX_ASK = 2          # 每条事实最多主动问2次
CONFIRM_REPLY_MAXLEN = 30    # 超过此长度的答复视为"没有在回答确认"，不接管

_YES_RE = re.compile(
    r"^(?:(?:对|是的?|是啊?|对呀|对啊?|对的|对滴|没错|嗯+|嗯呢|哎|好|行)[，,、。！!~～\s]*)+$")
_NO_RE = re.compile(r"(不是|不对|记错|错了|哪有|别瞎|乱记|没有的事|不是的)")
_NEWVAL_RE = re.compile(r"(?:叫|应该是|应该叫|是|改成|改为)\s*([^\s，。,.！!？?、；;]{1,12})")

def classify_confirm_reply(text):
    """老人对确认问句的答复分类：('yes',None) / ('no',新值或None) / (None,None)"""
    t = str(text or "").strip()
    if not t or len(t) > CONFIRM_REPLY_MAXLEN:
        return (None, None)
    nv = _NEWVAL_RE.search(t)
    if _NO_RE.search(t):
        return ("no", _norm_value(nv.group(1)) if nv else None)
    if _YES_RE.match(t):
        return ("yes", None)
    return (None, None)

def confirm_question(persona, call, key, value):
    if persona == "风趣幽默":
        return f"哎{call}，考考我记性——您说过{key}是{value}，我没记错吧？"
    if persona == "暖心知心":
        return f"{call}，我一直记着呢，您说过{key}是{value}，是这么说的吧？"
    return f"对了{call}，跟您对个事儿——您之前说{key}是{value}，我没记错吧？"

def confirm_yes_ack(persona, call, key, value):
    if persona == "风趣幽默":
        return f"嘿嘿，我就说嘛，{key}是{value}，我这记性靠得住！"
    if persona == "暖心知心":
        return f"嗯嗯，我记着呢，{key}是{value}。您跟我说过的我都放在心上。"
    return f"好嘞，我记住了，{key}是{value}。"

def confirm_no_ack(persona, call, new_value=None):
    if new_value:
        if persona == "风趣幽默":
            return f"哦哦是{new_value}！我这就改过来，您看我这不闹笑话了嘛。"
        if persona == "暖心知心":
            return f"是{new_value}呀，我这就记下来，谢谢您提醒我。"
        return f"哦，是{new_value}，我这就改过来。"
    if persona == "风趣幽默":
        return f"哎呀，是我记岔了，您多担待！"
    if persona == "暖心知心":
        return f"是我记岔了，您别往心里去，跟我说说，我记准了。"
    return f"是我记岔了，您多担待。"

# ---------------------------------------------------------------- 工具

def humanize_ago(ts, now):
    d = max(0.0, now - ts)
    if d < 60:
        return "刚刚"
    if d < 3600:
        return f"{int(d // 60)}分钟前"
    if d < 86400:
        return f"{int(d // 3600)}小时前"
    if d < 30 * 86400:
        return f"{int(d // 86400)}天前"
    return datetime.fromtimestamp(ts).strftime("%m月%d号")

# ---------------------------------------------------------------- MemoryBank

REFLECT_IMP_THRESHOLD = 15   # 自上次反思以来累计重要性达到即触发反思
REFLECT_CNT_THRESHOLD = 3    # 且至少新增3轮含记忆的对话
REFLECT_RETRY_COOLDOWN = 600

class MemoryBank:
    """事件流→记忆库→画像。线程安全；llm 可为 None（纯关键词模式）。"""

    def __init__(self, path="memory_bank.json", llm=None, clock=_time.time,
                 weights=(1.0, 1.0, 1.0)):
        self.path = path
        self.llm = llm
        self.clock = clock
        self.weights = tuple(weights)   # (新近性, 重要性, 相关性)
        self._lock = threading.RLock()
        self._id_seq = 0
        self.episodes = []          # 情节记忆：对话事件踪迹
        self.facts = []             # 语义记忆：画像事实（field+key 唯一）
        self.reflections = []       # 反思：高层画像结论
        self.pending_confirms = []  # 待主动确认的事实 [{"fact_id","ts"}]
        self.active_confirm = None  # 已问出、等老人答复 {"fact_id","ts"}
        self._reflect_imp = 0
        self._reflect_cnt = 0
        self._last_reflect_try = 0.0
        self._last_confirm_ask = 0.0
        self._load()

    # ---- 持久化 ----
    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        if not isinstance(data, dict):
            return
        self.episodes = data.get("episodes", []) or []
        self.facts = data.get("facts", []) or []
        self.reflections = data.get("reflections", []) or []
        self.pending_confirms = data.get("pending_confirms", []) or []
        self.active_confirm = data.get("active_confirm") or None
        self._reflect_imp = data.get("reflect_imp", 0)
        self._reflect_cnt = data.get("reflect_cnt", 0)
        self._last_reflect_try = data.get("last_reflect_try", 0.0)
        self._last_confirm_ask = data.get("last_confirm_ask", 0.0)
        # 恢复 id 序号，避免重启后 _next_id 生成重复 id
        for item in self.episodes + self.facts + self.reflections:
            try:
                seq = int(str(item.get("id", "")).rsplit("_", 1)[-1])
                self._id_seq = max(self._id_seq, seq)
            except (ValueError, IndexError, AttributeError):
                pass

    def _save(self):
        # 剪枝防膨胀：情节300 / 事实200 / 反思30 / 待确认5
        self.episodes = sorted(self.episodes, key=lambda e: -e.get("ts", 0))[:300]
        self.facts = sorted(self.facts, key=lambda f: -f.get("ts", 0))[:200]
        self.reflections = sorted(self.reflections, key=lambda r: -r.get("ts", 0))[:30]
        self.pending_confirms = self.pending_confirms[:5]
        data = {
            "episodes": self.episodes, "facts": self.facts,
            "reflections": self.reflections, "pending_confirms": self.pending_confirms,
            "active_confirm": self.active_confirm,
            "reflect_imp": self._reflect_imp, "reflect_cnt": self._reflect_cnt,
            "last_reflect_try": self._last_reflect_try,
            "last_confirm_ask": self._last_confirm_ask,
        }
        tmp = f"{self.path}.{os.getpid()}_{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def _next_id(self, prefix, now):
        self._id_seq = (self._id_seq + 1) % 100000
        return f"{prefix}{int(now * 1000)}_{self._id_seq:05d}"

    # ---- 在线抽取（每轮对话后调用）----
    def observe(self, user_text, assistant_text="", now=None):
        """抽取原子事实 → 更新情节/语义记忆 → 必要时反思。返回本轮摘要"""
        now = now or self.clock()
        user_text = str(user_text or "").strip()
        if not user_text:
            return {"new": [], "updated": [], "episode": None, "reflection": None}

        raw = []
        if self.llm is not None:
            try:
                raw = self._extract_llm(user_text, assistant_text)
            except Exception as e:
                print(f"[memory] LLM抽取失败，降级关键词: {e}")
        if not raw:
            raw = keyword_extract(user_text, now)
        if not raw:
            return {"new": [], "updated": [], "episode": None, "reflection": None}

        with self._lock:
            new_ids, updated_ids = [], []
            for f in raw[:3]:
                fid, is_new = self._add_fact(now=now, **f)
                (new_ids if is_new else updated_ids).append(fid)
            imp_max = max(f["importance"] for f in raw)
            ep = {
                "id": self._next_id("e", now),
                "text": "、".join(f"{f['key']}是{f['value']}" if f["key"] != f["value"]
                                  else f["value"] for f in raw[:3]),
                "quote": user_text[:60],
                "field": raw[0]["field"],
                "ts": now, "importance": imp_max,
                "strength": _strength_days(imp_max, is_fact=False),
                "recall_count": 0, "last_recall": now,
            }
            self.episodes.append(ep)
            self._reflect_imp += imp_max
            self._reflect_cnt += 1

        refl = self._maybe_reflect(now)          # LLM 调用在锁外
        with self._lock:
            self._save()
        return {"new": new_ids, "updated": updated_ids, "episode": ep["id"],
                "reflection": refl}

    def _extract_llm(self, user_text, assistant_text):
        prompt = EXTRACT_PROMPT.format(
            user_text=user_text[:300],
            assistant_text=str(assistant_text or "")[:200])
        txt = self.llm(prompt)
        return parse_llm_facts(txt) if txt else []

    def _add_fact(self, field, key, value, quote, importance, confidence, now):
        field = _norm_field(field)
        key = _norm_key(key)
        value = _norm_value(value)
        if not value:
            return ("", False)
        importance = _clamp_imp(importance)
        confidence = _clamp_conf(confidence)

        found = next((f for f in self.facts if f["field"] == field and f["key"] == key), None)
        if found is None:
            fact = {
                "id": self._next_id("f", now),
                "field": field, "key": key, "value": value,
                "quotes": [quote] if quote else [],
                "ts": now, "first_ts": now,
                "confidence": confidence, "importance": importance,
                "strength": _strength_days(importance, is_fact=True),
                "recall_count": 0, "last_recall": now,
                "history": [], "ask_count": 0,
            }
            self.facts.append(fact)
            if confidence < CONFIRM_CONF and importance >= 5:
                self._enqueue_confirm(fact["id"], now)
            return (fact["id"], True)

        if _norm_value(found["value"]) == value:
            # 重复提及 = 回忆强化（"孙子叫豆豆"被反复确认就永远记住）
            found["ts"] = now
            found["strength"] = min(STRENGTH_CAP_DAYS, found["strength"] * 1.3 + 1.0)
            found["confidence"] = min(1.0, found["confidence"] + 0.15)
            found["importance"] = max(found["importance"], importance)
            if quote and quote not in found["quotes"]:
                found["quotes"] = (found["quotes"] + [quote])[-5:]
            found["recall_count"] += 1
            found["last_recall"] = now
            return (found["id"], False)

        # 冲突：事实带时间戳，近期覆盖远期，旧值入历史
        found["history"].append({"value": found["value"], "ts": found["ts"],
                                 "quote": found["quotes"][0] if found["quotes"] else ""})
        found["history"] = found["history"][-5:]
        found["value"] = value
        found["ts"] = now
        found["confidence"] = confidence
        found["importance"] = max(found["importance"], importance)
        found["quotes"] = [quote] if quote else []
        if confidence < CONFIRM_CONF:
            self._enqueue_confirm(found["id"], now)
        return (found["id"], False)

    def _enqueue_confirm(self, fact_id, now):
        if any(c["fact_id"] == fact_id for c in self.pending_confirms):
            return
        if len(self.pending_confirms) >= 5:
            self.pending_confirms.pop(0)
        self.pending_confirms.append({"fact_id": fact_id, "ts": now})

    # ---- 检索（三因子）----
    REL_SIM_SCALE = 0.10   # 轻量相似度→[0,1] 的饱和尺度：明显相关≈0.10+，对齐 embedding 余弦量纲

    def _score(self, mem, query, now):
        last = mem.get("last_recall", mem.get("ts", now))
        hours = max(0.0, (now - last) / 3600.0)
        recency = 0.995 ** hours
        imp = mem.get("importance", 3) / 10.0
        if "text" in mem:      # 情节/反思
            text = mem["text"]
        else:                  # 事实：键+值+首条原话——老人自己的措辞是最好的检索线索
            text = f"{mem.get('key', '')}{mem.get('value', '')}"
            quotes = mem.get("quotes") or []
            if quotes:
                text += quotes[0]
        relevance = min(1.0, similarity(query, text) / self.REL_SIM_SCALE)
        w = self.weights
        return w[0] * recency + w[1] * imp + w[2] * relevance

    def retrieve(self, query, k=5, now=None, reinforce=False):
        """三因子检索：score = α·新近性 + β·重要性 + γ·相关性（淡忘记忆不参与）。
        与存活事实重复的情节（同一信息的踪迹）不再占检索名额。"""
        now = now or self.clock()
        with self._lock:
            live_facts = [f for f in self.facts if retrievability(f, now) >= FORGET_THRESHOLD]
            live_eps = [e for e in self.episodes if retrievability(e, now) >= FORGET_THRESHOLD]
            live_refl = [r for r in self.reflections if retrievability(r, now) >= FORGET_THRESHOLD]
            # 去重：情节文本同时含某事实的键与值 → 视为该事实的踪迹，退出检索池
            dup_eps = {id(e) for e in live_eps
                       for f in live_facts
                       if f.get("key") and f["key"] in e.get("text", "")
                       and str(f.get("value", "")) in e.get("text", "")}
            pool = live_facts + [e for e in live_eps if id(e) not in dup_eps] + live_refl
            scored = sorted(((self._score(m, query, now), m) for m in pool),
                            key=lambda x: -x[0])
            top = [m for _s, m in scored[:k]]
            if reinforce and top:
                for m in top:
                    _reinforce(m, now)
                self._save()
            return top

    def build_chat_context(self, query, k_facts=4, k_episodes=2, now=None):
        """检索相关记忆并格式化为对话上下文块；同时完成回忆强化。无记忆返回空串"""
        now = now or self.clock()
        with self._lock:
            facts = [f for f in self.facts if retrievability(f, now) >= FORGET_THRESHOLD]
            facts = sorted(facts, key=lambda f: -self._score(f, query, now))[:k_facts]
            used_keys = {f["key"] for f in facts}
            eps = [e for e in self.episodes if retrievability(e, now) >= FORGET_THRESHOLD]
            eps = [e for e in sorted(eps, key=lambda e: -self._score(e, query, now))
                   if not any(k and k in e["text"] for k in used_keys)][:k_episodes]
            refl = [r for r in self.reflections if retrievability(r, now) >= FORGET_THRESHOLD]
            refl = sorted(refl, key=lambda r: -self._score(r, query, now))[:1]
            recalled = facts + eps + refl
            for m in recalled:
                _reinforce(m, now)
            self._save()

        lines = []
        if facts or eps:
            lines.append("[你记得的关于老人的信息（聊天时自然地用，别生硬罗列）]")
            for f in facts:
                item = f["value"] if f["key"] == f["value"] else f"{f['key']}：{f['value']}"
                lines.append(f"- {item}")
            for e in eps:
                lines.append(f"- 老人提过：{e['text']}")
        if refl:
            lines.append("[你最近对老人的了解]")
            for r in refl:
                lines.append(f"- {r['text']}")
        return "\n".join(lines)

    # ---- 反思升华 ----
    def _maybe_reflect(self, now, force=False):
        with self._lock:
            ready = ((self._reflect_imp >= REFLECT_IMP_THRESHOLD
                      and self._reflect_cnt >= REFLECT_CNT_THRESHOLD) or force)
            if not ready or now - self._last_reflect_try < REFLECT_RETRY_COOLDOWN:
                return None
            self._last_reflect_try = now
            self._reflect_imp = 0
            self._reflect_cnt = 0
            if self.llm is None:
                return None
            eps = sorted(self.episodes, key=lambda e: -e.get("ts", 0))[:20]
            ep_lines = "\n".join(f"- [{humanize_ago(e['ts'], now)}] {e['text']}" for e in eps)
            prof_lines = "\n".join(f"- {f['field']}｜{f['key']}：{f['value']}"
                                   for f in self.facts[:30])
        try:
            txt = self.llm(REFLECT_PROMPT.format(episodes=ep_lines or "（暂无）",
                                                 profile=prof_lines or "（暂无）"))
        except Exception as e:
            print(f"[memory] 反思失败: {e}")
            txt = ""
        concl = parse_json_strings(txt) if txt else []
        if not concl:
            return None
        with self._lock:
            for c in concl:
                self.reflections.append({
                    "id": self._next_id("r", now), "text": c, "ts": now,
                    "importance": 7, "strength": 40.0,
                    "recall_count": 0, "last_recall": now,
                })
            self._save()
        return concl

    # ---- 主动确认 ----
    def pop_confirm_question(self, persona="踏实务实", call="奶奶", now=None):
        """取一条待确认事实并生成问句（限频：全局冷却、每条最多问2次、淡忘不问）"""
        now = now or self.clock()
        with self._lock:
            if now - self._last_confirm_ask < CONFIRM_COOLDOWN:
                return None
            cand = None
            while self.pending_confirms:
                c = self.pending_confirms[0]
                fact = next((f for f in self.facts if f["id"] == c["fact_id"]), None)
                if (fact is None or fact.get("ask_count", 0) >= CONFIRM_MAX_ASK
                        or retrievability(fact, now) < FORGET_THRESHOLD
                        or fact.get("confidence", 1.0) >= CONFIRM_CONF):
                    self.pending_confirms.pop(0)
                    continue
                cand = fact
                self.pending_confirms.pop(0)
                break
            if cand is None:
                return None
            cand["ask_count"] = cand.get("ask_count", 0) + 1
            self._last_confirm_ask = now
            self.active_confirm = {"fact_id": cand["id"], "ts": now}
            self._save()
            return confirm_question(persona, call, cand["key"], cand["value"])

    def resolve_confirm_reply(self, text, persona="踏实务实", call="奶奶", now=None):
        """老人对确认问句的答复闭环。返回回复文案；None=没有待确认或答复不相关（不接管）"""
        now = now or self.clock()
        with self._lock:
            ac, self.active_confirm = self.active_confirm, None
            if not ac or now - ac["ts"] > CONFIRM_TTL:
                return None
            fact = next((f for f in self.facts if f["id"] == ac["fact_id"]), None)
            if fact is None:
                return None
            act, new_value = classify_confirm_reply(text)
            if act is None:
                return None            # 老人在说别的：不接管，交给正常聊天
            if act == "yes":
                fact["confidence"] = 1.0
                fact["strength"] = min(STRENGTH_CAP_DAYS, fact["strength"] * 1.5 + 1.0)
                fact["last_recall"] = now
                fact["recall_count"] += 1
                self._save()
                return confirm_yes_ack(persona, call, fact["key"], fact["value"])
            # act == "no"
            if new_value:
                fact["history"].append({"value": fact["value"], "ts": fact["ts"],
                                        "quote": fact["quotes"][0] if fact["quotes"] else ""})
                fact["history"] = fact["history"][-5:]
                fact["value"] = new_value
                fact["ts"] = now
                fact["confidence"] = 0.9
                self._save()
                return confirm_no_ack(persona, call, new_value)
            self._save()
            return confirm_no_ack(persona, call)

    # ---- 画像与展示 ----
    def profile_snapshot(self, now=None):
        """当前画像（淡忘与低置信已过滤/标记），供 UI 与评测使用"""
        now = now or self.clock()
        with self._lock:
            snap = {}
            for f in self.facts:
                if retrievability(f, now) < FORGET_THRESHOLD:
                    continue
                snap.setdefault(f["field"], []).append({
                    "id": f["id"], "key": f["key"], "value": f["value"],
                    "confidence": f.get("confidence", 0.8),
                    "ts": f.get("ts", now),
                    "recall_count": f.get("recall_count", 0),
                    "history": f.get("history", []),
                })
            for field in snap:
                snap[field].sort(key=lambda x: -x["ts"])
            return {k: snap[k] for k in FIELDS if k in snap}

    def timeline(self, limit=8, now=None):
        now = now or self.clock()
        with self._lock:
            eps = [e for e in self.episodes if retrievability(e, now) >= FORGET_THRESHOLD]
            return sorted(eps, key=lambda e: -e["ts"])[:limit]

    def stats(self, now=None):
        now = now or self.clock()
        with self._lock:
            active_facts = [f for f in self.facts if retrievability(f, now) >= FORGET_THRESHOLD]
            return {
                "facts": len(active_facts),
                "episodes": len(self.episodes),
                "reflections": len(self.reflections),
                "recalls": sum(int(f.get("recall_count", 0)) for f in self.facts),
            }

    def panel_html(self, now=None):
        """侧边栏「记忆与画像」面板（保持旧面板暖色折叠风格）"""
        now = now or self.clock()
        snap = self.profile_snapshot(now)
        refls = sorted(self.reflections, key=lambda r: -r.get("ts", 0))[:3]
        eps = self.timeline(8, now)
        st = self.stats(now)

        html_parts = []
        # 1) 画像
        if snap:
            for idx, (field, items) in enumerate(snap.items()):
                icon, bg, border = FIELD_INFO.get(field, FIELD_INFO["其他"])
                entries = "".join(
                    f"<div style='padding:3px 0;font-size:15px;'>"
                    + (it['value'] if it['key'] == it['value']
                       else f"{it['key']}：{it['value']}")
                    + ("<span style='color:#E65100;font-size:12px;'>（待确认）</span>"
                       if it["confidence"] < CONFIRM_CONF else "")
                    + f"<span style='color:#aaa;font-size:12px;margin-left:6px;'>"
                      f"{humanize_ago(it['ts'], now)}</span></div>"
                    for it in items[:6])
                cid = f"mp{idx}"
                html_parts.append(f"""
        <div style='background:{bg};border:1px solid {border};border-radius:10px;margin-bottom:8px;overflow:hidden;'>
            <input type="checkbox" id="{cid}" checked style="display:none;">
            <label for="{cid}" style="display:flex;align-items:center;justify-content:space-between;padding:7px 10px;cursor:pointer;font-size:15px;font-weight:bold;user-select:none;">
                <span>{icon} {field} <span style="font-size:12px;color:#999;">{len(items)}条</span></span>
                <span style="font-size:12px;color:#999;">▼</span>
            </label>
            <div style="padding:0 10px 8px;">{entries}</div>
        </div>""")
        else:
            html_parts.append(
                "<div style='color:#8B5E34;padding:8px;text-align:center;'>"
                "多陪老人聊聊，小忆就会记住 TA 的事</div>")

        # 2) 反思
        if refls:
            entries = "".join(
                f"<div style='padding:3px 0;font-size:14px;color:#5D4037;'>"
                f"💭 {r['text']}<span style='color:#aaa;font-size:12px;margin-left:6px;'>"
                f"{humanize_ago(r.get('ts', now), now)}</span></div>"
                for r in refls)
            html_parts.append(
                f"<div style='background:#FFF8E1;border:1px solid #FFE082;border-radius:10px;"
                f"padding:8px 10px;margin-bottom:8px;'>"
                f"<div style='font-size:15px;font-weight:bold;margin-bottom:4px;'>💡 小忆的理解</div>"
                f"{entries}</div>")

        # 3) 记忆时间线
        if eps:
            entries = "".join(
                f"<div style='padding:4px 0 4px 16px;font-size:15px;position:relative;"
                f"border-left:2px solid #C88A52;margin-bottom:2px;'>"
                f"<span style='position:absolute;left:-5px;top:9px;width:8px;height:8px;"
                f"border-radius:50%;background:#C88A52;'></span>"
                f"{'⭐' if e.get('importance', 3) >= 7 else ''} {e['text']}"
                f"<span style='color:#aaa;font-size:12px;margin-left:4px;'>"
                f"{humanize_ago(e['ts'], now)}</span></div>"
                for e in reversed(eps))
            html_parts.append(
                f"<div style='background:#fff;border:1px solid #E0D3C0;border-radius:10px;"
                f"padding:8px 10px;'>"
                f"<div style='font-size:15px;font-weight:bold;margin-bottom:4px;'>🕘 最近记下的</div>"
                f"{entries}</div>")

        stats_line = (f"<div style='margin-top:8px;font-size:13px;color:#8B5E34;text-align:center;'>"
                      f"记住{st['facts']}件事 · 被想起{st['recalls']}次 · 沉淀{st['reflections']}条理解</div>")
        return "".join(html_parts) + stats_line

    # ---- 旧数据迁移 ----
    def import_legacy_events(self, path, max_age_days=7):
        """把旧版 memory_events.json 的关键词事件导入为情节记忆（一次性）"""
        if not os.path.exists(path):
            return 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                events = json.load(f)
        except Exception:
            return 0
        if not isinstance(events, list):
            return 0
        now = self.clock()
        type_map = {"health": ("健康", 5), "medication": ("用药", 6), "item": ("物品", 3),
                    "family": ("家庭", 5), "habit": ("习惯", 3), "emotion": ("情绪", 6)}
        n = 0
        with self._lock:
            existing = {(e.get("ts", 0), e.get("text", ""))
                       for e in self.episodes}   # 幂等：已导入的不重复导入
            for e in events:
                ts = float(e.get("time", 0) or 0)
                content = str(e.get("content", "")).strip()
                if not content or now - ts > max_age_days * 86400:
                    continue
                if (ts, content) in existing:
                    continue
                field, imp = type_map.get(e.get("type"), ("其他", 3))
                self.episodes.append({
                    "id": self._next_id("e", ts),
                    "text": content, "quote": content, "field": field,
                    "ts": ts, "importance": imp,
                    "strength": _strength_days(imp, is_fact=False),
                    "recall_count": 0, "last_recall": ts,
                })
                n += 1
            self._save()
        return n

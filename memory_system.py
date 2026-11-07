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
from datetime import datetime, timedelta

__all__ = [
    "MemoryBank", "FIELDS", "FIELD_INFO", "keyword_extract", "similarity",
    "retrievability", "parse_llm_facts", "parse_json_strings",
    "humanize_ago", "classify_confirm_reply",
    "SYMPTOM_VARIANTS", "match_symptoms",
    "parse_time_range", "humanize_day", "_boost_important",
    "SCENE_LEXICON", "VISITOR_WORDS", "PET_WORDS",
    "CORRECT_INTENT_RE", "OLD_VALUE_Q_RE",
    "SENSITIVE_SCENES", "DEDUP_WINDOW_SECONDS", "VISUAL_HIGH_CONFIDENCE",
    "EmergencyContactStore", "EMERGENCY_CALL_RE", "_extract_phone",
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
8. value 必须是老人原话里的原文措辞（可去掉语气词），不要改写、不要补全；找不到原话就不抽这条。
9. 位置类信息（东西放在哪）的 key 统一写成"物品名+位置"，如"降压药位置"、"老花镜位置"，保证多次提及能互相覆盖。

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

# 位置类键的特征词（仅当键本身带位置语义时才触发归一，不影响普通键）
_LOCATION_KEY_RE = re.compile(r"放在|搁在|存放在?|收在?|位置|在哪|哪里|哪儿|哪|啥地方|地方|什么|咋|嘛|放|搁|存|收")

def _norm_key(key):
    k = re.sub(r"[\s，。,.！!？?：:、]+", "", str(key or ""))
    k = re.sub(r"(名字|姓名)$", "", k)     # "孙子的名字" -> "孙子的"
    k = k.rstrip("的")                     # "孙子的" -> "孙子"
    # Bug⑤b：位置类键归一——"降压药放哪/药的位置/降压药存放" → "降压药位置"。
    # 依据：Fellegi & Sunter (1969) record linkage——同一实体的不同表述须归一到
    # 规范键再合并；_add_fact 的 (field,key) 冲突分支随即生效：新的覆盖旧的。
    if k.endswith("位置"):
        # 已是规范键：只剥"位置"前的"的"（"药的位置"→"药位置"），重跑归一会误伤
        # （"放大镜位置"→"大镜位置"）
        base = k[:-2].rstrip("的")
        return (f"{base}位置" if base else k)[:12]
    if _LOCATION_KEY_RE.search(k):
        obj = _LOCATION_KEY_RE.sub("", k).rstrip("的")
        if obj:
            k = f"{obj[:6]}位置"
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

# ---------------------------------------------------------------- Bug⑤a：值溯源校验（治编造）
# 依据：FactCC (Kryściński et al., EMNLP 2020) —— 生成内容必须有源文支撑跨度；
# SummaC (Laban et al., ACL 2022) 的 NLI 蕴含判断在此简化为"子串+滑窗相似度"，
# 零模型开销、离线可用，与本项目"无 LLM 也能跑"的约束一致。

_GROUND_STRIP_RE = re.compile(r"[\s，。,.！!？?；;：:\"'“”（）()\[\]、的了呢啊吧么呀]")

def _ground_norm(s):
    """溯源比对用的归一：去口语尾字/标点 + 中文数字→阿拉伯（两侧对称，仅供比较）"""
    s = _GROUND_STRIP_RE.sub("", str(s or ""))
    for cn, ar in (("一", "1"), ("二", "2"), ("两", "2"), ("三", "3"), ("四", "4"),
                   ("五", "5"), ("六", "6"), ("七", "7"), ("八", "8"), ("九", "9"),
                   ("十", "10"), ("点", ".")):
        s = s.replace(cn, ar)
    return s

def _value_grounded(value, user_text, min_sim=0.75):
    """抽取值（按逗号/顿号分段）必须每段都能在老人原话中命中：
    直接子串，或滑窗 bigram 相似度兜底（应对"上初二了/上初二"类尾差）。
    找不到 → False（视为编造，调用方丢弃）。"""
    text = _ground_norm(user_text)
    if not text:
        return False
    parts = [p for p in re.split(r"[，,、;；/]", str(value or "")) if p.strip()]
    if not parts:
        return False
    for p in parts:
        p = _ground_norm(p)
        if not p:
            continue
        if p in text:
            continue
        if len(p) >= 4:                    # 滑窗容差：窗口=值长+2
            n = len(p)
            hit = any(similarity(p, text[i:i + n + 2]) >= min_sim
                      for i in range(0, max(1, len(text) - n + 3)))
            if hit:
                continue
        return False
    return True

def _source_span(value, user_text):
    """定位值在原话中的来源分句（可溯源原话片段）；找不到则退回首句前60字。"""
    t = str(user_text or "")
    p = _ground_norm(value)
    if not p:
        return t[:60]
    for seg in re.split(r"[，,。！!？?；;]", t):
        if p in _ground_norm(seg):
            return seg.strip() or t[:60]
    return t[:60]

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

# Bug②修复：健康症状变体表——口语/书面/方言同义归一到规范症状名。
# 依据：CBLUE (arXiv:2106.08087) CMeEE 医学实体归一；同一症状必须覆盖全部常见变体，
# 否则"头痛/头昏/偏头风"等表述漏触发。本表是唯一数据源，app.py 主动关怀同源引用。
SYMPTOM_VARIANTS = {
    "头疼": ["头疼", "头痛", "偏头疼", "偏头痛", "偏头风", "头风", "头昏", "头昏沉", "头沉",
             # 口语插入语变体（"头…疼"之间夹程度/频率词，子串匹配够不到）
             "头有点疼", "头有点儿疼", "头很疼", "头特别疼", "头好疼", "头老是疼", "头总是疼"],
    "头晕": ["头晕", "眩晕", "晕乎乎", "天旋地转", "站不稳",
             "头有点晕", "头有点儿晕", "头很晕"],
    "心慌": ["心慌", "心悸", "心跳得慌", "心里发慌", "心跳快"],
    "胸闷": ["胸闷", "心口闷", "胸口发闷", "胸口疼", "气短", "喘不上气"],
    "膝盖疼": ["膝盖疼", "膝盖痛", "腿弯疼", "膝关节疼", "膝盖不好"],
    "腰疼": ["腰疼", "腰痛", "腰酸", "腰不舒服", "腰不好"],
    "胃不舒服": ["胃疼", "胃痛", "肚子疼", "肚子痛", "胃口不好", "胃不舒服", "肚子不舒服"],
}
SYMPTOM_CANON = {}
for _canon, _vs in SYMPTOM_VARIANTS.items():
    for _v in _vs:
        SYMPTOM_CANON.setdefault(_v, _canon)

def match_symptoms(text):
    """匹配症状变体，返回去重后的规范症状名列表。
    按变体长度降序扫描，保证"偏头痛"先于"头痛"命中、不被短变体截断。"""
    t = str(text or "")
    hits = []
    for v in sorted(SYMPTOM_CANON, key=len, reverse=True):
        if v in t and SYMPTOM_CANON[v] not in hits:
            hits.append(SYMPTOM_CANON[v])
    return hits

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

    # Bug②：先走变体表归一（头痛/头昏/偏头风 → 头疼），再补 KW_HEALTH_RE 的其余模式
    health_hits = match_symptoms(t)
    for kw in dict.fromkeys(KW_HEALTH_RE.findall(t)):   # 去重保序
        if kw not in health_hits and kw not in SYMPTOM_CANON:
            health_hits.append(kw)
    for kw in health_hits:
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

# ---------------------------------------------------------------- 时间型提问（反馈9）
# 客户贴的对话：问"前两天发生了什么事"，小忆反复拿今天的事回答。
# 根因：记忆注入只认"字面相似"，时间型提问（前两天/上周）匹配不到任何记忆，
# 小忆手里没料，只能拿最近上下文硬答。这里把口语时间词解析成"日期区间"，
# 区间内的情节 + 1 条重要事件单独注入，并且"过去类"区间不含今天。

TIME_QUERY_PATTERNS = [
    # (正则, 区间)  区间 = 相对今天的日期偏移闭区间 (start, end)，end<0 表示不含今天
    (re.compile(r"大前天"), (-3, -3)),
    (re.compile(r"前天"), (-2, -2)),
    (re.compile(r"前两天|前几天|前些天|这之前"), (-3, -1)),
    (re.compile(r"昨天|昨儿|昨晚|昨天晚上"), (-1, -1)),
    (re.compile(r"上个?星期|上个?礼拜"), "last_week"),
    (re.compile(r"上个月"), (-60, -1)),
    (re.compile(r"最近|最近几天|这几天|这阵子|这几回"), (-3, 0)),
]

TIME_QUERY_MAXLEN = 40   # 超过此长度的多为叙述而非提问，不做时间注入


def parse_time_range(text, now=None):
    """时间型提问 -> (start_off, end_off, 时间词)；非时间型提问返回 None。
    start/end 为相对今天的日期偏移（天），闭区间；end=0 表示含今天。"""
    t = str(text or "").strip()
    if not t or len(t) > TIME_QUERY_MAXLEN:
        return None
    for pat, rng in TIME_QUERY_PATTERNS:
        m = pat.search(t)
        if not m:
            continue
        if rng == "last_week":
            today = datetime.fromtimestamp(now or _time.time()).date()
            this_monday = today - timedelta(days=today.weekday())
            last_monday = this_monday - timedelta(days=7)
            last_sunday = this_monday - timedelta(days=1)
            return ((last_monday - today).days, (last_sunday - today).days, m.group(0))
        return (rng[0], rng[1], m.group(0))
    return None


def humanize_day(ts, now=None):
    """时间戳 -> 老人友好的日期说法：今天/昨天/前天/X月X号"""
    now = now or _time.time()
    d = datetime.fromtimestamp(ts).date()
    today = datetime.fromtimestamp(now).date()
    delta = (d - today).days
    if delta == 0:
        return "今天"
    if delta == -1:
        return "昨天"
    if delta == -2:
        return "前天"
    if delta == 1:
        return "明天"
    return d.strftime("%m月%d号")


# ---------------------------------------------------------------- 重要事件加权（反馈9 完整版）
# 生日、成绩、大病、家庭大事：重要性提到 8-10 分、衰减放慢到 60 天以上，
# 告别"生日前一天说的，今天就记不住"。
IMPORTANT_EVENT_RE = re.compile(
    r"生日|寿辰|过大寿|忌日|纪念日|结婚|金婚|银婚|钻石婚|去世|走了|病逝"
    r"|住院|开刀|手术|化疗|放疗|透析|出院|急救|大病|骨折"
    r"|成绩|考试|考了|考上|升学|高考|中考|得奖|获奖|金榜题名"
    r"|添丁|添了个|生了个|抱上|四世同堂|当兵|入伍|搬家|乔迁|退休")

IMPORTANT_EVENT_MIN_IMP = 8
IMPORTANT_EVENT_MIN_STRENGTH_DAYS = 60.0


def _boost_important(raw_facts):
    """命中的重要事件 importance 提到 >=8（调用方据此放慢衰减）。"""
    for f in raw_facts:
        blob = f"{f.get('field', '')}{f.get('key', '')}{f.get('value', '')}{f.get('quote', '')}"
        if IMPORTANT_EVENT_RE.search(blob):
            f["importance"] = max(int(f.get("importance", 5)), IMPORTANT_EVENT_MIN_IMP)
    return raw_facts


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
    # 反馈12（任务⑤）：确认从考试式问句改为自然带出，老人听着不打断
    if persona == "风趣幽默":
        return f"哎{call}，我这脑子灵光着呢——{key}是{value}，记得门儿清！"
    if persona == "暖心知心":
        return f"{call}，您跟我说过{key}是{value}，我一直记在心里呢。"
    return f"对了{call}，我记着呢，您之前提过{key}是{value}。"

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
    d0 = datetime.fromtimestamp(ts)
    return f"{d0.month}月{d0.day}号"   # 手拼：strftime 中文在 GB locale 终端会崩

# ---------------------------------------------------------------- B-5：三维检索（按人/物/场景）+ 显式更正
# 任务卡 B-5 口径（2026-11 分工）：
#   - 检索维度按人/物/场景，复用 similarity()（bigram），不新建模型
#   - 人物 MVP 只承诺三类（本人/访客/宠物）+ 人物档案；无档案的具名人物（"孙子"）
#     退回"访客"类——F-4 兜底：只显"有客人来过"，绝不显示猜的人名（AI 不猜）
#   - 场景字段 = H-2 帧的粗位置标签+家具标签（mem["scene"]，MVP 不做场景分类）；
#     无标签时退 SCENE_LEXICON 同义词表 + bigram 相似度（"厨房"↔"做饭"靠词典补召回）
#   - 显式更正："不叫豆豆叫毛毛" → 值更新 + 旧值入 history（可查，不是物理删）；
#     新旧值规则：AI 回答一律用新值；老人问"之前叫啥"→ 答旧值+补"后来改成X了"

SCENE_LEXICON = {
    "客厅": ["客厅", "沙发", "茶几", "看电视"],
    "厨房": ["厨房", "做饭", "烧菜", "炒菜", "烧水", "灶台", "锅", "洗碗"],
    "卧室": ["卧室", "床上", "床头柜", "睡觉", "午睡", "被子"],
    "卫生间": ["卫生间", "厕所", "洗手间", "洗澡", "洗漱"],
    "阳台": ["阳台", "晾衣服", "晒被子", "浇花"],
    "餐厅": ["餐厅", "餐桌", "吃饭"],
}
VISITOR_WORDS = ["客人", "来客", "来人", "有人来", "邻居", "保姆", "上门", "来访", "看望", "串门"]
PET_WORDS = ["猫", "狗", "宠物", "小鸟", "金鱼", "鹦鹉", "乌龟", "鸟"]
SELF_WORDS = ("我", "俺", "自己", "本人")
_FAMILY_RELATION_RE = re.compile(
    r"孙子|孙女|儿子|闺女|女儿|老伴|儿媳|儿媳妇|女婿|弟弟|妹妹|哥哥|姐姐|外孙")

# 显式更正意图（确定性流程，不调模型）。只认两种最不易误触发的句式：
#   ① "X不叫/别叫/不再叫OLD（了），叫/改成NEW"     ② "（把）X（的名字）改成/改为/改叫NEW"
# 其余自然聊天里的纠正由 observe 的 LLM 抽取走 _add_fact 冲突分支，同样保留
# history——两条路径殊途同归，本正则只是给最典型句式一条不依赖模型的高速路。
CORRECT_INTENT_RE = re.compile(
    r"(?P<thing0>[^\s，。,.！!？?；;]{0,8}?)(?:不叫|别叫|不再叫)"
    r"(?P<old0>[^\s，。,.！!？?；;]{1,12}?)[了吗呢啊呀]*[，,。]?\s*"
    r"(?:叫|改成|改为|改叫)(?P<new0>[^\s，。,.！!？?；;]{1,12})"
    r"|(?:把)?(?P<thing1>[^\s，。,.！!？?；;]{1,8}?)"
    r"(?:的名字|名字)?(?:改成|改为|改叫)(?P<new1>[^\s，。,.！!？?；;]{1,12})"
)

# 旧值问句："我孙子之前叫啥来着" → 答旧值 + 补"后来改成X了"（规则写死）
OLD_VALUE_Q_RE = re.compile(
    r"(?P<thing>[^\s，。,.！!？?；;]{0,8}?)"
    r"(?:以前|之前|原先)(?:的名字|名字)?(?:叫|是)(?:什么|啥|啥呀|啥来着|啥了|什么呢)"
)


def _strip_pronoun(thing):
    """更正/提问里的主语去掉称谓前缀："我孙子"→"孙子"、"把豆豆"→"豆豆"、"我家的猫"→"猫"。"""
    t = re.sub(r"^(?:我们家|俺们家|咱家|我家|把|让|给|俺们|俺|我|咱)", "", str(thing or ""))
    t = t.strip().strip("的").strip()
    return t


# ---------------------------------------------------------------- B-4：记忆写入规则（持续录制开闸前的"卫生标准"）
# 任务卡 B-4 口径（2026-11 分工）：
#   - 四类规则：什么记 / 什么不记 / 去重（同物同位置 30 分钟一条）/ 分级写入
#   - 什么不记：第三方只记"有人来过"（只判是不是老人，不跑第三方人脸识别）；
#     敏感场景默认拒写（装机 SOP 物理规避为主 + 区域标签拒写兜底）+ 免看清单口头配置
#   - **重要边界**：免看清单/敏感区拒写只管视觉写入路径（ingest_visual_event），
#     不管语音——老人亲口说的"我在卫生间摔了一跤"是健康事件必须记。
#     摄像头有隐私边界，老人的嘴没有（他自己选择说的）。

SENSITIVE_SCENES = ("卧室", "卫生间", "更衣", "厕所", "浴室", "洗手间", "洗澡间")
DEDUP_WINDOW_SECONDS = 30 * 60       # 去重窗口：同物品+同动作+同位置 30 分钟内只写一条
VISUAL_HIGH_CONFIDENCE = 0.8         # ≥0.8 进长期记忆（episodes）；<0.8 只进短期缓存
SHORT_TERM_BUFFER_CAP = 50           # 低置信短期缓存滚动上限（不进记忆箱、检索不可见）

# 免看清单口头配置（确定性流程，不调模型）：卧室/卫生间这类标准场景词归一后增删，
# 认不出的说法（"电视别看了"）不接管，放行给正常聊天
_IGNORE_ZONE_ADD_RE = re.compile(
    r"(?:(?P<zone_a>[^\s，。,.！!？?；;]{1,8}?)(?:别看|不要看|不想看|别拍|不想让看|不想被看)"
    r"|(?:别看|不要看|不想看|别拍)(?P<zone_b>[^\s，。,.！!？?；;]{1,8}))了?[吧呀啊呢]?")
_IGNORE_ZONE_DEL_RE = re.compile(
    r"(?:(?P<zone_a>[^\s，。,.！!？?；;]{1,8}?)(?:可以看|能看|想让你看|接着看)"
    r"|(?:可以看|能看|想让你看|接着看)(?P<zone_b>[^\s，。,.！!？?；;]{1,8}))了?[吧呀啊呢]?")


def _resolve_zone(text):
    """口头配置里的区域说法归一到标准场景标签（SCENE_LEXICON + 敏感区）。
    "卧室那屋别看了"→"卧室"；"电视别看了"认不出 → None（不接管）。"""
    t = str(text or "").strip()
    if not t or len(t) > 10:
        return None
    known = list(SCENE_LEXICON.keys()) + list(SENSITIVE_SCENES)
    for scene in known:
        if scene in t:
            return scene
    return None


# ---------------------------------------------------------------- B-11：紧急联络（确定性流程，不调模型）
# 任务卡 B-11 口径（主动呼叫部分，P0）：
#   - 登记三路径：老人自报（逐位复述确认，首选）/ 子女端代填（仍向老人复述）/ 上门代填
#     —— 三路径对后端只是 source 不同，逐位复述确认全部走这里
#   - 可随时更正（同一复述确认流程）；三条全失败 → 标记未设置 + 每天提醒一次
#   - 主动触发：老人语音"帮我给孩子打电话"/"打120" → 发起呼叫（不自主拨120，诚实标注）
#   - 呼叫失败：超时/无人接 → 重试 1 次 → 兜底播报（绝不静默）
#   - 被动转报（P1）不在本模块：异常触发（跌倒/漏药）→ 经 B-9 通道，依赖 H-7/B-9

EMERGENCY_CALL_RE = re.compile(
    r"(给(?:我)?(?:的)?(?:孩子|儿子|女儿|闺女|家属|家人|紧急联系人|联系人)[打发]?个?电话"
    r"|打电话给(?:我)?(?:的)?(?:孩子|儿子|女儿|闺女|家属|家人|紧急联系人|联系人)"
    r"|帮我[^，。,.!？?]{0,8}打(?:个)?电话"
    r"|(?:快|赶紧)?打(?:个)?120|呼叫120|叫救护车)")
_EMERGENCY_CONTACT_INTENT_RE = re.compile(
    r"紧急联系人|(?:孩子|儿子|女儿|闺女)(?:的电话|号码)?是|联系人电话是")
_CALL_120_RE = re.compile(r"打(?:个)?120|呼叫120|叫救护车")
_PHONE_DIGITS_RE = re.compile(r"[0-9〇零一二三四五六七八九十百两]{6,}")

# 复述确认答复分类复用 classify_confirm_reply（"对/没错/是"→yes；"不对/不是"→no）


def _digits_to_norm(s):
    """中文数字/〇 → 阿拉伯数字（老人口报号码常混念）：'一三八'→'138'。"""
    mapping = {"〇": "0", "零": "0", "一": "1", "二": "2", "两": "2", "三": "3",
               "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}
    return "".join(mapping.get(ch, ch) for ch in str(s or ""))


def _extract_phone(text):
    """从话语提取电话号码（确定性）：返回 11 位手机号或 None。
    '我儿子电话13812345678' / '一三八幺…'（中文数字）都认；不足 11 位 → None（不猜）。"""
    m = _PHONE_DIGITS_RE.search(_digits_to_norm(str(text or "")))
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(0))
    if len(digits) == 11 and digits.startswith("1"):
        return digits
    return None


def _format_phone(phone):
    """逐位复述展示用：'138-1234-5678'。"""
    p = str(phone or "")
    return f"{p[0:3]}-{p[3:7]}-{p[7:11]}" if len(p) == 11 else p


# ---------------------------------------------------------------- B-3：找物三级回答（B-1 PRD §3/§4 契约的执行点）
# 任务卡 B-3 口径：查找记忆与图谱，两层兜底，source 标 text/graph；
# B-2 spike no-go 降级 → 只跑文字层，接口不变（降级不破接口契约，不返工）。
# 时间窗（B-1 PRD）：graph ≤30 天直答 / 30–180 stale / >180 不主动展示；
# text ≤90 天 / >90 stale / >180 不主动引用。编造 0 容忍：无依据只说"我没瞅见"。

FIND_OBJECT_RE = re.compile(
    r"(?P<obj>[^\s，。,.！!？?；;]{1,10}?)"
    r"(?:在哪里|在哪里了|在哪|在哪儿|哪儿|哪里|放哪了|放哪儿|放哪|搁哪了|搁哪|"
    r"去哪了|哪儿去了|哪去了|什么地方|啥地方)[了呀啊呢]?")
_FIND_OBJECT_PRONOUN_RE = re.compile(r"^(?:你|您|我|他|她|它|咱|谁)")
GRAPH_WINDOW_DAYS = 30          # graph 层：30 天内直接答
GRAPH_STALE_DAYS = 180         # graph 层：超 180 天不主动展示
TEXT_STALE_DAYS = 90            # text 层：超 90 天提示"还在不"
TEXT_MAX_DAYS = 180            # text 层：超 180 天不主动引用


def _extract_find_object(text):
    """从话语提取找物物品名（确定性，不调模型）："我降压药放哪了"→"降压药"。
    不是找物句式、或物品是代词（"你在哪"）→ None。"""
    t = str(text or "").strip()
    if not t or len(t) > 25:
        return None
    m = FIND_OBJECT_RE.search(t)
    if not m:
        return None
    obj = _strip_pronoun(m.group("obj"))
    obj = re.sub(r"[在是的去到放搁]$", "", obj).strip()   # 剥被吞进去的尾部介词
    if not obj or _FIND_OBJECT_PRONOUN_RE.match(obj):
        return None
    return obj


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
        self.person_profiles = []  # 人物档案（B-5）：{name, calls, quotes, ts, first_ts}
        # ---- B-4 写入护栏状态 ----
        self.ignore_zones = []       # 免看清单（口头配置）：区域标签，帧源按标签跳过
        self.visual_dedup_log = {}   # 去重窗口："{物}|{动作}|{位置}" -> 最后写入 ts
        self.short_term_buffer = [] # 低置信视觉条目短期缓存（不进记忆箱、检索不可见）
        self.write_stats = {"blocked_by_zone": 0, "blocked_by_dedup": 0,
                            "low_conf_buffered": 0, "visual_written": 0}
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
        self.person_profiles = data.get("person_profiles", []) or []
        self.ignore_zones = data.get("ignore_zones", []) or []
        self.visual_dedup_log = data.get("visual_dedup_log", {}) or {}
        self.short_term_buffer = data.get("short_term_buffer", []) or []
        ws = data.get("write_stats") or {}
        self.write_stats.update({k: int(v) for k, v in ws.items()
                                 if k in self.write_stats})
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
            "reflections": self.reflections, "person_profiles": self.person_profiles,
            "ignore_zones": self.ignore_zones, "visual_dedup_log": self.visual_dedup_log,
            "short_term_buffer": self.short_term_buffer, "write_stats": self.write_stats,
            "pending_confirms": self.pending_confirms,
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
        # 反馈9 完整版：生日/成绩/大病/家庭大事重要性提到 >=8（衰减随 _add_fact 放慢）
        raw = _boost_important(raw)
        # Bug⑤a：值溯源校验——抽取值必须能在老人原话里找到，找不到视为编造，丢弃（留日志供评测统计）
        grounded = []
        for f in raw:
            if _value_grounded(f.get("value"), user_text):
                grounded.append(f)
            else:
                print(f"[memory] 丢弃疑似编造事实: {f.get('field')}/{f.get('key')}="
                      f"{f.get('value')}（原话中找不到）")
        raw = grounded
        if not raw:
            return {"new": [], "updated": [], "episode": None, "reflection": None}
        # Bug⑤c：quote 缺失时回填来源分句，回答/画像均可溯源
        for f in raw:
            if not f.get("quote"):
                f["quote"] = _source_span(f["value"], user_text)

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
                # 反馈9：重要事件情节的衰减同样放慢（>=8 分至少 60 天）
                "strength": (max(_strength_days(imp_max, is_fact=False),
                                 IMPORTANT_EVENT_MIN_STRENGTH_DAYS)
                             if imp_max >= IMPORTANT_EVENT_MIN_IMP
                             else _strength_days(imp_max, is_fact=False)),
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
                # 反馈9：生日/成绩/大病/家庭大事等高重要事实，衰减至少 60 天
                "strength": (max(_strength_days(importance, is_fact=True),
                                 IMPORTANT_EVENT_MIN_STRENGTH_DAYS)
                             if importance >= IMPORTANT_EVENT_MIN_IMP
                             else _strength_days(importance, is_fact=True)),
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

    # ---- 遗忘（"忘了吧"就真删记忆）----
    def forget(self, pred):
        """删除满足谓词 pred(item) 的事实与情节记忆，返回删除条数。
        同时清理指向已删事实的待确认/进行中的确认，防止删除后还追问。
        伦理依据：老人对自己的记忆有删除权（被遗忘权），明确请求必须真删、立即生效。"""
        with self._lock:
            before = len(self.facts) + len(self.episodes)
            self.facts = [f for f in self.facts if not pred(f)]
            self.episodes = [e for e in self.episodes if not pred(e)]
            live_ids = {f["id"] for f in self.facts}
            self.pending_confirms = [c for c in self.pending_confirms
                                     if c["fact_id"] in live_ids]
            if self.active_confirm and self.active_confirm["fact_id"] not in live_ids:
                self.active_confirm = None
            removed = before - (len(self.facts) + len(self.episodes))
            if removed:
                self._save()
            return removed

    # ---- 撤回清除（反馈2/任务⑥：撤掉的话不许留在记忆里）----
    @staticmethod
    def _span_covers(quote, text):
        """quote 是否是 text 里的一个片段（原话溯源匹配）。
        quote 本就是从老人原话里截的 ≤60 字 span，用 bigram 覆盖率 ≥0.75 判定，
        容忍 LLM 抽取时少许改写；同句式换值（"叫豆豆"vs"叫毛毛"覆盖率 0.6）
        不会误判；quote 太短（<4 字）要求完全包含，避免误删。"""
        quote, text = str(quote or "").strip(), str(text or "").strip()
        if not quote or not text:
            return False
        if quote in text:
            return True
        if len(quote) < 4:
            return False
        gq = _char_ngrams(quote)
        if not gq:
            return False
        return len(gq & _char_ngrams(text)) / len(gq) >= 0.75

    def forget_by_source(self, user_text):
        """按"原话来源"清除一条消息引发的记忆（撤回用）。

        - 情节：quote 是该消息前 60 字 → 覆盖即删
        - 事实：quotes 里命中该消息的引语剔除；剔完后
          有 history → 回滚到上一个值（该轮只是更新）；
          无 history → 整条删（该轮新建）
        返回 {"episodes": n, "facts_deleted": n, "facts_rolled_back": n, "facts_unquoted": n}
        """
        counts = {"episodes": 0, "facts_deleted": 0,
                  "facts_rolled_back": 0, "facts_unquoted": 0}
        with self._lock:
            eps_keep = [e for e in self.episodes
                        if not self._span_covers(e.get("quote", ""), user_text)]
            counts["episodes"] = len(self.episodes) - len(eps_keep)
            self.episodes = eps_keep

            facts_keep = []
            for f in self.facts:
                quotes = f.get("quotes") or []
                kept_q = [q for q in quotes
                          if not self._span_covers(q, user_text)]
                if len(kept_q) == len(quotes):
                    facts_keep.append(f)      # 与该消息无关
                    continue
                if kept_q:
                    f["quotes"] = kept_q      # 还有别的轮次支撑，只摘引语
                    counts["facts_unquoted"] += 1
                    facts_keep.append(f)
                    continue
                if f.get("history"):
                    prev = f["history"].pop()   # 回滚到该轮更新前的值
                    f["value"] = prev.get("value", f.get("value", ""))
                    f["ts"] = prev.get("ts", f.get("ts", 0))
                    f["quotes"] = [prev["quote"]] if prev.get("quote") else []
                    counts["facts_rolled_back"] += 1
                    facts_keep.append(f)
                else:
                    counts["facts_deleted"] += 1   # 该轮新建的事实：整条删
            self.facts = facts_keep

            # 清掉指向已删事实的待确认/进行中确认，防止撤回后还追问
            live_ids = {f["id"] for f in self.facts}
            self.pending_confirms = [c for c in self.pending_confirms
                                     if c["fact_id"] in live_ids]
            if self.active_confirm and self.active_confirm["fact_id"] not in live_ids:
                self.active_confirm = None
            if any(counts.values()):
                self._save()
            return counts

    # ---- 检索（三因子）----
    REL_SIM_SCALE = 0.10   # 轻量相似度→[0,1] 的饱和尺度：明显相关≈0.10+，对齐 embedding 余弦量纲

    @staticmethod
    def _mem_text(mem):
        """记忆的检索文本：情节/反思用原文；事实用键+值+首条原话（老人自己的措辞是最好的检索线索）"""
        if "text" in mem:
            return mem["text"]
        text = f"{mem.get('key', '')}{mem.get('value', '')}"
        quotes = mem.get("quotes") or []
        if quotes:
            text += quotes[0]
        return text

    def _score(self, mem, query, now):
        last = mem.get("last_recall", mem.get("ts", now))
        hours = max(0.0, (now - last) / 3600.0)
        recency = 0.995 ** hours
        imp = mem.get("importance", 3) / 10.0
        text = self._mem_text(mem)
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
        """检索相关记忆并格式化为对话上下文块；同时完成回忆强化。无记忆返回空串。
        相关性闸门（2026-10-21）：只注入与当前话语有字面交集的记忆——小模型面对
        一堆不相关记忆会逐条念成问句（长对话混乱的根因）；无相关记忆时最多补 1 条
        imp≥7 核心画像（疾病/家人类）作锚点，保留"被记住"体验。未被注入的记忆不再
        被强化，按遗忘曲线自然淡出——每轮对话都在"清理"不相关的记忆。"""
        now = now or self.clock()
        with self._lock:
            live_facts = [f for f in self.facts if retrievability(f, now) >= FORGET_THRESHOLD]
            rel_facts = sorted((f for f in live_facts
                                if similarity(query, self._mem_text(f)) > 0),
                               key=lambda f: -self._score(f, query, now))
            rel_ids = {id(f) for f in rel_facts}
            anchor = None
            if len(rel_facts) < k_facts:
                cands = [f for f in live_facts
                         if id(f) not in rel_ids and f.get("importance", 0) >= 7]
                if cands:
                    anchor = max(cands, key=lambda f: self._score(f, query, now))
            facts = rel_facts[:k_facts] + ([anchor] if anchor else [])
            used_keys = {f["key"] for f in facts}
            eps = [e for e in self.episodes
                   if retrievability(e, now) >= FORGET_THRESHOLD
                   and similarity(query, e["text"]) > 0]
            eps = [e for e in sorted(eps, key=lambda e: -self._score(e, query, now))
                   if not any(k and k in e["text"] for k in used_keys)][:k_episodes]
            refl = [r for r in self.reflections
                    if retrievability(r, now) >= FORGET_THRESHOLD
                    and similarity(query, r["text"]) > 0]
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
                # Bug⑤c：注入原话出处，回答可溯源（依据 FactCC 支撑跨度思想）
                q = (f.get("quotes") or [""])[0]
                if q and _norm_value(q) != f["value"]:
                    item += f"（您说过：{q[:30]}）"
                lines.append(f"- {item}")
            for e in eps:
                lines.append(f"- 老人提过：{e['text']}")
        if refl:
            lines.append("[你最近对老人的了解]")
            for r in refl:
                lines.append(f"- {r['text']}")
        return "\n".join(lines)

    # ---- 反馈9（任务①）：时间型提问专用上下文 ----
    def build_time_context(self, query, k_episodes=3, now=None):
        """"前两天发生了什么事"这类时间型提问：注入该日期区间内的情节（最多3条）
        + 1 条重要事件，并带"昨天/前天"式日期标签；回答限定在这些日期内，
        杜绝拿今天的事硬答。非时间型提问返回空串（调用方不必预判）。"""
        parsed = parse_time_range(query, now=now)
        if not parsed:
            return ""
        start_off, end_off, time_word = parsed
        now = now or self.clock()
        today = datetime.fromtimestamp(now).date()
        d0, d1 = today + timedelta(days=start_off), today + timedelta(days=end_off)
        with self._lock:
            in_range = [e for e in self.episodes
                        if retrievability(e, now) >= FORGET_THRESHOLD
                        and d0 <= datetime.fromtimestamp(e.get("ts", 0)).date() <= d1]
            in_range.sort(key=lambda e: (-e.get("importance", 0), -e.get("ts", 0)))
            picked = in_range[:k_episodes]
            extra = next((e for e in in_range if e.get("importance", 0) >= 7
                          and id(e) not in {id(p) for p in picked}), None)
            if extra:
                picked.append(extra)
            for m in picked:
                _reinforce(m, now)
            self._save()
        if not picked:
            return (f"[老人问的是“{time_word}”的事。你那几天没有留下任何记录，"
                    "就如实说记不太清了，千万别拿今天的事凑数。]")
        mid = lambda d: datetime(d.year, d.month, d.day, 12).timestamp()
        span = (humanize_day(mid(d0), now) if d0 == d1
                else f"{humanize_day(mid(d0), now)}到{humanize_day(mid(d1), now)}")
        lines = [f"[老人问的是“{time_word}”（{span}）发生的事。回答只讲下面这些日期的事，"
                 "别掺今天的内容，记不清的就直说。]"]
        for e in picked:
            lines.append(f"- {humanize_day(e.get('ts', 0), now)}：{e['text']}")
        return "\n".join(lines)

    # ---- B-5①：三维检索（按人/按物/按场景，统一入口）----
    def _resolve_person(self, q):
        """人物维度解析：档案优先（名字/称谓命中）；无档案退三类（本人/访客/宠物）。
        具名人物（如"孙子"）无档案 → fallback_visitor——只显"有客人来过"，
        绝不猜人名（F-4 兜底逻辑）。"""
        q = re.sub(r"^[我们俺们咱]*[我俺咱]", "", str(q or "").strip()) or str(q or "").strip()
        # 1) 档案优先（AI 不猜：档案只来自老人主动陈述+确认，见 add_person_profile）
        for p in self.person_profiles:
            names = [p.get("name", "")] + list(p.get("calls") or [])
            for n in names:
                if n and (n in q or similarity(n, q) >= 0.3):
                    return {"kind": "profile", "profile": p, "label": p.get("name", "")}
        # 2) 三类（MVP 只承诺这三类）
        if any(w in q for w in PET_WORDS):
            return {"kind": "pet", "profile": None, "label": "宠物"}
        if any(w in q for w in VISITOR_WORDS):
            return {"kind": "visitor", "profile": None, "label": "访客"}
        if any(w in q for w in SELF_WORDS):
            return {"kind": "self", "profile": None, "label": "本人"}
        # 3) 无档案的具名人物 → 退回访客类（不显示猜的人名）
        return {"kind": "fallback_visitor", "profile": None, "label": "访客"}

    def _resolve_scene(self, q):
        """场景维度解析：词典命中（"做饭"→厨房）→ 相似度兜底；认不出返回 None。"""
        q = str(q or "").strip()
        best, best_s = None, 0.0
        for canon, words in SCENE_LEXICON.items():
            if q == canon or any(w in q or q in w for w in words):
                return {"kind": "scene", "scene": canon, "label": canon}
            s = max((similarity(q, w) for w in words), default=0.0)
            if s > best_s:
                best, best_s = canon, s
        if best is not None and best_s >= 0.4:
            return {"kind": "scene", "scene": best, "label": best}
        return {"kind": "scene", "scene": None, "label": None}

    @staticmethod
    def _search_item(mem, score):
        """检索结果条目（JSON 可序列化；facts 带 history 供"旧值可查"）
        类型判定：事实有 key 无 text；情节有 text；反思只剩 text 无 field。"""
        if "key" in mem:
            mtype = "fact"
        elif "field" in mem:
            mtype = "episode"
        else:
            mtype = "reflection"
        item = {"type": mtype, "id": mem.get("id"), "ts": mem.get("ts", 0),
                "score": round(float(score), 3)}
        if "text" in mem:
            item["text"] = mem["text"]
            item["quote"] = mem.get("quote", "")
        else:
            quotes = mem.get("quotes") or []
            item.update({"key": mem.get("key"), "value": mem.get("value"),
                         "quote": quotes[0] if quotes else "",
                         "history": mem.get("history") or []})
        return item

    def search(self, query, by="object", k=10, now=None):
        """统一检索入口（B-5，供 /api/memory/search、B-8 盲盒、F-4 时间线调用）。
        by ∈ person|object|scene；复用三因子打分（新近性×重要性×相关性）。
        只读检索：不强化、不改动记忆（翻时间线≠回忆强化，防浏览把记忆越翻越牢）。
        返回 {"by","query","resolved","results":[...]}：
          - person.resolved.kind ∈ profile|visitor|pet|self|fallback_visitor
            （fallback_visitor = 查无档案的具名人物，UI 只显"有客人来过"，不显猜的名字）
          - fact 条目带 history，旧值随检索可查
        """
        by = str(by or "").strip().lower()
        q = str(query or "").strip()
        if by not in ("person", "object", "scene"):
            return {"by": by, "query": q, "resolved": None, "results": [],
                    "message": "by 只支持 person/object/scene"}
        if not q:
            return {"by": by, "query": "", "resolved": None, "results": [],
                    "message": "空查询"}
        try:
            k = max(1, min(50, int(k)))
        except (TypeError, ValueError):
            k = 10
        now = now or self.clock()
        with self._lock:
            pool = ([f for f in self.facts if retrievability(f, now) >= FORGET_THRESHOLD]
                    + [e for e in self.episodes if retrievability(e, now) >= FORGET_THRESHOLD]
                    + [r for r in self.reflections if retrievability(r, now) >= FORGET_THRESHOLD])
            if by == "person":
                resolved = self._resolve_person(q)
                if resolved["kind"] == "profile":
                    p = resolved["profile"]
                    names = [n for n in [p.get("name", "")] + list(p.get("calls") or []) if n]
                    pool = [m for m in pool if any(n in self._mem_text(m) for n in names)]
                    score_q = names[0]
                elif resolved["kind"] in ("visitor", "fallback_visitor"):
                    pool = [m for m in pool
                            if any(w in self._mem_text(m) for w in VISITOR_WORDS)]
                    score_q = q
                elif resolved["kind"] == "pet":
                    pool = [m for m in pool
                            if any(w in self._mem_text(m) for w in PET_WORDS)]
                    score_q = q
                else:   # self：老人的记忆本来就是关于本人的
                    score_q = q
                scored = sorted(((self._score(m, score_q, now), m) for m in pool),
                                key=lambda x: -x[0])
                resolved = {kk: vv for kk, vv in resolved.items() if kk != "profile"}
                resolved["name"] = resolved.get("label")
            elif by == "object":
                pool = [m for m in pool if similarity(q, self._mem_text(m)) > 0]
                def _boost(m):
                    key = m.get("key") or ""
                    return 2.0 if key in (q, f"{q[:6]}位置") else 0.0
                scored = sorted(((self._score(m, q, now) + _boost(m), m) for m in pool),
                                key=lambda x: -x[0])
                resolved = {"kind": "object", "label": q, "object": q}
            else:   # scene
                resolved = self._resolve_scene(q)
                canon = resolved.get("scene")
                words = SCENE_LEXICON.get(canon, []) + ([canon] if canon else [])
                if canon is not None:
                    # 词典已归位：只收标签（H-2）+词典词命中——相似度兜底会把
                    # "做饭"串到"餐桌吃饭"（字面"饭"重叠），场景检索宁可少收不串台
                    pool = [m for m in pool
                            if m.get("scene") == canon
                            or any(w in self._mem_text(m) for w in words)]
                else:
                    # 场景没认出来（词典外场景）→ 相似度兜底
                    pool = [m for m in pool if similarity(q, self._mem_text(m)) > 0]
                scored = sorted(((self._score(m, q, now), m) for m in pool),
                                key=lambda x: -x[0])
            results = [self._search_item(m, s) for s, m in scored[:k]]
        return {"by": by, "query": q, "resolved": resolved, "results": results}

    # ---- B-5②：显式更正（值更新 + 历史保留）----
    def _find_fact_fuzzy(self, key=None, old_value=None):
        """按 key（精确→bigram 模糊 ≥0.45，ASR 容错）或旧值定位事实；找不到返回 None。
        护栏：模糊匹配不碰"X位置"键（位置事实归 H-3/B-2 找物流程管，
        "把降压药改成饭后吃"绝不许覆写"降压药位置=药箱"）；显式带"位置"的 key 走精确匹配。"""
        if key:
            fact = next((f for f in self.facts if f["key"] == key), None)
            if fact is not None:
                return fact
            cands = [(similarity(key, f.get("key", "")), f) for f in self.facts
                     if not (f.get("key", "").endswith("位置") and not key.endswith("位置"))]
            best = max(cands, key=lambda x: x[0], default=None)
            if best and best[0] >= 0.45:
                return best[1]
        if old_value:
            old_value = _norm_value(old_value)
            return next((f for f in self.facts
                         if _norm_value(f.get("value", "")) == old_value
                         or similarity(f.get("value", ""), old_value) >= 0.6), None)
        return None

    def correct_fact(self, key, new_value, old_value=None, quote="", now=None):
        """显式更正（B-5）：值即时更新、旧值入 history 保留可查（不是物理删）。
        新旧值规则：AI 回答一律用新值（fact.value 已更新，检索/注入自然用新值）。
        找不到目标事实时：key 有效 → 当新事实记下（老人亲口说的最可靠）；否则 None。"""
        now = now or self.clock()
        key = _norm_key(key) if key else ""
        new_value = _norm_value(new_value)
        if not new_value:
            return None
        with self._lock:
            fact = self._find_fact_fuzzy(key=key or None, old_value=old_value)
            if fact is None and old_value is None and key:
                # "把豆豆改成毛毛"：豆豆不是键是旧值 → 按值再定位一次
                fact = self._find_fact_fuzzy(key=None, old_value=key)
            if fact is None:
                if not key:
                    return None
                field = "家庭" if _FAMILY_RELATION_RE.search(key) else "其他"
                fid, _ = self._add_fact(field=field, key=key, value=new_value,
                                        quote=quote, importance=7, confidence=0.95, now=now)
                self._save()
                return {"action": "added", "fact_id": fid, "key": key,
                        "value": new_value, "previous": None}
            if _norm_value(fact["value"]) == new_value:
                fact["confidence"] = min(1.0, fact.get("confidence", 0.8) + 0.1)
                fact["strength"] = min(STRENGTH_CAP_DAYS, fact.get("strength", 1.0) * 1.3 + 1.0)
                fact["recall_count"] = int(fact.get("recall_count", 0)) + 1
                fact["last_recall"] = now
                self._save()
                return {"action": "noop", "fact_id": fact["id"], "key": fact["key"],
                        "value": fact["value"], "previous": None}
            prev = fact["value"]
            fact["history"].append({"value": prev, "ts": fact["ts"],
                                    "quote": fact["quotes"][0] if fact["quotes"] else ""})
            fact["history"] = fact["history"][-5:]
            fact["value"] = new_value
            fact["ts"] = now
            fact["confidence"] = 0.95          # 老人亲口更正 = 最高置信
            fact["importance"] = max(int(fact.get("importance", 5)), 7)
            if quote and quote not in fact["quotes"]:
                fact["quotes"] = (fact["quotes"] + [quote])[-5:]
            fact["last_recall"] = now
            self._save()
            return {"action": "corrected", "fact_id": fact["id"], "key": fact["key"],
                    "value": new_value, "previous": prev}

    def history_of(self, key, now=None):
        """旧值可查（B-5）：按 key（精确→模糊→按当前值/历史值）定位事实。
        返回 {"matched": key|current|old, "key", "value", "history":[...]}；None=没记过。"""
        key = _norm_key(key) if key else ""
        if not key:
            return None
        with self._lock:
            fact = self._find_fact_fuzzy(key=key)
            if fact is not None:
                matched = "key"
            else:
                fact = next((f for f in self.facts
                             if _norm_value(f.get("value", "")) == key), None)
                if fact is not None:
                    matched = "current"
                else:
                    fact = next((f for f in self.facts
                                 if any(_norm_value(h.get("value", "")) == key
                                        for h in f.get("history") or [])), None)
                    matched = "old" if fact is not None else None
            if fact is None:
                return None
            return {"matched": matched, "key": fact["key"], "value": fact["value"],
                    "history": list(fact.get("history") or [])}

    def handle_explicit_correction(self, text, call_name="奶奶", now=None):
        """显式更正 + 旧值问句的确定性闭环（B-5，供 app.chat_response 调用）：
          - "我孙子不叫豆豆，叫毛毛" / "把豆豆改成毛毛" → 值更新、旧值入历史
          - "我孙子之前叫啥来着" → 答旧值 + 补"后来改成X了"（规则写死，不靠模型发挥）
        返回回复文案；None = 不是这两类句式（放行给正常聊天）。话术里绝不编造：
        没记过/没改过就返回 None 交给正常对话，不说"您以前叫X"。"""
        t = str(text or "").strip()
        if not t or len(t) > 40:      # 更正/旧值问句都是短句，长句多半是叙述
            return None
        now = now or self.clock()
        m = CORRECT_INTENT_RE.search(t)
        if m:
            if m.group("new0"):
                thing, old, new = m.group("thing0"), m.group("old0"), m.group("new0")
            else:
                thing, old, new = m.group("thing1"), None, m.group("new1")
            thing = _strip_pronoun(thing)
            new = _norm_value(new)
            if not new:
                return None
            res = self.correct_fact(key=thing or None, new_value=new,
                                    old_value=old, quote=t[:60], now=now)
            if res is None:
                return None
            if res["action"] == "corrected":
                return (f"好嘞{call_name}，我这就改过来——{res['key']}现在是"
                        f"{res['value']}了，以前那个我记在老账上，往后都用新的。")
            if res["action"] == "noop":
                return f"嗯呐{call_name}，{res['key']}就是{res['value']}，我记得牢牢的。"
            return f"好嘞{call_name}，记下了——{res['key']}是{res['value']}。"
        m = OLD_VALUE_Q_RE.search(t)
        if m:
            thing = _strip_pronoun(m.group("thing"))
            if not thing:
                return None
            hist = self.history_of(thing, now=now)
            if hist is None or not hist.get("history"):
                return None       # 没记过/没改过 → 放行正常聊天（不编造旧值）
            cur = hist["value"]
            if hist["matched"] == "old":   # 老人拿旧值来问（"豆豆之前叫啥"）
                return f"{thing}是{hist['key']}以前的叫法，后来改成{cur}了。"
            olds = [h.get("value", "") for h in hist["history"]]
            if len(olds) == 1:
                return f"您之前{hist['key']}叫{olds[0]}，后来改成{cur}了。"
            return f"您{hist['key']}以前叫过{'、'.join(olds)}，现在叫{cur}。"
        return None

    # ---- B-5：人物档案（建档路径归 F-4/H-3，本处只提供存储+检索解析）----
    def add_person_profile(self, name, call, quote="", now=None):
        """人物档案（B-5 存储）：姓名+称谓，不存人脸特征（人脸比对按 B-4 只存本地）。
        建档唯一路径 = 老人主动陈述（"这是我孙子豆豆"）+ 小忆确认——AI 不猜、
        子女端不建档（零子女配置原则）。同名已存在 → 合并称谓；返回 {"ok",...}。"""
        now = now or self.clock()
        name, call = _norm_value(name), _norm_value(call)
        if not name:
            return {"ok": False, "message": "名字没听清，您再说一遍"}
        with self._lock:
            for p in self.person_profiles:
                if p.get("name") == name:
                    if call and call not in (p.get("calls") or []):
                        p["calls"] = (p.get("calls") or []) + [call]
                    if quote and quote not in (p.get("quotes") or []):
                        p["quotes"] = (p.get("quotes") or [quote])[-5:]
                    p["ts"] = now
                    self._save()
                    return {"ok": True, "name": name, "call": call}
            prof = {"name": name, "calls": [call] if call else [],
                    "quotes": [quote] if quote else [], "ts": now, "first_ts": now}
            self.person_profiles.append(prof)
            self._save()
            return {"ok": True, "name": name, "call": call}

    def remove_person_profile(self, name):
        """删除档案（老人说"别叫了"→退回三类）。返回删除条数。"""
        name = _norm_value(name)
        if not name:
            return 0
        with self._lock:
            before = len(self.person_profiles)
            self.person_profiles = [p for p in self.person_profiles
                                    if p.get("name", "") != name
                                    and similarity(p.get("name", ""), name) < 0.6]
            removed = before - len(self.person_profiles)
            if removed:
                self._save()
            return removed

    # ---- B-4：视觉条目写入护栏（四类规则的统一执行点，B-2/H-2 帧源调用）----
    def _zone_blocked(self, position):
        """位置标签是否被拒写：免看清单（口头配置）或敏感区（默认防线）。"""
        pos = str(position or "").strip()
        if not pos:
            return False
        if any(z and (z in pos or pos in z) for z in self.ignore_zones):
            return True
        # 敏感区兜底：装机 SOP 物理规避为主，这里按位置标签做最后防线
        # （机位视野偶然覆盖敏感区边缘时，"敏感区条目不写入"仍是规则）
        return any(s in pos for s in SENSITIVE_SCENES)

    def ingest_visual_event(self, event_type, object_name, position,
                            confidence, ts=None, furniture=""):
        """视觉条目统一写入入口（B-4，持续录制开闸后的"卫生标准"执行点）。
        规则顺序：① 免看清单/敏感区拒写 ② 30 分钟去重 ③ 低置信只进短期缓存
        ④ 高置信写长期记忆（episodes，source="visual"）。返回 {"action", ...}。

        边界：本入口只管摄像头帧；老人亲口说的（observe 语音路径）不经此护栏——
        老人说"我在卫生间摔了一跤"是健康事件必须记，摄像头才有隐私边界。
        """
        now = ts if ts is not None else self.clock()
        event_type = str(event_type or "appear").strip() or "appear"
        obj = _norm_value(object_name)
        pos = str(position or "").strip()
        if not obj:
            return {"action": "invalid", "reason": "missing_object"}
        try:
            conf = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            conf = 0.0
        with self._lock:
            # ① 免看清单 / 敏感区拒写
            if self._zone_blocked(pos):
                self.write_stats["blocked_by_zone"] += 1
                self._save()
                return {"action": "blocked_by_zone", "zone": pos}
            # ② 去重：同物品+同动作+同位置 30 分钟内只写一条（更新时间戳，不重复堆积）
            dedup_key = f"{obj}|{event_type}|{pos}"
            last = self.visual_dedup_log.get(dedup_key)
            if last is not None and 0 <= (now - last) < DEDUP_WINDOW_SECONDS:
                self.write_stats["blocked_by_dedup"] += 1
                self._save()
                return {"action": "blocked_by_dedup",
                        "window_left_sec": int(DEDUP_WINDOW_SECONDS - (now - last))}
            # 窗口过期条目顺带清理（防日志无限增长）
            self.visual_dedup_log = {k: v for k, v in self.visual_dedup_log.items()
                                     if (now - v) < DEDUP_WINDOW_SECONDS * 4}
            self.visual_dedup_log[dedup_key] = now
            # ③④ 分级写入：低置信只进短期缓存（不进记忆箱、检索不可见）；高置信进长期
            entry = {"event_type": event_type, "object": obj, "position": pos,
                     "furniture": str(furniture or ""), "confidence": round(conf, 3),
                     "ts": now}
            if conf < VISUAL_HIGH_CONFIDENCE:
                entry["id"] = self._next_id("vb", now)
                self.short_term_buffer.append(entry)
                self.short_term_buffer = self.short_term_buffer[-SHORT_TERM_BUFFER_CAP:]
                self.write_stats["low_conf_buffered"] += 1
                self._save()
                return {"action": "buffered", "entry": entry}
            verb = {"place": "放下", "take": "拿走", "appear": "瞅见"}.get(event_type, "瞅见")
            if event_type == "appear":
                text = f"看见{obj}在{pos}" + (f"的{furniture}" if furniture else "")
            else:
                text = f"{obj}被{verb}了，在{pos}"
            ep = {"id": self._next_id("ve", now), "text": text, "quote": text,
                  "field": "物品", "ts": now, "importance": 4 if conf < 0.9 else 5,
                  "strength": 1.0, "recall_count": 0, "last_recall": now,
                  "source": "visual", "scene": pos, "confidence": round(conf, 3),
                  "object": obj, "furniture": str(furniture or "")}
            self.episodes.append(ep)
            self.write_stats["visual_written"] += 1
            self._save()
            return {"action": "written", "entry": ep}

    # ---- B-4：免看清单（口头配置：随时一句话增删，无设置界面——与零配置一致）----
    def add_ignore_zone(self, zone):
        """加入免看清单：帧源按标签跳过对应区域（区域级跳过按粗位置标签，不依赖场景分类）。"""
        zone = str(zone or "").strip()
        if not zone or zone in self.ignore_zones:
            return False
        self.ignore_zones.append(zone)
        self._save()
        return True

    def remove_ignore_zone(self, zone):
        """从免看清单移除（"卧室可以看了"）。返回是否移除。"""
        zone = str(zone or "").strip()
        if zone not in self.ignore_zones:
            return False
        self.ignore_zones.remove(zone)
        self._save()
        return True

    def handle_ignore_zone_request(self, text, call_name="奶奶", now=None):
        """免看清单口头配置闭环（B-4，供 app.chat_response 调用，确定性流程）：
          - "卧室别看了" / "别看卧室了" → 加入免看清单
          - "卧室可以看了" / "能看卧室了" → 移除
        认不出的说法（"电视别看了"）返回 None 放行正常聊天，绝不猜区域。"""
        t = str(text or "").strip()
        if not t or len(t) > 20:
            return None
        m = _IGNORE_ZONE_DEL_RE.search(t)
        if m:
            zone_raw = m.group("zone_a") or m.group("zone_b") or ""
            zone = _resolve_zone(zone_raw)
            if zone:
                if zone in self.ignore_zones:
                    self.remove_ignore_zone(zone)
                    return f"好嘞{call_name}，{zone}我接着帮您留心着。"
                return f"{call_name}，{zone}本来就在看着呢，没关。"
        m = _IGNORE_ZONE_ADD_RE.search(t)
        if m:
            zone_raw = m.group("zone_a") or m.group("zone_b") or ""
            zone = _resolve_zone(zone_raw)
            if zone:
                if zone not in self.ignore_zones:
                    self.add_ignore_zone(zone)
                return f"好嘞{call_name}，{zone}我不看了，您放心。"
        return None

    # ---- B-4：audit 每日自检（B-10 报告"记忆箱健康"项的数据源）----
    def health_audit(self, now=None):
        """记忆箱健康自检（每日跑，调用方归 B-10 调度）：
        条目数 / 重复率 / 敏感词命中 / 拦截统计。规则执行出 bug 能被发现，不是靠运气。
        - 敏感词命中只扫视觉条目（source="visual"）——语音条目无隐私边界
        - 视觉重复堆积 = 视觉条目 text 完全重复的对数（去重规则失效的信号）
        """
        now = now or self.clock()
        with self._lock:
            visual_eps = [e for e in self.episodes if e.get("source") == "visual"]
            texts = [e.get("text", "") for e in visual_eps]
            dup_pairs = len(texts) - len(set(texts))
            sensitive_hits = [e for e in visual_eps
                              if any(s in e.get("text", "") or s in e.get("scene", "")
                                     for s in SENSITIVE_SCENES)]
            audit = {
                "ts": now,
                "counts": {"facts": len(self.facts), "episodes": len(self.episodes),
                           "reflections": len(self.reflections),
                           "visual_episodes": len(visual_eps),
                           "short_term_buffer": len(self.short_term_buffer)},
                "visual_duplicate_pairs": dup_pairs,
                "sensitive_hits": len(sensitive_hits),
                "sensitive_hit_ids": [e.get("id") for e in sensitive_hits][:10],
                "blocked": dict(self.write_stats),
                "ignore_zones": list(self.ignore_zones),
            }
        issues = []
        if dup_pairs > 0:
            issues.append("visual_duplicate")
        if audit["sensitive_hits"] > 0:
            issues.append("sensitive_leak")
        audit["status"] = "attention" if issues else "healthy"
        audit["issues"] = issues
        return audit

    # ---- B-3：找物三级回答（B-1 PRD §3 的执行点；POST /api/find_object 走这里）----
    def find_object(self, text, call_name="奶奶", now=None):
        """找物三级回答（graph → text → none，B-1 PRD §3 话术写死）。
        返回 PRD §4 契约结构 + reply 话术；不是找物话语 → None（放行正常聊天）。

        graph 层 = 视觉条目（B-4 ingest_visual_event 写入，B-2 正式图谱
        到位后同字段兼容）；B-2 spike no-go 降级 = 无视觉条目时自动只剩 text 层，
        接口不变（source 标 text/graph，F-1 按此展示）。
        has_clean_crop 恒 False：H-2 裁剪图二次检测未供，F-1 不展示图、只语音答位置。
        """
        obj = _extract_find_object(text)
        if obj is None:
            return None
        now = now or self.clock()
        with self._lock:
            # ---- L1 图谱层（视觉条目，"我见过"）----
            best = None
            for e in self.episodes:
                if e.get("source") != "visual":
                    continue
                if e.get("confidence", 0) < VISUAL_HIGH_CONFIDENCE:
                    continue                      # 只引高置信条目（PRD：≥0.8）
                age = now - e.get("ts", 0)
                if age > GRAPH_STALE_DAYS * 86400:
                    continue                      # >180 天不主动展示
                name = e.get("object") or ""
                hit = (obj == name or obj in e.get("text", "")
                       or similarity(obj, e.get("text", "")) >= 0.3)
                if hit and (best is None or e.get("ts", 0) > best.get("ts", 0)):
                    best = e
            if best is not None:
                scene = best.get("scene") or ""
                furn = best.get("furniture") or ""
                position = f"{scene}的{furn}" if furn else scene
                age_days = (now - best.get("ts", 0)) / 86400.0
                when = humanize_ago(best.get("ts", 0), now)
                if age_days <= GRAPH_WINDOW_DAYS:
                    reply = (f"我瞅见它了——{obj}最后见到在{position}，"
                             f"是{when}。")
                else:
                    reply = (f"好久没见它了，上次{when}见着{obj}在{position}，"
                             f"还在不？")
                return {"tier": "graph", "source": "graph", "object": obj,
                        "position": position, "last_seen_at": best.get("ts", 0),
                        "confidence": best.get("confidence", 0),
                        "has_clean_crop": False, "crop_url": None,
                        "stale": age_days > GRAPH_WINDOW_DAYS, "too_old": False,
                        "reply": reply}
            # ---- L2 文字层（"您说过"，不冒充"我见过"）----
            fact = self._find_fact_fuzzy(key=f"{obj}位置")
            if fact is None:
                fact = self._find_fact_fuzzy(key=obj)
            if fact is not None and "位置" in fact.get("key", ""):
                age_days = (now - fact.get("ts", 0)) / 86400.0
                if age_days <= TEXT_MAX_DAYS:
                    pos_val = fact.get("value", "")
                    reply = f"这个我没瞅见，不过您上次说{obj}在{pos_val}。"
                    if age_days > TEXT_STALE_DAYS:
                        reply += "那是好久以前说的了，还在不？"
                    return {"tier": "text", "source": "text", "object": obj,
                            "position": pos_val, "last_seen_at": fact.get("ts", 0),
                            "confidence": fact.get("confidence", 0),
                            "has_clean_crop": False, "crop_url": None,
                            "stale": age_days > TEXT_STALE_DAYS, "too_old": False,
                            "reply": reply}
            # ---- L3 诚实兜底（编造 0 容忍：无依据只说没瞅见）----
            reply = (f"我没瞅见{obj}，您帮我找找？找不着您告诉我放哪儿了，"
                     f"我记上。您下次放哪儿跟我说一声，我下次就找得快了。")
            return {"tier": "none", "source": "none", "object": obj,
                    "position": None, "last_seen_at": None, "confidence": 0,
                    "has_clean_crop": False, "crop_url": None,
                    "stale": False, "too_old": False, "reply": reply}

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
        """侧边栏「记忆」面板（画像卡片已撤掉，改为后台导出 用户画像.txt，任务3）"""
        now = now or self.clock()
        # 待确认的事实（低置信度，等老人确认）→ 时间线上对应条目标"待确认"
        with self._lock:
            pending_ids = {c["fact_id"] for c in self.pending_confirms}
            pending_marks = set()
            for f in self.facts:
                if f["id"] in pending_ids:
                    pending_marks.add(f"{f['key']}{f['value']}")
                    pending_marks.add(f"{f['key']}是{f['value']}")
        refls = sorted(self.reflections, key=lambda r: -r.get("ts", 0))[:3]
        eps = self.timeline(8, now)
        st = self.stats(now)

        html_parts = []
        # 1) 反思
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

        # 2) 记忆时间线
        if eps:
            entries = "".join(
                f"<div style='padding:4px 0 4px 16px;font-size:15px;position:relative;"
                f"border-left:2px solid #C88A52;margin-bottom:2px;'>"
                f"<span style='position:absolute;left:-5px;top:9px;width:8px;height:8px;"
                f"border-radius:50%;background:#C88A52;'></span>"
                f"{'⭐' if e.get('importance', 3) >= 7 else ''} {e['text']}"
                f"{' ⏳待确认' if any(m in e['text'] for m in pending_marks) else ''}"
                f"<span style='color:#aaa;font-size:12px;margin-left:4px;'>"
                f"{humanize_ago(e['ts'], now)}</span></div>"
                for e in reversed(eps))
            html_parts.append(
                f"<div style='background:#fff;border:1px solid #E0D3C0;border-radius:10px;"
                f"padding:8px 10px;'>"
                f"<div style='font-size:15px;font-weight:bold;margin-bottom:4px;'>🕘 最近记下的</div>"
                f"{entries}</div>")

        if not html_parts:
            html_parts.append(
                "<div style='color:#8B5E34;padding:8px;text-align:center;'>"
                "多陪老人聊聊，小忆就会记住 TA 的事</div>")

        stats_line = (f"<div style='margin-top:8px;font-size:13px;color:#8B5E34;text-align:center;'>"
                      f"记住{st['facts']}件事 · 被想起{st['recalls']}次 · 沉淀{st['reflections']}条理解</div>")
        return "".join(html_parts) + stats_line

    # ---- 任务3：画像导出到 用户画像.txt（后台静默更新，不占界面） ----
    def export_profile_txt(self, path, name=""):
        """画像导出（反馈11/任务⑦：只增不删）——旧 txt 里已有的条目不因记忆库
        删除而消失。读旧文件解析"字段：值、值"→ 与新快照 union 去重 →
        旧字段保序在前，旧值在前新值在后。失败只打日志不影响主流程。"""
        try:
            snap = self.profile_snapshot()
            new_map = {}
            for field, items in snap.items():
                vals = []
                for it in items[:6]:
                    vals.append(it["value"] if it["key"] == it["value"]
                                else f"{it['key']}：{it['value']}")
                if vals:
                    new_map[field] = vals
            # 1) 解析旧文件（往返格式与写出对称：首冒号切字段、、分隔多值）
            old_fields, old_order = {}, []
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line or "：" not in line or line.startswith("姓名"):
                                continue
                            field, _, rest = line.partition("：")
                            field = field.strip()
                            vals = [v.strip() for v in rest.split("、") if v.strip()]
                            if field and vals:
                                old_fields[field] = vals
                                old_order.append(field)
                except Exception:
                    old_fields, old_order = {}, []
            # 2) union 合并：旧字段保序在前；同字段旧值在前、新值追加去重
            lines = []
            if name:
                lines.append(f"姓名：{name}")
            for field in old_order:
                merged = list(old_fields[field])
                for v in new_map.get(field, []):
                    if v not in merged:
                        merged.append(v)
                lines.append(f"{field}：{'、'.join(merged)}")
            for field, vals in new_map.items():
                if field not in old_fields:
                    lines.append(f"{field}：{'、'.join(vals)}")
            content = "\n".join(lines) + ("\n" if lines else "")
            tmp = f"{path}.{os.getpid()}_{threading.get_ident()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp, path)
        except Exception as e:
            print(f"画像导出失败: {e}")

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


# ================================================================ B-11：紧急联络存储与闭环
# 独立小存储（不进 facts/episodes——紧急联系人属于安全数据，不走画像淡忘/剪枝），
# 持久化到同目录 emergency_contact.json，原子写，读写持锁。

class EmergencyContactStore:
    """紧急联系人登记（B-11）：
    - contact: {"name", "phone", "relation", "source", "ts", "confirmed"}
      source ∈ {"elder"(老人自报，首选), "child"(子女端代填，仍向老人复述), "staff"(上门代填)}
    - pending: 复述确认流程状态 {"name","phone","relation","source","ts"}
      确认通过（老人说"对/没错"）才落 contact——防听岔（听岔常在后两位）
    - 呼叫日志 call_log: [{"ts","phone","name","target","status","retries","message"}]
      status ∈ {"dialing","failed","fallback"}；绝不静默——失败也写日志+返回兜底话术
    - 未设置兜底：contact 为空时 daily_nudge_due() 每天提醒一次（供 proactive 引擎调）
    时钟注入可测试；phone 校验 11 位手机号（不猜、不足位拒收）。
    """

    NUDGE_INTERVAL = 24 * 3600   # 未设置提醒：每天一次
    CONFIRM_EXPIRE = 10 * 60     # 复述确认超时：10 分钟没答就作废重来（防陈旧确认）

    def __init__(self, path="emergency_contact.json", clock=_time.time):
        self.path = path
        self.clock = clock
        self._lock = threading.RLock()
        self.contact = None
        self.pending = None
        self.call_log = []
        self._last_nudge_ts = 0.0
        self._caller = None            # 呼叫通道 callable（wechat_caller 注入）；None=诚实降级
        self._load()

    def set_caller(self, fn):
        """注入呼叫通道 caller(phone, name)->bool（B-11：微信语音拉起等）；None 恢复降级"""
        self._caller = fn

    # ---- 持久化（原子写，防断电损坏）----
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
        self.contact = data.get("contact") or None
        self.pending = data.get("pending") or None
        self.call_log = (data.get("call_log") or [])[-100:]
        self._last_nudge_ts = float(data.get("last_nudge_ts", 0.0) or 0.0)

    def _save(self):
        data = {"contact": self.contact, "pending": self.pending,
                "call_log": self.call_log[-100:],
                "last_nudge_ts": self._last_nudge_ts}
        tmp = f"{self.path}.{os.getpid()}_{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # ---- 登记第一步：提交候选 → 进入复述确认（三路径 source 区分，流程同一套）----
    def submit(self, name, phone, relation="", source="elder", now=None):
        """提交联系人候选，返回复述确认话术。
        phone 必须 11 位手机号——不足位/含糊 → 拒收并请老人重报（不猜）。
        source=child/staff 时仍向老人复述（老人必须知道名单是谁，不能"被配置"）。"""
        now = now or self.clock()
        name = _norm_value(name)
        digits = re.sub(r"\D", "", str(phone or ""))
        if not name:
            return {"ok": False, "message": "名字没听清，您再说一遍。"}
        if len(digits) != 11 or not digits.startswith("1"):
            return {"ok": False,
                    "message": "电话号码没记全，您把号码一位一位慢慢说，我记牢靠。"}
        with self._lock:
            self.pending = {"name": name, "phone": digits,
                            "relation": _norm_value(relation) or "家人",
                            "source": source, "ts": now}
            self._save()
        who = f"您的{self.pending['relation']}{name}" if relation else f"{name}"
        return {"ok": True, "pending": True,
                "message": (f"我念一遍您听对不对——{who}，电话"
                            f"{_format_phone(digits)}，一位一位说就是，"
                            f"{''.join(digits)}。对吧？")}

    # ---- 登记第二步：老人答复复述 → 确认/否认 ----
    def resolve_confirm(self, reply_text, now=None):
        """老人答复复述确认（"对/没错" / "不对"）。
        返回 {"ok", "action", "message"}；action ∈ confirmed/rejected/expired/none。
        复述确认 100% 执行：没有 pending 或已超时 → expired（重新走登记），绝无直接写入。"""
        now = now or self.clock()
        with self._lock:
            pend = self.pending
            if not pend:
                return {"ok": False, "action": "none",
                        "message": "咱们先说好记谁的电话，您报个名字和号码。"}
            if now - float(pend.get("ts", 0) or 0) > self.CONFIRM_EXPIRE:
                self.pending = None
                self._save()
                return {"ok": False, "action": "expired",
                        "message": "刚才那个确认耽搁久了，您再报一遍，我重新记。"}
            ans = classify_confirm_reply(str(reply_text or ""))[0]
            if ans == "yes":
                self.contact = {k: pend[k] for k in
                                ("name", "phone", "relation", "source")}
                self.contact["ts"] = now
                self.contact["confirmed"] = True
                self.pending = None
                self._last_nudge_ts = 0.0      # 设好了，未设置提醒清零
                self._save()
                return {"ok": True, "action": "confirmed",
                        "message": ("记好啦——紧急联系人是" + self.contact["relation"]
                                    + self.contact["name"] + "，电话"
                                    + _format_phone(self.contact["phone"])
                                    + "。真有事您说一声，我马上帮他打。")}
            if ans == "no":
                self.pending = None
                self._save()
                return {"ok": True, "action": "rejected",
                        "message": "那就是我记岔了，您重新说一遍，我再核对。"}
            return {"ok": False, "action": "none",
                    "message": "您说对没错就是记好了，说不对咱就重来。"}

    # ---- 查询/更正 ----
    def get(self):
        """当前已确认联系人（未设置返回 None）。"""
        with self._lock:
            return dict(self.contact) if self.contact else None

    def status(self, now=None):
        """状态总览（供 proactive 每日提醒与 API 查询）。"""
        now = now or self.clock()
        with self._lock:
            return {"set": bool(self.contact),
                    "contact": dict(self.contact) if self.contact else None,
                    "awaiting_confirm": bool(self.pending),
                    "last_call": self.call_log[-1] if self.call_log else None}

    def clear(self, now=None):
        """删除联系人（老人说"别记了"）。"""
        with self._lock:
            removed = self.contact is not None
            self.contact = None
            self.pending = None
            self._save()
        return removed

    # ---- 未设置兜底：每天提醒一次 ----
    def daily_nudge_due(self, now=None):
        """未设置时每天提醒一次（B-11 三路径全失败兜底）。
        返回提醒话术或 None（已设置 / 当天已提醒过 / 距上次还没到 24h）。"""
        now = now or self.clock()
        with self._lock:
            if self.contact or self.pending:
                return None
            if now - self._last_nudge_ts < self.NUDGE_INTERVAL:
                return None
            self._last_nudge_ts = now
            self._save()
            return ("您的紧急联系人还没设置，真出事了该打给谁呀？"
                    "您跟我说个名字和电话就行，我记好了帮您打。")

    # ---- 主动呼叫（MVP 电脑通道；不自主拨 120——诚实标注，不装）----
    def request_call(self, target="contact", caller=None, now=None):
        """发起呼叫请求（MVP：电脑呼叫通道由 caller callable 执行，如微信语音拉起）。
        - target="contact"：打给紧急联系人
        - target="120"：老人要打 120 —— MVP 不自主拨，返回诚实话术让他自己按
        caller(phone, name)->bool 执行实际呼叫（电话通道用号，微信语音通道按名字搜索），失败/超时 → 重试 1 次 → 仍失败 →
        兜底播报（"自己再试/按120/喊隔壁"），呼叫全程写日志——绝不静默。"""
        now = now or self.clock()
        if target == "120":
            with self._lock:
                self.call_log.append({"ts": now, "phone": "120", "name": "",
                                      "target": "120", "status": "fallback",
                                      "retries": 0,
                                      "message": "MVP通道不自主拨120，提示老人自拨"})
                self._save()
            return {"ok": True, "status": "fallback",
                    "message": "着急的事您自己按120最稳妥，我这就帮您叫家里人来。"}
        with self._lock:
            contact = dict(self.contact) if self.contact else None
        if not contact:
            return {"ok": False, "status": "no_contact",
                    "message": "您的紧急联系人还没设置呢，您先按120，"
                               "回头跟我说个电话号码，我记好帮您打。"}
        phone, name = contact["phone"], contact["name"]
        caller = caller or self._caller     # 未显式传则用注入通道（口语入口与 API 共用）
        if caller is None:
            # 无呼叫通道（未接微信/电话 app）：诚实说明，不装打通
            with self._lock:
                self.call_log.append({"ts": now, "phone": phone, "name": name,
                                      "target": "contact", "status": "failed",
                                      "retries": 0,
                                      "message": "无呼叫通道（caller未接）"})
                self._save()
            return {"ok": False, "status": "failed",
                    "message": "我现在还接不通电话呢，您自己打给" + name
                               + "最稳妥，电话" + _format_phone(phone) + "。"}
        ok, msg, attempts = False, "", 0
        for attempt in (1, 2):          # 失败 → 重试 1 次 → 兜底，绝不静默
            attempts = attempt
            try:
                ok = bool(caller(phone, name))   # 契约 caller(phone, name)：电话通道用号，微信通道用名
            except Exception as e:
                msg = str(e)
            if ok:
                break
        retries = attempts - 1          # 重试次数（首次不算重试）
        with self._lock:
            self.call_log.append({"ts": now, "phone": phone, "name": name,
                                  "target": "contact",
                                  "status": "dialing" if ok else "failed",
                                  "retries": retries, "attempts": attempts,
                                  "message": msg or ""})
            self._save()
        if ok:
            return {"ok": True, "status": "dialing",
                    "message": "我已经给您打通" + name + "了，您稍等一下。"}
        return {"ok": False, "status": "failed", "retries": retries,
                "message": "我打了没接通，您自己再试试"
                           + _format_phone(phone)
                           + "，着急就按120、喊喊隔壁邻居，别干等着。"}

    # ---- 口语入口（确定性，供 app.chat_response 调用）----
    def handle_chat(self, text, call_name="奶奶", now=None):
        """紧急联络口语闭环（B-11）：一句话接登记/确认/更正/呼叫。
        返回回复文案；None = 不是紧急联络句式（放行给正常聊天）。
        - "我儿子电话13812345678" → 复述确认（更正走同一流程，提交即覆盖 pending）
        - "对/不对"（有 pending 时）→ 确认/重来
        - "帮我给孩子打电话" → request_call("contact")
        - "打120" → 诚实标注话术（不自主拨）
        - "紧急联系人是谁/电话多少" → 查询（老人始终知道名单是谁）
        """
        t = str(text or "").strip()
        if not t or len(t) > 60:
            return None
        now = now or self.clock()
        # ① 复述确认答复优先（登记中途，老人在回答"对不对"）
        if self.pending and classify_confirm_reply(t)[0] in ("yes", "no") \
                and not EMERGENCY_CALL_RE.search(t):
            return self.resolve_confirm(t, now=now)["message"]
        # ② 紧急呼叫（最高优先级，含 120）
        if EMERGENCY_CALL_RE.search(t):
            if _CALL_120_RE.search(t):
                return self.request_call(target="120", now=now)["message"]
            return self.request_call(target="contact", now=now)["message"]
        # ③ 登记/更正：话语里带关系词+11 位号码
        phone = _extract_phone(t)
        if phone and (_EMERGENCY_CONTACT_INTENT_RE.search(t)
                      or re.search(r"儿子|女儿|闺女|孩子|家属|家人|孙子|孙女", t)):
            rel_m = re.search(r"儿子|女儿|闺女|孙子|孙女|家属|家人", t)
            relation = rel_m.group(0) if rel_m else ""
            nm_m = re.search(r"(?:叫|名字是)([\u4e00-\u9fa5]{1,4})"
                             r"(?=，|。|,|\.|电话|$|号码|的)", t)
            name = nm_m.group(1) if nm_m else (relation or "家人")
            return self.submit(name, phone, relation, source="elder", now=now)["message"]
        # ④ 查询："紧急联系人是谁/电话多少"
        if re.search(r"紧急联系人(?:是|叫|电话|号码|多少|谁)", t):
            c = self.get()
            if c:
                return ("您的紧急联系人是" + str(c.get("relation", "")) + " "
                        + str(c.get("name", "")) + "，电话"
                        + _format_phone(c.get("phone", "")) + "。")
            return "您的紧急联系人还没设置呢，您跟我说个名字和电话，我这就记上。"
        return None

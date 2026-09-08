# -*- coding: utf-8 -*-
"""
小忆提醒系统核心：中文时间语义解析 + 提醒存储/调度 + 依从性记录

设计约束：
- 仅依赖标准库（可在无 torch/GPU 环境下单元测试）
- 所有时间计算基于调用方注入的统一时钟（服务器时钟），杜绝跨机时区歧义
- 提醒数据原子落盘（防断电丢任务），启动时可补交"错过"的提醒
- 确认闭环：到点触发 -> 未确认重复提醒(最多2次) -> 升级通知家人（记录桩）
"""
import json
import os
import re
import threading
import time as _time
from datetime import datetime, timedelta

# ---------------------------------------------------------------- 中文数字

CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
            "六": 6, "七": 7, "八": 8, "九": 9}
WEEK_CN = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
WEEK_NAME = ["一", "二", "三", "四", "五", "六", "日"]


def cn_to_int(s):
    """中文数字/阿拉伯数字 -> int；解析失败返回 None"""
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    if re.fullmatch(r"\d{1,3}", s):
        return int(s)
    if "十" in s:
        left, _, right = s.partition("十")
        tens = CN_DIGIT.get(left, 1) if left else 1
        ones = CN_DIGIT.get(right, 0) if right else 0
        if (left and left not in CN_DIGIT) or (right and right not in CN_DIGIT):
            return None
        return tens * 10 + ones
    if all(ch in CN_DIGIT for ch in s):
        return sum(CN_DIGIT[ch] for ch in s)
    return None


# ---------------------------------------------------------------- 时间表达解析

DUR_RE = re.compile(
    r"(?P<num>[0-9]+(?:\.[0-9]+)?|一个半|半|[零一二两三四五六七八九十百]+)\s*"
    r"(?:个\s*)?(?P<unit>小时|钟头|刻钟|分钟|分|秒钟|秒)(?:钟)?")

CLOCK_RE = re.compile(
    r"(?P<qual>凌晨|半夜|清晨|早上|早晨|上午|中午|午后|下午|傍晚|晚上|夜里|夜晚)?\s*"
    r"(?P<hour>[0-9]{1,2}|[零一二两三四五六七八九十]{1,3})\s*[点时]"
    r"(?P<min>\s*(?:整|半|一刻|三刻|[0-9]{1,2}|[零一二两三四五六七八九十]{1,3})\s*分?)?")

REL_DATE_RE = re.compile(r"大后天|后天|明天|明日|今天|今日")
WEEK_DATE_RE = re.compile(r"(?:每)?(?:周|星期|礼拜)\s*([一二三四五六日天])")
MONTHDAY_RE = re.compile(
    r"(?:(?P<month>[0-9]{1,2}|[一二三四五六七八九十]{1,2})\s*月\s*)?"
    r"(?P<day>[0-9]{1,2}|[一二三四五六七八九十]{1,2})\s*[号日]")

# 模糊锚点：不建议精确追问，给一个默认时长让用户一次确认
ANCHORS = [
    ("睡醒", 7200), ("醒来", 7200), ("睡一觉", 7200), ("午睡", 7200),
    ("吃完饭", 2700), ("吃过饭", 2700), ("饭后", 2700),
    ("散完步", 5400), ("回来", 7200), ("到家", 7200), ("下楼", 3600),
    ("洗完澡", 2400), ("过一阵", 3600), ("过会儿", 1800), ("待会儿", 1800),
    ("等会儿", 1800), ("晚点", 1800),
]

MIN_REL_SECONDS = 3        # 相对时间下限（演示时"十秒后"也能用）
MAX_REL_SECONDS = 30 * 24 * 3600


def parse_duration_seconds(text):
    """'五分钟后'/'半小时'/'一个半小时'/'45分钟'/'一刻钟' -> 秒数"""
    m = DUR_RE.search(text)
    if not m:
        return None
    num, unit = m.group("num"), m.group("unit")
    if num == "半":
        val = 0.5
    elif num == "一个半":
        val = 1.5
    else:
        n = cn_to_int(num)
        if n is None:
            return None
        val = float(n)
    if unit in ("小时", "钟头"):
        secs = val * 3600
    elif unit == "刻钟":
        secs = val * 900
    elif unit in ("分钟", "分"):
        secs = val * 60
    else:
        secs = val
    secs = int(secs)
    if secs <= 0:
        return None
    return max(MIN_REL_SECONDS, min(MAX_REL_SECONDS, secs))


def _resolve_clock(m):
    """把 CLOCK_RE 的匹配结果规整为 (hour, minute)；qualifier 修正上下午"""
    h = cn_to_int(m.group("hour"))
    if h is None or h > 24:
        return None, None
    qual = m.group("qual") or ""
    mtxt = (m.group("min") or "").strip()
    minute = 0
    if mtxt:
        if mtxt in ("半",):
            minute = 30
        elif mtxt in ("一刻",):
            minute = 15
        elif mtxt in ("三刻",):
            minute = 45
        else:
            minute = cn_to_int(mtxt.replace("分", ""))
            if minute is None:
                return None, None
    if minute > 59:
        return None, None
    if qual in ("下午", "午后", "晚上", "夜里", "夜晚", "傍晚"):
        if h < 12:
            h += 12
    elif qual == "中午":
        if h < 11:
            h += 12
    # "晚上/半夜/凌晨十二点" = 午夜 24 点（落到次日 0 点）
    if h == 12 and qual in ("晚上", "夜里", "夜晚", "半夜", "凌晨"):
        h = 24
    return h, minute


def _at(day_dt, h, minute):
    """把 '24点'（午夜）等时刻落到某个日期上；h>=24 自动进位到次日"""
    extra_days, hh = divmod(int(h), 24)
    base = day_dt.replace(hour=hh, minute=minute, second=0, microsecond=0)
    if extra_days:
        base += timedelta(days=extra_days)
    return base


def _nearest_future_clock(now, h, minute, half_day=True):
    """裸时刻（'两点'）按最近未来消歧：候选 {h, h+12}；带了明确时段词（凌晨/下午等）则不再消歧"""
    if h == 24:
        cands = [24]
    else:
        cands = [h]
        if half_day and h not in (0, 12):
            cands.append(h + 12)
    best = None
    for cand in sorted(cands):
        dt = _at(now, cand, minute)
        if dt <= now:
            dt += timedelta(days=1)
        if best is None or dt < best:
            best = dt
    return best


def _parse_date_part(text):
    """返回 ("offset", n) / ("weekday", w) / ("monthday", month, day) / None"""
    m = REL_DATE_RE.search(text)
    if m:
        return ("offset", {"今天": 0, "今日": 0, "明天": 1, "明日": 1,
                           "后天": 2, "大后天": 3}[m.group(0)])
    m = WEEK_DATE_RE.search(text)
    if m:
        return ("weekday", WEEK_CN[m.group(1)])
    m = MONTHDAY_RE.search(text)
    if m:
        month = cn_to_int(m.group("month")) if m.group("month") else None
        day = cn_to_int(m.group("day"))
        if day and 1 <= day <= 31:
            return ("monthday", month, day)
    return None


def _combine_date_clock(now, date_part, h, minute):
    """日期部分 + 时刻 -> datetime；已过去的自动顺延到下一个匹配日"""
    kind = date_part[0]
    if kind == "offset":
        base = _at(now + timedelta(days=date_part[1]), h, minute)
        if date_part[1] == 0 and base <= now:
            base += timedelta(days=1)
        return base
    if kind == "weekday":
        w = date_part[1]
        days_ahead = (w - now.weekday()) % 7
        base = _at(now + timedelta(days=days_ahead), h, minute)
        if base <= now:
            base += timedelta(days=7)
        return base
    if kind == "monthday":
        month, day = date_part[1], date_part[2]
        if month is None:
            month = now.month
        year = now.year
        for _ in range(14):  # 无效日期（如 2月30号）或已过去 -> 逐月顺延
            try:
                base = _at(datetime(year, month, day), h, minute)
            except ValueError:
                month += 1
                if month > 12:
                    month, year = 1, year + 1
                continue
            if base <= now:
                month += 1
                if month > 12:
                    month, year = 1, year + 1
                continue
            return base
        return None
    return None


def parse_time_expr(text, now=None):
    """
    中文时间表达解析（服务器统一时钟）。

    返回 dict：
      {"kind":"fixed","fire_ts":float,"repeat":None|{"type":"daily","hour","minute"}|
       {"type":"weekly","weekday","hour","minute"},"hour":h,"minute":m}
      {"kind":"clarify","suggestion":秒|None,"repeat_type":..,"weekday":..,"date_offset":..}
    解析不了返回 None。
    """
    now = now or datetime.now()
    t = str(text or "").strip()
    if not t:
        return None

    # 1) 周期提醒：每天 / 每周X
    daily = re.search(r"每天|每日|天天", t)
    weekly = re.search(r"每\s*(?:周|星期|礼拜)\s*([一二三四五六日天])", t)
    if daily or weekly:
        m = CLOCK_RE.search(t)
        if not m:
            out = {"kind": "clarify", "suggestion": None,
                   "repeat_type": "weekly" if weekly else "daily"}
            if weekly:
                out["weekday"] = WEEK_CN[weekly.group(1)]
            return out
        h, minute = _resolve_clock(m)
        if h is None:
            return None
        if daily:
            base = _at(now, h, minute)
            if base <= now:
                base += timedelta(days=1)
            repeat = {"type": "daily", "hour": h % 24, "minute": minute}
        else:
            w = WEEK_CN[weekly.group(1)]
            base = _combine_date_clock(now, ("weekday", w), h, minute)
            repeat = {"type": "weekly", "weekday": w, "hour": h, "minute": minute}
        if base is None:
            return None
        return {"kind": "fixed", "fire_ts": base.timestamp(), "repeat": repeat,
                "hour": h, "minute": minute}

    # 2) 立即类
    if re.search(r"马上|立刻|现在就", t):
        return {"kind": "fixed", "fire_ts": now.timestamp() + 60,
                "repeat": None, "hour": None, "minute": None}

    # 3) 相对时间
    dur = parse_duration_seconds(t)
    if dur is not None:
        return {"kind": "fixed", "fire_ts": now.timestamp() + dur,
                "repeat": None, "hour": None, "minute": None}

    date_part = _parse_date_part(t)
    m = CLOCK_RE.search(t)
    clock_ok = False
    if m:
        h, minute = _resolve_clock(m)
        if h is not None:
            clock_ok = True

    # 4) 日期 + 时刻
    if date_part and clock_ok:
        base = _combine_date_clock(now, date_part, h, minute)
        if base is not None:
            return {"kind": "fixed", "fire_ts": base.timestamp(),
                    "repeat": None, "hour": h % 24, "minute": minute}
    # 5) 只有日期没有时刻 -> 一次澄清问几点
    if date_part:
        out = {"kind": "clarify", "suggestion": None, "repeat_type": None}
        if date_part[0] == "offset":
            out["date_offset"] = date_part[1]
        elif date_part[0] == "weekday":
            out["weekday"] = date_part[1]
        return out
    # 6) 只有时刻 -> 最近未来（带时段词时锁定上下午，不做 12 小时消歧）
    if clock_ok:
        base = _nearest_future_clock(now, h, minute,
                                     half_day=not (m.group("qual") or ""))
        if base is not None:
            return {"kind": "fixed", "fire_ts": base.timestamp(),
                    "repeat": None, "hour": base.hour, "minute": base.minute}
    # 7) 模糊锚点 -> 一次澄清（带默认时长）
    for kw, sug in ANCHORS:
        if kw in t:
            return {"kind": "clarify", "suggestion": sug,
                    "repeat_type": None, "weekday": None, "date_offset": None}
    return None


def looks_like_reminder(text):
    """轻量预过滤：命中才调用模型做意图解析，普通聊天零开销"""
    return bool(re.search(
        r"提醒|别忘|叫我|取消|推迟|改到|改个时间|改个提醒|闹钟|待办|定个|订个|每[天日周星期]",
        str(text or "")))


def fallback_extract(text):
    """LLM 意图解析失败时的纯正则回退（意图+槽位）"""
    t = str(text or "")
    if re.search(r"取消|不用提醒|别提醒", t):
        intent = "cancel"
    elif re.search(r"改到|改成|改个时间|改个提醒|推迟|提前", t):
        intent = "modify"
    elif re.search(r"(什么|哪些|有啥|有什么).{0,4}提醒|看看提醒|查.{0,3}提醒|提醒列表", t):
        intent = "list"
    elif re.search(r"提醒|别忘", t) \
            or (re.search(r"叫我", t) and parse_time_expr(t) is not None) \
            or (re.search(r"每[天日周星期]", t) and CLOCK_RE.search(t)):
        intent = "set"
    else:
        return None
    parsed = parse_time_expr(t)
    time_expr = ""
    if parsed:
        per = re.search(r"每[天日周星期]|每\s*(?:周|星期|礼拜)\s*[一二三四五六日天]", t)
        frag = DUR_RE.search(t) or CLOCK_RE.search(t)
        time_expr = frag.group(0) if frag else t
        if per:  # 周期前缀必须进时间片段，否则"每天"会退化成一次性提醒
            time_expr = per.group(0) + (frag.group(0) if frag else "")
    thing = re.sub(r"帮我|麻烦你?|劳驾|请你?|记得|别忘(?:了)?|到时[候]?|一下|每天|每日|天天|好吗|好不好|哈|哦|呀|吧|[，。！？!?,.]", "", t)
    # 日期词不进事项：'明天提醒我复查' -> '复查'
    thing = re.sub(r"大后天|后天|明[天日]|今[天日]|(?:周|星期|礼拜)[一二三四五六日天]|[0-9]{1,2}月|[0-9]{1,2}[号日]", "", thing)
    thing = re.sub(r"提醒我?|叫我|定个|订个", "", thing)
    if parsed:
        dm = DUR_RE.search(thing) or CLOCK_RE.search(thing)
        if dm:
            thing = thing.replace(dm.group(0), "")
        for kw, _sug in ANCHORS:
            if kw in thing:
                thing = thing.replace(kw, "之后")
                break
    thing = thing.strip(" ，。！？!?,.之后过")
    return {"intent": intent, "time": time_expr, "thing": thing, "target": ""}


DONE_RE = re.compile(r"吃了|服了|喝完|喝好|吃完|吃好|办完|办好|做完|做好|完成|睡醒|搞定|弄好")
LATER_RE = re.compile(r"还没|没有呢|等会|待会|稍后|再等等|过会|先不")


def match_confirmation(text):
    """触发后的用户回复分类：done=已办 / later=稍后 / None"""
    t = str(text or "")
    if LATER_RE.search(t):
        return "later"
    if DONE_RE.search(t):
        return "done"
    return None


# ---------------------------------------------------------------- 人设话术模板

def _humanize_duration(secs):
    if secs % 3600 == 0:
        return f"{secs // 3600}小时"
    if secs % 60 == 0 and secs >= 60:
        m = secs // 60
        if m == 30:
            return "半小时"
        if m == 45:
            return "45分钟"
        return f"{m}分钟"
    return f"{secs}秒"


def humanize_ts(ts, now=None):
    """时间戳 -> '今天下午3点半' 老人友好的说法"""
    now = now or datetime.now()
    dt = datetime.fromtimestamp(ts)
    day = (dt.date() - now.date()).days
    if day == 0:
        prefix = "今天"
    elif day == 1:
        prefix = "明天"
    elif day == 2:
        prefix = "后天"
    else:
        prefix = f"{dt.month}月{dt.day}号"
    h, mi = dt.hour, dt.minute
    if h == 0:
        seg, hh = "半夜", 12
    elif h < 6:
        seg, hh = "凌晨", h
    elif h < 10:
        seg, hh = "早上", h
    elif h < 12:
        seg, hh = "上午", h
    elif h == 12:
        seg, hh = "中午", 12
    elif h < 18:
        seg, hh = "下午", h - 12
    elif h < 19:
        seg, hh = "傍晚", h - 12
    else:
        seg, hh = "晚上", h - 12
    mi_str = "半" if mi == 30 else ("" if mi == 0 else f"{mi}分")
    return f"{prefix}{seg}{hh}点{mi_str}"


def repeat_desc(repeat):
    if not repeat:
        return ""
    if repeat.get("type") == "daily":
        return "每天"
    if repeat.get("type") == "weekly":
        return f"每周{WEEK_NAME[repeat.get('weekday', 0)]}"
    return ""


def humanize_repeat(repeat):
    """周期提醒的完整时间说法：'每天早上8点'（含时段词，不含具体日期）"""
    if not repeat:
        return ""
    return describe_reminder({"thing": "", "repeat": repeat}).strip()


def describe_reminder(r, now=None):
    """面板/播报用的一条提醒描述：'每天早上8点 吃药'"""
    if r.get("repeat"):
        rd = repeat_desc(r["repeat"])
        h, mi = r["repeat"].get("hour", 0), r["repeat"].get("minute", 0)
        mi_str = "半" if mi == 30 else ("" if mi == 0 else f"{mi}分")
        hh, seg = h, ""
        if h == 0:
            seg, hh = "半夜", 12
        elif h < 6:
            seg = "凌晨"
        elif h < 10:
            seg = "早上"
        elif h < 12:
            seg = "上午"
        elif h == 12:
            seg = "中午"
        elif h < 18:
            seg, hh = "下午", h - 12
        else:
            seg, hh = "晚上", h - 12
        return f"{rd}{seg}{hh}点{mi_str} {r['thing']}"
    return f"{humanize_ts(r['time'], now)} {r['thing']}"


def ack_set_message(personality, call, thing, time_str, repeat_str=""):
    when = repeat_str if repeat_str else time_str
    if thing in ("您交代的事", ""):
        core = f"{when}我会提醒您。"
    else:
        core = f"{when}提醒您{thing}。"
    if personality == "风趣幽默":
        return f"包在我身上！{core}到点我准时喊您，忘不了！"
    if personality == "暖心知心":
        return f"好的{call}，我帮您记着呢——{core}您放心忙您的。"
    return f"好嘞{call}，记下了：{core}"


def trigger_message(personality, call, thing):
    if "药" in thing:
        confirm = "药吃了吗？吃好了跟我说一声哦。"
    elif "水" in thing:
        confirm = "水喝了吗？跟我说一声哦。"
    else:
        confirm = "办好了跟我说一声哦。"
    if personality == "风趣幽默":
        return f"{call}！叮咚——时间到咯！该{thing}啦。{confirm}"
    if personality == "暖心知心":
        return f"{call}，时间到了呢，该{thing}啦。{confirm}"
    return f"{call}，到点啦——{thing}。{confirm}"


def reremind_message(personality, call, thing):
    if personality == "暖心知心":
        return f"{call}，我再轻轻问一句——{thing}，办好了吗？"
    if personality == "风趣幽默":
        return f"{call}，我来催您啦——{thing}，办好了吗？"
    return f"{call}，我再问一句——{thing}，办好了吗？"


def escalated_message(call, thing):
    return (f"{call}，{thing}这件事您还没顾上呢，我先记下来，也让家里人留意一下。"
            f"您忙您的，不着急。")


def missed_message(personality, call, thing):
    return (f"{call}，您不在的时候我记着呢——{thing}。"
            f"现在补上还来得及，办好了跟我说一声哦。")


def confirm_ack_message(personality, call, thing):
    if personality == "风趣幽默":
        return f"太棒啦！{thing}完成，我给您记上一功！"
    if personality == "暖心知心":
        return f"真好呀{call}，我这就放心啦。"
    return f"好嘞，我记下了。{call}真不错。"


def later_ack_message(call, minutes=10):
    return f"好的{call}，那过{minutes}分钟我再提醒您。"


def clarify_time_message(call, thing, suggestion=None):
    if suggestion:
        d = _humanize_duration(suggestion)
        return (f"{call}，好的——{thing}。那大概过多久提醒您呢？"
                f"拿不准的话，我就过{d}左右提醒您，行吗？")
    return f"{call}，好的，我帮您记着{thing}——那您想让我什么时候提醒您呢？"


def list_message(call, descs):
    if not descs:
        return f"{call}，现在没有待办的提醒哦。"
    lines = "；".join(f"{i+1}）{d}" for i, d in enumerate(descs))
    return f"{call}，您现在有{len(descs)}个提醒：{lines}。要取消或改动哪个，跟我说就行。"


def select_message(call, descs, action="取消"):
    lines = "；".join(f"{i+1}）{d}" for i, d in enumerate(descs))
    return f"您想{action}哪一个呢？{lines}。"


def confirm_cancel_message(call, desc):
    return f"您是要取消「{desc}」这个提醒吗？说“是”就取消哦。"


def modify_ask_message(call, desc):
    return f"想把「{desc}」改到什么时候呢？您可以说“下午三点”或者“半小时后”。"


def cancel_ack_message(personality, call, desc):
    return f"好，已经取消了：{desc}。"


def abort_message(call):
    return f"好的{call}，那先不动它。"


def capability_message(call):
    return (f"可以呀{call}！您跟我说“X分钟后提醒我做什么”，"
            f"比如“五分钟后提醒我喝水”，到点我就叫您。")


# ---------------------------------------------------------------- 提醒存储/调度

def _next_occurrence(repeat, now):
    if repeat.get("type") == "daily":
        base = now.replace(hour=repeat["hour"], minute=repeat["minute"],
                           second=0, microsecond=0)
        if base <= now:
            base += timedelta(days=1)
        return base
    if repeat.get("type") == "weekly":
        w = repeat.get("weekday", 0)
        days_ahead = (w - now.weekday()) % 7
        base = (now + timedelta(days=days_ahead)).replace(
            hour=repeat["hour"], minute=repeat["minute"], second=0, microsecond=0)
        if base <= now:
            base += timedelta(days=7)
        return base
    return None


class ReminderStore:
    """提醒任务持久化存储 + 依从性记录。所有时间比较用注入的 now，可测试。"""

    def __init__(self, path, clock=_time.time):
        self.path = path
        self.clock = clock
        self._lock = threading.Lock()
        data = self._load()
        self.items = data.get("reminders", []) if isinstance(data, dict) else (data or [])
        self.notices = data.get("notices", []) if isinstance(data, dict) else []

    # ---- 持久化 ----
    def _load(self):
        if not os.path.exists(self.path):
            return {"reminders": [], "notices": []}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"reminders": [], "notices": []}

    def _save(self):
        now = self.clock()
        keep = []
        for r in self.items:
            terminal = r.get("status") in ("done", "canceled", "missed")
            stale = now - r.get("updated", now) > 14 * 24 * 3600
            if terminal and stale:
                continue
            keep.append(r)
        self.items = keep
        self.notices = self.notices[-50:]
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"reminders": self.items, "notices": self.notices},
                      f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # ---- 基本操作 ----
    def add(self, thing, fire_ts, repeat=None, source="chat"):
        now = self.clock()
        r = {
            "id": f"r{int(now * 1000)}{len(self.items) % 1000:03d}",
            "thing": thing,
            "time": float(fire_ts),
            "repeat": repeat,
            "status": "scheduled",
            "source": source,
            "created": now,
            "updated": now,
            "last_fired_at": 0,
            "log": [],   # 每次触发一条：{"fired","confirmed","result","re_reminds","escalated"}
        }
        with self._lock:
            self.items.append(r)
            self._save()
        return r

    def get(self, rid):
        for r in self.items:
            if r["id"] == rid:
                return r
        return None

    def pending(self, now=None):
        now = now or self.clock()
        return sorted([r for r in self.items if r["status"] == "scheduled"],
                      key=lambda x: x["time"])

    def cancel(self, rid):
        r = self.get(rid)
        if not r or r["status"] != "scheduled":
            return False
        r["status"] = "canceled"
        r["updated"] = self.clock()
        with self._lock:
            self._save()
        return True

    def cancel_all(self):
        n = 0
        for r in self.items:
            if r["status"] == "scheduled":
                r["status"] = "canceled"
                r["updated"] = self.clock()
                n += 1
        if n:
            with self._lock:
                self._save()
        return n

    def modify(self, rid, fire_ts, repeat=None):
        r = self.get(rid)
        if not r or r["status"] != "scheduled":
            return False
        r["time"] = float(fire_ts)
        if repeat is not None:
            r["repeat"] = repeat
        elif r.get("repeat"):
            # 改到具体时刻后，周期提醒按新时刻对齐
            dt = datetime.fromtimestamp(fire_ts)
            if r["repeat"].get("type") == "daily":
                r["repeat"] = {"type": "daily", "hour": dt.hour, "minute": dt.minute}
            elif r["repeat"].get("type") == "weekly":
                r["repeat"] = {"type": "weekly", "weekday": dt.weekday(),
                               "hour": dt.hour, "minute": dt.minute}
        r["updated"] = self.clock()
        with self._lock:
            self._save()
        return True

    # ---- 触发与闭环 ----
    def due(self, now=None):
        now = now or self.clock()
        return [r for r in self.items
                if r["status"] == "scheduled" and r["time"] <= now]

    def mark_fired(self, rid, now=None):
        now = now or self.clock()
        r = self.get(rid)
        if not r:
            return None
        r["last_fired_at"] = now
        r["updated"] = now
        r["log"].append({"fired": now, "confirmed": None,
                         "result": "pending", "re_reminds": 0})
        if r.get("repeat"):
            nxt = _next_occurrence(r["repeat"], datetime.fromtimestamp(now))
            if nxt:
                r["time"] = nxt.timestamp()
        else:
            r["status"] = "fired"
        with self._lock:
            self._save()
        return r

    def awaiting_confirm(self, now=None, window=6 * 3600):
        """处于待确认状态的提醒（触发后未确认、升级后等迟来确认、未过窗口）"""
        now = now or self.clock()
        out = []
        for r in self.items:
            if not r.get("log"):
                continue
            entry = r["log"][-1]
            recent = (now - r.get("last_fired_at", 0)) < window
            if recent and (entry["result"] == "pending" or entry.get("escalated")):
                out.append(r)
        out.sort(key=lambda x: x.get("last_fired_at", 0), reverse=True)
        return out

    def advance_awaiting(self, now=None, interval=120, max_n=2):
        """推进待确认提醒的升级策略。
        返回 [(reminder, kind)]，kind: "reremind" | "escalate"
        策略：每 interval 未确认补一次提醒，累计 max_n 次后升级记录家人通知。"""
        now = now or self.clock()
        events = []
        for r in self.awaiting_confirm(now):
            entry = r["log"][-1]
            if entry.get("escalated"):
                continue
            n = entry.get("re_reminds", 0)
            waited = now - r.get("last_fired_at", 0)
            if n < max_n and waited >= interval * (n + 1):
                entry["re_reminds"] = n + 1
                r["updated"] = now
                events.append((r, "reremind"))
            elif n >= max_n and waited >= interval * (n + 1):
                entry["escalated"] = True
                entry["result"] = "missed"
                r["updated"] = now
                self.notices.append({"thing": r["thing"], "time": now,
                                     "reminder_id": r["id"]})
                events.append((r, "escalate"))
        if events:
            with self._lock:
                self._save()
        return events

    def confirm(self, rid, now=None):
        now = now or self.clock()
        r = self.get(rid)
        if not r or not r.get("log"):
            return False
        entry = r["log"][-1]
        if entry["result"] != "done":
            entry["result"] = "done"
            entry["confirmed"] = now
        if r["status"] == "fired":
            r["status"] = "done"
        r["updated"] = now
        with self._lock:
            self._save()
        return True

    def snooze(self, rid, delta, now=None):
        now = now or self.clock()
        r = self.get(rid)
        if not r or not r.get("log"):
            return None
        r["log"][-1]["result"] = "snoozed"
        if r.get("repeat"):
            # 周期提醒的"稍后"补一个一次性跟催任务，不打乱原周期
            nr = self.add(r["thing"], now + delta, None, source="snooze")
            r["updated"] = now
            with self._lock:
                self._save()
            return nr
        r["status"] = "scheduled"
        r["time"] = now + delta
        r["updated"] = now
        with self._lock:
            self._save()
        return r

    def missed_on_startup(self, now=None):
        """断电/关闭期间错过的提醒：一次性的记为 missed，周期性的顺延并记录错过"""
        now = now or self.clock()
        missed = []
        for r in self.items:
            if r["status"] != "scheduled" or r["time"] > now:
                continue
            if (now - r["time"]) > 24 * 3600:  # 太久的不再打扰
                r["status"] = "missed" if not r.get("repeat") else r["status"]
                if r.get("repeat"):
                    nxt = _next_occurrence(r["repeat"], datetime.fromtimestamp(now))
                    if nxt:
                        r["time"] = nxt.timestamp()
                r["updated"] = now
                continue
            r["log"].append({"fired": r["time"], "confirmed": None,
                             "result": "missed", "re_reminds": 0})
            if r.get("repeat"):
                nxt = _next_occurrence(r["repeat"], datetime.fromtimestamp(now))
                if nxt:
                    r["time"] = nxt.timestamp()
            else:
                r["status"] = "missed"
            r["updated"] = now
            missed.append(r)
        if missed:
            with self._lock:
                self._save()
        return missed

    # ---- 量化指标 ----
    def stats(self, now=None, days=30):
        now = now or self.clock()
        cutoff = now - days * 24 * 3600
        total = done = 0
        for r in self.items:
            for e in r.get("log", []):
                if e.get("fired", 0) < cutoff:
                    continue
                total += 1
                if e.get("result") == "done":
                    done += 1
        rate = round(done * 100 / total) if total else None
        return {"total": total, "done": done, "rate": rate}

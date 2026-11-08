"""子女端只读服务（F-7）：今日概况 + 健康异常摘要 + 情绪趋势 + 购药待办 + 告知/通知。

隐私边界在服务层写死（B-9 只读 API 的最小实现，B-9 落地后替换数据源、口径不变）：
  - 本模块没有任何读取聊天记录/会话的能力，返回体里也只有概况字段；
    「不展示完整聊天记录」不是前端不画，是这里根本拿不到。
  - 安心开关只控制「子女能不能看」，不控制采集：关掉后老人端功能全不受影响。
  - 购药回执只记「已购」并给老人生成一条通知；不改提醒、不改药量、不替老人做主。

数据源（全部只读）：
  reminders  —— 用药提醒（文本含「药」）→ 健康摘要 + 购药待办
  emotion_log —— 每轮对话的情绪打分 → 今日心情 + 7 天趋势（不含原话）
  fall_guard —— 跌倒状态机（asking/escalated 才算异常）
  「今日概况」每天生成一次后按自然日缓存（不是实时流，避免子女盯着当监控）。
"""
import json
import os
import threading
import time
from datetime import datetime, timedelta

# 子女端状态：安心开关 / 告知记录 / 今日概况缓存 / 购药回执 / 老人通知
# 单机单人，与项目根其它 JSON 数据文件同级
CHILD_FILE = "child_portal.json"

DAY_FMT = "%Y-%m-%d"

LABEL_CN = {"low": "低落", "neutral": "平淡", "high": "愉悦"}

# 子女端能看到 / 看不到（页面上原样展示，产品化的隐私边界）
CAN_SEE = [
    "吃药情况：提醒有没有按时确认",
    "心情概况：这些天的心情趋势",
    "购药待办：药快用完了没有",
]
CANNOT_SEE = ["聊天内容", "和谁聊了什么"]
PRIVACY_TEXT = "这里只放概况，不放聊天内容。老人知道您能看到这些，也可以随时关掉。"

CLOSED_TEXT = "已关闭"
CLOSED_HINT = "老人把「子女远程安心」关上了。老人端功能一切照旧，只是这里看不到概况。"

# 首次激活的告知话术（大字 + 语音同一句，老人始终知道子女能看到什么）
NOTICE_TEXT = ("您的孩子能看到您的吃药和心情概况，看不到聊天内容。"
               "您要是不同意，可以随时关掉。")


# ---------------------------------------------------------------- 持久化

def _default_state():
    return {
        "consent": {"enabled": True, "updated_at": None, "notified_at": None},
        "daily": {},        # {"date": "...", "text": "..."}
        "receipts": {},     # reminder_id -> {"at": ts, "text": "..."}
        "notices": [],      # 给老人的通知：[{id, text, at, acked}]
    }


def _load():
    data = None
    if os.path.exists(CHILD_FILE):
        try:
            with open(CHILD_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = None
    st = _default_state()
    if isinstance(data, dict):
        c = data.get("consent")
        if isinstance(c, dict):
            st["consent"].update({k: c[k] for k in ("enabled", "updated_at", "notified_at")
                                  if k in c})
        st["daily"] = data.get("daily") if isinstance(data.get("daily"), dict) else {}
        st["receipts"] = data.get("receipts") if isinstance(data.get("receipts"), dict) else {}
        st["notices"] = data.get("notices") if isinstance(data.get("notices"), list) else []
    return st


def _save(st):
    tmp = f"{CHILD_FILE}.{os.getpid()}_{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CHILD_FILE)


# ---------------------------------------------------------------- 只读数据源
# 四个 provider 集中在这里：测试可替换成假数据，不拉起引擎、不写任何记忆。

def _reminder_list():
    """提醒列表（只读）：与 /api/reminders 同一份结构。"""
    try:
        from backend.services import reminder_service
        return reminder_service.list()
    except Exception as e:
        print(f"[child_service] 读取提醒失败: {e}")
        return {"awaiting": [], "pending": [], "stats": {}, "notices": []}


def _emotion_log():
    """情绪日志（只读）：每轮 {time, label, score, note}，不含聊天原文。"""
    try:
        from backend.core.state import engine
        return list(engine.load_emotion_log() or [])
    except Exception as e:
        print(f"[child_service] 读取情绪日志失败: {e}")
        return []


def _fall_state():
    """跌倒状态机的当前状态（idle/suspect/asking/escalated/false_alarm）。"""
    try:
        import sys
        from pathlib import Path
        vision_dir = Path(__file__).resolve().parents[2] / "vision"
        if str(vision_dir) not in sys.path:
            sys.path.insert(0, str(vision_dir))
        from fall_guard import guard
        return str((guard.status() or {}).get("state") or "idle")
    except Exception:
        return "idle"


def _elder_chart_url():
    """老人版心情曲线（复用现有 /api/emotion-chart 生成图，不另画一套）。"""
    try:
        from backend.services import memory_service
        return (memory_service.emotion_chart() or {}).get("image_url")
    except Exception as e:
        print(f"[child_service] 生成心情曲线失败: {e}")
        return None


# ---------------------------------------------------------------- 小工具

def _day_key(ts):
    try:
        return datetime.fromtimestamp(float(ts)).strftime(DAY_FMT)
    except (TypeError, ValueError, OSError):
        return ""


def _med_items(rem):
    """从提醒列表里挑出用药项：(等确认, 未到点)。"""
    aw, pd = [], []
    for r in rem.get("awaiting") or []:
        if "药" in str(r.get("desc") or ""):
            aw.append(r)
    for r in rem.get("pending") or []:
        if "药" in str(r.get("desc") or ""):
            pd.append(r)
    return aw, pd


def _today_entries(log, now):
    key = _day_key(now)
    return [e for e in log if _day_key(e.get("time", 0)) == key]


# ---------------------------------------------------------------- 开关 / 告知

def consent():
    c = _load()["consent"]
    return {
        "enabled": bool(c.get("enabled", True)),
        "notified": bool(c.get("notified_at")),
        "updated_at": c.get("updated_at"),
        "notice_text": NOTICE_TEXT,
    }


def set_consent(enabled):
    """老人/子女都可关，也可再开。只影响子女端可见性，不碰采集。"""
    st = _load()
    st["consent"]["enabled"] = bool(enabled)
    st["consent"]["updated_at"] = time.time()
    _save(st)
    return {"ok": True, **consent()}


def mark_notified():
    """老人已听过首次告知（大字 + 语音之后点「我知道了」）。"""
    st = _load()
    st["consent"]["notified_at"] = time.time()
    _save(st)
    return {"ok": True, **consent()}


# ---------------------------------------------------------------- 今日概况

def _today_overview(now, rem, log):
    """一句话概况：每天只生成/缓存一次（不实时，避免变成监控流）。"""
    st = _load()
    key = _day_key(now)
    cached = st.get("daily") or {}
    if cached.get("date") == key and cached.get("text"):
        return {"date": key, "text": cached["text"]}

    aw_med, pd_med = _med_items(rem)
    today = _today_entries(log, now)
    label = (today[-1].get("label") if today else "") or ""

    if aw_med:
        med = f"吃药提醒有{len(aw_med)}条还没确认"
    elif pd_med:
        med = "吃药提醒还没到点"
    else:
        med = "没有吃药提醒"

    if not today:
        mood = "还没聊天"
        chats = ""
    else:
        mood = "心情有点低" if label == "low" else "心情还行"
        chats = f"，聊了{len(today)}次"

    text = f"今天{med}，{mood}{chats}。"
    st["daily"] = {"date": key, "text": text}
    _save(st)
    return {"date": key, "text": text}


# ---------------------------------------------------------------- 情绪趋势

def _emotion_points(log, now, days=7):
    """子女版趋势：按自然日聚合（平均分 + 当天主导情绪 + 次数），不含原话。"""
    points = []
    for i in range(days - 1, -1, -1):
        d = (datetime.fromtimestamp(now) - timedelta(days=i)).date()
        key = d.strftime(DAY_FMT)
        entries = [e for e in log if _day_key(e.get("time", 0)) == key]
        if not entries:
            continue
        scores = [float(e.get("score") or 0) for e in entries]
        labels = [str(e.get("label") or "neutral") for e in entries]
        # 主导情绪：取当天最后一条（与老人当下最接近），数量仅作说明
        label = labels[-1] if labels else "neutral"
        points.append({
            "date": f"{d.month}月{d.day}日",
            "score": round(sum(scores) / len(scores), 2),
            "label": label,
            "label_cn": LABEL_CN.get(label, "平淡"),
            "count": len(entries),
        })
    return points


def _emotion(log, now):
    today = _today_entries(log, now)
    label = str((today[-1].get("label") if today else "") or "")
    points = _emotion_points(log, now)
    return {
        "points": points,
        "days": 7,
        "has_data": bool(points),
        "today_label": label or None,
        "today_label_cn": LABEL_CN.get(label) if label else None,
        "elder_chart_url": _elder_chart_url() if points else None,
        # 两种视图：子女版=聚合点（points），老人版=现有曲线图（elder_chart_url）
    }


# ---------------------------------------------------------------- 健康摘要（行为层）

def _health(now, rem, log):
    """行为层面的异常摘要：用药没确认 / 可能摔倒 / 情绪低落。
    只描述行为，不做医疗判断。"""
    items = []
    aw_med, _pd_med = _med_items(rem)
    if aw_med:
        items.append({"kind": "med", "level": "warn",
                      "text": f"有{len(aw_med)}条吃药提醒还没确认，可能忘了"})

    fs = _fall_state()
    if fs == "asking":
        items.append({"kind": "fall", "level": "warn",
                      "text": "检测到可能摔倒，正在问老人有没有事"})
    elif fs == "escalated":
        items.append({"kind": "fall", "level": "warn",
                      "text": "可能有摔倒，已经转报给家里人了"})

    today = _today_entries(log, now)
    if today and str(today[-1].get("label") or "") == "low":
        items.append({"kind": "mood", "level": "warn",
                      "text": "今天说话里听着情绪有点低"})

    if not items:
        items.append({"kind": "ok", "level": "ok", "text": "今天没发现异常"})
    level = "warn" if any(i["level"] == "warn" for i in items) else "ok"
    return {"level": level, "items": items}


# ---------------------------------------------------------------- 购药待办

def _purchase_list(rem, st):
    """购药待办：含「药」的提醒 + 已处理回执（子女只建议，不替老人做主）。"""
    out = []
    receipts = st.get("receipts") or {}
    for r in rem.get("awaiting") or []:
        desc = str(r.get("desc") or "")
        if "药" not in desc:
            continue
        out.append({
            "id": r.get("id"),
            "text": desc,
            "when": "已过点，等老人确认",
            "receipt": receipts.get(str(r.get("id"))),
        })
    for r in rem.get("pending") or []:
        desc = str(r.get("desc") or "")
        if "药" not in desc:
            continue
        out.append({
            "id": r.get("id"),
            "text": desc,
            "when": r.get("eta") or "待办",
            "receipt": receipts.get(str(r.get("id"))),
        })
    return out


def _find_purchase(rid):
    rem = _reminder_list()
    for item in _purchase_list(rem, _load()):
        if str(item["id"]) == str(rid):
            return item
    return None


def purchase_receipt(rid):
    """子女处理完购药待办：记回执 + 给老人生成一条「孩子给您买了药」的通知。

    只记录，不改提醒状态、不改药量——老人用药自主权始终在老人手里。
    """
    rid = str(rid or "")
    item = _find_purchase(rid)
    if not item:
        return {"ok": False, "message": "没找到这条购药待办，可能已经处理了。"}

    now = time.time()
    st = _load()
    st["receipts"][rid] = {"at": now, "text": item["text"]}
    notice = {
        "id": f"n{int(now * 1000)}_{len(st['notices']) % 100:02d}",
        "text": (f"您孩子给您买了药：{item['text']}。"
                 f"吃不吃、什么时候吃，您自己拿主意。"),
        "at": now,
        "acked": False,
    }
    st["notices"].append(notice)
    st["notices"] = st["notices"][-10:]
    _save(st)
    return {"ok": True, "message": "记下了，也告诉您妈妈了。", "notice": notice}


# ---------------------------------------------------------------- 老人侧通知

def notices():
    """老人端取通知（购药回执等）：只读，不改状态。"""
    st = _load()
    return {"ok": True, "notices": list(st.get("notices") or [])}


def ack_notice(nid):
    """老人端已播报：标记已读，避免重复打扰。"""
    nid = str(nid or "")
    st = _load()
    hit = None
    for n in st.get("notices") or []:
        if str(n.get("id")) == nid:
            n["acked"] = True
            hit = n
    if hit is None:
        return {"ok": False, "message": "没找到这条通知。"}
    _save(st)
    return {"ok": True, "notice": hit}


# ---------------------------------------------------------------- 汇总

def summary(now=None):
    """子女端一页数据。关闭时只回 enabled=False，不泄露任何概况。"""
    c = consent()
    if not c["enabled"]:
        return {"ok": True, "enabled": False, "closed_text": CLOSED_TEXT,
                "closed_hint": CLOSED_HINT, "consent": c}

    now = time.time() if now is None else now
    rem = _reminder_list()
    log = _emotion_log()
    st = _load()
    return {
        "ok": True,
        "enabled": True,
        "consent": c,
        "today": _today_overview(now, rem, log),
        "health": _health(now, rem, log),
        "emotion": _emotion(log, now),
        "purchase": _purchase_list(rem, st),
        "privacy": {"can_see": CAN_SEE, "cannot_see": CANNOT_SEE, "text": PRIVACY_TEXT},
    }

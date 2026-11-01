r"""④ 感知 → 主动关心：把"外面变了"变成小忆主动说的一句话

用法：
    cd D:\xiaoyi_vision
    venv\Scripts\python.exe proactive_signal.py --poll        # 看现在有没有该说的
    venv\Scripts\python.exe proactive_signal.py --reset       # 清冷却状态（演示/测试用）

设计（为什么这么设计）：
    1. **只从"变化"里说话**，不从"状态"里说话。
       晴天说"今天晴天"是废话；"刚下雨了"才值得打断老人。
    2. **三层冷却**，缺一层就会刷屏：
       - 全局最小间隔 20 分钟（不能连着说）
       - 同类信号 2 小时（下雨不能每 30 分钟说一次）
       - 每天最多 6 条（有上限才敢放开）
    3. **22:00–07:00 闭嘴**。老人睡觉时被设备吵醒，比不做主动关心更糟。
    4. **产出的是"候选消息"，不是直接播报**。
       要不要说、什么时候说，交给小忆的主动对话引擎决定（那边已有自己的熔断）。
"""

import argparse
import json
import time
from datetime import datetime, date
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
STATE_FILE = DATA / "proactive_state.json"
QUEUE_FILE = DATA / "proactive_queue.jsonl"
import sys
sys.path.insert(0, str(HERE))
from perception_query import recent_perceptions  # noqa: E402

GLOBAL_GAP = 20 * 60      # 全局最小间隔 20 分钟
PER_KEY_GAP = 2 * 3600    # 同类信号 2 小时
DAILY_MAX = 6             # 每天最多 6 条
QUIET_START, QUIET_END = 22, 7


def in_quiet_hours(now=None):
    h = (now or datetime.now()).hour
    return h >= QUIET_START or h < QUIET_END


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"key_last": {}, "last_any": 0, "count": {}, "date": ""}


def save_state(st):
    DATA.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def _ts_of(iso):
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (ValueError, TypeError):
        return 0.0


def candidates(after_ts=0.0, demo_rain=False):
    """从最近 6 小时的汇总里挑"值得主动说"的变化

    after_ts: 水位线，只看比它更新的事件，避免同一条信号反复说
    返回按时间倒序（最新在前）
    """
    recs = recent_perceptions(hours=6)
    out = []

    if demo_rain:
        out.append({"key": "rain", "sig_ts": time.time(),
                    "text": "外头下雨了，您出门记得带把伞", "reason": "demo"})

    for rec in recs:
        sig_ts = _ts_of(rec.get("window_end") or rec.get("written_at"))
        if sig_ts <= after_ts:
            continue

        if rec.get("weather") == "rain":
            out.append({"key": "rain", "sig_ts": sig_ts,
                        "text": "外头下雨了，您出门记得带把伞",
                        "reason": "weather=rain"})

        for e in rec.get("events", []):
            if e.get("type") == "new_group_first_seen" and e.get("group") == "动物":
                out.append({"key": "animal", "sig_ts": sig_ts,
                            "text": "外头好像有小动物，您要不要瞅一眼",
                            "reason": f"new_group={e.get('labels')}"})

    out.sort(key=lambda c: -c["sig_ts"])
    return out


def poll(now=None, demo_rain=False):
    now = now or time.time()
    st = load_state()
    today = date.today().isoformat()
    st.setdefault("last_signal_ts", 0.0)
    if st.get("date") != today:
        st["date"] = today
        st["count"] = {}

    if in_quiet_hours():
        return [], "静默时段（22:00–07:00），不说话"

    sent_today = st["count"].get(today, 0)
    if sent_today >= DAILY_MAX:
        return [], f"今天已说 {sent_today} 条，到上限 {DAILY_MAX}"

    if now - st.get("last_any", 0) < GLOBAL_GAP:
        wait = int((GLOBAL_GAP - (now - st["last_any"])) / 60)
        return [], f"全局冷却中，还要 {wait} 分钟"

    ready = []
    for c in candidates(after_ts=st["last_signal_ts"], demo_rain=demo_rain):
        if now - st["key_last"].get(c["key"], 0) < PER_KEY_GAP:
            continue
        ready.append(c)

    if not ready:
        return [], "没有够格的信号（同类冷却中，或感知没有新变化）"

    # 一次只放一条，且取最新的那条 —— 老信号已经过时，不值得再说
    pick = ready[0]
    st["key_last"][pick["key"]] = now
    st["last_any"] = now
    st["last_signal_ts"] = max(st["last_signal_ts"], pick["sig_ts"])
    st["count"][today] = sent_today + 1
    save_state(st)

    DATA.mkdir(exist_ok=True)
    with open(QUEUE_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"),
                            "key": pick["key"], "text": pick["text"],
                            "reason": pick["reason"]}, ensure_ascii=False) + "\n")
    return [pick], "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--poll", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--show-state", action="store_true")
    ap.add_argument("--demo-rain", action="store_true",
                    help="测试用：注入一个'下雨'信号，验证话术与冷却")
    args = ap.parse_args()

    if args.reset:
        save_state({"key_last": {}, "last_any": 0, "count": {}, "date": ""})
        print("[OK] 冷却状态已清空")
        return 0

    if args.show_state:
        print(json.dumps(load_state(), ensure_ascii=False, indent=2))
        return 0

    if args.poll:
        msgs, why = poll(demo_rain=args.demo_rain)
        print(f"[状态] {why}")
        for m in msgs:
            print(f"[主动说] {m['text']}   （依据：{m['reason']}）")
        if not msgs:
            print("[主动说] （这次不说）")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

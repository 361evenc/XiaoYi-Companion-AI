r"""③ 查询接口：AI 工程师问"现在外面怎么样" / "最近几天感知到什么"时调这里

不联网、不起服务 —— 就是两个纯函数，谁要接进小忆就 import 走。

用法：
    from perception_query import latest_perception, recent_perceptions
    now = latest_perception()                    # 最新一帧（含摘要句）
    recent = recent_perceptions(hours=24)        # 最近 24h 的汇总条目
    recent = recent_perceptions(days=3)          # 最近 3 天

命令行自测：
    cd D:\xiaoyi_vision
    venv\Scripts\python.exe perception_query.py --latest
    venv\Scripts\python.exe perception_query.py --recent-hours 24
"""

import argparse
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
FRAMES_FILE = HERE / "data" / "world_perception.json"
SUMMARY_FILE = HERE / "data" / "perception_summary.json"


def _iter_jsonl(path):
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue    # 崩溃残留的半行，跳过不炸


def _parse_ts(ts):
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def latest_perception():
    """最新一帧感知。没有数据返回 None。"""
    last = None
    for rec in _iter_jsonl(FRAMES_FILE):
        last = rec
    return last


def recent_perceptions(hours=None, days=None, limit=100):
    """最近 N 小时/天的汇总条目（含事件）。两个都不传则返回最近 limit 条。"""
    if days:
        since = datetime.now() - timedelta(days=days)
    elif hours:
        since = datetime.now() - timedelta(hours=hours)
    else:
        since = None
    out = []
    for rec in _iter_jsonl(SUMMARY_FILE):
        if since:
            ts = _parse_ts(rec.get("window_end") or rec.get("written_at", ""))
            if ts and ts < since:
                continue
        out.append(rec)
    return out[-limit:]


def collect_events(hours=None, days=None):
    """把范围内所有汇总的事件拉平成一个列表 —— ④ 主动对话的信号源"""
    evts = []
    for rec in recent_perceptions(hours=hours, days=days, limit=10_000):
        for e in rec.get("events", []):
            e = dict(e)
            e["window_end"] = rec.get("window_end")
            evts.append(e)
    return evts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--latest", action="store_true")
    ap.add_argument("--recent-hours", type=float, default=0)
    ap.add_argument("--recent-days", type=float, default=0)
    ap.add_argument("--events", action="store_true", help="只看事件")
    args = ap.parse_args()

    if args.latest:
        rec = latest_perception()
        print(json.dumps(rec, ensure_ascii=False, indent=2) if rec else "(还没有任何帧记录)")
    if args.recent_hours or args.recent_days:
        recs = recent_perceptions(hours=args.recent_hours or None,
                                  days=args.recent_days or None)
        print(f"共 {len(recs)} 条汇总")
        for r in recs[-10:]:
            print(f"  [{r['window_start']}] scene={r['scene']} weather={r['weather']} "
                  f"events={len(r.get('events', []))}  {r['summary_text']}")
    if args.events:
        for e in collect_events(hours=args.recent_hours or None,
                                days=args.recent_days or None):
            print(json.dumps(e, ensure_ascii=False))
    if not (args.latest or args.recent_hours or args.recent_days or args.events):
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

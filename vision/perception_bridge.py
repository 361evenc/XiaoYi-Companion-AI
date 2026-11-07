r"""③ 最后一环：把感知事件写进小忆的记忆库

关键发现（省掉改小忆代码）：
    app.py 第 752 行，小忆**每次启动**都会调用
        memory_bank.import_legacy_events("memory_events.json")
    这个函数：
        - 读一个 JSON 数组：[{"time": 时间戳, "content": "文字", "type": "分类"}]
        - 把每条变成一条"情节记忆"
        - **幂等**（按 time+content 去重，重复导入不会重复写入）

    → 所以我们只要把感知事件**追加**进 memory_events.json，
      小忆下次启动就自动变成"它记得的事"，**一行代码都不用改小忆**。

用法：
    cd D:\xiaoyi_vision
    venv\Scripts\python.exe perception_bridge.py --export
    venv\Scripts\python.exe perception_bridge.py --export --min-importance 5
    venv\Scripts\python.exe perception_bridge.py --dry-run     # 只看要写什么，不动文件

要写多重要的：
    只导"值得记住"的感知 —— 默认只导事件（新物体首次出现 / 场景切换 / 天气突变），
    不是把 43,200 帧全塞进去。记忆库情节上限 300 条，塞满就没用了。
"""

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUMMARY_FILE = HERE / "data" / "perception_summary.json"

# ⚠️ 默认**不写进小忆的仓库**，先写本地的 sidecar 文件。
# 原因见文件末尾「已知缺陷」：走 import_legacy_events 会把感知事件渲染成
# "老人提过：…"，语义是错的。等小忆那边支持 source 字段后再 --memory-dir 指过去。
DEFAULT_MEMORY_DIR = HERE / "data" / "memory_sync"
EVENTS_NAME = "memory_events.json"

# 感知事件的文字模板（写进记忆库的就是这句话，会原样出现在"记忆箱"里）
EVENT_TEXT = {
    "new_group_first_seen": "看到{group}（{labels}）",
    "scene_change": "从{_from}换到了{to}",
}
SCENE_CN = {
    "outdoor_sky": "窗外", "street": "街上", "park": "公园", "garden": "花园",
    "meal": "饭桌", "dining": "餐厅", "pet": "宠物在的地方", "bedroom": "卧室",
    "indoor_dim": "昏暗的房间", "living_room": "客厅", "indoor_plant": "屋里",
    "kitchen": "厨房",
}
LABEL_CN = {
    "person": "人", "cat": "猫", "dog": "狗", "bird": "鸟",
    "potted plant": "盆栽", "vase": "花瓶", "bowl": "碗", "cup": "杯子",
    "dining table": "餐桌", "chair": "椅子", "couch": "沙发", "bed": "床",
    "refrigerator": "冰箱", "microwave": "微波炉", "clock": "钟",
    "laptop": "电脑", "book": "书", "bottle": "瓶子", "sandwich": "三明治",
    "wine glass": "酒杯", "fork": "叉子", "knife": "刀", "spoon": "勺子",
}


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
                    continue


def _ts_of(iso):
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (ValueError, TypeError):
        return time.time()


def build_events(only_events=True):
    """把汇总里的 events 转成小忆记忆格式 [{time, content, type}]"""
    out = []
    for rec in _iter_jsonl(SUMMARY_FILE):
        when = rec.get("window_end") or rec.get("written_at")
        ts = _ts_of(when)
        if not only_events:
            # 非事件模式：每个窗口一条整体描述
            out.append({"time": ts, "type": "perception",
                        "content": f"[感知] {rec.get('summary_text', '')}"})
            continue
        for e in rec.get("events", []):
            t = e.get("type")
            if t == "new_group_first_seen":
                group = e.get("group", "东西")
                labels_cn = [LABEL_CN.get(x, x) for x in e.get("labels", [])]
                if group == "人":
                    text = "看到有人"
                elif group == "其他":
                    text = "看到" + ("、".join(labels_cn) if labels_cn else "东西")
                else:
                    text = f"看到{group}" + (f"（{'、'.join(labels_cn)}）" if labels_cn else "")
            elif t == "scene_change":
                text = (f"从{SCENE_CN.get(e.get('from'), e.get('from'))}换到了"
                        f"{SCENE_CN.get(e.get('to'), e.get('to'))}")
            else:
                text = json.dumps(e, ensure_ascii=False)
            out.append({"time": ts, "type": "perception", "content": f"[感知] {text}"})
    return out


def merge_into(memory_dir, new_events, dry_run=False):
    """把新事件合并进 memory_events.json（按 time+content 去重）"""
    path = Path(memory_dir) / EVENTS_NAME
    existing = []
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []

    seen = {(round(float(x.get("time", 0)), 3), str(x.get("content", ""))) for x in existing}
    added = []
    for ev in new_events:
        key = (round(float(ev["time"]), 3), ev["content"])
        if key in seen:
            continue
        seen.add(key)
        added.append(ev)

    if dry_run:
        return len(existing), added, path

    if added:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(existing + added, f, ensure_ascii=False, indent=2)
    return len(existing), added, path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", action="store_true", help="真正写进小忆的记忆文件")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写文件")
    ap.add_argument("--all-windows", action="store_true",
                    help="不只导事件，每个汇总窗口都导一条")
    ap.add_argument("--memory-dir", default=str(DEFAULT_MEMORY_DIR))
    args = ap.parse_args()

    if not (args.export or args.dry_run):
        ap.print_help()
        return 0

    events = build_events(only_events=not args.all_windows)
    print(f"从汇总里提取到 {len(events)} 条候选（{'仅事件' if not args.all_windows else '全部窗口'}）")
    if not events:
        print("[!] 没有可导出的感知事件 —— 先跑 perception_service.py 攒数据")
        return 0

    before, added, path = merge_into(args.memory_dir, events, dry_run=args.dry_run)
    print(f"记忆文件    : {path}")
    print(f"原有条数    : {before}")
    print(f"本次新增    : {len(added)} 条")
    for ev in added[:10]:
        print("   +", ev["content"])
    if len(added) > 10:
        print(f"   ... 还有 {len(added) - 10} 条")

    if args.dry_run:
        print("\n[dry-run] 没写文件。加 --export 才会真写。")
    else:
        print("\n[OK] 已写入 sidecar。")
        print("     要真正喂给小忆：加 --memory-dir \"D:\\xiaoyi\\XiaoYi-Companion-AI-main\"")
        print("     ⚠️ 但见文末「已知缺陷」—— 小忆现在会把它读成『老人提过』，语义不对。")
    return 0


r"""
已知缺陷（2026-10-28 实测发现，别忽略）
=========================================

现象：把感知事件写进小忆的 memory_events.json 后，小忆启动会正确导入成情节记忆
（`import_legacy_events` 幂等，实测 7 条导入、二次导入 0 条，链路本身没问题）。
但 `build_chat_context()` 渲染出来是：

    - 老人提过：[感知] 看到有人

**"老人提过"是错的** —— 那是小忆看到的，不是老人说的。
客户/评委一看就知道是硬塞进去的，比不做还糟。

根因：小忆的记忆系统只区分 `field`（健康/用药/物品…），没有 `source` 概念，
`build_chat_context` 里把情节一律渲染成"老人提过：…"。

正确的修法（改小忆两处，约 6 行）：
    1. `import_legacy_events` 里给 episode 加一个字段：`"source": "perception"`
       （或复用 type，把 type 存进 episode）
    2. `build_chat_context` 渲染时按 source 分支：
           source == "perception" -> "小忆看到过：{text}"
           否则                   -> "老人提过：{text}"

在没改之前：**别用 --memory-dir 指向小忆**，先把感知当"当前状态"用
（`perception_query.latest_perception()`），这条路不需要改小忆也不会说错话。
"""


if __name__ == "__main__":
    raise SystemExit(main())

r"""③ 感知落库：帧源 → 检测 → world_perception.json + 每 30 分钟汇总

这是 ③ 的主程序：把 ② 的"单帧描述"变成长期运行的记录服务。

用法（模拟跑，1 分钟 = 1 个汇总周期）：
    cd D:\xiaoyi_vision
    venv\Scripts\python.exe perception_service.py --interval 2 --summary-every 60 --duration 0.01

正式跑（真实 30 分钟汇总）：
    venv\Scripts\python.exe perception_service.py --interval 2 --duration 24

产出：
    data/world_perception.json   —— 帧记录（JSON Lines，一行一条）
    data/perception_summary.json —— 30 分钟汇总（一行一条）

查询接口（给 AI 工程师）见 perception_query.py，不在这文件里。

设计决定：
    1. 帧记录用 JSON Lines（每行一个独立 JSON），不用一个大 JSON 数组 ——
       追加写不用回读整个文件，跑 24h 也不会内存膨胀，崩了也不丢已写的行
    2. 汇总独立成文件，不让"查最近状态"的人去扫几十万行帧记录
    3. 新物体首次出现 / 场景变化 = 事件，也写进汇总（④ 主动对话的信号源）
"""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import sys
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from describe_frame import describe_frame, load_model
from event_trigger import EventTrigger

DATA_DIR = HERE / "data"
FRAMES_FILE = DATA_DIR / "world_perception.json"
SUMMARY_FILE = DATA_DIR / "perception_summary.json"


def append_jsonl(path, record):
    """JSON Lines 追加写：一行一条，崩了不丢已写数据"""
    path.parent.mkdir(exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def summarize(window, seen_groups, prev_scene):
    """把一个窗口内的帧压成一条汇总 + 提取事件（新物体首次出现/场景切换）

    seen_groups: set，历史上出现过的物体组（会原地更新）
    prev_scene:  上一窗口的主场景（str 或 None）
    返回 (汇总dict, 新的prev_scene)
    """
    if not window:
        return None, prev_scene
    groups = {}
    for rec in window:
        for o in rec["objects"]:
            groups.setdefault(o["group"], set()).add(o["label"])
    groups = {k: sorted(v) for k, v in groups.items()}

    # 事件：本窗口新出现的组（历史里没见过的）
    events = []
    for g, labels in groups.items():
        if g not in seen_groups:
            events.append({"type": "new_group_first_seen", "group": g, "labels": labels})
            seen_groups.add(g)

    # 事件：与上一窗口相比场景切换
    scenes = []
    for rec in window:
        s = rec["scene"]
        if s not in scenes:
            scenes.append(s)
    main_scene = max(set(scenes), key=scenes.count) if scenes else "unknown"
    if prev_scene and main_scene != prev_scene:
        events.append({"type": "scene_change", "from": prev_scene, "to": main_scene})

    last_weather = window[-1]["weather"]
    return {
        "window_start": window[0]["ts"],
        "window_end": window[-1]["ts"],
        "frames": len(window),
        "scene": main_scene,
        "scenes_seen": scenes,
        "weather": last_weather,
        "groups": groups,
        "summary_text": window[-1]["summary"],   # 用窗口内最后一条的摘要句
        "events": events,
        "written_at": datetime.now().isoformat(timespec="seconds"),
    }, main_scene


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--source", default="sim", choices=["sim", "camera", "image"],
                    help="帧源：sim 模拟 / camera 真机摄像头 / image 单图")
    ap.add_argument("--camera-index", type=int, default=0)
    ap.add_argument("--image", default="", help="--source image 时用哪张图")
    ap.add_argument("--summary-every", type=float, default=1800,
                    help="汇总窗口秒数，正式值 1800（30 分钟）；测试可改小")
    ap.add_argument("--duration", type=float, default=0, help="跑几小时，0=一直跑")
    args = ap.parse_args()

    model = load_model()

    from sources import make_source
    if args.source == "camera":
        src = make_source("camera", index=args.camera_index, interval=args.interval)
    elif args.source == "image":
        src = make_source("image", image_path=args.image or str(HERE / "frames" / "01_sky.jpg"),
                          interval=args.interval)
    else:
        src = make_source("sim", frames_dir=str(HERE / "frames"), interval=args.interval)

    if not src.ok():
        print("[X] 帧源不可用")
        return 1

    DATA_DIR.mkdir(exist_ok=True)
    print(f"[OK] 感知服务启动 | {src.describe()}")
    print(f"[OK] 汇总窗口 {args.summary_every}s")
    print(f"[OK] 帧记录 -> {FRAMES_FILE}")
    print(f"[OK] 汇总    -> {SUMMARY_FILE}")

    deadline = time.time() + args.duration * 3600 if args.duration > 0 else None
    window = []
    window_deadline = time.time() + args.summary_every
    seen = set()          # 历史上出现过的物体组（事件判断用）
    prev_scene = None     # 上一窗口的主场景
    n_frames = n_summaries = 0

    # T-1 端侧事件 → 云VLM 调度器（5 类事件闸门，平时 0 次 VLM）
    # MVP：vlm_call=None（诚实降级不调 VLM）；接 VLM 后注入 callable(image, prompt)->str
    trigger = EventTrigger(vlm_call=None,
                           state_path=DATA_DIR / "event_trigger_state.json")

    try:
        while True:
            if deadline and time.time() >= deadline:
                print("[TIMEUP] 到达设定时长，正常停止")
                break
            ok, frame, meta = src.read()
            if ok:
                rec = describe_frame(frame, meta, model)
                append_jsonl(FRAMES_FILE, rec)
                window.append(rec)
                n_frames += 1
                if n_frames % 100 == 0:
                    print(f"[HEART] 已记录 {n_frames} 帧 | 汇总 {n_summaries} 条")
            if time.time() >= window_deadline:
                if window:
                    s, prev_scene = summarize(window, seen, prev_scene)
                    # T-1：5 类事件闸门 → 云 VLM 细描（平时 0 次 VLM，只有事件才调）
                    if s["events"]:
                        s["events"] = trigger.on_summary_events(s["events"])
                        for e in s["events"]:
                            tag = " [VLM]" if e.get("vlm_called") else ""
                            print(f"[EVENT] {e}{tag}")
                    append_jsonl(SUMMARY_FILE, s)
                    n_summaries += 1
                    window = []
                window_deadline = time.time() + args.summary_every
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n[STOP] 收到 Ctrl+C，正常退出")

    if window:
        s, prev_scene = summarize(window, seen, prev_scene)
        append_jsonl(SUMMARY_FILE, s)
        n_summaries += 1
        print("[OK] 收尾：未满窗口的帧也已汇总")

    print(f"[DONE] 帧 {n_frames} 条 | 汇总 {n_summaries} 条")
    try:
        src.release()      # 真机摄像头必须释放，否则再次打开会失败
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

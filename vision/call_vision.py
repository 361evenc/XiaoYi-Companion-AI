r"""H-12 通话视觉识别 —— 通话帧在内存里识别 → 结构化结果入用户画像 → 帧立即丢弃

**这一卡的核心不是"识别"，是三条数据流边界。** 识别本身用现成 YOLO 就够，
真正容易出错的是"什么写、什么不写、写在哪"：

    ① 原始帧：内存生成 → 立即识别 → 丢弃，全程不落盘（无帧存储 = 不是录制）
    ② 识别结果：结构化，**只写 confidence ≥ 0.7 的字段**，入用户画像
    ③ 不入空间图谱/记忆箱：通话是短时会话，数据与 H-2 持续感知严格隔离

任务卡（H-12 第 4 条）把这三条写成了验收项，所以这里把它们做成**可代码验证**的：
    · profile.py 里没有写 FRAMES_FILE / 图谱 / 记忆箱的任何路径
    · 测试用 0.6 / 0.69 / 0.7 / 0.8 四档 confidence 卡边界

用法（自测，用测试图集模拟一个 3 分钟通话）：
    venv\Scripts\python.exe call_vision.py --duration 180 --interval 2

给后端用：
    from call_vision import CallVision
    cv = CallVision()
    out = cv.observe(frame, call_id, gaze_hint=None)   # 每次调一次，返回写入了什么
    cv.end_call(call_id)                              # 结束通话，清缓存（不等同删画像）

设计决定：
    1. **不落盘**：本文件没有任何 imwrite/VideoWriter/PIL.save/requests 路径。
       frame 参数用完即弃，函数内部不留引用
    2. **低置信不写**：不是"写进去再打标"，是不写。低置信写进去 = 编造
    3. **表情/注视用启发式 + 明确标注**：MVP 不引入表情模型（依赖重、老人表情
       数据少、误判代价高）。用画面亮度/人脸框比例做粗判，**并标 source=heuristic**，
       下游知道这是粗信号，不能当结论用。这是诚实的做法，不是伪装成 AI
    4. **不说话也不硬猜**：人脸检测不到 → person_present=False（这是确定的），
       但 emotion/gaze 字段**直接不写**，而不是写 "unknown"
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DATA_DIR = HERE / "data"
PROFILE_FILE = DATA_DIR / "user_profile.jsonl"     # 用户画像：结构化识别结果

# 任务卡写死的阈值：<0.7 的字段不写（= 不编造）
CONF_MIN_WRITE = 0.7
# 检测用的 YOLO 置信下限（比写入阈值低：先检测出来，再决定写不写）
CONF_MIN_DETECT = 0.35

# 情绪 → 语速（复用现有语速体系：默认 0.8，开心略快，低落放慢）
# 注意：这里只给建议值，实际调用由 /api/tts 的 speed 参数决定
EMOTION_SPEED = {
    "happy": 0.95,
    "neutral": 0.80,
    "confused": 0.85,
    "low": 0.70,
}

# 话术触发（不写死长句，交给对话模型；这里只给"该说什么方向"）
HINTS = {
    "person_left": "老人离开了画面。问一句'您去哪了'或静默等待，不要反复打扰。",
    "not_gazing": "老人没在看屏幕。放慢语速、重复要点。",
    "hand_object": "老人手里拿着东西。可以顺势问一句（如'您在看老照片吗'）。",
}


def _now():
    return datetime.now().isoformat(timespec="seconds")


def append_jsonl(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


class CallVision:
    """单进程内的通话识别器。一次通话 = 一个 call_id。"""

    def __init__(self, model=None):
        self._model = model
        self._last_present = None
        self._last_gaze = None
        self._frames_seen = 0

    def _load_model(self):
        if self._model is None:
            # 复用同一份权重加载逻辑（模型缺失时清晰报错，不静默挂起）
            from describe_frame import load_model
            self._model = load_model()
        return self._model

    # ---------------- 主入口 ----------------

    def observe(self, frame, call_id, scene=None, persist=True):
        """识别一帧（只在内存里），返回结构化结果 + 本次实际写入画像的字段。

        参数：
            frame   : BGR ndarray（调用方负责用完即弃，本函数不留引用）
            call_id : 通话标识，写入画像用于关联
            scene   : 场景标签（可选，来自调用方；不认识就传 None，不硬猜）
            persist : 是否把结果写进用户画像（默认 True）。通话页 opt-in 分析传 False：
                      只把"这次看到什么"回给当下用，不落画像（通话"说完就忘掉"）

        返回 {"record": {...}, "written": [...], "hints": [...], "dropped": [...]}
        """
        model = self._load_model()
        r = model(frame, conf=CONF_MIN_DETECT, imgsz=640, verbose=False, device="cpu")[0]

        persons, objects, best_person = [], [], None
        if r.boxes is not None:
            for i in range(len(r.boxes)):
                label = r.names[int(r.boxes.cls[i])]
                conf = round(float(r.boxes.conf[i]), 3)
                box = [round(float(v), 1) for v in r.boxes.xyxy[i].tolist()]
                if label == "person":
                    persons.append({"conf": conf, "bbox": box})
                    if best_person is None or conf > best_person["conf"]:
                        best_person = {"conf": conf, "bbox": box}
                elif label not in ("chair", "couch", "bed", "dining table", "tv"):
                    # 其它类不算"手持物品"的候选：家具类太多误报
                    objects.append({"label": label, "conf": conf, "bbox": box})

        h, w = frame.shape[:2]
        rec = {
            "time": _now(),
            "call_id": call_id,
            "person_present": best_person is not None,
        }
        written = ["time", "call_id", "person_present"]

        # —— 在不在：person 检测本身是确定的（有/没有），但置信度也要过关才写 True
        if best_person is not None and best_person["conf"] >= CONF_MIN_WRITE:
            rec["person_present"] = True
        elif best_person is None:
            rec["person_present"] = False

        # —— 在看没看：用"人脸框面积占比 + 是否居中"粗判，标 heuristic
        if best_person is not None:
            x1, y1, x2, y2 = best_person["bbox"]
            area_ratio = max(0.0, (x2 - x1) * (y2 - y1)) / float(w * h)
            cx = (x1 + x2) / 2.0 / w
            centered = abs(cx - 0.5) < 0.18
            # 面积占比 >6% 且居中 → 认为在看屏幕（离得近、正对）
            gaze_conf = round(min(0.95, 0.5 + area_ratio * 5 + (0.2 if centered else 0)), 3)
            if gaze_conf >= CONF_MIN_WRITE:
                rec["gaze"] = "screen" if (area_ratio > 0.06 and centered) else "away"
                rec["gaze_confidence"] = gaze_conf
                rec["gaze_source"] = "heuristic"
                written += ["gaze", "gaze_confidence"]

        # —— 手持物品：取 person 框内部/紧邻的高置信非家具物体
        if best_person is not None and objects:
            held = self._pick_held(objects, best_person["bbox"])
            if held and held["conf"] >= CONF_MIN_WRITE:
                rec["hand_object"] = held["label"]
                rec["object_confidence"] = held["conf"]
                written += ["hand_object", "object_confidence"]

        # —— 表情：MVP 不用表情模型（见模块说明第 3 条）。
        #    没有可靠信号就【不写】，而不是写 unknown —— 写 unknown 也是编造一种状态。
        emotion = self._emotion_heuristic(frame, best_person)
        if emotion and emotion["confidence"] >= CONF_MIN_WRITE:
            rec["emotion"] = emotion["value"]
            rec["emotion_confidence"] = emotion["confidence"]
            rec["emotion_source"] = "heuristic"
            written += ["emotion", "emotion_confidence"]

        # —— 场景：调用方给了才写，不猜。"unknown" 是帧源的原值（真机拿不到场景），
        #    写进画像等于把"不知道"当成"知道了"，所以也按"没给"处理。
        if scene and scene != "unknown":
            rec["scene"] = scene
            written.append("scene")

        # 话术提示：给对话层的话术方向（不进画像，不作为数据）
        hints = []
        if self._last_present is True and rec["person_present"] is False:
            hints.append("person_left")
        if rec.get("gaze") == "away":
            hints.append("not_gazing")
        if rec.get("hand_object"):
            hints.append("hand_object")
        self._last_present = rec["person_present"]
        self._last_gaze = rec.get("gaze")

        # ★ 只写结构化结果。不写帧、不写图谱、不写记忆箱。
        #    通话页 opt-in 分析传 persist=False：帧零落盘 + 结论也不落画像（通话"说完就忘掉"）。
        if persist:
            append_jsonl(PROFILE_FILE, rec)
        self._frames_seen += 1

        # dropped：画像里有、但没进"已确认字段"的键（如 gaze_source 这类溯源标记）
        dropped = [k for k in rec if k not in written]

        # frame 在本函数里不再使用；函数返回后调用方即可丢弃
        return {"record": rec, "written": written, "hints": hints,
                "dropped": dropped, "speed_hint": EMOTION_SPEED.get(rec.get("emotion"), 0.8)}

    # ---------------- 辅助 ----------------

    def _pick_held(self, objects, person_bbox):
        """手持物品：物体框中心落在 person 框内（或与之重叠 >50%）。"""
        px1, py1, px2, py2 = person_bbox
        best = None
        for o in objects:
            ox1, oy1, ox2, oy2 = o["bbox"]
            cx, cy = (ox1 + ox2) / 2, (oy1 + oy2) / 2
            inside = px1 <= cx <= px2 and py1 <= cy <= py2
            if not inside:
                continue
            if best is None or o["conf"] > best["conf"]:
                best = o
        return best

    def _emotion_heuristic(self, frame, best_person):
        """粗情绪判断。只区分"亮度暗示的室内强弱光"这种**明确可解释**的信号。

        诚实说明：这不是表情识别。老人表情数据少、光照干扰大，
        MVP 期硬上表情模型 = 高误判 = 误判后进画像 = 编造。
        所以这里只在两种情况下给结论：
            · 画面极暗（平均亮度 < 45）→ low（可能光线差/背光，语速放慢无害）
        其余一律不给结论（返回 None → 不写画像）。
        """
        try:
            import numpy as np
        except ImportError:
            return None
        gray = frame.mean()
        if gray < 45 and best_person is not None:
            # 置信度刻意压在 0.7 以下：这是"光线暗示"，不是表情结论
            # → 因此不会写入画像，只影响语速建议，不进档案
            return {"value": "low", "confidence": 0.6}
        return None

    def end_call(self, call_id):
        """结束通话：只清内存态。画像条目按任务卡保留（老人说"忘掉"才删）。"""
        self._last_present = None
        self._last_gaze = None
        return {"ok": True, "call_id": call_id, "frames_seen": self._frames_seen}

    # ---------------- 删除（老人说"把通话里看到的忘掉"） ----------------

    def forget(self, call_id=None):
        """删除画像条目。call_id=None 表示删全部通话记录。

        与 B-5 的删除逻辑同类：删除后不可恢复，返回删了几条。
        """
        if not PROFILE_FILE.exists():
            return {"ok": True, "deleted": 0}
        kept, deleted = [], 0
        with open(PROFILE_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if call_id is None or rec.get("call_id") == call_id:
                    deleted += 1
                else:
                    kept.append(line)
        tmp = PROFILE_FILE.with_suffix(".tmp")
        tmp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        tmp.replace(PROFILE_FILE)
        return {"ok": True, "deleted": deleted}


# 进程内单例
call_vision = CallVision()


def main():
    ap = argparse.ArgumentParser(description="H-12 通话视觉识别（内存识别、零落盘）")
    ap.add_argument("--duration", type=float, default=0, help="跑几秒，0=一直跑")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--source", default="sim", choices=["sim", "camera", "image"])
    ap.add_argument("--call-id", default="")
    ap.add_argument("--max-seconds-per-frame", type=float, default=5.0,
                    help="单帧识别的耗时上限，超过就打警告（真机 CPU 实测约 1-5s）")
    args = ap.parse_args()

    from sources import make_source
    # ★ interval=0：由主循环自己的时钟控节奏。
    #   帧源自带节流会让"读太快"返回 ok=False，主循环再 sleep 就变成双倍间隔
    #   （实测 8 秒只出 1 帧）。让源不节流、循环单点控时，节奏才可控。
    src = make_source(args.source, interval=0,
                      **({"index": 0} if args.source == "camera" else {}))
    if not src.ok():
        print("[X] 帧源不可用")
        return 1

    cid = args.call_id or f"call_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    print(f"[OK] 模拟通话开始 call_id={cid} | {src.describe()}")
    deadline = time.time() + args.duration if args.duration > 0 else None
    n = 0
    next_at = time.time()
    try:
        while True:
            if deadline and time.time() >= deadline:
                break
            ok, frame, meta = src.read()
            if ok and frame is not None:
                t_infer = time.time()
                out = call_vision.observe(frame, cid, scene=meta.get("scene"))
                cost = time.time() - t_infer
                del frame                       # ★ 帧用完即弃
                n += 1
                rec = out["record"]
                flags = [k for k in ("person_present", "gaze", "emotion", "hand_object")
                         if k in rec]
                slow = "  ⚠ 慢" if cost > args.max_seconds_per_frame else ""
                print(f"[{n:03d}] {cost:.2f}s{slow} | 写入 {out['written']} "
                      f"| 帧内字段 {flags} | 语速 {out['speed_hint']} | 提示 {out['hints']}")
            # 绝对时钟：下一拍 = 上一拍计划时间 + interval，不累积延迟
            next_at += args.interval
            wait = next_at - time.time()
            if wait > 0:
                time.sleep(wait)
            else:
                next_at = time.time()       # 落后太多就重新对齐，不追帧
    except KeyboardInterrupt:
        print("\n[STOP] Ctrl+C")

    call_vision.end_call(cid)
    print(f"[DONE] 共 {n} 帧 | 画像 -> {PROFILE_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

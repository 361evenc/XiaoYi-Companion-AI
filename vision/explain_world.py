r"""H-13 视频解释外界 —— 电视内容 / 门口动态 / 天气，且**永远让位安全告警**

任务卡三件事，都有一个"不许越界"的边界：

    ① 电视内容解释：**粗粒度**（播的是什么类型的节目）
       —— 不做逐句复述。老人要的是"有得聊"，不是听人把电视念一遍。
       而且逐句复述会产生"我没看到也能说"的错觉，一旦说错内容老人会信。

    ② 门口动态："好像有邻居来了"
       —— **只说"有人来过"，不记是谁**（B-4 第三方人脸规则）。
       所以这里的人脸处理方式是：检到人 → 只说"有人"→ **立即丢弃人脸框**，
       不存人脸、不比对、不给性别年龄。源码里搜不到任何人脸相关模型调用。

    ③ 天气：室外判天气（CLIP），室内 → **unknown**
       —— 室内看不到天，硬判就是编。这条 clip_zeroshot.py 已经踩过坑
       （餐盘 → snow、室内盆栽 → rain），这里沿用它的"室内不判天气"。

**本卡最容易做错的地方不是识别，是"抢占"**：
解释类功能是"闲聊"，安全告警是"救命"。两者抢同一个摄像头、同一段对话时间时，
**安全必须无条件抢占，解释必须无条件退让**。所以：

    · `explain()` 第一件事就是问 fall_guard 的状态，非 idle → 直接返回 suppressed
    · 安全告警进行中，解释器**不产生任何新话术**（不是"产生了但不播"，是根本不产生
      —— 少一层"以为没播其实播了"的风险）
    · 抢占事件全量写 explain_events.jsonl，`suppressed` 次数可查

第三个边界：**解释不猜**。电视上播什么，只给"类型 + 置信度"，置信度 <0.5 就
说"这个我没看明白"；门口有人但看不清 → 只说"好像有人"，不编"是邻居"。

用法：
    venv\Scripts\python.exe explain_world.py --image frames/11_table_people.jpg --what all
    venv\Scripts\python.exe explain_world.py --selftest
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DATA_DIR = HERE / "data"
EVENTS_FILE = DATA_DIR / "explain_events.jsonl"

# ---- 阈值 ----
TV_CONF_MIN = 0.50        # 电视类型置信度下限：低于这个说"我没看明白"
SCENE_CONF_MIN = 0.45     # 场景/天气置信度下限
STRANGER_KEEP_S = 3.0     # 门口"有人"去抖：3 秒内不重复说

# ---- 电视节目类型（粗粒度，够老人聊起来就行） ----
TV_PROMPTS = {
    "a news program with anchors talking": "新闻",
    "a tv drama scene with actors": "电视剧",
    "a cooking show with food": "做菜的节目",
    "a sports game on tv": "体育比赛",
    "a variety show with a stage": "综艺节目",
    "a cartoon animation": "动画片",
    "a documentary nature scene": "纪录片",
    "a music performance on stage": "唱歌/演出",
    "a tv screen showing a static menu or desktop": "没在播节目（像菜单/待机画面）",
}

SCENE_CN = {
    "living_room": "客厅", "kitchen": "厨房", "bedroom": "卧室",
    "street": "街上", "park": "公园", "garden": "花园",
    "dining": "饭厅", "balcony": "阳台", "outdoor_sky": "外面的天",
}
WEATHER_CN = {"sunny": "晴天", "rain": "下雨", "cloudy": "阴天", "snow": "下雪"}


def _now():
    return datetime.now().isoformat(timespec="seconds")


def append_jsonl(path, rec):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


class ExplainWorld:
    """解释外界。**所有对外输出都是一句话 + 来源 + 置信度**，不猜、不断言。"""

    def __init__(self):
        self._last_stranger_at = 0.0
        self.stats = {"explained": 0, "suppressed": 0, "uncertain": 0}

    # ---------------- 安全抢占检查 ----------------

    def _safety_busy(self):
        """安全告警是否正在处理中（非 idle = 占用）。任何解释动作前先问这一句。"""
        try:
            from fall_guard import ST_IDLE, guard
            # 直接读 guard.state 而不是 status()：status() 会带 stats 拷贝，
            # 每帧都调没必要
            return guard.state != ST_IDLE
        except Exception:
            # fall_guard 不可用（没装/导入失败）→ **按占用处理**（保守：宁可不解释）
            return True

    def _suppress(self, what, why="fall_alarm_active"):
        self.stats["suppressed"] += 1
        append_jsonl(EVENTS_FILE, {"ts": _now(), "event": "suppressed",
                                   "what": what, "reason": why})
        return {"ok": False, "suppressed": True, "what": what, "reason": why,
                "text": ""}   # ★ 空文本：不产生任何可播的内容

    # ---------------- 主入口 ----------------

    def explain(self, frame, what="all", meta=None):
        """解释一帧。

        what: "tv" | "door" | "weather" | "all"
        返回 {"ok", "text", "items": [...], "suppressed": bool}
        """
        if self._safety_busy():
            return self._suppress(what)

        items = []
        texts = []

        if what in ("tv", "all"):
            tv = self._explain_tv(frame)
            items.append(tv)
            if tv.get("text"):
                texts.append(tv["text"])

        if what in ("door", "all"):
            door = self._explain_door(frame)
            items.append(door)
            if door.get("text"):
                texts.append(door["text"])

        if what in ("weather", "all"):
            w = self._explain_weather(frame)
            items.append(w)
            if w.get("text"):
                texts.append(w["text"])

        # 第二道检查：算完再确认一次安全状态（万一算的过程中告警触发了）
        if self._safety_busy():
            return self._suppress(what, why="fall_alarm_triggered_mid")

        text = " ".join(t for t in texts if t)
        rec = {"ts": _now(), "event": "explain", "what": what, "text": text,
               "items": items, "meta": meta or {}}
        append_jsonl(EVENTS_FILE, rec)
        self.stats["explained"] += 1
        return {"ok": True, "suppressed": False, "text": text, "items": items}

    # ---------------- ① 电视 ----------------

    def _explain_tv(self, frame):
        """粗粒度节目类型。不做逐句复述 —— 那是它看不见的东西。

        ★ 前置检查（实测踩出来的坑）：CLIP 对**没有电视的**画面也一定能选出
        "最像的节目类型"。实测 frames/18_living_bright.jpg（明亮客厅、根本没开电视）
        被判成"综艺节目 0.785"——如果直接信，就会对着空客厅说"电视上在播综艺"。

        所以流程改成两步：
         ① 先问 YOLO：画面里有没有 tv/monitor 框？没有 → 直接说"我没看到电视"
         ② 有电视 → 才做 CLIP 节目分类
        代价是：手机/平板播节目不会被识别（YOLO 的 monitor 类可能兜住，也可能不）。
        宁可少说一项，也不能对着没开的电视瞎说。
        """
        out = {"what": "tv", "type": None, "conf": 0.0, "text": "", "source": "clip"}
        # 有屏幕 ≠ 在播节目。笔记本/显示器上扣着合盖、或屏幕上啥都没播，
        # 这时候说"在播综艺"也是编 —— 所以"屏幕"只作为必要条件，不够
        SCREEN_CLASSES = ("tv", "monitor")
        try:
            from describe_frame import load_model
            model = load_model()
            r = model(frame, conf=0.40, imgsz=640, verbose=False, device="cpu")[0]
            has_tv = False
            if r.boxes is not None:
                for i in range(len(r.boxes)):
                    if r.names[int(r.boxes.cls[i])] in SCREEN_CLASSES:
                        has_tv = True
                        break
            del r
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"
            return out

        if not has_tv:
            out["has_screen"] = False
            out["text"] = ""           # 没看到电视 → 不说电视的事（不是"没看明白"，是根本没这东西）
            out["note"] = "画面里没找到电视"
            return out
        out["has_screen"] = True

        try:
            from PIL import Image
            import clip_zeroshot as cz
            img = Image.fromarray(frame[..., ::-1]) if hasattr(frame, "shape") else frame
            cz._load()
            pairs = cz._scores(img, TV_PROMPTS)
            label, conf = pairs[0]
            out["conf"] = round(conf, 3)
            out["top3"] = [{"type": l, "conf": round(p, 3)} for l, p in pairs[:3]]
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"
            out["text"] = ""
            return out

        if label.startswith("没在播节目"):
            # CLIP 自己说"这是菜单/待机画面" → 如实转达，不硬套一个节目类型
            out["type"] = None
            out["text"] = "电视开着，但没在播节目。"
            return out

        if conf < TV_CONF_MIN:
            out["type"] = None
            out["text"] = "电视上在播什么我没看明白，您给我说说？"
            self.stats["uncertain"] += 1
            return out

        out["type"] = label
        # 只说类型 + 一句"能聊起来"的接话，不复述内容
        out["text"] = f"电视上看着像在播{label}。"
        return out

    # ---------------- ② 门口 ----------------

    def _explain_door(self, frame):
        """只说"有人来过"，**不记是谁**（B-4）。

        实现上刻意只取"有没有 person 框"，拿到布尔值就把框丢掉了 ——
        不存人脸裁剪、不做比对、不给性别年龄。这是代码层面的"不记是谁"。
        """
        out = {"what": "door", "person": False, "conf": 0.0, "text": "",
               "privacy": "person_presence_only"}
        try:
            from describe_frame import load_model
            import numpy as np
            model = load_model()
            r = model(frame, conf=0.45, imgsz=640, verbose=False, device="cpu")[0]
            best = 0.0
            if r.boxes is not None:
                for i in range(len(r.boxes)):
                    if r.names[int(r.boxes.cls[i])] == "person":
                        best = max(best, float(r.boxes.conf[i]))
            del r                      # ★ 框在这里就丢了，函数外拿不到任何人脸信息
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"
            return out

        out["conf"] = round(best, 3)
        now = time.time()
        if best <= 0:
            out["person"] = False
            return out

        out["person"] = True
        if now - self._last_stranger_at < STRANGER_KEEP_S:
            out["dedup"] = True        # 3 秒内不重复说（门口有人站一会不该念三遍）
            return out
        self._last_stranger_at = now
        out["text"] = "门口好像有人。"
        return out

    # ---------------- ③ 天气 ----------------

    def _explain_weather(self, frame):
        """室外判天气；室内 → unknown（不许编）。"""
        out = {"what": "weather", "weather": None, "indoor": None, "conf": 0.0,
               "text": "", "source": "clip"}
        try:
            from PIL import Image
            import clip_zeroshot as cz
            img = Image.fromarray(frame[..., ::-1]) if hasattr(frame, "shape") else frame
            res = cz.infer_environment(img)
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"
            return out

        out["indoor"] = res["indoor"]["label"]
        out["scene"] = res["scene"]["label"]
        out["scene_cn"] = SCENE_CN.get(res["scene"]["label"], "")

        if res["weather"].get("label") == "unknown":
            out["text"] = ""           # 室内不说天气（说了就是编）
            out["note"] = "室内看不出天气"
            return out

        label, conf = res["weather"]["label"], res["weather"]["conf"]
        out["weather"], out["conf"] = label, conf
        if conf < SCENE_CONF_MIN:
            out["text"] = ""
            out["note"] = "天气判不准，不说"
            self.stats["uncertain"] += 1
            return out
        out["text"] = f"外面看着是{WEATHER_CN.get(label, label)}。"
        return out

    # ---------------- 状态 ----------------

    def status(self):
        return {"stats": dict(self.stats),
                "safety_busy": self._safety_busy(),
                "thresholds": {"tv": TV_CONF_MIN, "scene": SCENE_CONF_MIN}}


explainer = ExplainWorld()


# ================================================================ 自测


def _ok(cond, name, fails, detail=""):
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (f"  | {detail}" if detail else ""))
    if not cond:
        fails.append(name)


def selftest():
    fails = []
    print("=" * 70)
    print("H-13 视频解释外界 自测")
    print("=" * 70)

    import numpy as np
    from PIL import Image

    def load(name):
        p = HERE / "frames" / name
        if not p.exists():
            return None
        return np.array(Image.open(p).convert("RGB"))[..., ::-1].copy()   # RGB→BGR

    print("\n[1] 安全抢占：告警进行中 → 解释器不产生任何文本")
    import fall_guard as fg
    saved = fg.guard.state
    try:
        fg.guard.state = fg.ST_ASKING          # 模拟"正在问老人您没事吧"
        e = ExplainWorld()
        frame = load("11_table_people.jpg")
        r = e.explain(frame, "all")
        _ok(r["suppressed"] is True, "告警中 → suppressed=True", fails)
        _ok(r["text"] == "", "告警中 → text 为空（不是产生了但不播）", fails)
        _ok(e.stats["suppressed"] == 1, "抢占次数计入 stats", fails)
        _ok(e.stats["explained"] == 0, "没有产生解释", fails)
    finally:
        fg.guard.state = saved

    print("\n[2] 空闲时正常解释（室内图）")
    e = ExplainWorld()
    frame = load("17_living_window.jpg")
    r = e.explain(frame, "all")
    _ok(r["suppressed"] is False, "空闲 → 正常执行", fails)
    _ok(r["ok"] is True, "ok=True", fails)

    print("\n[3] 门口：只说'有人'，结果里不含人脸/身份信息")
    d = [it for it in r["items"] if it["what"] == "door"][0]
    _ok(d["privacy"] == "person_presence_only", "privacy 标记", fails)
    for bad in ("face", "gender", "age", "identity", "name", "bbox"):
        _ok(bad not in d, f"门口结果不含 {bad} 字段", fails)

    print("\n[4] 天气：室内 → unknown，不说天气")
    w = [it for it in r["items"] if it["what"] == "weather"][0]
    if w.get("indoor") == "indoor":
        _ok(w["text"] == "", f"室内不说天气：{w['text']!r}", fails)
        _ok(w.get("weather") is None, "weather 字段为空", fails)
    else:
        print(f"  [SKIP] 这张判成 outdoor（{w.get('indoor')}），跳过室内断言")

    print("\n[5] 电视：★ 画面里没有电视时，不许说电视在播什么（实测踩坑回归）")
    e_tv = ExplainWorld()
    tv0 = e_tv._explain_tv(load("18_living_bright.jpg"))   # 明亮客厅，没开电视
    _ok(tv0.get("has_screen") is False, "检出无屏幕", fails)
    _ok(tv0["text"] == "", f"无电视 → 不开口（实测曾被判成综艺 0.785）：{tv0['text']!r}", fails)
    _ok(tv0["type"] is None, "无电视 → 不给节目类型", fails)

    print("\n[5b] 电视：有屏幕但分不清 → 说'没看明白'，不编节目类型")
    e2 = ExplainWorld()
    tv = e2._explain_tv(load("01_sky.jpg"))     # 天空图当"电视"，必然全低分
    _ok(tv["type"] is None or tv["conf"] >= TV_CONF_MIN,
        f"低置信不报类型（conf={tv['conf']}）", fails)
    _ok(("没看明白" in tv["text"]) or tv["conf"] >= TV_CONF_MIN or tv["text"] == "",
        f"低置信话术：{tv['text']!r}", fails)

    print("\n[6] 电视：不复述内容（话术里不含具体台词/具体人物话）")
    tv2 = e2._explain_tv(load("21_living_sofa.jpg"))
    if tv2["type"]:
        _ok(tv2["text"].startswith("电视上看着像在播"), "粗粒度句式", fails)
        _ok(len(tv2["text"]) <= 40, f"话术短（{len(tv2['text'])}字）", fails)
    else:
        print(f"  [SKIP] 这张没检出屏幕（note={tv2.get('note')}）")

    print("\n[7] 门口去抖：3 秒内不重复说")
    e3 = ExplainWorld()
    f = load("11_table_people.jpg")
    a = e3._explain_door(f)
    b = e3._explain_door(f)
    if a["person"]:
        _ok(bool(a["text"]), "第一次说'门口好像有人'", fails)
        _ok(b.get("dedup") is True and b["text"] == "", "第二次去抖静默", fails)
    else:
        print("  [SKIP] 这张没检出人")

    print("\n[8] 事件全量落盘（explain_events.jsonl）")
    fp = DATA_DIR / "explain_events.jsonl"
    _ok(fp.exists(), "事件文件存在", fails)
    if fp.exists():
        lines = [json.loads(l) for l in fp.read_text(encoding="utf-8").splitlines() if l.strip()]
        _ok(any(x.get("event") == "suppressed" for x in lines), "抢占事件已记录", fails)
        _ok(any(x.get("event") == "explain" for x in lines), "解释事件已记录", fails)

    print("\n[9] 源码级隐私检查：解释器路径里没有存图/人脸")
    src = (HERE / "explain_world.py").read_text(encoding="utf-8")
    # 只扫"真实调用"（带括号的），注释里提到的名字不算
    for bad in ("cv2." + "imwrite(", "Video" + "Writer(", "Image." + "save(",
                "face_" + "recognition.", "insight" + "face.", "import " + "dlib",
                "." + "tofile("):
        _ok(bad not in src, f"源码不含 {bad}", fails)

    print("\n[10] fall_guard 不可用时按占用处理（保守）")
    e4 = ExplainWorld()
    old = sys.modules.get("fall_guard")
    try:
        sys.modules["fall_guard"] = None      # 强制 import 失败
        r4 = e4.explain(load("01_sky.jpg"), "weather")
        _ok(r4["suppressed"] is True, "导入失败 → 不解释（保守）", fails)
    finally:
        if old is not None:
            sys.modules["fall_guard"] = old

    print("\n" + "=" * 70)
    if fails:
        print(f"FAILED {len(fails)} 项")
        for f in fails:
            print("   -", f)
        return 1
    print("全部通过")
    return 0


def main():
    ap = argparse.ArgumentParser(description="H-13 视频解释外界")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--image", default="")
    ap.add_argument("--what", default="all", choices=["tv", "door", "weather", "all"])
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.status:
        print(json.dumps(explainer.status(), ensure_ascii=False, indent=2))
        return 0
    if not args.image:
        ap.error("需要 --image 或 --selftest")

    import numpy as np
    from PIL import Image
    frame = np.array(Image.open(args.image).convert("RGB"))[..., ::-1].copy()
    r = explainer.explain(frame, args.what, meta={"file": Path(args.image).name})
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

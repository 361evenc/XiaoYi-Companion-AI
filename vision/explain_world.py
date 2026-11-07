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
# ★★ 2026-10-28 撤回的一个"修复"，留档说明为什么不能再这么改
#
# 现象：同一台电视、同一屏幕位置，只换屏幕内容，YOLO 的 tv 置信度变化极大：
#     黑屏 0.92 / 纯白 0.92 / 新闻(主播脸) 0.81 / 动画 0.15~0.48 / 两台老电视 0.00
# 也就是说：**电视真在播节目的时候，YOLO 反而认不出这是电视。**
#
# 我先加的修法是：YOLO 没框到时，若 CLIP 对"电视节目"打分 ≥0.62 也算有电视。
# 结果 frames_real/no_tv（16 张真的没电视的客厅/卧室）**7 张误开口**，CLIP 分数
# 高达 0.987 / 0.973 / 0.971。原先"0.62 够用"的判断，是拿旧逻辑下被 YOLO 前置
# 过滤后剩下的分数估的——那些图根本没进过 CLIP，我拿一个空集去定阈值。
# ★ 教训：阈值必须用**真正会走进这条分支的样本**去对撞，不能拿被上游过滤掉的样本估。
#
# 结论：CLIP 分不清"房间里有一台电视在播节目"和"房间本身看起来像电视节目画面"。
# 它不是检测器，是风格/语义相似度。所以**只能**靠 YOLO 做存在性判断，
# 宁可漏（电视播动画时可能不开口），不可误报（对空客厅说在播综艺 = 编造）。
# 漏报是对不起用户（少解释一次），误报是骗用户（说了不存在的事）。红线选后者。
TV_STRONG_CONF = None    # 已废弃：留空以阻止有人再拿它绕开 YOLO 闸门
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

        ★ 前置检查①：CLIP 对**没有电视的**画面也一定能选出"最像的节目类型"。
        实测 frames/18_living_bright.jpg（明亮客厅、根本没开电视）被判成
        "综艺节目 0.785"——直接信就会对着空客厅说"电视上在播综艺"。

        ★ 实测发现②（2026-10-28）：**YOLO 用"屏幕里有没有人"判断电视。**
        同一台电视、同一屏幕位置，只换屏幕内容：
            黑屏 0.92 / 纯白 0.92 / 新闻(主播脸) 0.81 / 动画 0.15~0.48 / 老电视 0.00
        电视真在播节目时反而认不出。我试过"YOLO 没框到就让 CLIP 兜底"，
        结果 no_tv 组 16 张里 7 张误开口（CLIP 给空客厅打了 0.987）。
        **所以这里保留 YOLO 作为唯一的存在性判据，接受漏报。**
        理由见文件头 TV_STRONG_CONF 那段注释：漏报是对不起用户，误报是骗用户。
        """
        out = {"what": "tv", "type": None, "conf": 0.0, "text": "", "source": "clip"}
        SCREEN_CLASSES = ("tv", "monitor")
        tv_box = 0.0
        try:
            from describe_frame import load_model
            model = load_model()
            r = model(frame, conf=0.40, imgsz=640, verbose=False, device="cpu")[0]
            if r.boxes is not None:
                for i in range(len(r.boxes)):
                    c = r.names[int(r.boxes.cls[i])]
                    if c in SCREEN_CLASSES:
                        tv_box = max(tv_box, float(r.boxes.conf[i]))
            del r
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"
            return out
        out["tv_box_conf"] = round(tv_box, 3)

        if tv_box < 0.40:
            out["has_screen"] = False
            out["text"] = ""           # 没看到电视 → 不说电视的事
            out["note"] = f"画面里没找到电视（yolo_max={tv_box:.2f}）"
            return out

        out["has_screen"] = True
        out["screen_source"] = "yolo"

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


def batch(dir_path, expect_tv, what="tv", out_json=None):
    """批量验收：一个目录里的真实照片，逐张跑，出"该开口的开了多少 / 不该开口的开嘴几次"。

    expect_tv:
        True  —— 这个目录里**应该**有电视在播（frames_real/tv）
        False —— 这个目录里**没有**电视（frames_real/no_tv），一张都不许提"电视上在播"

    **为什么不判"节目类型对不对"**：
    这批样本里没有"这张是体育/新闻"的可靠标注（我自己看也算标注，但样本量小到
    做不出有意义的准确率）。所以这里只判**开不开口**这个二分类——它恰恰对应
    代码里真正的失败模式（对空客厅说在播综艺），而且是 0/1 可判、不掺主观。

    节目类型准确率要靠 tv_synth 合成组，且得人工过一遍标注，另立一项。
    """
    import numpy as np
    from PIL import Image

    d = Path(dir_path)
    files = sorted([p for p in d.glob("*")
                    if p.suffix.lower() in (".jpg", ".jpeg", ".png")]) if d.exists() else []
    tag = "应开口" if expect_tv else "不该开口"
    print("=" * 74)
    print(f"H-13 批量验收（{d.name}，{len(files)} 张，{tag}）")
    print("=" * 74)
    if not files:
        print("  [SKIP] 目录不存在或没有图")
        return {"skipped": True, "samples": 0}

    rows, opened, errors = [], 0, 0
    for p in files:
        frame = np.array(Image.open(p).convert("RGB"))[..., ::-1].copy()
        r = explainer.explain(frame, what, meta={"batch": d.name})
        items = {it.get("what"): it for it in r.get("items", [])}
        tv = items.get("tv") or {}
        text = tv.get("text") or ""
        has_screen = tv.get("has_screen")
        spoke = bool(text)
        if spoke:
            opened += 1
        if tv.get("error"):
            errors += 1
        rows.append({"file": p.name, "has_screen": has_screen, "spoke": spoke,
                     "type": tv.get("type"), "conf": tv.get("conf"), "text": text,
                     "tv_box_conf": tv.get("tv_box_conf"),
                     "screen_source": tv.get("screen_source"),
                     "top1": (tv.get("top3") or [{}])[0].get("type"),
                     "note": tv.get("note"), "error": tv.get("error")})
        flag = "嘴" if spoke else "—"
        verdict = ""
        if expect_tv and not spoke:
            verdict = "  (漏说)"
        if not expect_tv and spoke:
            verdict = "  !! 没电视却开了口"
        print(f"  [{flag}] 屏={str(has_screen):<5} yolo={str(tv.get('tv_box_conf')):<5} "
              f"clip={str(tv.get('conf')):<6} {p.name[:38]:<40}{verdict}")
        if spoke:
            print(f"        → {text}")

    n = len(files)
    print("-" * 74)
    if expect_tv:
        print(f"开口 {opened}/{n}（应尽量多；漏说 = 该解释时没解释，老人会问第二次）")
    else:
        print(f"开口 {opened}/{n}（要求 0 —— 没有电视时说'在播综艺'就是编造）")
    if errors:
        print(f"异常 {errors}/{n}")

    passed = (opened == 0) if not expect_tv else (opened >= n * 0.5)
    print("→", "通过" if passed else "未通过", f"（errors={errors}）")
    if out_json:
        Path(out_json).write_text(json.dumps(
            {"dir": str(d), "expect_tv": expect_tv, "samples": n, "spoke": opened,
             "errors": errors, "passed": passed, "rows": rows},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告：{out_json}")
    return {"skipped": False, "samples": n, "spoke": opened, "errors": errors,
            "passed": passed, "rows": rows}


def main():
    ap = argparse.ArgumentParser(description="H-13 视频解释外界")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--image", default="")
    ap.add_argument("--dir", default="", help="批量验收目录")
    ap.add_argument("--expect-tv", choices=["yes", "no"], default="yes",
                    help="该目录是否应该有电视（批量模式用）")
    ap.add_argument("--out", default="", help="批量模式的报告路径")
    ap.add_argument("--what", default="all", choices=["tv", "door", "weather", "all"])
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.status:
        print(json.dumps(explainer.status(), ensure_ascii=False, indent=2))
        return 0
    if args.dir:
        # 批量模式只跑 tv：door/weather 是别的项，混在一起会污染"该不该提电视"的判定
        r = batch(args.dir, args.expect_tv == "yes", what="tv", out_json=args.out or None)
        return 0 if (r.get("skipped") or r.get("passed")) else 1
    if not args.image:
        ap.error("需要 --image / --dir / --selftest")

    import numpy as np
    from PIL import Image
    frame = np.array(Image.open(args.image).convert("RGB"))[..., ::-1].copy()
    r = explainer.explain(frame, args.what, meta={"file": Path(args.image).name})
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

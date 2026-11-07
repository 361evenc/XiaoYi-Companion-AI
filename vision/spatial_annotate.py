"""H-3 语音空间标注 · 硬件侧（采集 + 检测 + 位置描述 + 读回确认 + 轨迹观测位）

**先说清楚这一卡为什么拆成两半，以及我做了哪一半。**

任务卡 H-3 的前置是 `H-2（帧源）+ B-2（图谱写入）`。B-2 至今**没有任何代码**
（`backend/` 里搜不到 spatial/graph 实现），意味着"写入空间图谱"这一半的
目标位置还不存在。硬写一个"图谱"到 vision/data 里，是绕过 B-2 另起炉灶——
将来 B-2 落地时两份数据必然打架。

所以本文件只做 **H-3 里属于硬件线的、不依赖 B-2 的那一半**：

    ① 抓帧        老人说"药箱在这儿" → 立刻抓一帧（不用等下一拍）
    ② 检测        本地 YOLO 找出画面里的物体
    ③ 位置描述    把"框在画面里的位置"翻成老人听得懂的话（"窗台那一带，偏左"）
    ④ 读回确认    生成问句「药箱在客厅窗台，对吧？」
    ⑤ 确认/否认   最多问 2 次；2 次都否认 → 说"这次我没看懂，您再跟我说一遍"
    ⑥ 写入意图    **返回一个 dict，不自己写图谱**（交给 B-2 的 /api/spatial）

**读回确认 100% 执行，是代码结构保证的**：`annotate()` 只返回"待确认"，
`confirm()` 是唯一能产出 `write_intent` 的函数。没有"直接写入"的路径——
这不是靠自觉遵守，是靠"根本没有这个函数"。

另外一个诚实的边界：**位置粒度到"家具 + 大方位"为止，不到"第几层抽屉"。**
YOLO 只给框，框在画面哪个位置不等于在房间哪个位置（摄像头视角决定一切）。
所以话术一律用"在…那一带""看着像在…上"，不用"就在…里"。测 10 件物品时，
"正确"判定也按这个粒度算——比这更细就是编。

用法：
    venv\\Scripts\\python.exe spatial_annotate.py --selftest
    venv\\Scripts\\python.exe spatial_annotate.py --image frames/20_kitchen_dining.jpg --object 药箱
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

MAX_CONFIRM = 2            # 任务卡：最多重标 2 次
COARSE_CONF = 0.35         # 标注场景用低阈值（老人已经说了东西在哪，宁可多框）

# 画面九宫格 → 老人听得懂的方位词
# 注意用词：不用"左上角"（老人对"角"的想象是房间角），用"偏左/中间/偏右"+"上/中/下"
H_AREA = {0: "偏左", 1: "中间", 2: "偏右"}
V_AREA = {0: "上半", 1: "中间", 2: "下半"}


def _now():
    return datetime.now().isoformat(timespec="seconds")


def area_word(bbox, shape):
    """框中心落在哪个格子 → 方位词。返回 (方位词, 格子坐标)。"""
    h, w = shape[:2]
    x1, y1, x2, y2 = bbox
    cx = (x1 + x2) / 2.0 / max(1, w)
    cy = (y1 + y2) / 2.0 / max(1, h)
    gi = 0 if cx < 1 / 3 else (1 if cx < 2 / 3 else 2)
    gj = 0 if cy < 1 / 3 else (1 if cy < 2 / 3 else 2)
    if gj == 1 and gi == 1:
        return "画面正当中", (gi, gj)
    if gj == 1:
        return f"画面{H_AREA[gi]}", (gi, gj)
    return f"画面{V_AREA[gj]}{H_AREA[gi]}", (gi, gj)


# ================================================================ 标注会话


class SpatialAnnotate:
    """一次标注 = 一个会话。会话内记住问了几次，不许无限循环。"""

    def __init__(self):
        self._session = None

    # ---------------- ① ~ ④ ----------------

    def annotate(self, frame, object_name, scene=None, model=None):
        """抓到的帧 + 老人说的物品名 → 待确认的描述（**不写任何存储**）。

        返回：
            {"ok", "object", "found", "where", "ask", "candidates", "attempt", "frame_ts"}
        """
        if not object_name or not object_name.strip():
            return {"ok": False, "reason": "empty_object"}

        object_name = object_name.strip()
        att = 1
        if self._session and self._session["object"] == object_name:
            att = self._session["attempt"] + 1
            if att > MAX_CONFIRM:
                # 任务卡话术：2 次仍不行就说这句，且**清会话**（别一直缠着老人）
                self._session = None
                return {"ok": False, "reason": "gave_up", "attempt": att,
                        "ask": "这次我没看懂，您再跟我说一遍。"}

        try:
            if model is None:
                from describe_frame import load_model
                model = load_model()
            r = model(frame, conf=COARSE_CONF, imgsz=640, verbose=False, device="cpu")[0]
            objects = []
            if r.boxes is not None:
                for i in range(len(r.boxes)):
                    label = r.names[int(r.boxes.cls[i])]
                    if label == "person":
                        continue          # 人不作为"物品位置"的锚点
                    objects.append({
                        "label": label,
                        "conf": round(float(r.boxes.conf[i]), 3),
                        "bbox": [round(float(v), 1) for v in r.boxes.xyxy[i].tolist()],
                    })
        except Exception as e:
            return {"ok": False, "reason": f"detect_failed: {type(e).__name__}"}

        # 选锚点：优先"家具"（桌子/柜子/椅子这类是位置的参照物），否则选最大的框
        anchors = [o for o in objects
                   if o["label"] in ("dining table", "chair", "couch", "bed", "bench",
                                     "refrigerator", "sink", "oven", "toaster")]
        pool = anchors or objects

        if pool:
            # 家具取面积最大的（老人说的"窗台/桌上"通常就是画面里最大的那件家具）
            best = max(pool, key=lambda o: (o["bbox"][2] - o["bbox"][0]) *
                       (o["bbox"][3] - o["bbox"][1]))
            where_word, grid = area_word(best["bbox"], frame.shape)
            anchor_cn = _cn_label(best["label"])
            where = f"{scene or '屋里'}{anchor_cn}那一片（{where_word}）"
        else:
            where_word, grid = "画面中间", (1, 1)
            anchor_cn = None
            where = "画面中间那一带"

        ask = f"{object_name}在{where}，对吧？"
        self._session = {"object": object_name, "attempt": att, "where": where,
                         "anchor": anchor_cn, "grid": list(grid), "ts": _now()}
        return {
            "ok": True,
            "object": object_name,
            "found": bool(pool),
            "where": where,
            "anchor": anchor_cn,
            "grid": list(grid),
            "ask": ask,
            "attempt": att,
            "candidates": objects[:5],
            "frame_ts": _now(),
        }

    # ---------------- ⑤ ~ ⑥ ----------------

    def confirm(self, accepted, object_name=None, scene=None):
        """老人确认/否认。**这是唯一能产出 write_intent 的地方。**

        返回 write_intent 的字段按 B-2 的 /api/spatial 需要来定；
        如果 B-2 最终字段不同，改这一个函数的输出即可，采集侧不用动。
        """
        if not self._session:
            return {"ok": False, "reason": "no_pending"}

        s = self._session
        if not accepted:
            # 否认：保留会话以支持重标（attempt 已由 annotate 自增）
            return {"ok": True, "accepted": False, "attempt": s["attempt"],
                    "ask_again": "那我再看看，您再指一下？" if s["attempt"] < MAX_CONFIRM
                    else "这次我没看懂，您再跟我说一遍。"}

        # ★ 确认后才产生写入意图。这里 **只返回，不落盘**
        intent = {
            "action": "upsert_object_location",
            "object": object_name or s["object"],
            "scene": scene,
            "where": s["where"],
            "anchor": s["anchor"],
            "grid": s["grid"],
            "source": "voice_annotate",      # 与 H-2 的持续感知来源区分（T-2 溯源要求）
            "confidence": 0.9,               # 老人亲口确认 → 高置信
            "confirmed_at": _now(),
            "confirmed_by": "elder_voice",
        }
        self._session = None
        return {"ok": True, "accepted": True, "write_intent": intent,
                "reply": f"好，我记住了：{intent['object']}在{s['where']}。"}

    def pending(self):
        return dict(self._session) if self._session else None


def _cn_label(label):
    """COCO 类名 → 老人听得懂的词（只覆盖会当锚点的几类）"""
    m = {"dining table": "桌子", "chair": "椅子", "couch": "沙发", "bed": "床",
         "bench": "长凳", "refrigerator": "冰箱", "sink": "水池", "oven": "烤箱",
         "toaster": "烤面包机", "potted plant": "花盆", "vase": "花瓶"}
    return m.get(label, label)


# ================================================================ 轨迹观测位（模拟 spike 的③）

class Trajectory:
    """在"标注点"处连续采几拍，判断该处有没有动过（被拿走 / 没动）。

    为什么需要它：任务卡 spike 第①项（动作识别 ≥70%）在 H-2 的持续录制里
    缺"放置/拿走"的判定能力。这块本质是轨迹分析（同一个像素区有没有发生变化），
    不需要新模型。这里做成一个**独立的观测位**，不碰 H-2 的录制循环。

    ★ 这不等于 spike 做完了。spike 要的是"50 个真实事件样本"，
      我这里只能给出"判定器"，样本和标注还得真人拍。
    """

    def __init__(self, tol=0.02):
        self.tol = tol
        self.states = {}          # key -> {"seen": int, "last": float, "stat": "present|absent"}

    def observe(self, key, present):
        """每次观测一个标注点：present = 这一拍还在原位吗。"""
        st = self.states.setdefault(key, {"seen": 0, "last": None, "stat": None,
                                          "changes": []})
        st["seen"] += 1
        prev = st["stat"]
        st["stat"] = "present" if present else "absent"
        if prev is not None and prev != st["stat"]:
            st["changes"].append({"at": _now(), "from": prev, "to": st["stat"]})
        st["last"] = _now()
        return dict(st)

    def detect_events(self, key):
        """从变化记录里推"拿走 / 放回"事件（present→absent = 拿走）。"""
        st = self.states.get(key) or {}
        evs = []
        for c in st.get("changes", []):
            evs.append({"type": "taken" if c["to"] == "absent" else "placed",
                        "at": c["at"]})
        return evs


# ================================================================ 自测


def _ok(cond, name, fails, detail=""):
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (f"  | {detail}" if detail else ""))
    if not cond:
        fails.append(name)


def selftest():
    fails = []
    print("=" * 72)
    print("H-3 语音空间标注（硬件侧）自测")
    print("=" * 72)

    import numpy as np
    from PIL import Image

    def load(name):
        p = HERE / "frames" / name
        if not p.exists():
            return None
        return np.array(Image.open(p).convert("RGB"))[..., ::-1].copy()

    print("\n[1] 读回确认 100% 执行：annotate 不返回写入，只有 confirm 能产出")
    sa = SpatialAnnotate()
    frame = load("20_kitchen_dining.jpg")
    r = sa.annotate(frame, "药箱", scene="厨房")
    _ok(r["ok"] is True, "annotate 成功", fails)
    _ok("write_intent" not in r, "annotate 结果里没有 write_intent（不许直接写）", fails)
    _ok(r["ask"].endswith("对吧？"), f"是问句：{r['ask']}", fails)
    _ok("药箱" in r["ask"], "问句含物品名", fails)

    print("\n[2] 否认 → 再问一次（attempt=2）")
    c1 = sa.confirm(False)
    _ok(c1["accepted"] is False, "否认不产生写入", fails)
    _ok("write_intent" not in c1, "否认结果无 write_intent", fails)
    r2 = sa.annotate(frame, "药箱", scene="厨房")
    _ok(r2["attempt"] == 2, f"第二次标注 attempt=2（实际 {r2.get('attempt')}）", fails)

    print("\n[3] 第 3 次 → 放弃，用任务卡话术")
    r3 = sa.annotate(frame, "药箱", scene="厨房")
    _ok(r3["ok"] is False and r3["reason"] == "gave_up", "超过 2 次放弃", fails)
    _ok("再跟我说一遍" in r3["ask"], f"放弃话术：{r3['ask']}", fails)

    print("\n[4] 确认 → 产出写入意图（source=voice_annotate）")
    sa2 = SpatialAnnotate()
    r4 = sa2.annotate(frame, "遥控器", scene="厨房")
    ok4 = sa2.confirm(True)
    _ok(ok4["accepted"] is True, "确认成功", fails)
    wi = ok4.get("write_intent") or {}
    _ok(wi.get("action") == "upsert_object_location", "写入意图动作", fails)
    _ok(wi.get("source") == "voice_annotate", "source 与持续感知区分", fails)
    _ok(wi.get("confidence") == 0.9, "老人确认 → 高置信", fails)
    _ok(wi.get("object") == "遥控器", "物品名正确", fails)
    _ok(sa2.pending() is None, "确认后会话清空", fails)

    print("\n[5] 方位词：九宫格映射正确、无'左上角'这种词")
    shape = (720, 1280, 3)
    cases = [
        ([100, 100, 300, 300], "画面上半偏左"),
        ([500, 300, 800, 450], "画面正当中"),
        ([1000, 500, 1250, 700], "画面下半偏右"),
    ]
    for box, want in cases:
        got, _g = area_word(box, shape)
        _ok(got == want, f"{box} → {got}", fails)
        _ok("角" not in got, f"不用'角'字：{got}", fails)

    print("\n[6] 空物品名 / 无检测结果 → 不崩、不乱猜")
    _ok(sa2.annotate(frame, "  ")["ok"] is False, "空物品名拒绝", fails)
    blank = np.full((480, 640, 3), 245, dtype=np.uint8)
    r6 = sa2.annotate(blank, "老花镜", scene="卧室")
    _ok(r6["ok"] is True, "空白画面也能走（不崩）", fails)
    _ok(r6["found"] is False, "found=False", fails)
    _ok("中间" in r6["where"], f"无锚点时退化为画面中间：{r6['where']}", fails)

    print("\n[7] 轨迹观测位：拿走 / 放回识别")
    tr = Trajectory()
    for _ in range(3):
        tr.observe("眼镜@床头", True)
    tr.observe("眼镜@床头", False)
    tr.observe("眼镜@床头", False)
    tr.observe("眼镜@床头", True)
    evs = tr.detect_events("眼镜@床头")
    kinds = [e["type"] for e in evs]
    _ok(kinds == ["taken", "placed"], f"事件序列：{kinds}", fails)

    print("\n[8] 源码检查：没有往图谱/记忆箱直接写的路径")
    src = (HERE / "spatial_annotate.py").read_text(encoding="utf-8")
    for bad in ("world_" + "perception.json", "memory_" + "sync", "append_" + "jsonl("):
        _ok(bad not in src, f"源码不含 {bad}", fails)

    print("\n" + "=" * 72)
    if fails:
        print(f"FAILED {len(fails)} 项")
        for f in fails:
            print("   -", f)
        return 1
    print("全部通过")
    return 0


def main():
    ap = argparse.ArgumentParser(description="H-3 语音空间标注（硬件侧）")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--image", default="")
    ap.add_argument("--object", default="药箱")
    ap.add_argument("--scene", default="")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.image:
        ap.error("需要 --image 或 --selftest")

    import numpy as np
    from PIL import Image
    frame = np.array(Image.open(args.image).convert("RGB"))[..., ::-1].copy()
    sa = SpatialAnnotate()
    r = sa.annotate(frame, args.object, scene=(args.scene or None))
    print(json.dumps(r, ensure_ascii=False, indent=2))
    print("\n（这一步只生成待确认的描述，没有写任何存储）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

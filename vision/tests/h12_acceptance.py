"""H-12 验收脚本：三条数据流边界 + 置信度阈值 + 话术/语速映射

跑法（在 vision/ 目录）：
    venv\\Scripts\\python.exe tests\\h12_acceptance.py

验收项（对照任务卡 H-12）：
  A. 原始帧零落盘 + 图谱/记忆箱零写入（源码静态扫描）
  B. confidence < 0.7 的字段零写入（注入假模型，精确测 0.69 / 0.70 边界）
  C. 运行时只在 data/user_profile.jsonl 追加
  D. 识别结果 -> 话术方向 / 语速

为什么要假模型：真模型跑出来只有一个置信度组合，测不到 0.69 与 0.70
这条边界。假模型能精确构造任意置信度，边界才真的被验过。
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402

PASS, FAIL = "[PASS]", "[FAIL]"
failures = []


def check(name, ok, detail=""):
    print(f"{PASS if ok else FAIL} {name}" + (f" | {detail}" if detail else ""))
    if not ok:
        failures.append(name)
    return ok


# ---------------- A. 零落盘 / 零写图谱 ----------------

FORBIDDEN_PATTERNS = [
    r"cv2\.imwrite", r"cv2\.VideoWriter", r"\.tofile\(",
    r"\.save\(\s*['\"].*\.(jpg|jpeg|png)", r"requests\.post", r"urlopen",
    r"world_perception\.json", r"perception_summary\.json",
    r"memory_bank", r"space_graph", r"knowledge_graph",
]


def check_no_persist_chain():
    print("\n--- A. 帧零落盘 + 图谱/记忆箱零写入（源码扫描）---")
    text = (HERE / "call_vision.py").read_text(encoding="utf-8", errors="replace")
    for pat in FORBIDDEN_PATTERNS:
        hit = re.search(pat, text)
        check(f"无 {pat}", hit is None, hit.group(0) if hit else "")
    check("有『帧用完即弃』的处理", "del frame" in text or "用完即弃" in text)
    files = set(re.findall(r"^([A-Z_]+_FILE)\s*=", text, flags=re.M))
    check("只定义画像一个数据文件", files == {"PROFILE_FILE"}, str(files))


# ---------------- 假模型：可注入任意置信度 ----------------

class _XY:
    def __init__(self, box):
        self._b = box

    def tolist(self):
        return self._b


class _XYXY:
    def __init__(self, boxes):
        self._boxes = boxes

    def __len__(self):
        return len(self._boxes)

    def __getitem__(self, i):
        return _XY(self._boxes[i])


class _Boxes:
    """贴近 ultralytics：cls/conf 可下标，xyxy 可下标且元素有 tolist()。"""
    def __init__(self, items, label_id):
        self._items = items
        self.cls = [label_id[it[0]] for it in items]
        self.conf = [it[1] for it in items]
        self.xyxy = _XYXY([it[2] for it in items])

    def __len__(self):
        return len(self._items)


def make_fake_model(items):
    """items: [(label, conf, [x1,y1,x2,y2])] -> 可被 model(frame, ...) 调用的假模型"""
    label_id = {"person": 0, "cup": 1, "book": 2, "chair": 3}
    names = {v: k for k, v in label_id.items()}

    class _Model:
        def __call__(self, frame, **kw):
            return [type("R", (), {"names": names,
                                   "boxes": _Boxes(items, label_id)})()]

    return _Model()


# ---------------- B. 阈值边界 ----------------

def check_threshold_boundary():
    print("\n--- B. confidence < 0.7 的字段零写入 ---")
    from call_vision import CallVision, PROFILE_FILE

    if PROFILE_FILE.exists():
        PROFILE_FILE.unlink()

    frame = np.full((720, 1280, 3), 130, dtype=np.uint8)

    # 场景 1：person 0.80（够写） + cup 0.69（不够） + book 0.70（刚好够）
    out = CallVision(model=make_fake_model([
        ("person", 0.80, [400, 100, 880, 700]),
        ("cup", 0.69, [420, 300, 520, 400]),
        ("book", 0.70, [600, 320, 720, 420]),
    ])).observe(frame, "t_th")
    rec = out["record"]
    check("person 0.80 -> person_present=True", rec.get("person_present") is True, str(rec))
    check("手持物品取最高置信（book 0.70 胜 cup 0.69）",
          rec.get("hand_object") == "book", str(rec.get("hand_object")))
    check("0.69 的 cup 完全没进画像",
          "cup" not in json.dumps(rec, ensure_ascii=False), str(rec))

    # 场景 2：person 只有 0.65 -> 不能据此给"在看"结论
    out2 = CallVision(model=make_fake_model([
        ("person", 0.65, [400, 100, 880, 700]),
    ])).observe(frame, "t_th_2")
    r2 = out2["record"]
    check("person 0.65 -> 不给 gaze 结论",
          "gaze" not in r2 or r2.get("gaze_confidence", 0) >= 0.7, str(r2))

    # 场景 3：无人 -> 确定地写 False，但绝不凭空造 emotion/hand_object
    r3 = CallVision(model=make_fake_model([])).observe(frame, "t_th_3")["record"]
    check("无人 -> person_present=False", r3.get("person_present") is False, str(r3))
    check("无人 -> 不写 emotion", "emotion" not in r3, str(r3))
    check("无人 -> 不写 hand_object", "hand_object" not in r3, str(r3))

    lines = [json.loads(x) for x in PROFILE_FILE.read_text(encoding="utf-8").splitlines() if x.strip()]
    check("画像共 3 条", len(lines) == 3, f"got {len(lines)}")
    bad = [(k, r[k]) for r in lines
           for k in ("gaze_confidence", "emotion_confidence", "object_confidence")
           if k in r and r[k] < 0.7]
    check("所有 *_confidence 字段 >= 0.7", not bad, str(bad))
    check("画像条目无帧/图像字段",
          all(not (set(r) & {"image", "frame", "jpg", "video", "img", "raw"}) for r in lines))


# ---------------- C. 只在画像里追加 ----------------

def check_no_side_files():
    print("\n--- C. 运行时只在 data/user_profile.jsonl 追加 ---")
    from call_vision import CallVision
    data = HERE / "data"
    before = {p.name for p in data.glob("*")} if data.exists() else set()
    frame = np.full((480, 640, 3), 120, dtype=np.uint8)
    CallVision(model=make_fake_model([("person", 0.9, [100, 50, 500, 450])])).observe(
        frame, "t_side")
    after = {p.name for p in data.glob("*")} if data.exists() else set()
    check("只新增 user_profile.jsonl", (after - before) <= {"user_profile.jsonl"},
          f"新增 {after - before}")


# ---------------- D. 话术 / 语速 ----------------

def check_hints_and_speed():
    print("\n--- D. 识别结果 -> 话术 / 语速 ---")
    from call_vision import CallVision, EMOTION_SPEED, HINTS
    frame = np.full((720, 1280, 3), 130, dtype=np.uint8)

    cv = CallVision(model=make_fake_model([("person", 0.9, [400, 100, 880, 700])]))
    out1 = cv.observe(frame, "t_a")
    check("人在且正对 -> 不报 person_left", "person_left" not in out1["hints"],
          str(out1["hints"]))
    cv._model = make_fake_model([])
    out2 = cv.observe(frame, "t_a")
    check("人离开 -> person_left 提示", "person_left" in out2["hints"], str(out2["hints"]))
    check("person_left 有对应话术方向", "person_left" in HINTS)

    out3 = CallVision(model=make_fake_model([
        ("person", 0.9, [400, 100, 880, 700]),
        ("book", 0.85, [600, 320, 720, 420]),
    ])).observe(frame, "t_b")
    check("手持物品 -> hand_object 提示", "hand_object" in out3["hints"], str(out3["hints"]))

    check("语速映射有序 low<neutral<confused<happy",
          EMOTION_SPEED["low"] < EMOTION_SPEED["neutral"]
          < EMOTION_SPEED["confused"] < EMOTION_SPEED["happy"], str(EMOTION_SPEED))
    check("默认语速沿用现有 0.8", EMOTION_SPEED["neutral"] == 0.8)


def main():
    check_no_persist_chain()
    check_threshold_boundary()
    check_no_side_files()
    check_hints_and_speed()

    print("\n" + "=" * 50)
    if failures:
        print(f"{FAIL} 未通过 {len(failures)} 项：{failures}")
        return 1
    print(f"{PASS} H-12 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

r"""②-C：结构化帧描述 —— 帧进，一条 world_perception JSON 出

这是「world_perception.json 的字段长什么样」的正式定版。
和 ① 的帧源、② 的检测拼成一条链：

    FrameSource.read()  →  YOLO(frame)  →  describe_frame()  →  JSON

用法：
    cd D:\xiaoyi_vision
    venv\Scripts\python.exe describe_frame.py --image frames\11_table_people.jpg
    venv\Scripts\python.exe describe_frame.py --all            # 跑全图集，逐条打印
    venv\Scripts\python.exe describe_frame.py --all --json out\frames_desc.json

设计决定（依据 ②-B 实测，别改）：
    1. scene 不由 YOLO 产出 —— COCO 没有天空/街道/草地，一律取 meta.scene
    2. 27 个细类对老人没意义 —— 归并成 7 组（人/植物/餐具/家具/动物/家电/其他）
    3. conf<0.40 的框丢弃 —— 滤掉"盆栽被当碗"这类低置信误检
    4. 检出为空是正常结果 —— summary 写"没看到明显的东西"，不许崩
"""

import argparse
import json
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(HERE))
from frame_source import FrameSource  # 复用 ① 的帧源（含 letterbox 和 meta）

MODEL_PATH = HERE / "models" / "yolov8n.pt"
OUT_DIR = HERE / "out"
META_FILE = HERE / "frames" / "meta.json"


def load_model(path=None):
    """加载 YOLO，权重缺失时立刻报错退出，不做静默联网下载。

    为什么要有这个函数：ultralytics 在权重不存在时会自动去 github.com 拉取。
    在 github.com 不通的网络里（国内常见），它会静默挂起数分钟且无任何提示，
    看起来像"程序卡死"。所以这里先检查文件，缺失就直接告诉人怎么补。

    权重获取方式（任选其一）：
      1. 已有 v8n 权重：放到 vision/models/yolov8n.pt
      2. 能联网：python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"
         然后把下载到的 yolov8n.pt 移进 vision/models/
      3. 换源：从镜像站下载 yolov8n.pt 后同上
    """
    target = Path(path) if path else MODEL_PATH
    if not target.exists():
        sys.stderr.write(
            f"\n[ERROR] 找不到模型权重：{target}\n"
            "\n  这不是代码问题，是缺文件。ultralytics 默认会自动下载，\n"
            "  但在 github.com 不通的网络里会静默挂起，所以这里直接停住。\n"
            "\n  补法（任选其一）：\n"
            "    1) 已有 yolov8n.pt → 复制到 "
            f"{MODEL_PATH.parent}\\\n"
            "    2) 能联网 → pip install ultralytics && python -c "
            "\"from ultralytics import YOLO; YOLO('yolov8n.pt')\"\n"
            "       然后把下载到的 yolov8n.pt 移进 models/\n"
            "    3) 用镜像站下 yolov8n.pt，同样放进 models/\n"
            "\n  文件约 6.2MB，只需放一次。\n\n"
        )
        sys.exit(1)

    from ultralytics import YOLO
    return YOLO(str(target))

# ---- 27 细类 → 7 大组（依据 ②-B 实测命中清单）----
GROUP_MAP = {
    "person": "人",
    "potted plant": "植物", "vase": "植物",
    "bowl": "餐具", "cup": "餐具", "fork": "餐具", "knife": "餐具",
    "spoon": "餐具", "wine glass": "餐具", "bottle": "餐具",
    "chair": "家具", "couch": "家具", "bed": "家具", "bench": "家具",
    "dining table": "家具",
    "cat": "动物", "dog": "动物", "bird": "动物",
    "tv": "家电", "laptop": "家电", "mouse": "家电", "remote": "家电",
    "keyboard": "家电", "cell phone": "家电", "microwave": "家电",
    "oven": "家电", "toaster": "家电", "sink": "家电", "refrigerator": "家电",
    "clock": "家电",
}
GROUP_ORDER = ["人", "动物", "植物", "餐具", "家具", "家电", "其他"]

CONF_MIN = 0.40          # 低于此置信度的框丢弃（依据 ②-B 误检分析）
MAX_OBJECTS_OUT = 8      # 摘要句里最多报这么多个物体，防"38 个目标"刷屏

# ---- 摘要句模板（口语，给老人听）----
SCENE_CN = {
    "outdoor_sky": "窗外", "street": "街上", "park": "公园", "garden": "花园",
    "meal": "饭桌", "dining": "餐厅", "pet": "宠物", "bedroom": "卧室",
    "indoor_dim": "昏暗的房间", "living_room": "客厅", "indoor_plant": "屋里",
    "kitchen": "厨房",
}
WEATHER_CN = {"sunny": "晴", "cloudy": "多云", "rain": "雨", "clear": "晴"}


def build_summary(scene, weather, groups_counter):
    """摘要句。注意：真机摄像头给不出 scene/weather（都是 unknown），
    这时必须退化成"画面里…"，不能输出 "unknown看到人" 这种东西。"""
    known_scene = scene in SCENE_CN
    scene_cn = SCENE_CN.get(scene, "")
    parts = []
    for g in GROUP_ORDER:
        if g in groups_counter:
            n = groups_counter[g]
            parts.append(g if n == 1 else f"{n}处{g}")

    if not parts:
        return f"{scene_cn}没看到什么明显的东西" if known_scene else "画面里没看到什么明显的东西"

    if known_scene:
        head = f"{scene_cn}看到{parts[0]}"
    else:
        head = f"画面里看到{parts[0]}"
    if len(parts) > 1:
        head += "，还有" + "、".join(parts[1:])

    # 只有"确定的户外场景 + 已知天气"才补天气，避免 unknown 露出来
    if known_scene and scene in ("outdoor_sky", "street", "park", "garden") and weather in WEATHER_CN:
        head += f"（{WEATHER_CN[weather]}）"
    return head


def describe_frame(frame, meta, model, conf_min=CONF_MIN):
    """frame: BGR ndarray；meta: 帧源标签。返回 dict —— world_perception 单条"""
    t0 = time.perf_counter()
    r = model(frame, conf=conf_min, imgsz=640, verbose=False, device="cpu")[0]
    infer_ms = round(1000 * (time.perf_counter() - t0), 1)

    objects = []
    if r.boxes is not None:
        for i in range(len(r.boxes)):
            label = r.names[int(r.boxes.cls[i])]
            conf = round(float(r.boxes.conf[i]), 3)
            objects.append({
                "group": GROUP_MAP.get(label, "其他"),
                "label": label,
                "conf": conf,
                "bbox": [round(float(v), 1) for v in r.boxes.xyxy[i].tolist()],
            })
        objects.sort(key=lambda d: -d["conf"])
        objects = objects[:MAX_OBJECTS_OUT]

    groups_counter = Counter(o["group"] for o in objects)
    return {
        "ts": meta["ts"],
        "frame_id": meta["frame_id"],
        "scene": meta["scene"],          # 不由 YOLO 产出，取 meta
        "weather": meta["weather"],
        "slot": meta["slot"],
        "objects": objects,
        "summary": build_summary(meta["scene"], meta["weather"], groups_counter),
        "infer_ms": infer_ms,
        "source": "sim",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="", help="只跑这一张（如 frames\\09_meal.jpg）")
    ap.add_argument("--all", action="store_true", help="跑全图集")
    ap.add_argument("--json", default="", help="把结果数组写到这个文件")
    args = ap.parse_args()

    model = load_model()

    if not args.all and not args.image:
        args.image = str(HERE / "frames" / "11_table_people.jpg")

    records = []

    if args.image:
        p = Path(args.image)
        meta_map = json.loads(META_FILE.read_text(encoding="utf-8")) if META_FILE.exists() else {}
        rgb = np.asarray(Image.open(p).convert("RGB"), dtype=np.uint8)
        frame = rgb[:, :, ::-1].copy()
        m = meta_map.get(p.name, {})
        meta = {"ts": datetime.now().isoformat(timespec="seconds"),
                "frame_id": 1, "scene": m.get("scene", "unknown"),
                "weather": m.get("weather", "sunny"),
                "slot": "noon", "path": str(p)}
        rec = describe_frame(frame, meta, model)
        records.append(rec)
    else:
        src = FrameSource(str(HERE / "frames"), interval=0.0)
        n = len(src.files)
        for i in range(n):
            ok, frame, meta = src.read()
            if not ok:
                continue
            records.append(describe_frame(frame, meta, model))

    for rec in records:
        print(json.dumps(rec, ensure_ascii=False))

    print("\n" + "=" * 62)
    for rec in records:
        name = Path(args.image).name if args.image else f"帧{rec['frame_id']}"
        print(f"  [{name}] {rec['summary']}")
        print(f"      objects={len(rec['objects'])}  infer={rec['infer_ms']}ms")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        print(f"\n[OK] 已写入 {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

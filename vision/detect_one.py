r"""②-A：单张图跑 YOLOv8n，看它到底能认出什么

用法：
    cd D:\xiaoyi_vision
    venv\Scripts\python.exe detect_one.py --image frames\09_meal.jpg
    venv\Scripts\python.exe detect_one.py --image frames\07_potted_plant.jpg --save

说明：
    - 纯 CPU 推理，不需要显卡
    - 模型权重放 models\yolov8n.pt；没有的话会尝试自动下载到当前目录
    - --save 会把画好框的图存到 out\ 下，答辩可以直接贴
"""

import argparse
import os
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODEL_DIR = HERE / "models"
OUT_DIR = HERE / "out"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default=str(HERE / "frames" / "09_meal.jpg"))
    ap.add_argument("--model", default=str(MODEL_DIR / "yolov8n.pt"))
    ap.add_argument("--conf", type=float, default=0.25, help="置信度阈值")
    ap.add_argument("--imgsz", type=int, default=640, help="推理时的输入尺寸")
    ap.add_argument("--save", action="store_true", help="保存画框图到 out\\")
    args = ap.parse_args()

    from ultralytics import YOLO  # 延迟导入，import 失败时报错更清楚

    img_path = Path(args.image)
    if not img_path.exists():
        print(f"[X] 图片不存在：{img_path}")
        return 1

    # 优先用本地权重，避免每次联网
    if os.path.exists(args.model):
        model_ref = args.model
        print(f"[OK] 使用本地权重：{model_ref}")
    else:
        model_ref = "yolov8n.pt"
        print(f"[WARN] 本地没有 {args.model}，尝试自动下载 yolov8n.pt ...")

    t_load = time.perf_counter()
    model = YOLO(model_ref)
    load_ms = (time.perf_counter() - t_load) * 1000

    t0 = time.perf_counter()
    result = model(str(img_path), conf=args.conf, imgsz=args.imgsz, verbose=False, device="cpu")[0]
    ms = (time.perf_counter() - t0) * 1000

    boxes = result.boxes
    names = result.names
    n = 0 if boxes is None else len(boxes)

    print("=" * 60)
    print(f"图片      : {img_path.name}")
    print(f"原图尺寸  : {result.orig_shape}")
    print(f"推理尺寸  : {args.imgsz}px | 置信度阈值 {args.conf}")
    print(f"模型加载  : {load_ms:.0f} ms（只算一次，实际管线里不重复）")
    print(f"推理耗时  : {ms:.0f} ms")
    print(f"检出      : {n} 个目标")
    print("=" * 60)

    if n == 0:
        print("（没有检出任何目标 —— 这是正常结果，不是错误）")
    else:
        for i in range(n):
            cls_id = int(boxes.cls[i])
            conf = float(boxes.conf[i])
            xyxy = [round(float(v), 1) for v in boxes.xyxy[i].tolist()]
            print(f"  {i + 1:2d}. {names[cls_id]:<16s} conf={conf:.2f}  bbox={xyxy}")

    # 类别统计（给 ②-B 映射表用）
    if n:
        from collections import Counter
        counter = Counter(names[int(c)] for c in boxes.cls)
        print("  类别统计: " + ", ".join(f"{k}×{v}" for k, v in counter.most_common()))

    if args.save:
        OUT_DIR.mkdir(exist_ok=True)
        out_file = OUT_DIR / f"{img_path.stem}_det.jpg"
        result.save(filename=str(out_file))
        print(f"\n[OK] 画框图已保存：{out_file}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

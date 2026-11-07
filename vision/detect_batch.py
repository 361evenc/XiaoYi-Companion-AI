r"""②-B 数据来源：一次加载模型，跑完整个图集，统计真实出现的 COCO 类别

用法：
    cd D:\xiaoyi_vision
    venv\Scripts\python.exe detect_batch.py
    venv\Scripts\python.exe detect_batch.py --conf 0.25 --save

产出：
    控制台：逐图检出 + 全图集类别统计 + 平均推理耗时
    out\batch_result.json：结构化结果，供 ②-C 直接复用
"""

import argparse
import json
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODEL_DIR = HERE / "models"
OUT_DIR = HERE / "out"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(HERE / "frames"))
    ap.add_argument("--model", default=str(MODEL_DIR / "yolov8n.pt"))
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    from ultralytics import YOLO

    frames = sorted(p for p in Path(args.dir).iterdir()
                    if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    if not frames:
        print(f"[X] 没有图片：{args.dir}")
        return 1

    print(f"图集 {len(frames)} 张 | conf={args.conf} | imgsz={args.imgsz}")
    t_load = time.perf_counter()
    model = YOLO(args.model)
    print(f"模型加载 {1000 * (time.perf_counter() - t_load):.0f} ms（只算一次）\n")

    rows, per_image_time = [], []
    for p in frames:
        t0 = time.perf_counter()
        r = model(str(p), conf=args.conf, imgsz=args.imgsz, verbose=False, device="cpu")[0]
        ms = 1000 * (time.perf_counter() - t0)
        per_image_time.append(ms)

        items = []
        if r.boxes is not None:
            for i in range(len(r.boxes)):
                items.append({
                    "label": r.names[int(r.boxes.cls[i])],
                    "conf": round(float(r.boxes.conf[i]), 3),
                    "bbox": [round(float(v), 1) for v in r.boxes.xyxy[i].tolist()],
                })
        items.sort(key=lambda d: -d["conf"])
        rows.append({"file": p.name, "orig_shape": list(r.orig_shape),
                     "infer_ms": round(ms, 1), "objects": items})

        tag = "  ".join(f"{o['label']}({o['conf']:.2f})" for o in items) or "（空）"
        print(f"  {p.name:<26s} {len(items):2d} 个  {ms:5.0f} ms  {tag}")

        if args.save:
            OUT_DIR.mkdir(exist_ok=True)
            r.save(filename=str(OUT_DIR / f"{p.stem}_det.jpg"))

    total = Counter()
    empty = []
    for row in rows:
        if not row["objects"]:
            empty.append(row["file"])
        for o in row["objects"]:
            total[o["label"]] += 1

    avg = sum(per_image_time) / len(per_image_time)
    print("\n" + "=" * 62)
    print(f"平均推理耗时 : {avg:.0f} ms（{1000 / avg:.1f} 帧/秒，纯 CPU）")
    print(f"检出为空的图 : {len(empty)} 张" + (f"  → {', '.join(empty)}" if empty else ""))
    print("全图集类别统计 :")
    if total:
        for name, cnt in total.most_common():
            print(f"    {name:<16s} × {cnt}")
    else:
        print("    （一个都没有）")

    OUT_DIR.mkdir(exist_ok=True)
    out_json = OUT_DIR / "batch_result.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"conf": args.conf, "imgsz": args.imgsz,
                   "avg_infer_ms": round(avg, 1), "images": rows},
                  f, ensure_ascii=False, indent=2)
    print(f"\n[OK] 结构化结果：{out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

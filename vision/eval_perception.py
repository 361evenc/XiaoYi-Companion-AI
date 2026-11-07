r"""⑦ 评测：拿标注当真值，算检测的召回率 / 精确率

用法：
    cd D:\xiaoyi_vision
    venv\Scripts\python.exe eval_perception.py
    venv\Scripts\python.exe eval_perception.py --conf 0.25     # 换阈值再比一版
    venv\Scripts\python.exe eval_perception.py --report out\评测报告.md

口径（先说清楚，免得被问住）：
    按**图**算，不按**框**算。
      召回 = 标注里有该组、且这张图被检出该组 的图数 ÷ 标注有该组的图数
      精确 = 检出该组的图里、标注也有的图数 ÷ 检出该组的图数
    为什么不按框算：标注只标了"有没有"，没标"几个、在哪"，按框算不出来，
    硬按框算就是编数字。

局限（写在报告里，不藏）：
    1. 标注是 AI 目视缩略图的预标，未人工复核 → 真值本身有误差
    2. 21 张，未达 30 张目标（Commons 接口 503，无法补图）
    3. 只评 7 个物体组，不评"场景/天气"（那是 ⑤ 的事）
"""

import argparse
import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ANNO_FILE = HERE / "annotations.json"
MODEL = HERE / "models" / "yolov8n.pt"
OUT_DIR = HERE / "out"

GROUP_ORDER = ["人", "动物", "植物", "餐具", "家具", "家电", "其他"]


def load_annotations():
    data = json.loads(ANNO_FILE.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def run_detect(conf):
    from ultralytics import YOLO
    from pathlib import Path as P
    import sys
    sys.path.insert(0, str(HERE))
    from describe_frame import describe_frame, GROUP_MAP

    model = YOLO(str(MODEL))
    frames_dir = HERE / "frames"
    meta_file = frames_dir / "meta.json"
    meta_map = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}

    import numpy as np
    from PIL import Image

    results = {}
    for p in sorted(frames_dir.iterdir()):
        if p.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            continue
        rgb = np.asarray(Image.open(p).convert("RGB"), dtype=np.uint8)
        frame = rgb[:, :, ::-1].copy()
        m = meta_map.get(p.name, {})
        meta = {"ts": "eval", "frame_id": 0, "scene": m.get("scene", "unknown"),
                "weather": m.get("weather", "unknown"), "slot": "eval", "path": str(p)}
        rec = describe_frame(frame, meta, model, conf_min=conf)
        results[p.name] = sorted({o["group"] for o in rec["objects"]})
    return results


def evaluate(pred, anno):
    STOI = []
    for g in GROUP_ORDER:
        got = {k for k, v in pred.items() if g in v}          # 检出的图
        truth = {k for k, v in anno.items() if g in v}        # 标注有的图
        tp = len(got & truth)
        recall = tp / len(truth) if truth else None
        prec = tp / len(got) if got else None
        STOI.append({
            "group": g,
            "truth_images": len(truth),
            "pred_images": len(got),
            "tp": tp,
            "recall": recall,
            "precision": prec,
            "missed": sorted(truth - got),
            "false_pos": sorted(got - truth),
        })
    return STOI


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conf", type=float, default=0.40)
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    anno = load_annotations()
    print(f"标注 {len(anno)} 张 | 阈值 conf={args.conf}")
    t0 = time.time()
    pred = run_detect(args.conf)
    print(f"检测完成，用时 {time.time() - t0:.1f}s\n")

    rows = evaluate(pred, anno)

    print(f"{'组':<6}{'标注图':>6}{'检出图':>6}{'命中':>6}{'召回':>8}{'精确':>8}")
    print("-" * 44)
    for r in rows:
        rc = f"{r['recall']:.0%}" if r["recall"] is not None else "—"
        pc = f"{r['precision']:.0%}" if r["precision"] is not None else "—"
        print(f"{r['group']:<6}{r['truth_images']:>6}{r['pred_images']:>6}{r['tp']:>6}{rc:>8}{pc:>8}")

    # 微平均（按图-组对）
    tot_truth = sum(r["truth_images"] for r in rows)
    tot_tp = sum(r["tp"] for r in rows)
    tot_pred = sum(r["pred_images"] for r in rows)
    macro_r = f"{tot_tp / tot_truth:.0%}" if tot_truth else "—"
    macro_p = f"{tot_tp / tot_pred:.0%}" if tot_pred else "—"
    print("-" * 44)
    print(f"{'合计':<6}{tot_truth:>6}{tot_pred:>6}{tot_tp:>6}{macro_r:>8}{macro_p:>8}")

    if args.report:
        out = Path(args.report)
        out.parent.mkdir(exist_ok=True)
        L = []
        L.append("# 视觉线 ⑦ 评测报告（第 1 版）")
        L.append("")
        L.append(f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
        L.append(f"- 图集：`frames/` **{len(anno)} 张**（目标 30 张，Commons 接口返回 503 未能补齐）")
        L.append(f"- 模型：YOLOv8n（COCO 预训练，纯 CPU）| 置信度阈值 **{args.conf}**")
        L.append(f"- 合计召回 **{macro_r}**（{tot_tp}/{tot_truth}）| 合计精确 **{macro_p}**（{tot_tp}/{tot_pred}）")
        L.append("")
        L.append("## 分组结果")
        L.append("")
        L.append("| 组 | 标注有该组的图 | 检出该组的图 | 命中 | 召回 | 精确 |")
        L.append("|---|---|---|---|---|---|")
        for r in rows:
            rc = f"{r['recall']:.0%}" if r["recall"] is not None else "—"
            pc = f"{r['precision']:.0%}" if r["precision"] is not None else "—"
            L.append(f"| {r['group']} | {r['truth_images']} | {r['pred_images']} | {r['tp']} | {rc} | {pc} |")
        L.append("")
        L.append("## 漏检与误检明细")
        L.append("")
        for r in rows:
            if r["missed"]:
                L.append(f"- **{r['group']} 漏检**：{', '.join(r['missed'])}")
            if r["false_pos"]:
                L.append(f"- **{r['group']} 误检**：{', '.join(r['false_pos'])}")
        L.append("")
        L.append("## 口径与局限（必须一起交给客户）")
        L.append("")
        L.append("**按图算，不按框算。** 标注只标了「这张图里有没有这一类」，没标个数和位置；")
        L.append("按框算召回率是算不出来的，硬算就是编数字。")
        L.append("")
        L.append("1. 标注为 **AI 目视缩略图的预标，未经人工复核** → 真值本身有误差，需复核后重跑。")
        L.append(f"2. 仅 **{len(anno)} 张**，未达 30 张目标（Commons 接口 503）。")
        L.append("3. 只评 7 个物体组。场景/天气不在本报告范围（见 ⑤ CLIP 的结论）。")
        L.append("4. 不可映射类别（天空/街道/草地）本就不在 COCO 里，未计入分母。")
        out.write_text("\n".join(L), encoding="utf-8")
        print(f"\n[OK] 报告已写入 {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

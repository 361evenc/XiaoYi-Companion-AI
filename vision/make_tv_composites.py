"""把真实节目帧"放进"真实房间照片的电视屏幕上，造 H-13 的有效样本。

**为什么必须合成 —— 因为我试过不合成。**
Wikimedia 上的"客厅+电视"生活照，绝大多数电视是**关着的**（黑屏）。
上一轮抓 11 张，只有 4 张电视亮着，且屏幕里是什么根本看不清；
再往前一轮，11 张里只有 4 张真有电视。数量、清晰度、内容真实性三者
在公开图片库里凑不齐 —— 这不是找法不对，是这类照片本身没人拍。

**合成到什么程度算诚实？**
  · 底图 = 真房间（Wikimedia CC，真实光照、真实电视边框）
  · 屏幕 = 真节目帧（Wikimedia PD 影片的代表帧）**且经过 YOLO 检测到的框**
  · 只做透视贴合 + 轻微亮度衰减（模拟屏幕发光），**不重绘任何东西**
  · 每条记录写入 meta.json：底图、源帧、授权、框坐标、贴合方式
照这个做，测得出"CLIP 认不认得出屏幕上是什么"，测不出"电视反光、摩尔纹、
拍摄噪点"。后者只能真机拍，交付说明里单列为待补。

**屏幕框从哪来**：YOLO 检出的 tv/monitor 框，四角不外扩。
不手工量——手工量容易把内容贴到电视外面，反而污染验收。

用法：
    python make_tv_composites.py --list    # 看能配多少组
    python make_tv_composites.py           # 生成到 frames_real/tv_synth/
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REAL = HERE / "frames_real"
OUT = REAL / "tv_synth"
META = OUT / "meta.json"


def _screen_boxes(img_path):
    """用 YOLO 找屏幕框，返回 [(x1,y1,x2,y2,conf)]，按面积降序。"""
    from describe_frame import load_model
    m = load_model()
    r = m(str(img_path), conf=0.30, imgsz=640, verbose=False, device="cpu")[0]
    out = []
    if r.boxes is None:
        return out
    for i in range(len(r.boxes)):
        cls = r.names[int(r.boxes.cls[i])]
        if cls in ("tv", "monitor"):
            x1, y1, x2, y2 = map(float, r.boxes.xyxy[i].tolist())
            out.append((x1, y1, x2, y2, float(r.boxes.conf[i])))
    del r
    out.sort(key=lambda b: -((b[2] - b[0]) * (b[3] - b[1])))
    return out


def paste_quad(base_path, content_path, quad, bright=0.88):
    """把 content 缩放到 quad 的包围盒后贴上去。

    ★ 故意**不做透视变换**：YOLO 框是轴对齐矩形，屏幕在照片里本来就
    基本正对镜头；强行透视反而会引入边框错位。贴准比贴花重要。
    """
    from PIL import Image, ImageEnhance
    base = Image.open(base_path).convert("RGB")
    content = Image.open(content_path).convert("RGB")
    x1, y1, x2, y2 = [int(round(v)) for v in quad]
    w, h = max(2, x2 - x1), max(2, y2 - y1)

    # 屏幕比例和节目帧比例不一致时，按"适应+少量裁切"处理，别拉伸变形
    # （拉伸会让画面里的人变胖变矮，CLIP 可能因此判错，那是合成本身引入的偏差）
    cr = content.width / content.height
    br = w / h
    if cr > br:          # 内容更宽 → 按高缩放，左右裁
        nh = h
        nw = int(h * cr)
        content = content.resize((nw, nh), Image.LANCZOS)
        left = (nw - w) // 2
        content = content.crop((left, 0, left + w, nh))
    else:                # 内容更高 → 按宽缩放，上下裁
        nw = w
        nh = int(w / cr)
        content = content.resize((nw, nh), Image.LANCZOS)
        top = (nh - h) // 2
        content = content.crop((0, top, nw, top + h))

    # 屏幕是自发光的，比环境亮一点；压到 0.88 是模拟照片里的曝光，不是特效
    content = ImageEnhance.Brightness(content).enhance(bright)
    base.paste(content, (x1, y1))
    return base


def plan():
    """列出可行的组合：每个房间照片 × 每帧节目内容。

    ★ 三个会污染验收的房间，显式排除（不是"懒得处理"，是处理不了）：
      · 05_Laptop_in_the_living_room：底图上是一台**笔记本**，YOLO 同时给出了
        laptop 0.87 和 tv 0.82 两个框。往笔记本屏幕上贴"电视在播动画"，
        测的就不是电视识别了。
      · 09_Modern_Living_Room：框只有 214x305，是竖长条（框偏了），贴进去
        内容会被裁成竖条，跟真实屏幕比例不符。
    这两个留在 frames_real/tv/ 里当"反例/观察"是可以的，但**不能进验收集**。
    """
    rooms = [p for p in (REAL / "tv").glob("*.jpg")]
    frames = [p for p in (REAL / "tv" / "content").glob("*.jpg")]
    skip_room = ("05_Laptop", "09_Modern")
    combos = []
    for room in sorted(rooms):
        if any(s in room.name for s in skip_room):
            print(f"  - 跳过 {room.name[:40]}（底图场景不适合当电视样本）")
            continue
        boxes = _screen_boxes(room)
        if not boxes:
            continue
        # 框太小（<36x28）贴上去也看不清，算无效样本
        big = [b for b in boxes if (b[2] - b[0]) >= 36 and (b[3] - b[1]) >= 28]
        if not big:
            continue
        # 横宽比超过 1.8 说明框偏了（正常屏幕不会这么竖），跳过
        big = [b for b in big if (b[2] - b[0]) / max(1, b[3] - b[1]) >= 0.8]
        if not big:
            print(f"  - 跳过 {room.name[:40]}（框比例不像屏幕）")
            continue
        for fr in sorted(frames):
            combos.append((room, big[0], fr))
    return combos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--bright", type=float, default=0.88)
    args = ap.parse_args()

    combos = plan()
    print(f"可用组合：{len(combos)}（房间有屏幕框 × 节目帧）")
    if args.list:
        for room, b, fr in combos:
            print(f"  {room.name[:34]:<36} 框 {int(b[2]-b[0])}x{int(b[3]-b[1])} "
                  f"conf={b[4]:.2f}  ← {fr.name[:38]}")
        return 0 if combos else 1
    if not combos:
        print("[X] 没有可用组合：要么房间照片里没电视，要么框太小。")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    meta = []
    for i, (room, box, fr) in enumerate(combos, 1):
        kind = fr.name.split("_")[0]          # "动画" / "新闻"
        name = f"synth_{i:02d}_{kind}_{room.stem[:18]}_{fr.stem[:12]}.jpg"
        name = "".join(c for c in name if c.isalnum() or c in "._-")[:90]
        img = paste_quad(room, fr, box[:4], args.bright)
        img.save(OUT / name, quality=90)
        meta.append({"file": name, "room": room.name, "content": fr.name,
                     "kind": kind, "box": [round(v, 1) for v in box[:4]],
                     "box_conf": round(box[4], 3),
                     "synth": "screen region pasted; nothing else retouched"})
        print(f"  ✓ {name}")
    META.write_text(json.dumps(
        {"note": "屏幕区域为合成（真实房间照片 + 真实节目帧）。不可用于对外演示，"
                 "只能用于内部识别能力评估。",
         "items": meta}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n共 {len(meta)} 张 → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

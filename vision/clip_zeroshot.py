r"""⑤ CLIP 零样本：判断"这帧有没有 XX"，补 YOLO 认不出的东西

为什么需要它（承接 ②-B 的结论）：
    YOLO 只能认 COCO 80 类。天空、街道、草地、雨 —— 它一个都认不出（21 张里 5 张空检出）。
    而 CLIP 是**图文对齐**模型：给它一句"a rainy street"，它就能算这图和这句话有多像。
    → 不需要训练，就能判断"有没有下雨""是室内还是室外""这是厨房还是卧室"。

    真机摄像头拿不到 weather/scene（都是 unknown），CLIP 正好补这个洞。

用法：
    cd D:\xiaoyi_vision
    venv\Scripts\python.exe clip_zeroshot.py --image frames\01_sky.jpg
    venv\Scripts\python.exe clip_zeroshot.py --camera          # 抓一帧摄像头来判断
    venv\Scripts\python.exe clip_zeroshot.py --dir frames      # 全图集跑一遍看统计
    venv\Scripts\python.exe clip_zeroshot.py --image xxx --json

首次运行会从 hf-mirror 下 ViT-B-32 权重（约 350MB，只需一次），之后完全离线。
"""

import argparse
import json
import os
import sys
from pathlib import Path

# ⚠️ 必须放在 import open_clip / huggingface_hub 之前
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

HERE = Path(__file__).resolve().parent
MODEL_NAME = "ViT-B-32"
PRETRAINED = "laion2b_s34b_b79k"

# 英文提示词（CLIP 的文本编码器只认英文）→ 我们自己的标签
WEATHER_PROMPTS = {
    "a sunny day with clear sky": "sunny",
    "a rainy wet day": "rain",
    "an overcast cloudy day": "cloudy",
    "a snowy day": "snow",
}
SCENE_PROMPTS = {
    "a living room with a sofa": "living_room",
    "a kitchen": "kitchen",
    "a bedroom with a bed": "bedroom",
    "a street with road and buildings": "street",
    "a park with grass and trees": "park",
    "a garden with flowers and plants": "garden",
    "a dining room with a table": "dining",
    "a balcony": "balcony",
    "a view of the sky": "outdoor_sky",
}
INOUT_PROMPTS = {
    "an indoor scene inside a room": "indoor",
    "an outdoor scene outside": "outdoor",
}

_model = None
_tokenizer = None
_preprocess = None


def _load():
    global _model, _tokenizer, _preprocess
    if _model is not None:
        return
    import open_clip
    import torch
    print(f"[..] 加载 {MODEL_NAME}/{PRETRAINED}（首次会下载约350MB）", flush=True)
    _model, _, _preprocess = open_clip.create_model_and_transforms(MODEL_NAME, pretrained=PRETRAINED)
    _model.eval()
    _tokenizer = open_clip.get_tokenizer(MODEL_NAME)


def _scores(image_obj, prompt_map):
    """返回 [(label, prob)]，按概率降序"""
    import torch
    texts = list(prompt_map.keys())
    with torch.no_grad():
        img = _preprocess(image_obj).unsqueeze(0)
        img_feat = _model.encode_image(img)
        txt_feat = _model.encode_text(_tokenizer(texts))
        img_feat = img_feat / img_feat.norm(dim=-1, keepdim=True)
        txt_feat = txt_feat / txt_feat.norm(dim=-1, keepdim=True)
        logits = (100.0 * img_feat @ txt_feat.T).softmax(dim=-1)[0]
    pairs = [(prompt_map[t], float(p)) for t, p in zip(texts, logits.tolist())]
    pairs.sort(key=lambda x: -x[1])
    return pairs


def infer_environment(image_obj):
    """给一张 PIL.Image，返回 {"indoor", "scene", "weather"}（含置信度）

    重要：**室内图不判天气**。
    实测发现，对室内照片硬判天气会得出"下雪 0.51""下雨 0.86"这种胡说
    （09_meal 餐盘 → snow、19 室内盆栽 → rain）。室内根本看不到天，
    诚实的输出是 unknown，把天气交给"窗景/天气API/室外摄像头"。
    """
    _load()
    inout = _scores(image_obj, INOUT_PROMPTS)
    scene = _scores(image_obj, SCENE_PROMPTS)

    indoor_label, indoor_conf = inout[0]
    if indoor_label == "indoor" and indoor_conf >= 0.60:
        weather = {"label": "unknown", "conf": 0.0, "note": "室内看不出天气"}
    else:
        w = _scores(image_obj, WEATHER_PROMPTS)
        weather = {"label": w[0][0], "conf": round(w[0][1], 3)}

    return {
        "indoor": {"label": indoor_label, "conf": round(indoor_conf, 3)},
        "scene": {"label": scene[0][0], "conf": round(scene[0][1], 3),
                  "top3": [{"label": l, "conf": round(p, 3)} for l, p in scene[:3]]},
        "weather": weather,
    }


def _report(name, res):
    print(f"\n{name}")
    print(f"  室内外 : {res['indoor']['label']:<10s} {res['indoor']['conf']:.2f}")
    print(f"  场景   : {res['scene']['label']:<12s} {res['scene']['conf']:.2f}")
    w = res["weather"]
    if w.get("label") == "unknown":
        print(f"  天气   : —（{w.get('note', 'unknown')}）")
    else:
        print(f"  天气   : {w['label']:<10s} {w['conf']:.2f}")
    print("  场景前三: " + "  ".join(f"{t['label']}({t['conf']:.2f})" for t in res["scene"]["top3"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="")
    ap.add_argument("--dir", default="")
    ap.add_argument("--camera", action="store_true")
    ap.add_argument("--json", default="", help="把结果写到这个文件")
    args = ap.parse_args()

    from PIL import Image

    results = []

    if args.camera:
        from sources import CameraSource
        print("[..] 打开摄像头抓一帧")
        src = CameraSource(index=0, interval=0)
        ok, frame, meta = src.read()
        src.release()
        if not ok:
            print("[X] 摄像头没抓到帧")
            return 1
        img = Image.fromarray(frame[..., ::-1])   # BGR -> RGB
        res = infer_environment(img)
        _report("摄像头实时帧", res)
        results.append({"file": "camera:0", **res})

    if args.image:
        img = Image.open(args.image).convert("RGB")
        res = infer_environment(img)
        _report(Path(args.image).name, res)
        results.append({"file": Path(args.image).name, **res})

    if args.dir:
        files = sorted(p for p in Path(args.dir).iterdir()
                       if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
        agree_scene = 0
        meta_file = Path(args.dir) / "meta.json"
        meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
        for p in files:
            res = infer_environment(Image.open(p).convert("RGB"))
            _report(p.name, res)
            results.append({"file": p.name, **res})

        # 与 meta.json 的人工标签对比（这是 ⑤ 的客观指标）
        compared, hit_scene, hit_weather, w_compared = 0, 0, 0, 0
        for r in results:
            m = meta.get(r["file"])
            if not m:
                continue
            compared += 1
            if m.get("scene") == r["scene"]["label"]:
                hit_scene += 1
            # 室内图不判天气，不计入天气统计
            if r["weather"].get("label") != "unknown":
                w_compared += 1
                if m.get("weather") == r["weather"]["label"]:
                    hit_weather += 1
        if compared:
            print("\n" + "=" * 60)
            print(f"与 meta.json 人工标签对比（{compared} 张）")
            print(f"  场景一致 : {hit_scene}/{compared} = {hit_scene / compared:.0%}")
            if w_compared:
                print(f"  天气一致 : {hit_weather}/{w_compared} = {hit_weather / w_compared:.0%}"
                      f"（只统计室外 {w_compared} 张）")
            else:
                print("  天气一致 : 本批全是室内，不统计")
            print("  注：meta 的 scene 是「我看这张图像什么」，CLIP 是「图文相似度最高的那个」，")
            print("      两个口径本来就不完全等价，这个数字只作参考，不是准确率。")

    if args.json:
        Path(args.json).parent.mkdir(exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n[OK] 已写入 {args.json}")

    if not (args.image or args.dir or args.camera):
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

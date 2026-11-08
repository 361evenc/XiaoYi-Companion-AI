r"""F-6 拍摄质量检测 —— 只判"太暗 / 太糊"两类

任务卡（F-6）把拍中反馈压到最小：**只做亮度和清晰度检测**，不做对象级反馈
（"有点歪，摆正一点"不在 MVP）。这里刻意不引入 cv2：实测主环境只装了
PIL + numpy（vision/requirements.txt 里的 opencv 属于另一条视觉线的独立 venv），
为了两类检测再装 90MB 的 opencv 不划算。Laplacian 方差用 numpy 的切片卷积算，
和 cv2.Laplacian 同一套原理，结果可直接回归。

判定口径：
    · 太暗   —— 灰度均值 < DARK_MEAN
    · 太糊   —— Laplacian 方差 < BLUR_VAR
    · 两类都中 → verdict 取 dark（先解决光线，光线好了再谈对焦，反馈不堆两句）

**这不是"识别"**：本模块只回答"这张图能不能拿去看"，不判断图里是什么，
也不参与 H-4 的成功率统计。F-6 的验收数字由 B-10 用 H-4 的识别结果收口。

命令行（校准阈值用）：
    python photo_quality.py --selftest
    python photo_quality.py frames_real/old_photo/01.jpg
"""

import argparse
import base64
import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

# ---------------------------------------------------------------- 阈值

# 统一缩到长边 640 再算，保证阈值不受原图分辨率影响
MAX_SIDE = 640

# 灰度均值（0-255）。室内正常拍照大约 90-160；现有 15 张真实帧最低 70.3，
# 阈值压到 65 留出余量，避免把"暗一点的正常室内照"误伤成"太暗"
DARK_MEAN = 65.0

# Laplacian 方差。对 640px 的文档/照片：清晰 ≥ 数百，明显手抖/失焦通常 < 100
BLUR_VAR = 100.0

# 太小（长边 < 200）的图放大后全是插值，判糊
MIN_SIDE = 200


def _to_gray_array(image_bytes: bytes) -> np.ndarray:
    im = Image.open(io.BytesIO(image_bytes))
    im = ImageOps.exif_transpose(im)   # 手机照片带方向信息，先摆正再算
    im = im.convert("L")
    w, h = im.size
    scale = MAX_SIDE / max(w, h)
    if scale != 1:
        im = im.resize((max(1, round(w * scale)), max(1, round(h * scale)),
                        ), Image.Resampling.LANCZOS)
    return np.asarray(im, dtype=np.float32)


def _laplacian_variance(arr: np.ndarray) -> float:
    """四邻域 Laplacian（与 cv2.Laplacian ksize=1 同核）+ 方差。

    用切片而不是循环：整图一次向量化算完，1 秒内出结果，不拖慢拍照链路。
    """
    if arr.shape[0] < 3 or arr.shape[1] < 3:
        return 0.0
    lap = (
        -4.0 * arr[1:-1, 1:-1]
        + arr[:-2, 1:-1]
        + arr[2:, 1:-1]
        + arr[1:-1, :-2]
        + arr[1:-1, 2:]
    )
    return float(lap.var())


def analyze_bytes(image_bytes: bytes) -> dict:
    """对一张图的字节流做检测，返回可解释的机器判定。

    返回里带原始指标和阈值，前端只负责把 verdict 翻成老人听得懂的话；
    调阈值时不用翻代码，看返回就知道差多少。
    """
    arr = _to_gray_array(image_bytes)
    h, w = arr.shape
    brightness = float(arr.mean())
    sharpness = _laplacian_variance(arr)

    issues = []
    if min(w, h) < MIN_SIDE:
        issues.append("blurry")       # 太小 = 放大也看不清，归到"糊"
    elif sharpness < BLUR_VAR:
        issues.append("blurry")
    if brightness < DARK_MEAN:
        issues.append("dark")

    verdict = "dark" if "dark" in issues else ("blurry" if issues else "ok")
    return {
        "ok": verdict == "ok",
        "verdict": verdict,
        "issues": issues,
        "brightness": round(brightness, 1),
        "sharpness": round(sharpness, 1),
        "size": [w, h],
        "thresholds": {"dark_mean": DARK_MEAN, "blur_var": BLUR_VAR,
                       "min_side": MIN_SIDE},
    }


def analyze_base64(text: str) -> dict:
    """接受纯 base64 或 dataURL（data:image/jpeg;base64,...）。

    前端压缩管线产出的是纯 base64，但记忆箱里存的可能是 dataURL；
    两种都吃，少一个"格式不对"的坑。
    """
    s = (text or "").strip()
    if s.startswith("data:"):
        comma = s.find(",")
        if comma < 0:
            raise ValueError("dataURL 缺少逗号分隔")
        s = s[comma + 1:]
    try:
        raw = base64.b64decode(s, validate=True)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"base64 解码失败：{e}") from e
    if not raw:
        raise ValueError("图片为空")
    return analyze_bytes(raw)


def analyze_path(path) -> dict:
    return analyze_bytes(Path(path).read_bytes())


# ---------------------------------------------------------------- 自检

def _make_samples(tmp: Path):
    """三张可复现的图：清晰 / 太暗 / 太糊。

    不依赖外部素材，验收在任何机器上都能跑；阈值改动后先跑这个。
    """
    from PIL import ImageDraw

    w, h = 900, 640
    img = Image.new("RGB", (w, h), (250, 249, 245))
    d = ImageDraw.Draw(img)
    # 模拟说明书：密集横线 + 文字块（高频边缘，清晰时应是高方差）
    for y in range(40, h - 30, 26):
        d.line([(40, y), (w - 40, y)], fill=(20, 20, 24), width=2)
        d.rectangle([60, y + 8, 60 + (w - 160) // 2, y + 18], fill=(30, 30, 34))
    sharp = tmp / "f6_sharp.png"
    img.save(sharp)

    dark = tmp / "f6_dark.png"
    Image.eval(img, lambda v: v * 0.18).save(dark)   # 均值 ~45

    from PIL import ImageFilter
    blur = tmp / "f6_blur.png"
    img.filter(ImageFilter.GaussianBlur(radius=7)).save(blur)

    flat = tmp / "f6_flat.png"   # 纯色（没有任何字）= 没对上焦/没内容
    Image.new("RGB", (w, h), (245, 244, 240)).save(flat)
    return sharp, dark, blur, flat


def selftest() -> int:
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="f6_quality_"))
    sharp, dark, blur, flat = _make_samples(tmp)
    rows = []
    failed = 0

    def check(label, cond, got):
        nonlocal failed
        mark = "OK " if cond else "!! "
        if not cond:
            failed += 1
        print(f"  {mark}{label}：{json.dumps(got, ensure_ascii=False)}")

    r = analyze_path(sharp)
    check("清晰文档 → ok", r["verdict"] == "ok", r)
    rows.append(r)

    r = analyze_path(dark)
    check("压暗图 → dark", r["verdict"] == "dark", r)
    rows.append(r)

    r = analyze_path(blur)
    check("高斯模糊 → blurry", r["verdict"] == "blurry", r)
    rows.append(r)

    r = analyze_path(flat)
    check("纯色无内容 → blurry", r["verdict"] == "blurry", r)
    rows.append(r)

    # 又暗又糊：只回一条主判定（dark 优先），但 issues 要两条都在
    both = tmp / "f6_both.png"
    from PIL import ImageFilter
    Image.eval(Image.open(blur), lambda v: v * 0.18).save(both)
    r = analyze_path(both)
    check("又暗又糊 → verdict=dark 且 issues 含两类",
          r["verdict"] == "dark" and set(r["issues"]) == {"dark", "blurry"}, r)
    rows.append(r)

    # base64 / dataURL 两种入参都要能吃
    b64 = base64.b64encode(sharp.read_bytes()).decode()
    r1 = analyze_base64(b64)
    r2 = analyze_base64("data:image/png;base64," + b64)
    check("纯 base64 与 dataURL 结果一致",
          r1["verdict"] == "ok" and r2["verdict"] == "ok"
          and r1["brightness"] == r2["brightness"], {"b64": r1["verdict"], "url": r2["verdict"]})

    print(f"\n阈值：暗 {DARK_MEAN} / 糊 {BLUR_VAR} / 最小边长 {MIN_SIDE}")
    print("自检通过" if not failed else f"自检失败 {failed} 项")
    return 0 if not failed else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest or not args.paths:
        sys.exit(selftest())
    for p in args.paths:
        print(p, json.dumps(analyze_path(p), ensure_ascii=False))


if __name__ == "__main__":
    main()

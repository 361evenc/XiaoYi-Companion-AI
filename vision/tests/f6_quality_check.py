r"""F-6 拍摄质量检测验收 —— 只判"太暗 / 太糊"两类

跑法（项目根目录）：
    python vision\tests\f6_quality_check.py

为什么用合成图做主样本：阈值要可复现。清晰文档、压暗、高斯模糊三张图
在同一台机器上每次结果一致；再叠加现有 15 张真实帧做"不误伤"回归。
真实老人拍的暗照片无法在 CI 里复现，属于 B-10 的人工 A/B 项（见修改说明）。
"""

import base64
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VISION = HERE.parent
ROOT = VISION.parent
sys.path.insert(0, str(VISION))
sys.path.insert(0, str(ROOT))

from photo_quality import _make_samples, analyze_base64, analyze_path  # noqa: E402

failed = 0


def check(cond, label, detail=""):
    global failed
    if cond:
        print(f"  OK  {label}" + (f"  {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  !!  {label}" + (f"  {detail}" if detail else ""))


def main():
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="f6_accept_"))
    sharp, dark, blur, flat = _make_samples(tmp)

    print("\n[1/5] 三类判定：清晰 / 太暗 / 太糊")
    r_sharp = analyze_path(sharp)
    check(r_sharp["verdict"] == "ok", "清晰文档判 ok", f"brightness={r_sharp['brightness']} sharpness={r_sharp['sharpness']}")
    r_dark = analyze_path(dark)
    check(r_dark["verdict"] == "dark", "压暗图判太暗", f"brightness={r_dark['brightness']}")
    r_blur = analyze_path(blur)
    check(r_blur["verdict"] == "blurry", "模糊图判太糊", f"sharpness={r_blur['sharpness']}")
    r_flat = analyze_path(flat)
    check(r_flat["verdict"] == "blurry", "纯色无字图判太糊", f"sharpness={r_flat['sharpness']}")

    print("\n[2/5] 又暗又糊：只回一条主判定，issues 记两类")
    from PIL import Image

    both = tmp / "both.png"
    Image.eval(Image.open(blur), lambda v: v * 0.18).save(both)
    r_both = analyze_path(both)
    check(r_both["verdict"] == "dark", "主判定为 dark（先解决光线）")
    check(set(r_both["issues"]) == {"dark", "blurry"}, "issues 同时含两类", str(r_both["issues"]))
    check(len(r_both["issues"]) == 2, "不重复堆同一类")

    print("\n[3/5] 入参兼容：纯 base64 / dataURL 结果一致，坏数据明确报错")
    b64 = base64.b64encode(sharp.read_bytes()).decode()
    r1 = analyze_base64(b64)
    r2 = analyze_base64("data:image/png;base64," + b64)
    check(r1["verdict"] == "ok" and r2["verdict"] == "ok", "两种入参都判 ok")
    check(r1["brightness"] == r2["brightness"], "同图同结果（没有重复缩放误差）")
    try:
        analyze_base64("not-base64!!!")
        check(False, "坏数据应抛 ValueError")
    except ValueError:
        check(True, "坏数据抛 ValueError（接口可回 400）")

    print("\n[4/5] 接口路由存在且只做检测（不含任何“歪/摆正”对象级反馈）")
    from backend.api import photo_scene_api

    paths = [r.path for r in photo_scene_api.router.routes]
    check("/api/photo/quality" in paths, "路由 /api/photo/quality 已注册")
    api_src = (ROOT / "backend" / "api" / "photo_scene_api.py").read_text(encoding="utf-8")
    quality_fn = api_src.split("def quality(", 1)[-1]
    # 说明文字（docstring）里点名"歪/摆正"是为了写清边界，不算产出文案；先剥掉再判
    import re

    quality_fn_body = re.sub(r'r?"""[\s\S]*?"""', "", quality_fn)
    check("analyze_base64" in quality_fn, "quality 接口复用同一个检测实现")
    check(
        "歪" not in quality_fn_body and "摆正" not in quality_fn_body,
        "接口层不产出对象级反馈文案",
    )

    print("\n[5/5] 真实帧回归：不把正常照片误伤成“太暗/太糊”")
    frames = sorted((VISION / "frames").glob("*.jpg"))
    if not frames:
        check(False, "缺少真实帧样本 vision/frames/*.jpg")
    else:
        bad = []
        for p in frames[:15]:
            r = analyze_path(p)
            if r["verdict"] != "ok":
                bad.append((p.name, r["verdict"], r["brightness"], r["sharpness"]))
        check(len(bad) == 0, f"{min(15, len(frames))} 张真实帧全部判 ok", str(bad[:3]))

    print("\n" + "-" * 66)
    if failed:
        print(f"FAILED：{failed} 项未通过")
        return 1
    print("PASS：F-6 拍摄质量检测验收通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

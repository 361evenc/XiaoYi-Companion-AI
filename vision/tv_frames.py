"""取 H-13「电视在播什么」的屏幕内容帧（真实节目画面）。

**为什么不找"客厅里开着电视"的照片 —— 找不到。**
上一轮抓 11 张，只有 4 张电视亮着，而且屏幕里是什么根本看不清。
拿黑屏电视测"在播什么节目"，测出来的必然是误报，等于没测。

**为什么不 PIL 画一个电视框 —— 那就成了自己造样本。**
H-4 已经有渲染组覆盖"干净版面"；H-13 要的是真实感：真实房间、真实屏幕、
真实画面。所以拆成两半各自求真：
  · 房间 = 真拍的照片（Wikimedia CC）
  · 屏幕内容 = 真片子里的帧（Wikimedia PD 影片）
只做一件事：把真帧按透视贴进真照片的屏幕区域。**这一步必须写明是合成的。**

**取帧的两条路（第二版，2026-10-28 被限流逼出来的）**：
  ① 原始路：下整部 .webm/.ogv（几十~几百 MB）→ cv2 解码 seek。
     问题：upload.wikimedia.org 对连续下载会限流（实测 429 + retry-after 600）。
  ② **缩略路（现在用这个）**：视频文件的 thumburl 就是**代表帧**的 JPG，
     在 thumb.wikimedia.org 上，不受原始域限流，一次 20KB。
     代价：只能拿**一帧**（通常是影片开头/标题卡），不能挑选时间点。

② 的代价是真的代价：老动画的标题卡是纯文字，测不出"动画"的视觉特征。
所以这里对每部片取多张不同尺寸的缩略图？不行——尺寸变了帧不变。
**真解法是取多部片子，每部拿一帧**。数量换多样性，这是 ② 唯一能做的事，
写在这里是为了让后面的人知道：这一组的画面多样性受限于此，不是随手抓的。

**为什么用 PD 老动画/新闻片而不是现在的中文节目**：
版权。Wikimedia 上能合法拿到的"节目画面"，绝大多数是 1930-1950 年代美国
PD 影片。它们能测出 CLIP 认不认得出「动画/新闻」，但**测不出中文节目**
（《新闻联播》片头、央视体育频道）的识别效果——这是样本的硬边界，
报告里必须标出来，不能拿数字假装覆盖了中文场景。

用法：
    python tv_frames.py --get            # 取帧到 frames_real/tv/content/
    python tv_frames.py --list           # 只列，不下载
"""

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "frames_real" / "tv" / "content"
META = OUT / "meta.json"
API = "https://commons.wikimedia.org/w/api.php"
UA = "xiaoyi-vision-research/1.0 (internal test sample collection)"

# ───────────────────────────────────────────── 素材清单
# key = 期望节目类型（必须落在 explain_world.TV_PROMPTS 的分类口径里）
# value = [(File:xxx, 期望类型)]
#
# 挑片子的标准：**片名一看就知道是什么类型**，不选那些"打开是字幕卡"的。
# 动画选角色出镜的、新闻选有人的，别选那种一开头就是片头的。
SOURCES = {
    "动画": [
        "File:Betty Boop - Judge For A Day (1935).webm",
        "File:Popeye Gopher Spinach.webm",
        "File:Shuteye Popeye (1952).webm",
        "File:Private Eye Popeye (1954).webm",
        "File:Betty Boop And Grampy (1935).webm",
    ],
    "新闻": [
        "File:John Pershing Newsreel 1940.ogv",
        "File:1940-05-22 Mrs Roosevelt In Red Cross Appeal.ogv",
        "File:Wendell Willkie addressing the crowd at the Republican National Convention, 1940.ogv",
    ],
}


def _session():
    import requests
    s = requests.Session()
    s.trust_env = True
    s.headers.update({"User-Agent": UA})
    return s


def _thumbs(s, titles, width=1280):
    """一次查多部片，拿到各自的**代表帧** URL（thumb 域，不受原始下载限流）。"""
    out = {}
    for i in range(0, len(titles), 5):
        batch = titles[i:i + 5]
        r = s.get(API, params={
            "action": "query", "format": "json",
            "titles": "|".join(batch), "prop": "imageinfo",
            "iiprop": "url|size|extmetadata", "iiurlwidth": width,
        }, timeout=30)
        d = r.json()
        for p in (d.get("query", {}).get("pages") or {}).values():
            ii = (p.get("imageinfo") or [{}])[0]
            tu = ii.get("thumburl")
            if tu:
                out[p["title"]] = {
                    "thumb": tu, "w": ii.get("iiurlwidth"), "h": ii.get("iiurlheight"),
                    "page": ii.get("descriptionurl", ""),
                    "license": ((ii.get("extmetadata") or {}).get("LicenseShortName")
                                or {}).get("value", ""),
                }
        time.sleep(0.4)
    return out


def get(width=1280):
    OUT.mkdir(parents=True, exist_ok=True)
    s = _session()
    all_titles = [t for ts in SOURCES.values() for t in ts]
    info = _thumbs(s, all_titles, width)
    meta, n = {}, 0
    for kind, titles in SOURCES.items():
        keep = []
        for t in titles:
            it = info.get(t)
            if not it:
                print(f"  [X] 查不到 {t[:50]}")
                continue
            # thumb 的宽高不一定等于请求宽度，按实际响应判断
            name = f"{kind}_{len(keep) + 1:02d}_{t[5:40].replace(' ', '_')}.jpg"
            name = "".join(c for c in name if c.isalnum() or c in "._-")[:80]
            dest = OUT / name
            if not dest.exists():
                try:
                    r = s.get(it["thumb"], timeout=60)
                except Exception as e:
                    print(f"  [X] 下载异常 {type(e).__name__} {t[:40]}")
                    continue
                if r.status_code != 200 or len(r.content) < 3000:
                    print(f"  [X] {r.status_code} {len(r.content)}B  {t[:45]}")
                    continue
                dest.write_bytes(r.content)
                time.sleep(0.5)
            keep.append({"file": name, "kind": kind, "source": t,
                         "license": it["license"], "page": it["page"],
                         "note": "影片代表帧（thumb 服务输出），非人工挑选的时间点"})
            n += 1
            print(f"  ✓ {name}")
        meta[kind] = keep
    META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n共 {n} 帧 → {OUT}")
    print("⚠ 这些只是**屏幕内容**；要贴进真实房间照片才成 H-13 的样本。")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--get", action="store_true")
    ap.add_argument("--width", type=int, default=1280)
    args = ap.parse_args()
    if args.list:
        for k, ts in SOURCES.items():
            print(f"[{k}] {len(ts)} 部")
            for t in ts:
                print("   ", t)
        return 0
    if args.get:
        return get(args.width)
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())

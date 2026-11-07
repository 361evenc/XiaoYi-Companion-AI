"""抓真实测试样本：老照片 / 电视画面 / 药品说明书

**为什么要有这个脚本 —— 验收不能自己造样本。**

前面 H-4 的说明书验收用的是 Pillow 渲染的版面（干净、无噪声）。那是必要的
（可重复），但不够（真实照片有反光、斜拍、折痕）。H-13 的电视判断甚至
完全没有"真有电视在播"的样本。所以这里从 Wikimedia Commons 抓**真实照片**。

**为什么是 Wikimedia Commons**：
  · 公共领域/CC 授权，可合法用于内部研究测试
  · 有 API，可精确按分类筛（不靠搜索引擎的运气）
  · 全是**真人真物真场景**，没有 AI 生成图（AI 图会把验收做虚）

**三条硬规矩（写在代码里，不是嘴上说）**：
  ① 只当测试样本，不进产品、不外发、不入库（存 frames_real/，gitignore）
  ② 老照片只取**历史照片（作者去世 >70 年或明确 PD）**，不取现代人的家庭照——
     现代家庭照哪怕授权可查，也不该拿来测"这是您孙子吧"
  ③ 每张记来源 URL + 授权，出问题时能追

用法：
    venv\\Scripts\\python.exe fetch_test_samples.py --list          # 只列不下载
    venv\\Scripts\\python.exe fetch_test_samples.py --get old_photo # 下载某组
    venv\\Scripts\\python.exe fetch_test_samples.py --get all
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "frames_real"
MANIFEST = OUT_DIR / "manifest.json"

API = "https://commons.wikimedia.org/w/api.php"
UA = "xiaoyi-vision-research/1.0 (internal test sample collection)"


def _session():
    import requests
    s = requests.Session()
    s.trust_env = True          # 本机直连被限，环境代理可用
    s.headers.update({"User-Agent": UA})
    return s


# ================================================================ 抓取规则

GROUPS = {
    # ---- 老照片：只要历史黑白/褐色家庭合影，作者久远，PD ----
    "old_photo": {
        "goal": "20 张老照片，测'认出的人正确率≥80% 或正确说不确定'",
        "queries": [
            "family portrait 1900 photograph",
            "family group photograph 1920s",
            "wedding photograph 1930",
            "grandmother portrait vintage photograph",
            "family photograph 1940s black and white",
        ],
        "categories": [
            "Category:Family portraits",
            "Category:Black and white photographs of families",
            "Category:1900s photographs",
        ],
        "min_width": 500,
    },
    # ---- 电视：只要真能看见屏幕且有画面内容的 ----
    #
    # ★ 2026-10-28 第二版：第一轮 11 张里只有 4 张真有电视（01 是房子外观、
    #   07 是挂照片的墙、08/10 是酒店大堂），命中率太低。原因同样是
    #   "关键词搜文件命名空间会捞到描述里提到该词的任何图"。
    #   改法：主攻分类遍历（Category 里的人为过过一遍），关键词只做补充。
    "tv": {
        "goal": "10-20 张电视正在播节目，测节目类型判断 + '无电视不开口'的反面",
        "queries": [
            "television broadcast on screen",
            "news program on television screen",
            "football match on TV screen",
            "watching news on television",
        ],
        "categories": [
            "Category:Televisions in use",
            "Category:People watching television",
            "Category:Television screens",
            "Category:Televisions switched on",
        ],
        "min_width": 500,
        "exclude": ["House", "Palmer_Avenue", "Refugees"],
    },
    # ---- 药品说明书：只要真实的药品包装/说明书特写 ----
    #
    # ★ 2026-10-28 修正：第一轮的英文关键词（"pharmaceutical packaging box" 等）
    #   抓回来 14 张全是 1900 年代英文古董药盒（"Gout & Rheumatic Mixture"）。
    #   OCR 能读出字，但**一个中文药品字段都抽不出来**，等于拿 100 年前的英国药盒
    #   去测"帮中国老人读中文说明书"——样本和场景不匹配，测出来的数字是假的。
    #   所以改用中文药名。中成药包装上"功能主治/用法用量/注意事项"字段齐全，
    #   正是老人会拿着问的那一类。
    "medicine": {
        "goal": "10-20 张中文药品包装，测 OCR 在反光/斜拍下的字段准确率",
        "queries": [
            "中成药",
            "Lianhua Qingwen",
            "Pien tze huang",
            "Fufang Shiwei Pian",
            "Jingfukang",
            "Chinese patent medicine box",
        ],
        "categories": [
            "Category:Traditional Chinese medicine",
            "Category:Chinese patent medicine",
        ],
        "min_width": 400,
        # 同名异物（见 collect() 里的说明）
        "exclude": ["cement", "Diesel", "Petrol", "Transnet", "XBJ", "@PEK",
                    "Boeing", "Airport", "Museum", "Trade imagery",
                    "Metro City Plaza", "Discover"],
    },
    # ---- 反例：画面里**没有**电视的客厅/室内 ----
    # H-13 踩过最重的坑：CLIP 对没有电视的画面也必选一类，把"明亮客厅"判成
    # "电视在播综艺 conf=0.785"。修好之后必须持续用**反例**盯着，否则会退化。
    "no_tv": {
        "goal": "10 张没有电视的客厅，测'无电视不开口'（一张都不许开口）",
        "queries": [
            "living room sofa window daylight",
            "living room interior furniture",
            "bedroom interior apartment",
            "dining room table chairs interior",
        ],
        "categories": [
            "Category:Living rooms",
            "Category:Interiors of apartments",
        ],
        "min_width": 500,
    },
}


# ================================================================ API 调用


def _json(r, what=""):
    """容错解析：Wikimedia 被限流时会返回 HTML 错误页，直接 r.json() 会抛 JSONDecodeError。
    这里明确区分"被限流"和"没结果"，并返回 None 让上层跳过。"""
    try:
        return r.json()
    except Exception:
        if r.status_code in (429, 403, 503) or "text/html" in r.headers.get("content-type", ""):
            print(f"  [!] 被限流/拒绝（{r.status_code}）{what}，稍后重试")
        else:
            print(f"  [!] 返回非 JSON（{r.status_code}）{what}")
        return None


def search_files(sess, query, limit=12):
    """按关键词搜文件命名空间。返回 [ {title, ...} ]"""
    r = sess.get(API, params={
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": 6,
        "gsrlimit": limit, "prop": "imageinfo",
        "iiprop": "url|size|extmetadata", "iiurlwidth": 1400,
    }, timeout=30)
    d = _json(r, "search") or {}
    pages = ((d.get("query") or {}).get("pages") or {})
    out = []
    for p in pages.values():
        ii = (p.get("imageinfo") or [{}])[0]
        if not ii.get("url"):
            continue
        out.append({
            "title": p.get("title", ""),
            "url": ii.get("thumburl") or ii.get("url"),
            "width": ii.get("width", 0),
            "height": ii.get("height", 0),
            "license": ((ii.get("extmetadata") or {}).get("LicenseShortName") or {}).get("value", ""),
            "artist": ((ii.get("extmetadata") or {}).get("Artist") or {}).get("value", "")[:80],
            "page": ii.get("descriptionurl", ""),
        })
    return out


def category_files(sess, cat, limit=12):
    r = sess.get(API, params={
        "action": "query", "format": "json", "generator": "categorymembers",
        "gcmtitle": cat, "gcmtype": "file", "gcmlimit": limit,
        "prop": "imageinfo", "iiprop": "url|size|extmetadata", "iiurlwidth": 1400,
    }, timeout=30)
    d = _json(r, "category") or {}
    pages = ((d.get("query") or {}).get("pages") or {})
    out = []
    for p in pages.values():
        ii = (p.get("imageinfo") or [{}])[0]
        if not ii.get("url"):
            continue
        out.append({
            "title": p.get("title", ""),
            "url": ii.get("thumburl") or ii.get("url"),
            "width": ii.get("width", 0),
            "height": ii.get("height", 0),
            "license": ((ii.get("extmetadata") or {}).get("LicenseShortName") or {}).get("value", ""),
            "artist": ((ii.get("extmetadata") or {}).get("Artist") or {}).get("value", "")[:80],
            "page": ii.get("descriptionurl", ""),
        })
    return out


def collect(sess, group, per_source=10):
    """把关键词搜索 + 分类遍历的结果去重合并。"""
    cfg = GROUPS[group]
    seen, items = set(), []
    for q in cfg["queries"]:
        try:
            for it in search_files(sess, q, per_source):
                if it["title"] in seen:
                    continue
                seen.add(it["title"])
                it["via"] = f"search:{q}"
                items.append(it)
        except Exception as e:
            print(f"  [!] 搜索失败 {q}: {type(e).__name__}")
        time.sleep(0.3)
    for c in cfg["categories"]:
        try:
            for it in category_files(sess, c, per_source):
                if it["title"] in seen:
                    continue
                seen.add(it["title"])
                it["via"] = f"cat:{c}"
                items.append(it)
        except Exception as e:
            print(f"  [!] 分类失败 {c}: {type(e).__name__}")
        time.sleep(0.3)
    # 过滤：宽度够（太小的看不清字/认不出人）
    items = [i for i in items if (i.get("width") or 0) >= cfg["min_width"]]
    # 过滤：关键词搜文件命名空间会捞到同名异物（搜"中成药"捞回柴油、水泥、飞机
    # 的照片，因为上传者把这几个词写进了同一描述里）。用排除词挡掉，
    # 不然又会拿"不是药的东西"去测"读药盒"，数字是假的。
    bad = tuple(cfg.get("exclude", ()))
    if bad:
        before = len(items)
        items = [i for i in items if not any(w.lower() in i["title"].lower() for w in bad)]
        if before != len(items):
            print(f"  过滤掉 {before - len(items)} 张同名异物（{'/'.join(bad)}）")
    return items


def download(sess, item, dest):
    try:
        r = sess.get(item["url"], timeout=60)
        if r.status_code != 200 or len(r.content) < 5000:
            return False
        dest.write_bytes(r.content)
        return True
    except Exception as e:
        print(f"  [!] 下载失败 {item['title']}: {type(e).__name__}")
        return False


def load_manifest():
    if MANIFEST.exists():
        try:
            return json.loads(MANIFEST.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"groups": {}, "note": "仅内部测试用；不入库、不外发、不进产品"}


def save_manifest(m):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="只列候选不下载")
    ap.add_argument("--get", default="", help="old_photo | tv | medicine | all")
    ap.add_argument("--max", type=int, default=25, help="每组最多下载几张")
    args = ap.parse_args()

    groups = list(GROUPS) if args.get == "all" else ([args.get] if args.get else [])
    if not groups:
        for g, c in GROUPS.items():
            print(f"{g:<12}{c['goal']}")
        print("\n用法：--get old_photo / tv / medicine / all")
        return 0

    sess = _session()
    man = load_manifest()

    for g in groups:
        if g not in GROUPS:
            print(f"[X] 未知组：{g}")
            return 2
        print(f"\n{'=' * 70}\n[{g}] {GROUPS[g]['goal']}\n{'=' * 70}")
        items = collect(sess, g)
        print(f"候选 {len(items)} 张（已按最小宽度 {GROUPS[g]['min_width']} 过滤）")
        if args.list:
            for it in items[:20]:
                print(f"  {it['width']:>5}x{it['height']:<5} {it['license']:<14} {it['title'][:60]}")
            continue

        d = OUT_DIR / g
        d.mkdir(parents=True, exist_ok=True)
        got = []
        for it in items:
            if len(got) >= args.max:
                break
            safe = "".join(ch for ch in it["title"].replace("File:", "")
                           if ch.isalnum() or ch in " -_")[:60].strip().replace(" ", "_")
            ext = os.path.splitext(it["url"].split("?")[0])[1] or ".jpg"
            dest = d / f"{len(got) + 1:02d}_{safe}{ext}"
            if dest.exists():
                got.append({"file": dest.name, **{k: it.get(k) for k in
                                                  ("title", "url", "license", "artist", "page", "via")}})
                continue
            if download(sess, it, dest):
                got.append({"file": dest.name, **{k: it.get(k) for k in
                                                  ("title", "url", "license", "artist", "page", "via")}})
                print(f"  [{len(got):>2}] {dest.name[:56]}")
            time.sleep(0.4)

        man["groups"][g] = {"downloaded": len(got), "dir": str(d), "items": got}
        save_manifest(man)
        print(f"→ 已存 {len(got)} 张到 {d}")

    print(f"\n清单：{MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

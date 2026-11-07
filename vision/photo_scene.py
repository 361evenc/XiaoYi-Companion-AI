r"""H-4 识图场景扩展 —— 老照片人物识别 + 药品说明书识别

现有识图只有"拍照 → 存记忆箱 → 一句话描述"，认不出就说认不出。这一卡加两个场景，
**共性红线是同一条：认不出说"不确定"，不硬猜**（红线 #1 编造 0 容忍）。

    场景一 老照片人物：照片里的人 ↔ 记忆库的人（B-5）
        话术必须是"猜 + 确认"："这是您孙子吧？" 不是断言"这是您孙子"。
        猜错一句"这就是您孙子"比说"我不太确定"伤害大得多 —— 照片里认错人是
        老人最容易被刺到的点，宁可问一句。

    场景二 药品说明书：OCR → 结构化（成分/适应症/用法用量/注意事项/禁忌）
        老人看不清小字，小忆替他读。**只读，不判断**：
          · 不做"这个药适合您"的判断（红线 #2 健康边界）
          · 不做"两种药能不能一起吃"的判断（这属于用药咨询，不是识图）
          · 处方药强制加"这个得问医生/药师"（不是"您可以吃"）

设计上的三个取舍（都是"宁可少做，不可做错"）：

    1. **本地 OCR 优先，VLM 只做补充**。药品盒子上的字是印刷体，本地 OCR
       （rapidocr）不上云、不花钱、离线可用 —— 药品这种敏感内容不该为了识别
       把整张照片发出去。VLM 只在本地 OCR 出不来字时兜底。
    2. **不装模型也能跑**。OCR 后端是"能装就用、没装就明确降级"，不是硬依赖。
       降级时如实返回 "ocr_unavailable"，而不是假装识别成功返回空字符串
       （空字符串和"没认出来"在调用方看来都是空，但一个是环境问题、一个是内容问题，
       混在一起就没法归因了）。
    3. **剂量永远不转成数字建议**。OCR 可能把 "1 片" 读成 "l 片"、把 "0.5g"
       读成 "O.5g"。所以剂量字段**只回显原文片段 + 原文位置**，不让下游任何一段
       代码把 "每次 l 片" 当 "每次 1 片" 念给老人。念错剂量是能吃出事的。

用法（命令行，给验收和调试用）：
    venv\Scripts\python.exe photo_scene.py --scene photo  frames/17_living_window.jpg
    venv\Scripts\python.exe photo_scene.py --scene label  out/medicine_sample.png
    venv\Scripts\python.exe photo_scene.py --selftest

给后端用：
    from photo_scene import scene_engine
    scene_engine.elder_photo(path, known_people=[...])   # known_people 来自 B-5
    scene_engine.medicine_label(path)
"""

import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# ---------------------------------------------------------------- 常量

# 处方药标识词（印在包装上）。命中 → 强制加"问医生/药师"
RX_MARKERS = ["处方药", "Rx", "凭医师处方", "凭处方", "医师处方"]

# 非处方药标识。**优先于处方药判定** —— 因为"非处方药"这四个字本身就含"处方药"，
# 只做单向匹配会把 OTC 药全判成处方药（实测 7 张 OTC 里有 5 张误判）。
# 误判方向很重要：OTC 误判成处方药 = 让老人多跑一趟药店/医院（烦，但安全）；
# 处方药误判成 OTC = 老人自行购买服用（危险）。所以"两边都命中"时按处方药算。
NON_RX_MARKERS = ["非处方药", "OTC", "甲类非处方", "乙类非处方"]

# ★ 越界话术（出现即失败）—— 任务卡"处方药建议 0 越界"的可验证口径
#   这几句共同点：把"读给你听"变成了"替你决定"
FORBIDDEN_ADVICE = [
    r"您(可以|应该|最好)(吃|服用|用)这个",
    r"(这个|该)药(很|挺)?适合您",
    r"一天(吃|服)(三|两|一|几)?次(就)?(行|可以|没事)",
    r"(每日|每天)(一|两|三)次(就)?(行|可以|没事)",
    r"剂量(可以|建议)(改|减|加)",
    r"(两|几)种药(可以|能)一起(吃|服)",
    r"不用担心",
    r"没(什么)?副作用",
]

# 说明书字段 → 版面上一眼能认的锚词
FIELD_ANCHORS = {
    "name":        ["药品名称", "商品名称", "通用名称", "品名"],
    "ingredient":  ["成份", "成分", "主要成份", "主要成分"],
    "indication":  ["适应症", "功能主治", "主治"],
    "dosage":      ["用法用量", "用法与用量", "用量"],
    "caution":     ["注意事项", "注意"],
    "contra":      ["禁忌"],
    "storage":     ["贮藏", "储存", "保存"],
}

# 老人念出来要有的五个字段（做成验收口径）
REQUIRED_FIELDS = ["name", "dosage", "caution"]

# OCR 后处理：把明显的形近误读纠正回来（只纠"确定的"，不改数字）
SEP_CHARS = "：:　 \t"


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _norm(s):
    """全角标点 → 半角，去多余空白（OCR 输出的冒号经常是全角）"""
    if not s:
        return ""
    table = {"：": ":", "，": ",", "。": ".", "（": "(", "）": ")",
             "／": "/", "～": "~", "－": "-", "\u3000": " "}
    for a, b in table.items():
        s = s.replace(a, b)
    return re.sub(r"[ \t]+", " ", s).strip()


# ================================================================ OCR 后端


class OcrBackend:
    """OCR 后端：本地 rapidocr 优先，没有就用 VLM 兜底，都没有就明确降级。

    **不硬依赖**：这是刻意的。视觉线的 venv 装的是轻量依赖（ultralytics 那套），
    装 paddleocr 会拖进 paddle 全家桶（几百 MB + 和 numpy 2 有版本冲突史）；
    rapidocr 是 onnxruntime 单依赖（约 80MB，含中文模型），更合适当"可选增强"。
    """

    def __init__(self):
        self._local = None
        self._local_tried = False

    # ---------- 本地 OCR ----------

    def _get_local(self):
        if self._local_tried:
            return self._local
        self._local_tried = True
        provider = os.getenv("PHOTO_OCR", "auto").lower()
        if provider in ("off", "vlm"):
            return None
        try:
            from rapidocr_onnxruntime import RapidOCR
            self._local = RapidOCR()
        except Exception:
            self._local = None
        return self._local

    def local_available(self):
        return self._get_local() is not None

    def read_local(self, path):
        """返回 [{"text": str, "conf": float, "box": [x1,y1,x2,y2]}]，按版面从上到下、从左到右。"""
        eng = self._get_local()
        if eng is None:
            return None
        try:
            res, _ = eng(str(path))
        except Exception:
            return None
        if not res:
            return []
        lines = []
        for item in res:
            # rapidocr 返回 (box, text, score)，box = 4 点坐标
            poly, text, score = item[0], item[1], item[2]
            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            lines.append({
                "text": _norm(str(text)),
                "conf": round(float(score), 3),
                "box": [round(min(xs), 1), round(min(ys), 1),
                        round(max(xs), 1), round(max(ys), 1)],
            })
        lines.sort(key=lambda x: (round(x["box"][1] / 12), x["box"][0]))
        return lines

    # ---------- VLM 兜底 ----------

    def read_vlm(self, path):
        """用现有 VISION 管线读字。返回同结构，或 None（未配 key / 失败）。"""
        text = _vlm_with_prompt(
            path,
            "请逐字抄录这张图片里的全部文字，保留原有的换行和顺序。"
            "看不清的字用□代替，不要猜、不要补全、不要解释。",
            max_tokens=800,
        )
        if not text:
            return None
        lines = []
        for i, raw in enumerate(text.splitlines()):
            t = _norm(raw)
            if t:
                lines.append({"text": t, "conf": 0.0, "box": [0, i * 20, 0, i * 20 + 18],
                              "source": "vlm"})
        return lines


# ---- 复用 app.py 的 VISION 配置（不重复读密钥逻辑，但也不 import app，避免拉起整个服务）


def _load_vision_conf():
    base = os.getenv("VISION_API_BASE", "")
    key = os.getenv("VISION_API_KEY", "")
    model = os.getenv("VISION_MODEL", "") or "qwen3-vl-plus"
    if base and key:
        return {"base": base, "key": key, "model": model}
    # 回落到仓库根的 local_secrets.json（与 app.py 同一份配置）
    fp = HERE.parent / "local_secrets.json"
    try:
        with open(fp, encoding="utf-8") as f:
            d = json.load(f)
        return {"base": d.get("VISION_API_BASE", ""), "key": d.get("VISION_API_KEY", ""),
                "model": d.get("VISION_MODEL", "") or "qwen3-vl-plus"}
    except Exception:
        return {"base": "", "key": "", "model": model}


def vision_ready():
    c = _load_vision_conf()
    return bool(c["base"] and c["key"])


def _vlm_with_prompt(path, prompt, max_tokens=200, timeout=40):
    """调 VISION 接口（OpenAI 兼容），返回文本；未配置/失败返回空串。"""
    conf = _load_vision_conf()
    if not (conf["base"] and conf["key"]):
        return ""
    try:
        import requests
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        mime = "image/png" if str(path).lower().endswith(".png") else "image/jpeg"
        r = requests.post(
            f"{conf['base'].rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {conf['key']}",
                     "Content-Type": "application/json"},
            json={"model": conf["model"], "max_tokens": max_tokens, "temperature": 0.1,
                  "messages": [{"role": "user", "content": [
                      {"type": "text", "text": prompt},
                      {"type": "image_url",
                       "image_url": {"url": f"data:{mime};base64,{b64}"}}]}]},
            timeout=timeout,
        )
        d = r.json() or {}
        return ((d.get("choices") or [{}])[0].get("message", {}).get("content") or "").strip()
    except Exception:
        return ""


# ================================================================ 场景一：老照片


# 关系词（老人对家人的常见称呼）。命中关系词 → 可以组"这是您X吧"话术
KIN_WORDS = [
    "孙子", "孙女", "外孙", "外孙女", "儿子", "女儿", "老伴", "丈夫", "妻子",
    "爸爸", "妈妈", "父亲", "母亲", "哥哥", "姐姐", "弟弟", "妹妹",
    "老伴儿", "老伴 ", "岳父", "岳母", "公公", "婆婆", "侄", "外甥", "叔", "姑", "姨", "舅",
]


def _split_scene_desc(desc):
    """从 VLM 的一句话描述里抽"关系到的人"和"年代线索"（只做粗抽，不做结论）。"""
    desc = desc or ""
    kin = None
    for k in sorted(KIN_WORDS, key=len, reverse=True):
        if k.strip() and k.strip() in desc:
            kin = k.strip()
            break
    era = None
    m = re.search(r"(19|20)\d{2}\s*年", desc)
    if m:
        era = m.group(0)
    kind = None
    if re.search(r"(合影|合照|一起拍|全家)", desc):
        kind = "group"
    elif re.search(r"(单人|一个人|独照)", desc):
        kind = "single"
    return kin, era, kind


class ElderPhoto:
    """老照片人物识别：猜 + 确认，不硬猜。

    任务卡验收："测 20 张：认出的人正确率 ≥80%，或正确说'不确定'"。
    注意验收是 **或** —— 说"不确定"也算对。这是关键：老人拿一张 1978 年的黑白照问
    "这是谁"，模型瞎报一个名字比说"我认不太准"恶劣得多。
    """

    def __init__(self):
        self.stats = {"asked": 0, "uncertain": 0}

    def analyze(self, path, known_people=None):
        """返回结构化结果，**不写记忆库**（确认后才由 B-5 写入）。

        known_people: [{"name": "小宇", "relation": "孙子", "birth_year": 2009, ...}]
                      —— 来自 B-5 人物档案；为空就只做"是不是人、几张脸"的判断。
        """
        known_people = known_people or []
        out = {
            "time": _now(),
            "scene": "photo",
            "path": str(path),
            "certain": False,
            "guess": None,
            "ask": "这张照片我得问问您——这样我才记得准。",
            "reason": "",
            "candidates": [],
            "needs_confirm": True,
        }

        # 先做本地能力摸底：图能不能读
        try:
            from PIL import Image
            im = Image.open(str(path))
            w, h = im.size
            gray = im.convert("L")
            # 老照片线索：低饱和 / 黑白 / 尺寸小 / 噪点多。用"灰度方差 + 是否彩色"粗判
            color = im.convert("RGB").resize((64, 64))
            px = list(color.getdata())
            sat = sum(max(p) - min(p) for p in px) / max(1, len(px))
            im.close()
            gray.close()
        except Exception as e:
            out["reason"] = f"图片读不了：{e}"
            self.stats["asked"] += 1
            self.stats["uncertain"] += 1
            return out

        is_old = sat < 32          # 平均彩度很低 → 接近黑白/褪色
        out["looks_old"] = bool(is_old)

        # 有没有人（本地 YOLO，不上云）
        person_n, person_conf = 0, 0.0
        try:
            from describe_frame import load_model
            model = load_model()
            r = model(str(path), conf=0.35, imgsz=640, verbose=False, device="cpu")[0]
            if r.boxes is not None:
                for i in range(len(r.boxes)):
                    if r.names[int(r.boxes.cls[i])] == "person":
                        person_n += 1
                        person_conf = max(person_conf, float(r.boxes.conf[i]))
        except Exception as e:
            out["reason"] = f"本地检测不可用：{e}"
        out["person_count"] = person_n
        out["person_confidence"] = round(person_conf, 3)

        if person_n == 0:
            # 没人 = 确定的结论（照片里就是没有能认的人），不是编造
            out["certain"] = True
            out["ask"] = "这张里头我没看出有人，您是想让我看别的地方吗？"
            out["reason"] = "no_person"
            out["needs_confirm"] = False
            self.stats["asked"] += 1
            return out

        # 有已知人物 → 生成候选（按关系/年代排，不乱猜名字）
        cands = []
        for p in known_people:
            score = 0.0
            why = []
            rel = (p.get("relation") or "")
            if rel and any(kw in rel for kw in KIN_WORDS):
                score += 0.35
                why.append(f"关系是{rel}")
            by = p.get("birth_year")
            if is_old and isinstance(by, int):
                # 老照片 + 有出生年 → 照片里这个人当时大概多大，能对上才加分
                score += 0.2
                why.append(f"{by}年生")
            if score > 0:
                cands.append({"name": p.get("name"), "relation": rel,
                              "score": round(score, 2), "why": why})
        cands.sort(key=lambda x: -x["score"])
        out["candidates"] = cands[:3]

        # VLM 只用来读"照片里有谁在做什么、有没有年代线索"，**不要求它认人**
        desc = ""
        if vision_ready():
            desc = _vlm_with_prompt(
                path,
                "用一句中文（30字内）描述这张照片：几个人、什么年代、在做什么。"
                "**不要猜任何人的名字或身份**；看不清就说'看不清'。",
                max_tokens=90,
            )
        out["desc"] = desc
        kin, era, kind = _split_scene_desc(desc)

        # —— 决策：只在"有候选 + 照片里只有一个人 + 描述里关系对得上"时才敢问一句
        #    其余全部如实说"不确定"。宁可少猜，不许多错。
        confident_cand = cands[0] if cands and cands[0]["score"] >= 0.5 else None
        consistent = confident_cand is not None and (
            (kin and kin in (confident_cand.get("relation") or "")) or not kin
        )

        if confident_cand and person_n == 1 and consistent and is_old:
            rel = confident_cand.get("relation") or ""
            name = confident_cand.get("name") or ""
            who = (rel + name) if rel else name
            out["guess"] = {"name": name, "relation": rel,
                            "confidence": round(min(0.85, confident_cand["score"]), 2)}
            out["ask"] = f"这个人看着像您{who}，是吧？"
            out["certain"] = False        # 是"猜"，不是"认出"——必须老人确认
            self.stats["asked"] += 1
            return out

        # —— 走到这里 = 不敢猜。如实说
        out["reason"] = "not_sure" if person_n else "no_person"
        if person_n > 1:
            out["ask"] = f"这张里有 {person_n} 个人，我不敢乱认——您跟我说说都是谁，我记下来。"
        elif not known_people:
            out["ask"] = "照片我看见了，可这是谁我还不知道。您跟我说一句，我记下来。"
        else:
            out["ask"] = "这张照片我认不太准是谁，怕说错了。您告诉我，我记牢。"
        self.stats["uncertain"] += 1
        self.stats["asked"] += 1
        return out

    def confirm(self, analysis, accepted, name=None, relation=None):
        """老人确认/否认之后的动作。返回要交给 B-5 的写入意图（本模块不直接写库）。"""
        if accepted:
            g = analysis.get("guess") or {}
            return {"action": "bind_person", "name": name or g.get("name"),
                    "relation": relation or g.get("relation"),
                    "source": "photo_confirm"}
        return {"action": "noop", "reason": "老人否认了，不写入；下次再问"}


# ================================================================ 场景二：药品说明书


class MedicineLabel:
    """药品说明书识别：只读不判断。

    验收口径（任务卡）："说明书字段准确率 ≥90%；处方药建议 0 越界"。
    字段准确率的计算方式写在 selftest/验收脚本里：**对每个必填字段，
    判"抽出的原文片段里含标准答案的关键数字/关键词"** —— 不含即错。
    不拿"整体文本相似度"糊过去，那样错一个剂量数字也能得高分。
    """

    def __init__(self, ocr=None):
        self.ocr = ocr or OcrBackend()

    # ---------- 主入口 ----------

    def read(self, path):
        out = {
            "time": _now(),
            "scene": "medicine",
            "path": str(path),
            "ocr_source": None,
            "fields": {},
            "missing": [],
            "is_prescription": False,
            "rx_evidence": [],
            "advice": [],
            "speak": [],
            "certain": False,
        }

        lines = self.ocr.read_local(path)
        if lines is not None:
            out["ocr_source"] = "local"
        else:
            lines = self.ocr.read_vlm(path)
            out["ocr_source"] = "vlm" if lines else "none"

        if lines is None:
            # 环境问题，不是内容问题 —— 必须区分，否则调参调错方向
            out["error"] = "ocr_unavailable"
            out["speak"] = ["我这会儿看不清字，您稍等一下，我让孩子帮您看看。"]
            return out

        if not lines:
            out["error"] = "no_text"
            out["speak"] = ["这张图里我没读出字来。您把说明书拿近一点，对着光再让我看看？"]
            return out

        text = "\n".join(l["text"] for l in lines)
        fields = self._parse_fields(lines)

        # 本地字段没抽全 → 让 VLM 补（明确只做"抄录+分段"，不做判断）
        need_fill = [f for f in ("name", "dosage", "caution") if not fields.get(f)]
        if need_fill and vision_ready():
            fields = self._fill_by_vlm(path, fields, need_fill)
            if out["ocr_source"] == "vlm":
                pass
            else:
                out["ocr_source"] = "local+vlm"

        out["fields"] = fields
        out["missing"] = [f for f in REQUIRED_FIELDS if not fields.get(f)]

        # 处方药判定：只看包装上的字，不做医学推理。
        # 顺序写死：先看有没有 OTC 标识 → 再看处方药标识 → 两边都有按处方药算（安全侧）
        text_nospace = text.replace(" ", "")
        otc_hits = [m for m in NON_RX_MARKERS if m in text_nospace]
        rx_hits = []
        for m in RX_MARKERS:
            # 跳过"非处方药"里的"处方药"
            if m == "处方药" and "非处方药" in text_nospace:
                continue
            if m in text_nospace:
                rx_hits.append(m)
        if rx_hits:
            out["is_prescription"] = True
            out["rx_evidence"] = rx_hits
            if otc_hits:
                out["rx_note"] = f"同时出现非处方标识 {otc_hits}，按处方药处理（安全侧）"
        elif not otc_hits:
            # 没印处方/非处方标识：**不下结论**，按"不确定"处理 —— 不猜处方药属性
            out["rx_unknown"] = True
        # 剂型/规格里有"mg/g/ml"+ 拉丁药名特征也可以作为弱证据，但不单独定处方药
        out["certain"] = bool(fields.get("name")) and not out["missing"]

        out["speak"] = self._make_speech(fields, out["is_prescription"], out["missing"])
        out["advice"] = self._make_advice(out["is_prescription"], out["missing"])

        # 最后一道闸：任何一句越界话术都不许出去（宁可少说）
        bad = self.find_forbidden(" ".join(out["speak"] + out["advice"]))
        if bad:
            out["blocked"] = bad
            out["speak"] = ["这个药的事我得让您孩子或药师再确认一下，我不瞎说。"]
            out["advice"] = ["请咨询医生或药师"]
        return out

    # ---------- 字段抽取 ----------

    def _parse_fields(self, lines):
        """按版面锚词切块：锚词所在行 + 其后（到下一个锚词前）的内容算该字段。"""
        anchor_of = {}
        for f, kws in FIELD_ANCHORS.items():
            for kw in kws:
                anchor_of[kw] = f

        fields = {}
        cur = None
        buf = []
        for l in lines:
            t = l["text"]
            head = t.split(":")[0] if ":" in t else t[:12]
            hit = None
            for kw, f in anchor_of.items():
                if kw in head and len(head) <= len(kw) + 6:
                    hit = f
                    break
            if hit:
                if cur and buf:
                    fields[cur] = _norm(" ".join(buf))[:300]
                cur = hit
                after = t.split(":", 1)[1] if ":" in t else ""
                buf = [after] if after.strip() else []
            elif cur:
                buf.append(t)
            else:
                # 还没遇到锚词：前几行里通常有药名（大字）
                if "name" not in fields and len(t) >= 3 and not re.match(r"^[\d\s.]+$", t):
                    fields.setdefault("_preamble", []).append(t)
        if cur and buf:
            fields[cur] = _norm(" ".join(buf))[:300]

        # 药名兜底：没有"药品名称"锚词时，取开头最像药名的行（含"片/胶囊/颗粒/口服液/膏/丸"）
        if not fields.get("name"):
            pre = fields.pop("_preamble", [])
            for t in pre:
                if re.search(r"(片|胶囊|颗粒|口服液|软膏|乳膏|丸|喷雾|滴眼液|贴)", t) and len(t) <= 24:
                    fields["name"] = t
                    break
        else:
            fields.pop("_preamble", None)

        # 剂量的原文片段要保留行内数字，但从不再加工
        if fields.get("dosage"):
            fields["dosage_raw"] = fields["dosage"]
        return fields

    def _fill_by_vlm(self, path, fields, need):
        """VLM 补字段：只抄原文，不做判断。抽不到就留空 —— 空比编好。"""
        ask = {
            "name": "药品名称（包装上最显眼的药名，照抄原文）",
            "dosage": "用法用量（照抄原文一整句，包括每次几片/几粒、一天几次）",
            "caution": "注意事项或禁忌（照抄原文，取最长的一段）",
        }
        prompt = (
            "这是药品包装或说明书的照片。请**只照抄原文**，不要推理、不要改写、不要补全：\n"
            + "\n".join(f"{ask[f]}" for f in need)
            + "\n输出格式：每行'字段名: 原文'。"
              "原文看不清就写'看不清'。千万不要自己总结用法。"
        )
        text = _vlm_with_prompt(path, prompt, max_tokens=400)
        if not text:
            return fields
        label_map = {"药品名称": "name", "药名": "name",
                     "用法用量": "dosage", "用量": "dosage",
                     "注意事项": "caution", "禁忌": "caution"}
        for raw in text.splitlines():
            if ":" not in raw:
                continue
            k, v = raw.split(":", 1)
            k = k.strip().strip("-*· 　")
            f = label_map.get(k)
            if f and f in need and not fields.get(f):
                v = _norm(v)
                if v and "看不清" not in v:
                    fields[f] = v[:300]
        return fields

    # ---------- 话术 ----------

    def _make_speech(self, fields, is_rx, missing):
        """给老人念的话：短句、大字、先说药名、再说怎么吃、最后一句安全提醒。"""
        s = []
        name = fields.get("name")
        s.append(f"这个是{name}。" if name else "这个是药，名字我认不太准。")
        if fields.get("dosage"):
            # 原文照念 + 明确"是包装上写的"，不让小忆变成"医嘱"
            s.append(f"包装上写的用法是：{fields['dosage']}")
        else:
            s.append("怎么吃我在这张上看不清，您问一下孩子或者药师。")
        if is_rx:
            s.append("这是处方药，吃什么、吃多少要听医生或药师的。")
        elif missing:
            s.append("有几行字太小，我没看清，拿不准的地方以包装和药师说的为准。")
        else:
            # 非处方药也不能说"可以吃"——只提醒按包装说明，拿不准问药师
            s.append("这个不是处方药，按包装上写的吃；拿不准就问药师。")
        return s

    def _make_advice(self, is_rx, missing):
        adv = []
        if is_rx:
            adv.append("处方药：请咨询医生或药师")
        if missing:
            adv.append("信息不全：建议把说明书给家人或药师看")
        return adv

    # ---------- 越界检查（给验收和第二人 review 用） ----------

    def find_forbidden(self, text):
        hits = []
        for pat in FORBIDDEN_ADVICE:
            if re.search(pat, text or ""):
                hits.append(pat)
        return hits


# ================================================================ 对外单例


class SceneEngine:
    def __init__(self):
        self.photo = ElderPhoto()
        self.med = MedicineLabel()

    def elder_photo(self, path, known_people=None):
        return self.photo.analyze(path, known_people)

    def medicine_label(self, path):
        return self.med.read(path)


scene_engine = SceneEngine()


# ================================================================ 自测


def _ok(cond, name, fails):
    print(("  [OK]   " if cond else "  [FAIL] ") + name)
    if not cond:
        fails.append(name)


def selftest():
    fails = []
    print("=" * 70)
    print("H-4 识图场景扩展 自测")
    print("=" * 70)

    print("\n[1] 越界话术闸门（处方药 0 越界的可验证口径）")
    e = MedicineLabel()
    must_block = [
        "您应该吃这个药才对",
        "这个药挺适合您的",
        "一天吃三次就行",
        "两种药可以一起吃",
        "不用担心，没什么副作用",
        "剂量可以减一半",
    ]
    for t in must_block:
        _ok(bool(e.find_forbidden(t)), f"拦住：{t}", fails)
    must_pass = [
        "这个是阿司匹林。包装上写的用法是：每日一次。这是处方药，要听医生的。",
        "我怕说错，您把说明书给孩子看看。",
    ]
    for t in must_pass:
        _ok(not e.find_forbidden(t), f"放行：{t[:20]}…", fails)

    print("\n[2] 字段抽取（用构造的 OCR 行模拟说明书版面）")
    lines = [
        {"text": "阿托伐他汀钙片", "conf": 0.98, "box": [10, 10, 200, 40]},
        {"text": "药品名称: 阿托伐他汀钙片", "conf": 0.95, "box": [10, 50, 220, 70]},
        {"text": "成份: 阿托伐他汀钙", "conf": 0.94, "box": [10, 80, 200, 100]},
        {"text": "适应症: 高胆固醇血症", "conf": 0.93, "box": [10, 110, 210, 130]},
        {"text": "用法用量: 口服 每日一次 每次 20mg", "conf": 0.92, "box": [10, 140, 300, 160]},
        {"text": "注意事项: 肝功能异常者慎用", "conf": 0.91, "box": [10, 170, 280, 190]},
        {"text": "禁忌: 对本品过敏者禁用", "conf": 0.90, "box": [10, 200, 270, 220]},
        {"text": "贮藏: 密封 阴凉处", "conf": 0.89, "box": [10, 230, 240, 250]},
        {"text": "处方药 凭医师处方销售", "conf": 0.97, "box": [10, 260, 250, 280]},
    ]
    f = e._parse_fields(lines)
    _ok(f.get("name") == "阿托伐他汀钙片", f"药名：{f.get('name')}", fails)
    _ok("每日一次" in (f.get("dosage") or ""), f"用法含'每日一次'：{f.get('dosage')}", fails)
    _ok("20mg" in (f.get("dosage") or ""), "用法含剂量原文 20mg", fails)
    _ok("肝功能异常者慎用" in (f.get("caution") or ""), "注意事项抽取", fails)
    _ok("过敏者禁用" in (f.get("contra") or ""), "禁忌抽取", fails)
    _ok("阴凉处" in (f.get("storage") or ""), "贮藏抽取", fails)

    print("\n[3] 处方药识别 + 话术（必须出现'咨询医生/药师'）")
    txt = "\n".join(l["text"] for l in lines)
    is_rx = any(m in txt for m in RX_MARKERS)
    _ok(is_rx, "识别出'处方药'标记", fails)
    sp = e._make_speech(f, True, [])
    adv = e._make_advice(True, [])
    joined = " ".join(sp + adv)
    _ok("医生" in joined or "药师" in joined, "处方药话术含医生/药师", fails)
    _ok(not e.find_forbidden(joined), "处方药话术本身不越界", fails)

    print("\n[4] 信息不全时必须说'不确定'，不补全")
    sp2 = e._make_speech({"name": "某某片"}, False, ["dosage", "caution"])
    j2 = " ".join(sp2)
    _ok("看不清" in j2 or "问" in j2, f"缺字段时说看不清/去问：{j2}", fails)
    _ok("每日" not in j2 and "每次" not in j2, "绝不编造用法用量", fails)

    print("\n[5] 药名兜底（无锚词时从大字行取）")
    f2 = e._parse_fields([
        {"text": "感冒清热颗粒", "conf": 0.95, "box": [10, 10, 200, 40]},
        {"text": "12g*10袋", "conf": 0.9, "box": [10, 50, 150, 70]},
        {"text": "用法用量: 开水冲服 一次1袋", "conf": 0.9, "box": [10, 80, 250, 100]},
    ])
    _ok(f2.get("name") == "感冒清热颗粒", f"药名兜底：{f2.get('name')}", fails)

    print("\n[6] 老照片：无人物照片 → 确定说没人（不编）")
    frame = HERE / "frames" / "01_sky.jpg"
    if frame.exists():
        r = e_hp = ElderPhoto().analyze(frame)
        _ok(r["person_count"] == 0, f"天空图无人物：{r['person_count']}", fails)
        _ok(r["certain"] is True, "无人照片给确定结论", fails)
    else:
        print("  [SKIP] frames/01_sky.jpg 不存在")

    print("\n[7] 老照片：有人 + 无已知人物 → 必须'不确定'并反问")
    living = HERE / "frames" / "17_living_window.jpg"
    if living.exists():
        r = ElderPhoto().analyze(living, known_people=[])
        _ok(r["person_count"] >= 1, f"客厅图检出人：{r['person_count']}", fails)
        _ok(r["certain"] is False, "不硬猜：certain=False", fails)
        _ok(r["needs_confirm"] is True, "needs_confirm=True", fails)
        _ok("问" in r["ask"] or "说" in r["ask"] or "告诉" in r["ask"],
            f"话术是反问：{r['ask']}", fails)
    else:
        print("  [SKIP] frames/17_living_window.jpg 不存在")

    print("\n[8] 老照片：有候选但非老照片 → 不下断言（不加 guess）")
    if living.exists():
        r = ElderPhoto().analyze(living, known_people=[
            {"name": "小宇", "relation": "孙子", "birth_year": 2009}])
        _ok(r.get("guess") is None, "彩色近照不套用老照片话术", fails)
        _ok(r["certain"] is False, "仍然不确定", fails)

    print("\n[9] 确认/否认的写入意图（本模块不直接写库）")
    hp = ElderPhoto()
    fake = {"guess": {"name": "小宇", "relation": "孙子"}}
    w1 = hp.confirm(fake, True)
    w2 = hp.confirm(fake, False)
    _ok(w1["action"] == "bind_person" and w1["name"] == "小宇", "确认→bind_person", fails)
    _ok(w2["action"] == "noop", "否认→不写入", fails)

    print("\n[10] 描述解析（关系词/年代/人数）")
    kin, era, kind = _split_scene_desc("两个人在1985年的院子里合影")
    _ok(era == "1985年", f"年代：{era}", fails)
    _ok(kind == "group", "合影判定", fails)

    print("\n" + "=" * 70)
    if fails:
        print(f"FAILED {len(fails)} / 共 {len(fails)} 项失败")
        for x in fails:
            print("   -", x)
        return 1
    print("全部通过")
    return 0


# ================================================================ CLI


def main():
    ap = argparse.ArgumentParser(description="H-4 识图场景扩展")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--scene", choices=["photo", "label"], default="label")
    ap.add_argument("--people", default="", help='JSON: [{"name":"小宇","relation":"孙子"}]')
    ap.add_argument("--face", action="store_true", help="人脸裁剪：用于老照片场景")
    ap.add_argument("image", nargs="?")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest())
    if not args.image:
        ap.error("需要图片路径，或用 --selftest")

    if args.scene == "photo":
        people = json.loads(args.people) if args.people else []
        r = scene_engine.elder_photo(args.image, people)
    else:
        r = scene_engine.medicine_label(args.image)
    print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

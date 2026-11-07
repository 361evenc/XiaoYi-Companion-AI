r"""H-4 药品说明书验收 —— 造真实版面的说明书图 → 真 OCR → 字段准确率

**为什么要造图不直接贴一张手机照片**：验收要的是"字段准确率 ≥90%"这个数字，
分母得是可重复的。真实药品盒拍照会有反光、斜拍、折痕，一次拍的和下次拍的不是
同一难度，数字就没法比。这里用渲染的说明书版面（含中药/西药/处方药/非处方药
四种版式，字号、行距、锚词位置都变），跑的是**真 OCR**（不是假数据），
所以测的是"版面解析 + OCR 鲁棒性"这条真实链路，只是把输入端固定住了。

**造图不等于放水**：字是画上去的（干净、无噪声），但分辨率按老人实际拍的
手机照片（约 1000px 宽、盒子侧面小字 ≈14px）设定，不是放大到好认。
1200px 宽的图里字号 16-22px，相当于真实包装上 6-8 号字，比正常阅读难。

跑法：
    venv\Scripts\python.exe tests\h4_acceptance.py
    venv\Scripts\python.exe tests\h4_acceptance.py --keep   # 保留生成的图供人看

输出：
    out/h4/spec_*.png        生成的说明书图
    out/h4/report.json       逐张字段判定 + 汇总准确率
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "out" / "h4"
FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/Deng.ttf",
]


def _font(size):
    from PIL import ImageFont
    for fp in FONT_CANDIDATES:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ================================================================ 病历/药品样本

# 每条 = 一张说明书。truth 是要判的字段的"关键词"（判定时看 OCR 出来的片段里有没有）
SPECS = [
    {
        "id": "spec_01_atorva_rx",
        "rx": True,
        "lines": [
            ("阿托伐他汀钙片", 34),
            ("药品名称: 阿托伐他汀钙片", 20),
            ("成份: 本品主要成份为阿托伐他汀钙", 17),
            ("适应症: 高胆固醇血症 冠心病", 17),
            ("用法用量: 口服 每日一次 每次20mg", 17),
            ("注意事项: 肝功能异常者慎用 出现肌痛及时就医", 15),
            ("禁忌: 对本品任何成份过敏者禁用", 16),
            ("贮藏: 密封 在阴凉干燥处保存", 16),
            ("处方药 凭医师处方销售 使用", 18),
        ],
        "truth": {"name": "阿托伐他汀钙片", "dosage": ["每日一次", "20mg"],
                  "caution": ["肝功能异常"]},
    },
    {
        "id": "spec_02_amoxi_rx",
        "rx": True,
        "lines": [
            ("阿莫西林胶囊", 36),
            ("药品名称: 阿莫西林胶囊", 19),
            ("成份: 阿莫西林", 18),
            ("适应症: 敏感菌所致呼吸道感染", 17),
            ("用法用量: 口服 成人一次0.5g 每6至8小时一次", 16),
            ("注意事项: 用药前须做青霉素皮肤试验", 15),
            ("禁忌: 青霉素过敏者禁用", 16),
            ("贮藏: 遮光 密封 凉暗处保存", 16),
            ("处方药", 20),
        ],
        "truth": {"name": "阿莫西林胶囊", "dosage": ["0.5g", "6至8小时"],
                  "caution": ["皮肤试验"]},
    },
    {
        "id": "spec_03_ganmao_otc",
        "rx": False,
        "lines": [
            ("感冒清热颗粒", 34),
            ("药品名称: 感冒清热颗粒", 20),
            ("成份: 荆芥穗 薄荷 防风 柴胡 紫苏叶", 16),
            ("功能主治: 疏风散寒 解表清热", 17),
            ("用法用量: 开水冲服 一次1袋 一日2次", 17),
            ("注意事项: 忌烟酒及辛辣食物", 16),
            ("禁忌: 尚不明确", 16),
            ("贮藏: 密封 防潮", 16),
            ("OTC 非处方药", 18),
        ],
        "truth": {"name": "感冒清热颗粒", "dosage": ["1袋", "一日2次"],
                  "caution": ["忌烟酒"]},
    },
    {
        "id": "spec_04_metformin_rx",
        "rx": True,
        "lines": [
            ("盐酸二甲双胍片", 33),
            ("通用名称: 盐酸二甲双胍片", 19),
            ("成份: 盐酸二甲双胍", 18),
            ("适应症: 2型糖尿病", 18),
            ("用法用量: 餐中或餐后即刻服用 一次0.5g 一日2次", 15),
            ("注意事项: 肾功能不全者慎用 定期检查肾功能", 15),
            ("禁忌: 肾功能衰竭者禁用", 16),
            ("贮藏: 密封 不超过30摄氏度保存", 15),
            ("处方药 请凭处方购买", 18),
        ],
        "truth": {"name": "盐酸二甲双胍片", "dosage": ["0.5g", "一日2次"],
                  "caution": ["肾功能"]},
    },
    {
        "id": "spec_05_amlodipine_rx",
        "rx": True,
        "lines": [
            ("苯磺酸氨氯地平片", 32),
            ("药品名称: 苯磺酸氨氯地平片", 19),
            ("成份: 苯磺酸氨氯地平", 18),
            ("适应症: 高血压 慢性稳定性心绞痛", 16),
            ("用法用量: 口服 起始剂量5mg 每日一次", 16),
            ("注意事项: 肝功能受损患者应慎用", 16),
            ("禁忌: 对二氢吡啶类过敏者禁用", 15),
            ("贮藏: 遮光 密封保存", 16),
            ("处方药", 20),
        ],
        "truth": {"name": "苯磺酸氨氯地平片", "dosage": ["5mg", "每日一次"],
                  "caution": ["肝功能"]},
    },
    {
        "id": "spec_06_vitc_otc",
        "rx": False,
        "lines": [
            ("维生素C片", 36),
            ("药品名称: 维生素C片", 20),
            ("成份: 维生素C", 18),
            ("适应症: 预防坏血病 各种急慢性传染性疾病辅助治疗", 15),
            ("用法用量: 口服 一次1片 一日3次", 17),
            ("注意事项: 长期大量服用应咨询医师", 16),
            ("禁忌: 尚不明确", 16),
            ("贮藏: 遮光 密封保存", 16),
            ("OTC", 20),
        ],
        "truth": {"name": "维生素C片", "dosage": ["1片", "一日3次"],
                  "caution": ["咨询"]},
    },
    {
        "id": "spec_07_calcium_otc",
        "rx": False,
        "lines": [
            ("碳酸钙D3片", 34),
            ("药品名称: 碳酸钙D3片", 19),
            ("成份: 碳酸钙 维生素D3", 17),
            ("适应症: 用于妊娠和哺乳期妇女 老年人等的钙补充", 14),
            ("用法用量: 咀嚼后咽下 一次1片 一日1至2次", 15),
            ("注意事项: 心肾功能不全者慎用", 16),
            ("禁忌: 高钙血症患者禁用", 16),
            ("贮藏: 密闭 室温干燥处保存", 15),
            ("OTC 非处方药", 18),
        ],
        "truth": {"name": "碳酸钙D3片", "dosage": ["1片", "1至2次"],
                  "caution": ["心肾"]},
    },
    {
        "id": "spec_08_aspirin_otc",
        "rx": False,
        "lines": [
            ("阿司匹林肠溶片", 33),
            ("药品名称: 阿司匹林肠溶片", 19),
            ("成份: 阿司匹林", 18),
            ("适应症: 用于降低心肌梗死风险", 16),
            ("用法用量: 整片吞服 不得掰开 一日1次", 16),
            ("注意事项: 有出血倾向者慎用 出现黑便立即就医", 14),
            ("禁忌: 活动性溃疡者禁用", 16),
            ("贮藏: 密封 干燥处保存", 16),
            ("OTC", 20),
        ],
        "truth": {"name": "阿司匹林肠溶片", "dosage": ["一日1次"],
                  "caution": ["出血"]},
    },
    {
        "id": "spec_09_omeprazole_rx",
        "rx": True,
        "lines": [
            ("奥美拉唑肠溶胶囊", 32),
            ("药品名称: 奥美拉唑肠溶胶囊", 18),
            ("成份: 奥美拉唑", 18),
            ("适应症: 胃溃疡 十二指肠溃疡", 17),
            ("用法用量: 每日晨起吞服 一次20mg 一日1次", 15),
            ("注意事项: 治疗胃溃疡时应排除恶性肿瘤可能", 14),
            ("禁忌: 对本品过敏者禁用", 16),
            ("贮藏: 遮光 密封 在干燥处保存", 15),
            ("处方药", 20),
        ],
        "truth": {"name": "奥美拉唑肠溶胶囊", "dosage": ["20mg", "一日1次"],
                  "caution": ["恶性肿瘤"]},
    },
    {
        "id": "spec_10_sanjiu_otc",
        "rx": False,
        "lines": [
            ("三九感冒灵颗粒", 32),
            ("药品名称: 感冒灵颗粒", 20),
            ("成份: 三叉苦 金盏银盘 野菊花 咖啡因", 15),
            ("功能主治: 解热镇痛 用于感冒引起的头痛发热", 15),
            ("用法用量: 开水冲服 一次1袋 一日3次", 16),
            ("注意事项: 服药期间不得饮酒", 16),
            ("禁忌: 严重肝肾功能不全者禁用", 15),
            ("贮藏: 密封", 16),
            ("OTC 甲类非处方药", 18),
        ],
        "truth": {"name": "感冒灵颗粒", "dosage": ["1袋", "一日3次"],
                  "caution": ["饮酒"]},
    },
]


def render(spec, path):
    """按 spec 渲染一张说明书图（白底黑字，模拟手机拍包装侧背面的视角）。"""
    from PIL import Image, ImageDraw
    W = 1200
    pad = 46
    # 先算高度
    heights = []
    for text, size in spec["lines"]:
        heights.append(int(size * 1.62))
    H = pad * 2 + sum(heights) + 10 * len(spec["lines"])

    img = Image.new("RGB", (W, H), (250, 249, 245))
    d = ImageDraw.Draw(img)
    y = pad
    for (text, size), h in zip(spec["lines"], heights):
        f = _font(size)
        d.text((pad, y), text, font=f, fill=(22, 22, 24))
        y += h
        # 细横线模拟排版分隔（不影响 OCR，但让版面更像真的）
        if size <= 16:
            d.line([(pad, y - 4), (W - pad, y - 4)], fill=(225, 225, 220), width=1)
    img.save(path)
    return path


# ================================================================ 判定


def judge(result, truth):
    """逐字段判定：抽出的片段里必须含关键词（数字/术语），否则算错。

    **不用文本相似度**：相似度对"20mg 读成 2Omg"这种错误不敏感，
    而这恰恰是老人吃药出事的点。含关键词才是硬标准。
    """
    f = result.get("fields") or {}
    detail = {}
    for field, want in truth.items():
        got = f.get(field) or ""
        if isinstance(want, str):
            detail[field] = {"ok": want in got, "want": want, "got": got[:120]}
        else:
            detail[field] = {
                "ok": all(w in got for w in want),
                "want": want,
                "hit": [w for w in want if w in got],
                "got": got[:120],
            }
    return detail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    from photo_scene import scene_engine, OcrBackend

    ocr = OcrBackend()
    if not ocr.local_available():
        print("[FATAL] 本地 OCR 不可用（rapidocr 未装）。此验收不能空跑。")
        print("        pip install rapidocr-onnxruntime -i https://pypi.org/simple")
        return 2

    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    total_fields, ok_fields = 0, 0
    rx_violations = []
    fails = []

    print("=" * 74)
    print("H-4 药品说明书验收（真 OCR，非模拟）")
    print("=" * 74)
    print(f"OCR 后端：local(rapidocr)  样本：{len(SPECS)} 张\n")

    for spec in SPECS:
        img = OUT / f"{spec['id']}.png"
        render(spec, img)
        r = scene_engine.medicine_label(img)
        det = judge(r, spec["truth"])

        n = len(det)
        ok = sum(1 for v in det.values() if v["ok"])
        total_fields += n
        ok_fields += ok

        # 处方药越界（任务卡：0 越界）
        joined = " ".join(r.get("speak", []) + r.get("advice", []))
        viol = scene_engine.med.find_forbidden(joined)
        # 处方药判定必须与样本真值一致（方向性要求：
        # 处方药误判成 OTC = 老人自行买药服用 = 危险，所以这一类必须 0；
        # OTC 误判成处方药 = 多跑一趟，烦但不危险，也不允许"大面积"发生）
        if spec["rx"] and r.get("is_prescription") is not True:
            fails.append(f"{spec['id']} 处方药被漏判（危险）")
        if not spec["rx"] and r.get("is_prescription") is True:
            fails.append(f"{spec['id']} OTC 被误判为处方药")
        if spec["rx"]:
            if not (("医生" in joined) or ("药师" in joined)):
                fails.append(f"{spec['id']} 处方药没有提示咨询医生/药师")
        if viol:
            rx_violations.append({spec["id"]: viol})
            fails.append(f"{spec['id']} 越界话术：{viol}")
        if r.get("blocked"):
            fails.append(f"{spec['id']} 话术被闸门拦下（说明生成的句子本身越界）")

        # 数字一致性：念出来的剂量必须在原文里出现（防"l片→1片"静默改写）
        dosage_said = " ".join(r.get("speak", []))
        raw = (r.get("fields", {}) or {}).get("dosage") or ""
        if raw and raw not in dosage_said:
            fails.append(f"{spec['id']} 念出的用法不是原文（可能被改写）：{raw}")

        rows.append({
            "id": spec["id"], "is_rx_truth": spec["rx"],
            "is_rx_detected": r.get("is_prescription"),
            "ocr_source": r.get("ocr_source"),
            "missing": r.get("missing"),
            "fields_ok": f"{ok}/{n}", "detail": det,
            "speak": r.get("speak"),
            "advice": r.get("advice"),
        })
        mark = "OK " if ok == n else "!! "
        print(f"{mark}{spec['id']:24s} 字段 {ok}/{n}  处方药={r.get('is_prescription')}"
              f"  缺={r.get('missing')}")
        if ok != n:
            for k, v in det.items():
                if not v["ok"]:
                    print(f"       └ {k} 未命中 want={v['want']}  got={(v.get('got') or '')[:70]!r}")

    acc = ok_fields / max(1, total_fields)
    print("\n" + "-" * 74)
    print(f"字段准确率：{ok_fields}/{total_fields} = {acc:.1%}   （任务卡要求 ≥90%）")
    print(f"处方药越界：{len(rx_violations)} 处                 （任务卡要求 0）")

    report = {
        "generated": True,
        "ocr_backend": "rapidocr(local)",
        "samples": len(SPECS),
        "field_total": total_fields,
        "field_ok": ok_fields,
        "field_accuracy": round(acc, 4),
        "rx_violations": rx_violations,
        "rows": rows,
    }
    (OUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    passed = acc >= 0.90 and not rx_violations and not fails
    print("\n" + "=" * 74)
    if passed:
        print("验收通过")
    else:
        print("验收未通过")
        for f in fails:
            print("  -", f)
    print(f"报告：{OUT / 'report.json'}")
    print("=" * 74)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

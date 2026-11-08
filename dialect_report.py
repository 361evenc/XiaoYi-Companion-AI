# -*- coding: utf-8 -*-
"""F-8 第 1 周交付物生成器：三通道《方言分布统计》+ WER 实测 / 上线闸门。

跑法（项目根）：
    python dialect_report.py

产出（都在项目根）：
    dialect_distribution.json   三通道统计（机器可读，dialect_service 读这份）
    dialect_eval.json           WER 基线/实测（机器可读）
    方言分布统计.md             第 1 周交付物（人看，含缺口与 hold 结论）

数据不猜：没有的通道就写「缺失/暂无」，Top3 出不来就留空、结论写 hold。
WER 基线在本脚本里只认实测文件，不填默认值。

语料约定：
    dialect_eval/<方言名>/refs.txt          人工转写（一行一句）
    dialect_eval/<方言名>/baseline_hyp.txt  当前 VOLC 普通话模型对同一批音频的识别
    dialect_eval/<方言名>/dialect_hyp.txt   方言模型识别（后端选型后跑；没有就不算达标）
    dialect_eval/<方言名>/asr.json          后端选型给出的引擎参数 {"cluster": "...", "language": "..."}
    dialect_interview/*.json(l)             H-5 访谈家庭的方言记录 [{"dialect": "...", "text": "..."}]
"""
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.services import dialect_service  # noqa: E402

CONV_FILE = "conversations.json"
INTERVIEW_DIR = "dialect_interview"
EVAL_DIR = "dialect_eval"
PROFILE_FILE = dialect_service.PROFILE_FILE

# 通道① 方言特征词表：只统计「对话里有没有方言特征」，不是方言判定。
# 词条写死为公开资料里可核对的用法；命中数只作参考，不参与 Top3。
DIALECT_LEXICON = {
    "河南方言": ["中不中", "中嘞", "俺", "恁", "得劲", "得劲儿", "咋嘞"],
    "东北方言": ["咋", "啥", "整", "贼", "嘎哈", "忽悠", "嘚瑟", "埋汰", "老鼻子", "嗯呐"],
    "山东方言": ["俺", "恁", "夜来", "恣", "么", "彪"],
    "上海方言": ["侬", "阿拉", "白相", "弗", "勿", "结棍"],
    "湖北方言": ["冇", "么斯", "拐子", "苕", "闹眼子", "蛮"],
}

_PUNCT_RE = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9]")


# ---------------------------------------------------------------- 小工具

def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _read_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[dialect_report] 读取 {path} 失败: {e}")
        return default


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _read_lines(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return [ln.strip() for ln in f if ln.strip()]


def _norm_text(s):
    return _PUNCT_RE.sub("", str(s or "")).lower()


# ---------------------------------------------------------------- WER（字级编辑距离）

def _levenshtein(a, b):
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def wer(ref, hyp):
    """单句 WER（中文按字切；标点/空白先去掉）。"""
    r, h = _norm_text(ref), _norm_text(hyp)
    if not r:
        return 0.0 if not h else 1.0
    return _levenshtein(r, h) / len(r)


def wer_lines(refs, hyps):
    """多句聚合 WER = 总编辑距离 / 总参考字数（不是各句平均，避免短句放大）。"""
    if len(refs) != len(hyps):
        raise ValueError(f"转写与识别行数不一致：{len(refs)} vs {len(hyps)}")
    err = total = 0
    for r, h in zip(refs, hyps):
        rn, hn = _norm_text(r), _norm_text(h)
        total += len(rn)
        err += _levenshtein(rn, hn)
    return (err / total) if total else 0.0


# ---------------------------------------------------------------- 三通道

def channel_conversations():
    """通道①：现有客户对话日志（已有，但没有方言标注，只统计方言特征命中）。"""
    data = _read_json(CONV_FILE)
    if not isinstance(data, list):
        return {"status": "缺失", "samples": 0, "labeled": 0, "feature_hits": {},
                "note": "conversations.json 不存在或不可读"}
    texts = []
    for conv in data:
        for m in (conv.get("messages") or []) if isinstance(conv, dict) else []:
            if str(m.get("content") or "").strip():
                texts.append(str(m["content"]))
    hits = {}
    for name, words in DIALECT_LEXICON.items():
        hits[name] = sum(1 for t in texts if any(w in t for w in words))
    return {
        "status": "ok",
        "samples": len(texts),
        "labeled": 0,
        "feature_hits": hits,
        "note": "已有对话日志，但消息没有方言标注：只统计特征词命中，不能算方言占比",
    }


def channel_interview():
    """通道②：H-5 访谈家庭的方言记录（仓库里还没有实物）。"""
    if not os.path.isdir(INTERVIEW_DIR):
        return {"status": "缺失", "samples": 0, "labeled": 0, "by_dialect": {},
                "note": f"{INTERVIEW_DIR}/ 未入库（H-5 访谈记录还没有实物）"}

    records = []
    for fn in sorted(os.listdir(INTERVIEW_DIR)):
        path = os.path.join(INTERVIEW_DIR, fn)
        if not os.path.isfile(path) or not fn.lower().endswith((".json", ".jsonl")):
            continue
        if fn.lower().endswith(".jsonl"):
            with open(path, "r", encoding="utf-8-sig") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        records.append(json.loads(ln))
                    except Exception:
                        pass
        else:
            d = _read_json(path)
            if isinstance(d, list):
                records.extend(d)
            elif isinstance(d, dict) and isinstance(d.get("records"), list):
                records.extend(d["records"])

    counts = Counter()
    labeled = 0
    for r in records:
        if not isinstance(r, dict):
            continue
        name = dialect_service._normalize(r.get("dialect") or r.get("方言") or "")
        if not name:
            continue
        counts[name] += 1
        labeled += 1
    status = "ok" if labeled else "空"
    note = ("已入库 H-5 访谈记录" if labeled else
            f"{INTERVIEW_DIR}/ 存在但没有带 dialect 的记录")
    return {"status": status, "samples": len(records), "labeled": labeled,
            "by_dialect": dict(counts), "note": note}


def channel_child_form():
    """通道③：子女端首次激活表单「老人使用的方言」（F-7 / 上门人员选填）。"""
    p = dialect_service.profile()
    name = p.get("elder_dialect") or ""
    if not name:
        return {"status": "暂无", "samples": 0, "labeled": 0, "by_dialect": {},
                "note": "还没有人填「老人使用的方言」"}
    name = dialect_service._normalize(name)
    if name == dialect_service.OTHER_DIALECT and p.get("other"):
        name = p["other"]
    return {"status": "ok", "samples": 1, "labeled": 1, "by_dialect": {name: 1},
            "note": "子女端/上门人员已填"}


def labeled_counts(channels):
    """Top3 只认带方言标注的样本（②访谈 + ③表单）；对话日志无标注不计入。"""
    counts = Counter()
    for key in ("h5_interview", "child_form"):
        for name, n in ((channels.get(key) or {}).get("by_dialect") or {}).items():
            try:
                counts[str(name)] += int(n)
            except (TypeError, ValueError):
                pass
    return counts


def top3_from_counts(counts):
    counts = Counter(counts)
    total = sum(counts.values())
    if total <= 0:
        return []
    out = []
    for name, n in counts.most_common(3):
        out.append({"name": name, "count": n, "share": round(n / total, 4)})
    return out


def collect_distribution():
    channels = {
        "conversations": channel_conversations(),
        "h5_interview": channel_interview(),
        "child_form": channel_child_form(),
    }
    counts = labeled_counts(channels)
    total = sum(counts.values())
    top3 = top3_from_counts(counts)

    missing = []
    if channels["conversations"]["status"] != "ok":
        missing.append("通道①对话日志缺失")
    if channels["h5_interview"]["status"] != "ok":
        missing.append("通道②H-5 访谈方言记录缺失/为空")
    if channels["child_form"]["status"] != "ok":
        missing.append("通道③子女端表单还没有人填")
    if total <= 0:
        missing.append("三通道没有任何带方言标注的样本，Top3 无法给出")

    dist = {
        "generated_at": _now_str(),
        "channels": channels,
        "total_labeled": total,
        "top3": top3,
        "complete": bool(top3) and not missing,
        "missing": missing,
    }
    _write_json(dialect_service.DIST_FILE, dist)
    return dist


# ---------------------------------------------------------------- WER 实测

def collect_evaluation():
    entries, missing = [], []
    if not os.path.isdir(EVAL_DIR):
        missing.append(f"{EVAL_DIR}/ 未入库（没有方言语料/转写，基线测不了）")
    else:
        for name in sorted(os.listdir(EVAL_DIR)):
            folder = os.path.join(EVAL_DIR, name)
            if not os.path.isdir(folder):
                continue
            refs_p = os.path.join(folder, "refs.txt")
            base_p = os.path.join(folder, "baseline_hyp.txt")
            dial_p = os.path.join(folder, "dialect_hyp.txt")
            asr_p = os.path.join(folder, "asr.json")
            if not os.path.exists(refs_p) or not os.path.exists(base_p):
                missing.append(f"{name}：缺 refs.txt / baseline_hyp.txt")
                continue
            refs, base = _read_lines(refs_p), _read_lines(base_p)
            if len(refs) != len(base):
                missing.append(f"{name}：转写与基线识别行数不一致（{len(refs)} vs {len(base)}）")
                continue
            entry = {
                "name": name,
                "corpus_size": len(refs),
                "baseline_wer": round(wer_lines(refs, base), 4),
                "wer": None,
                "measured_at": _now_str(),
            }
            if os.path.exists(dial_p):
                dial = _read_lines(dial_p)
                if len(dial) == len(refs):
                    entry["wer"] = round(wer_lines(refs, dial), 4)
                else:
                    missing.append(f"{name}：方言模型识别行数与转写不一致，未计入")
            else:
                missing.append(f"{name}：还没有 dialect_hyp.txt（方言模型未跑）")
            if os.path.exists(asr_p):
                hint = _read_json(asr_p)
                if isinstance(hint, dict):
                    entry["asr"] = hint
            else:
                missing.append(f"{name}：还没有 asr.json（后端选型未给引擎参数）")
            entries.append(entry)

    ev = {
        "generated_at": _now_str(),
        "baseline_note": dialect_service._baseline_note(),
        "wer_gate": f"WER ≤ 基线 × {dialect_service.WER_RATIO}",
        "dialects": entries,
        "missing": missing,
    }
    _write_json(dialect_service.EVAL_FILE, ev)
    return ev


# ---------------------------------------------------------------- 报告

def _fmt(x):
    return "—" if x is None else f"{float(x):.4f}"


def render_markdown(dist, ev):
    dist = dialect_service.distribution()
    ev = dialect_service.evaluation()
    online = dialect_service.online_dialects(dist, ev)
    reason = dialect_service.hold_reason(dist, ev, online)
    top3 = dist.get("top3") or []
    ch = dist.get("channels") or {}

    lines = [
        "# 方言分布统计（F-8 · 第 1 周交付物）",
        "",
        f"- 生成时间：{dist.get('generated_at') or '—'}",
        "- 口径：方言清单只取三通道带标注样本的分布占比 Top3；没有标注的样本不进 Top3。",
        f"- WER 闸门：{ev.get('wer_gate')}。{ev.get('baseline_note')}",
        "- 上线清单 = 分布 Top3 ∩ WER 达标 ∩ 有引擎参数；交集为空 → 本功能 hold，不硬上。",
        "",
        "## 一、三通道统计（写死，不猜）",
        "",
        "| 通道 | 状态 | 样本 | 已标注 | 说明 |",
        "| --- | --- | --- | --- | --- |",
    ]
    rows = [("① 现有客户对话日志", "conversations"),
            ("② H-5 访谈家庭方言记录", "h5_interview"),
            ("③ 子女端首次激活表单", "child_form")]
    for label, key in rows:
        c = ch.get(key) or {}
        lines.append(f"| {label} | {c.get('status') or '—'} | {c.get('samples', 0)} | "
                     f"{c.get('labeled', 0)} | {c.get('note') or ''} |")

    lines += ["", "## 二、Top3 方言（分布占比）", ""]
    if top3:
        lines += ["| 排名 | 方言 | 占比 | 样本 |", "| --- | --- | --- | --- |"]
        for i, t in enumerate(top3, 1):
            lines.append(f"| {i} | {t.get('name')} | {t.get('share', 0) * 100:.1f}% | {t.get('count')} |")
    else:
        lines.append("**无法给出 Top3**：三通道还没有带方言标注的样本。")
        for m in dist.get("missing") or []:
            lines.append(f"- 缺口：{m}")

    hits = (ch.get("conversations") or {}).get("feature_hits") or {}
    if hits:
        lines += ["", "## 三、对话日志里的方言特征（通道①，仅参考）", "",
                  "特征词命中不是方言判定，也不参与 Top3；只说明「现有日志里出现了这些方言痕迹」。", "",
                  "| 方言 | 命中消息数 |", "| --- | --- |"]
        for name, n in hits.items():
            lines.append(f"| {name} | {n} |")

    lines += ["", "## 四、WER 闸门（WER ≤ 基线 × 0.8）", ""]
    if ev.get("dialects"):
        lines += ["| 方言 | 语料句数 | 基线 WER（普通话模型） | 方言模型 WER | 0.8×基线 | 是否达标 | 引擎参数 |",
                  "| --- | --- | --- | --- | --- | --- | --- |"]
        for e in ev["dialects"]:
            base = e.get("baseline_wer")
            gate = "—" if base is None else f"{float(base) * dialect_service.WER_RATIO:.4f}"
            ok = dialect_service.wer_pass(e)
            hint = "有" if dialect_service.has_engine_hint(e) else "缺"
            lines.append(f"| {e.get('name')} | {e.get('corpus_size', 0)} | {_fmt(base)} | "
                         f"{_fmt(e.get('wer'))} | {gate} | {'达标' if ok else '未达标'} | {hint} |")
    else:
        lines.append("**还没有任何 WER 实测**：语料/转写未入库，基线数字未定死。")
    for m in ev.get("missing") or []:
        lines.append(f"- 缺口：{m}")

    lines += ["", "## 五、结论", "",
              f"- 上线清单：{'、'.join(online) if online else '无（0 个方言）'}",
              f"- 状态：{'上线可用' if online else 'hold（不硬上）'}",
              f"- 原因：{reason}", ""]

    clone = dialect_service.VOICE_CLONE
    lines += ["## 六、声音复刻（不在本卡，防顺手做）", "",
              f"- 默认状态：{'开' if clone['enabled'] else '关'}",
              f"- 状态说明：{clone['status']}",
              f"- 前置：{' + '.join(clone['requires'])}",
              f"- 备注：{clone['note']}", ""]

    lines += ["## 七、下一步（按数据缺口排）", "",
              "1. 通道②：把 H-5 访谈家庭的方言记录落到 `dialect_interview/*.jsonl`（字段 `dialect` / `text`）。",
              "2. 通道③：让子女或上门人员在子女端首次激活时选填「老人使用的方言」。",
              "3. 采集目标方言的老人音频：按 `dialect_eval/<方言名>/` 放 refs/baseline_hyp，跑一次本脚本定死基线。",
              "4. 后端选型（O-9 三人共审）给出方言引擎参数（`asr.json`），方言模型结果放 `dialect_hyp.txt`，再跑本脚本看闸门。",
              ""]
    with open("方言分布统计.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------- 入口

def main():
    dist = collect_distribution()
    ev = collect_evaluation()
    render_markdown(dist, ev)
    cfg = dialect_service.config()
    print("=" * 60)
    print("F-8 方言分布统计 / 上线闸门")
    print("=" * 60)
    print(f"三通道已标注样本：{dist['total_labeled']}")
    print(f"Top3：{', '.join(t['name'] for t in dist['top3']) or '（无法给出）'}")
    print(f"WER 实测：{len(ev['dialects'])} 个方言")
    print(f"上线清单：{', '.join(cfg['online']) or '无'}")
    print(f"状态：{'上线可用' if cfg['online'] else 'hold'}")
    print(f"原因：{cfg['hold_reason']}")
    print("已写出：dialect_distribution.json / dialect_eval.json / 方言分布统计.md")


if __name__ == "__main__":
    main()

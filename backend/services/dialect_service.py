"""方言识别服务（F-8）：分布口径 + WER 上线闸门 + ASR 方言路由。

三件事写死在这里，不猜、不硬上：
  1. 方言清单 = 《方言分布统计》的 Top3（dialect_distribution.json）。
     统计没齐全 / Top3 为空 → 本功能 hold，一个方言都不上线。
  2. 上线 = 分布 Top3 ∩ WER ≤ 基线×0.8 ∩ 有引擎参数（dialect_eval.json）。
     基线 = 当前 VOLC 普通话模型在同一批老人方言语料上的实测 WER，本卡第 1 周定死。
     任一条件不满足 → 该方言不上线，交集为空 → hold。
  3. 本构建不做分方言 ASR：各地方言模型 + 语料（河南/东北/山东/上海/湖北）
     不在本构建内，一律走普通话模型识别（覆盖大多数口语中文 + 地域口音）。
     asr_gate 恒放行，绝不回「还没学会」；不假装听懂某个具体方言。

亲属声音复刻不在本卡（P1 最后一项、单独评审，红线#10）。这里只留一个默认关的
开关位与说明，没有任何复刻接口——防止被顺手做了。
"""
import json
import os
import threading
import time

# 数据文件（项目根，与其它 JSON 数据同级）
DIST_FILE = "dialect_distribution.json"    # 三通道分布统计（dialect_report.py 产出）
EVAL_FILE = "dialect_eval.json"            # WER 实测 / 基线（dialect_report.py 产出）
PROFILE_FILE = "dialect_profile.json"      # 通道③：子女端首次激活表单填的「老人使用的方言」

MANDARIN = "普通话"
WER_RATIO = 0.8                            # 验收：WER ≤ 基线 × 0.8

# 采集表单的方言选项：来源写死为《数据集调研笔记》里已记录的 ElderWell 方言标注。
# 注意：这是「采集选项」，不是上线清单；上线清单只由 Top3 ∩ WER 闸门生成。
DIALECT_OPTIONS = [
    {"name": "河南方言", "source": "ElderWell 数据集（河南话）"},
    {"name": "东北方言", "source": "ElderWell 数据集（东北话）"},
    {"name": "山东方言", "source": "ElderWell 数据集（山东话）"},
    {"name": "上海方言", "source": "ElderWell 数据集（上海话）"},
    {"name": "湖北方言", "source": "ElderWell 数据集（湖北话）"},
]
OTHER_DIALECT = "其他方言"
FORM_NAMES = {o["name"] for o in DIALECT_OPTIONS} | {OTHER_DIALECT}

# 记录里常见的口语叫法 → 统一名
ALIASES = {"河南话": "河南方言", "东北话": "东北方言", "山东话": "山东方言",
           "上海话": "上海方言", "湖北话": "湖北方言",
           "mandarin": MANDARIN, "zh-CN": MANDARIN, "zh-cn": MANDARIN}

# 风险姿态：声音复刻默认关、显式同意 + 伦理审查后才可能开（本卡不实现）
VOICE_CLONE = {
    "enabled": False,
    "status": "未实现（P1 最后一项，单独评审）",
    "requires": ["显式同意", "伦理审查（B-6 已含亲属声线项；T-7 红线#10）"],
    "note": "默认关是安全姿态，不是产品开关；本卡不做复刻。",
}


# ---------------------------------------------------------------- 读写

def _read_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[dialect_service] 读取 {path} 失败: {e}")
        return default


def _write_json(path, data):
    tmp = f"{path}.{os.getpid()}_{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------- 分布 / 评估

def distribution():
    """三通道分布统计（只读）。没有报告文件时返回一份「未产出」的诚实空态。"""
    raw = _read_json(DIST_FILE)
    if not isinstance(raw, dict):
        return {
            "generated_at": None,
            "complete": False,
            "total_labeled": 0,
            "top3": [],
            "channels": {},
            "missing": ["未运行 dialect_report.py：三通道统计还没有产出"],
        }
    top3 = [t for t in (raw.get("top3") or []) if isinstance(t, dict) and t.get("name")][:3]
    channels = raw.get("channels") if isinstance(raw.get("channels"), dict) else {}
    return {
        "generated_at": raw.get("generated_at"),
        "complete": bool(raw.get("complete")) and bool(top3),
        "total_labeled": int(raw.get("total_labeled") or 0),
        "top3": top3,
        "channels": channels,
        "missing": list(raw.get("missing") or []),
    }


def evaluation():
    """WER 实测与基线（只读）。没有实测文件时 dialects 为空，闸门自然全不通过。"""
    raw = _read_json(EVAL_FILE)
    if not isinstance(raw, dict):
        return {
            "generated_at": None,
            "baseline_note": _baseline_note(),
            "wer_gate": f"WER ≤ 基线 × {WER_RATIO}",
            "dialects": [],
            "missing": ["dialect_eval/ 语料未入库：基线还没实测"],
        }
    dialects = [e for e in (raw.get("dialects") or []) if isinstance(e, dict) and e.get("name")]
    return {
        "generated_at": raw.get("generated_at"),
        "baseline_note": raw.get("baseline_note") or _baseline_note(),
        "wer_gate": raw.get("wer_gate") or f"WER ≤ 基线 × {WER_RATIO}",
        "dialects": dialects,
        "missing": list(raw.get("missing") or []),
    }


def _baseline_note():
    return ("基线 = 当前 VOLC 普通话模型在同一批老人方言语料上实测的 WER；"
            "WER 按字级编辑距离计算，本卡第 1 周定死后写进《方言分布统计》。")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def wer_pass(entry):
    """WER ≤ 基线×0.8 才算达标。基线缺失/为 0 一律不算达标（没有基线就算不出低 20%）。"""
    wer, base = _num(entry.get("wer")), _num(entry.get("baseline_wer"))
    if wer is None or base is None or base <= 0:
        return False
    return wer <= base * WER_RATIO


def has_engine_hint(entry):
    """后端模型选型（O-9）必须给出可调用的引擎参数（至少 cluster）才可能上线。"""
    hint = entry.get("asr")
    return isinstance(hint, dict) and bool(str(hint.get("cluster") or "").strip())


# ---------------------------------------------------------------- 闸门

def _top3_names(dist):
    return [str(t.get("name")) for t in dist.get("top3") or []]


def wer_pass_names(dist, ev):
    names = _top3_names(dist)
    by_name = {str(e.get("name")): e for e in ev.get("dialects") or []}
    return [n for n in names if n in by_name and wer_pass(by_name[n])]


def online_dialects(dist=None, ev=None):
    """上线清单 = Top3 ∩ WER 达标 ∩ 有引擎参数。"""
    dist = distribution() if dist is None else dist
    ev = evaluation() if ev is None else ev
    by_name = {str(e.get("name")): e for e in ev.get("dialects") or []}
    out = []
    for n in _top3_names(dist):
        e = by_name.get(n)
        if e and wer_pass(e) and has_engine_hint(e):
            out.append(n)
    return out


def hold_reason(dist=None, ev=None, online=None):
    dist = distribution() if dist is None else dist
    ev = evaluation() if ev is None else ev
    online = online_dialects(dist, ev) if online is None else online
    if not dist.get("complete"):
        return "《方言分布统计》未齐全（三通道有缺失或 Top3 无占比）→ 本功能 hold，不硬上"
    if not _top3_names(dist):
        return "《方言分布统计》Top3 为空 → 本功能 hold，不硬上"
    if not wer_pass_names(dist, ev):
        return "Top3 方言没有 WER ≤ 基线×0.8 的实测数据 → 本功能 hold，不硬上"
    if not online:
        return "WER 达标但缺引擎参数（后端选型未完成）→ 本功能 hold，不硬上"
    return "上线：" + "、".join(online)


def dialect_reason(name, dist=None, ev=None):
    """某个方言为什么不能听：按 分布 → WER → 引擎参数 逐层给原因。"""
    dist = distribution() if dist is None else dist
    ev = evaluation() if ev is None else ev
    name = _normalize(name)
    top3 = _top3_names(dist)
    by_name = {str(e.get("name")): e for e in ev.get("dialects") or []}
    if name not in top3:
        return "不在分布 Top3（统计未齐全或占比不够）"
    e = by_name.get(name)
    if not e or _num(e.get("baseline_wer")) is None:
        return "基线 WER 未定死（本周先测基线）"
    if _num(e.get("wer")) is None:
        return "还没有方言模型实测 WER"
    if not wer_pass(e):
        base = _num(e.get("baseline_wer")) or 0
        return f"WER {_num(e.get('wer')):.3f} 未达到 0.8×基线（{base * WER_RATIO:.3f}）"
    if not has_engine_hint(e):
        return "WER 达标，但缺引擎参数（后端选型未完成）"
    return "可上线"


def _normalize(name):
    s = str(name or "").strip()
    if not s:
        return ""
    return ALIASES.get(s, s)


# ---------------------------------------------------------------- ASR 路由

def asr_gate(dialect):
    """ASR 前过闸门（放行版）。

    返回 {requested, dialect, mode, refused, reply, hint}
      - 本构建不做分方言 ASR：各地方言模型 + 语料（河南/东北/山东/上海/湖北）不在本构建内，
        一律走普通话模型（覆盖大多数口语中文 + 地域口音），不假装听懂某个具体方言。
      - refused 恒为 False：不拦、不说「还没学会」；请求的方言只作记录，识别一律按普通话进行。
    """
    requested = str(dialect or "").strip()
    name = _normalize(requested) or MANDARIN
    return {"requested": requested, "dialect": name, "mode": "mandarin",
            "refused": False, "reply": None,
            "hint": None,  # None = 引擎按普通话（app.transcribe_audio 的缺省路径）
            "reason": "本构建只有普通话模型（覆盖多数地域口音），未含各地方言模型"}


# ---------------------------------------------------------------- 通道③：采集表单

def profile():
    raw = _read_json(PROFILE_FILE)
    if not isinstance(raw, dict):
        raw = {}
    return {
        "elder_dialect": str(raw.get("elder_dialect") or ""),
        "other": str(raw.get("other") or ""),
        "by": str(raw.get("by") or ""),
        "updated_at": raw.get("updated_at"),
    }


def set_profile(dialect, other="", by="child"):
    """子女端/上门人员选填「老人使用的方言」——只进统计，不直接决定 ASR。"""
    name = str(dialect or "").strip()
    if name and name not in FORM_NAMES:
        return {"ok": False, "message": "表单里没有这个方言，请先选一个。"}
    if name == OTHER_DIALECT and not str(other or "").strip():
        return {"ok": False, "message": "选了「其他方言」，请写一下是哪种。"}
    st = {"elder_dialect": name, "other": str(other or "").strip()[:40],
          "by": str(by or "child")[:16], "updated_at": time.time()}
    _write_json(PROFILE_FILE, st)
    return {"ok": True, "profile": profile()}


def form_options():
    """采集表单选项：文档里已记录的五种 + 其他。"""
    return list(DIALECT_OPTIONS) + [{"name": OTHER_DIALECT, "source": "表单补充，不预设"}]


# ---------------------------------------------------------------- 前端配置

def config():
    dist, ev = distribution(), evaluation()
    online = online_dialects(dist, ev)
    wer_ok = wer_pass_names(dist, ev)
    return {
        "ok": True,
        "mandarin": MANDARIN,
        "options": [
            {"name": o["name"], "supported": o["name"] in online,
             "reason": dialect_reason(o["name"], dist, ev)}
            for o in DIALECT_OPTIONS
        ],
        "top3": _top3_names(dist),
        "wer_pass": wer_ok,
        "online": online,
        "hold": not online,
        "hold_reason": hold_reason(dist, ev, online),
        "baseline": {"note": ev["baseline_note"], "wer_gate": ev["wer_gate"],
                     "dialects": [{"name": e.get("name"),
                                   "baseline_wer": e.get("baseline_wer"),
                                   "wer": e.get("wer"),
                                   "corpus_size": e.get("corpus_size"),
                                   "pass": wer_pass(e)}
                                  for e in ev.get("dialects") or []]},
        "distribution": {
            "complete": bool(dist.get("complete")),
            "total_labeled": dist.get("total_labeled", 0),
            "generated_at": dist.get("generated_at"),
            "missing": dist.get("missing") or [],
            "channels": dist.get("channels") or {},
        },
        "voice_clone": dict(VOICE_CLONE),
    }

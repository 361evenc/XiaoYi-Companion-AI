r"""H-7 跌倒/地滑安全告警 —— 检测 + 三段响应 + 误报/召回可测

**这一卡的核心不是"检测得准"，是"宁可误报，不可漏报"。**

任务卡写死了原则修正：跌倒检测的正确口径 = **宁可误报让人确认，不可漏报**。
漏报真跌倒 = 生命风险；误报 = 一次虚惊，有确认流程兜着。
所以这里所有设计取舍都朝"保召回"方向偏：

    1. 触发阈值低（conf ≥ 0.5），先报再说，由老人/家属确认
    2. 单人检测用"人框 + 框的宽高比"判断姿态：宽 > 高 = 横躺/倒地
    3. **连续 N 拍**（默认 2 拍）才报 —— 不是降召回，是过滤单帧抖动
       （YOLO 单帧误检很常见；1 拍就报会把月误报率顶上天，最后被迫调高阈值
       反而杀召回。用"时间持续性"代替"提高阈值"来压误报，这是保召回的正解）
    4. 站起来就取消（倒地后自己爬起来 = 误报或已脱险，不再追问）

**三段响应（任务卡写死，不能改话术方向）：**
    ① 老人答"没事"       → 误报，归档（计入误报统计，供阈值调参）
    ② 老人答"要帮忙"     → 通知子女 + 提示 120
    ③ 60s 无应答，追问 1 次仍无 → **直接通知子女** + 提示 120
      （无应答很可能就是没法应答 —— 绝不等人回答）

**不自主拨 120**：MVP 做不到电脑拨号，写诚实：
    "我已经给您孩子打了，着急的事您自己按 120"

**不做医疗判断**：只问"您没事吧"、只转达，不说"您可能骨折了"这类话
（红线 #2：健康边界）。

用法（自测，模拟倒地序列）：
    venv\Scripts\python.exe fall_guard.py --selftest
    venv\Scripts\python.exe fall_guard.py --source camera --interval 1

给后端用：
    from fall_guard import guard
    guard.feed(person_box, frame_shape)      # 每次识别到 person 就喂一次
    guard.tick()                             # 主循环每拍调一次，推进超时状态机
    guard.answer("没事")                      # 老人应答（ASR 文本）
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DATA_DIR = HERE / "data"
EVENTS_FILE = DATA_DIR / "fall_events.jsonl"     # 每次触发/应答/转报，全量审计

# ---- 参数（任务卡写死 / 工程可调的都标明来源） ----
CONF_MIN_TRIGGER = 0.5       # 任务卡：触发阈值 conf ≥ 0.5（低阈值保召回）
ASPECT_FALL = 1.15           # 宽/高 > 1.15 视为横躺/倒地（人直立时约 0.35-0.6）
VERTICALITY_FALL = 0.72      # 人的竖直位置：框顶高于这一比例 = 头已经低到地面附近
                             #   （站立/坐着时框顶一般 0.15-0.55；躺在地上 0.6-0.9）
BOTTOM_MIN = 0.92            # 框底必须贴近画面下方（躺在地上的人才会这样）
CONFIRM_FRAMES = 2           # 连续几拍判定（过滤单帧误检；不是提高阈值）
CONFIRM_WINDOW_S = 6.0       # 这 2 拍必须在 6 秒内（掉帧不误判为"持续倒地"）
TIMEOUT_ANSWER_S = 60.0      # 任务卡：无应答 60s 超时
FOLLOWUP_ONCE = True         # 任务卡：追问 1 次

# 固定话术（任务卡写死方向，不许改含义）
ASK_TEXT = "我看您可能摔倒了，您没事吧？"
ASK_AGAIN = "您还好吗？您跟我说句话。"
NO_ANSWER_TEXT = "我已经给您孩子打了，着急的事您自己按120"

# 应答判定：老人不会说标准话术，会被 ASR 出各种说法
OK_WORDS = ["没事", "没事儿", "我没事", "没摔", "没摔倒", "还行", "好的", "好着呢",
            "没事的", "起来了", "我起来了", "没大事"]
HELP_WORDS = ["帮忙", "帮帮我", "救命", "起不来", "站不起来", "疼", "动不了",
              "摔了", "摔着", "来人", "不行了", "扶我"]

# 状态机
ST_IDLE = "idle"                 # 平时
ST_SUSPECT = "suspect"           # 疑似（第 1 拍，等第 2 拍确认）
ST_ASKING = "asking"             # 已发问，等应答
ST_ESCALATED = "escalated"       # 已转报子女
ST_FALSE = "false_alarm"         # 误报归档

MEDICAL_WORDS = ["骨", "骨折", "脑", "中风", "心梗", "内脏", "出血", "脱臼", "诊断", "症状"]


def _now():
    return datetime.now().isoformat(timespec="seconds")


def append_jsonl(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


class FallGuard:
    """跌倒/地滑告警状态机。进程内单例。"""

    def __init__(self):
        self.state = ST_IDLE
        self.suspect_at = None          # 第 1 拍倒地的时间
        self.suspect_count = 0          # 连续倒地拍数
        self.asking_at = None           # 发问时间
        self.followed_up = False        # 是否已追问过
        self.last_trigger = None        # 最近一次触发记录（供测试/审计查）
        self.stats = {"triggers": 0, "false_alarms": 0, "escalations": 0,
                      "cancelled": 0}

    # ---------------- 检测入口 ----------------

    def feed(self, person_box, frame_shape):
        """喂一个人框。person_box: [x1,y1,x2,y2]；frame_shape: (h, w[, c])。

        判定跌倒要**两个条件同时满足**（单看宽高比会漏掉侧躺画面）：
            ① 宽高比 ≥ ASPECT_FALL          —— 框变横
            ② 框顶高过 VERTICALITY_FALL 且框底贴近画面下沿 —— 头到了地面高度

        为什么必须加 ②：真实素材里"两人并排躺在沙发上看书"的
        person 框会被合并成一个大横框（实测宽高比 1.75），但框顶在中部、
        框底不贴地 —— 那是正常活动，不是跌倒。只用宽高比会直接误报。

        返回本次判定：{"fallen": bool, "aspect": float, "reason": str}
        """
        if not person_box or len(person_box) != 4:
            return {"fallen": False, "aspect": 0.0, "reason": "no_person"}
        x1, y1, x2, y2 = [float(v) for v in person_box]
        fh = float(frame_shape[0]) if frame_shape else 720.0
        w = max(1e-6, x2 - x1)
        h = max(1e-6, y2 - y1)
        aspect = w / h                                   # 宽/高
        top_ratio = y1 / fh
        bottom_ratio = y2 / fh

        wide = aspect >= ASPECT_FALL
        low = top_ratio >= VERTICALITY_FALL and bottom_ratio >= BOTTOM_MIN
        fallen = wide and low

        if fallen:
            reason = (f"宽高比 {aspect:.2f}≥{ASPECT_FALL} 且 框顶 {top_ratio:.2f}"
                      f"≥{VERTICALITY_FALL}（头已到地面高度）")
        elif wide:
            reason = (f"宽高比 {aspect:.2f}≥{ASPECT_FALL} 但 框顶 {top_ratio:.2f}"
                      f"<{VERTICALITY_FALL} —— 像是并躺/久坐，不是跌倒")
        else:
            reason = f"宽高比 {aspect:.2f}<{ASPECT_FALL}（直立/坐姿）"

        if not fallen:
            # 站起来（或本来就没倒）→ 取消疑似，不追问
            if self.state in (ST_SUSPECT,):
                self._log({"event": "cancelled", "reason": "检测到直立，疑似解除"})
                self.stats["cancelled"] += 1
                self._reset()
            return {"fallen": False, "aspect": aspect, "reason": reason}

        # —— 倒地 ——
        if self.state == ST_IDLE:
            self.state = ST_SUSPECT
            self.suspect_at = time.time()
            self.suspect_count = 1
            return {"fallen": True, "aspect": aspect,
                    "reason": reason + " | 第 1 拍，等确认"}
        if self.state == ST_SUSPECT:
            gap = time.time() - (self.suspect_at or 0)
            if gap > CONFIRM_WINDOW_S:
                # 隔太久的两拍不算"持续倒地"，重新计数
                self.suspect_at = time.time()
                self.suspect_count = 1
                return {"fallen": True, "aspect": aspect,
                        "reason": reason + " | 间隔过长，重新计拍"}
            self.suspect_count += 1
            if self.suspect_count >= CONFIRM_FRAMES:
                self._trigger(aspect)
                return {"fallen": True, "aspect": aspect,
                        "reason": reason + f" | 连续 {self.suspect_count} 拍，已触发"}
            return {"fallen": True, "aspect": aspect, "reason": reason + " | 计数中"}
        return {"fallen": True, "aspect": aspect,
                "reason": reason + f" | 已在 {self.state} 状态"}

    def _trigger(self, aspect):
        self.state = ST_ASKING
        self.asking_at = time.time()
        self.followed_up = False
        self.stats["triggers"] += 1
        rec = {"event": "trigger", "ts": _now(), "aspect": round(aspect, 3),
               "ask": ASK_TEXT}
        self.last_trigger = rec
        self._log(rec)

    # ---------------- 超时推进（主循环每拍调） ----------------

    def tick(self, now=None):
        """推进超时状态机。返回 {"action": ...}，调用方据此播报。

        action:
            None       什么也不用做
            "followup" 追问一次（播 ASK_AGAIN）
            "escalate" 无应答超时 → 通知子女 + 提示 120
        """
        now = now or time.time()
        if self.state != ST_ASKING:
            return {"action": None, "state": self.state}

        elapsed = now - (self.asking_at or now)
        # 第一次追问：过半超时时间还没应答
        if FOLLOWUP_ONCE and not self.followed_up and elapsed >= TIMEOUT_ANSWER_S / 2:
            self.followed_up = True
            self._log({"event": "followup", "ts": _now(), "ask": ASK_AGAIN})
            return {"action": "followup", "text": ASK_AGAIN, "state": self.state}

        if elapsed >= TIMEOUT_ANSWER_S:
            self._escalate("no_answer")
            return {"action": "escalate", "text": NO_ANSWER_TEXT,
                    "state": self.state, "reason": "无应答超时"}
        return {"action": None, "state": self.state}

    # ---------------- 应答（老人说话后调） ----------------

    def answer(self, text):
        """处理老人对"您没事吧"的应答。返回 {"action", "text", ...}。

        判定顺序：先判"要帮忙"再判"没事" —— "我没事但是起不来"应算求助，
        宁可多报一次，也不能把求助当成没事（保召回原则）。
        """
        t = (text or "").strip()
        if self.state != ST_ASKING:
            return {"action": "ignored", "state": self.state,
                    "reason": "当前不在询问状态"}
        if any(w in t for w in HELP_WORDS):
            self._escalate("help_requested", text=t)
            return {"action": "escalate", "state": self.state,
                    "reason": "老人求助", "text": NO_ANSWER_TEXT}
        if any(w in t for w in OK_WORDS):
            self.stats["false_alarms"] += 1
            self._log({"event": "false_alarm", "ts": _now(), "answer": t})
            self._reset()
            return {"action": "confirmed_ok", "state": self.state,
                    "reason": "老人说没事，归档为误报"}
        # 说了别的话（没听清/听不懂）→ 不当作"没事"，继续等超时
        self._log({"event": "unclear_answer", "ts": _now(), "answer": t})
        return {"action": "unclear", "state": self.state,
                "reason": "应答没听懂，继续等超时（不猜成没事）"}

    # ---------------- 转报 ----------------

    def _escalate(self, reason, text=""):
        self.state = ST_ESCALATED
        self.stats["escalations"] += 1
        self._log({"event": "escalate", "ts": _now(), "reason": reason,
                   "answer": text, "notify": "children", "tip_120": True})

    def poll_escalation(self):
        """后端轮询用：取走"待转报"事件（取走即清，避免重复通知子女）。"""
        if self.state != ST_ESCALATED:
            return None
        rec = {"reason": "fall_escalation", "ts": _now(),
               "text": NO_ANSWER_TEXT, "notify": "children", "tip_120": True}
        self._reset()          # 转报完回 idle，等下一次
        return rec

    # ---------------- 工具 ----------------

    def _reset(self):
        self.state = ST_IDLE
        self.suspect_at = None
        self.suspect_count = 0
        self.asking_at = None
        self.followed_up = False

    def _log(self, rec):
        append_jsonl(EVENTS_FILE, rec)

    def status(self):
        return {"state": self.state, "stats": dict(self.stats),
                "asking_elapsed": (round(time.time() - self.asking_at, 1)
                                   if self.asking_at else None)}


def check_medical_wording(text):
    """红线 #2 校验：安全告警话术不许出现医疗判断词。

    第二人 code review 时可以直接调这个函数跑一遍所有话术常量。
    """
    hits = [w for w in MEDICAL_WORDS if w in (text or "")]
    return {"ok": not hits, "hits": hits, "text": text}


guard = FallGuard()


# ---------------- 自测：模拟倒地序列 + 召回测试 ----------------

def selftest():
    """构造 10 次真跌倒（含 5 次无应答）+ 10 次正常活动，验证召回与误报。"""
    global _fails
    print("=" * 60)
    print("H-7 自测：召回 / 误报 / 三段响应")
    print("=" * 60)
    fails = []

    def ok(cond, name, detail=""):
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail else ""))
        if not cond:
            fails.append(name)
        return cond

    # 1. 召回：10 次跌倒（贴地的横躺框），其中 5 次无应答
    #    倒地的人头在画面下部：框 [100, 540, 980, 715] → 880 宽/175 高，
    #    宽高比 5.0、框顶 0.75、框底 0.99 —— 两个条件都满足
    FALLEN_BOX = [100, 540, 980, 715]
    hit = 0
    for i in range(10):
        g = FallGuard()
        g.feed(FALLEN_BOX, (720, 1280))
        g.feed(FALLEN_BOX, (720, 1280))        # 第 2 拍确认
        triggered = (g.state == ST_ASKING)
        if triggered:
            hit += 1
        if i < 5:
            # 5 次应答"没事"
            r = g.answer("我没事")
            ok(r["action"] == "confirmed_ok", f"第{i+1}次应答没事 -> 归档误报", str(r["action"]))
        else:
            # 5 次无应答：推进到超时（先过半程追问，再走满 60s）
            t = g.asking_at
            fu = g.tick(now=t + TIMEOUT_ANSWER_S / 2 + 1)
            ok(fu["action"] == "followup", f"第{i+1}次 过半程先追问", str(fu["action"]))
            g.tick(now=t + TIMEOUT_ANSWER_S + 1)
            ok(g.state == ST_ESCALATED, f"第{i+1}次 60s 无应答 -> 转报", g.state)
            esc = g.poll_escalation()
            ok(bool(esc and esc["notify"] == "children"),
               f"第{i+1}次 转报事件含 notify=children", str(esc))
    ok(hit >= 9, f"召回 {hit}/10（要求 ≥9）")

    # 2. 误报：10 次正常活动（直立框）不得触发
    false_pos = 0
    for _ in range(10):
        g = FallGuard()
        upright = [500, 100, 780, 700]         # 280 宽 / 600 高 → 宽高比 0.47
        for _k in range(5):
            g.feed(upright, (720, 1280))
        if g.state != ST_IDLE:
            false_pos += 1
    ok(false_pos == 0, f"正常活动误报 {false_pos}/10（要求 0）")

    # 2b. 关键回归：真实素材 17_living_window.jpg 的"两人并躺看书"
    #     实测 person 框 [338, 357, 964, 715] → 宽高比 1.75，只看宽高比必误报。
    #     加"框顶必须高过 0.72"这条之后不报 —— 这是本卡最重要的误报防线。
    g = FallGuard()
    for _ in range(3):
        g.feed([338, 357, 964, 715], (720, 1280))
    ok(g.state == ST_IDLE, "并躺看书大横框不误报（真实素材回归）", g.state)

    # 3. 单帧抖动不触发（过滤单帧误检）
    g = FallGuard()
    g.feed(FALLEN_BOX, (720, 1280))   # 只有 1 拍
    ok(g.state == ST_SUSPECT, "单拍倒地只进 suspect，不触发")

    # 4. 站起来取消
    g = FallGuard()
    g.feed(FALLEN_BOX, (720, 1280))
    g.feed([500, 100, 780, 700], (720, 1280))
    ok(g.state == ST_IDLE, "倒地后直立 -> 疑似解除")

    # 5. 三段响应
    g = FallGuard()
    for _ in range(2):
        g.feed(FALLEN_BOX, (720, 1280))
    r = g.answer("我起不来，帮帮我")
    ok(r["action"] == "escalate", "求助 -> 立即转报", str(r["action"]))
    ok(g.poll_escalation() is not None, "转报事件可被轮询取走")

    g = FallGuard()
    for _ in range(2):
        g.feed(FALLEN_BOX, (720, 1280))
    g.tick(now=g.asking_at + TIMEOUT_ANSWER_S / 2 + 1)   # 先追问一次
    r = g.tick(now=g.asking_at + TIMEOUT_ANSWER_S + 1)   # 再走满 60s
    ok(r["action"] == "escalate", "无应答超时 -> 直接转报", str(r["action"]))

    # 6. 歧义应答不猜成没事
    g = FallGuard()
    for _ in range(2):
        g.feed(FALLEN_BOX, (720, 1280))
    r = g.answer("嗯……")
    ok(r["action"] == "unclear" and g.state == ST_ASKING, "听不懂的应答不当作没事",
       str(r["action"]))

    # 7. 医疗边界
    for txt in (ASK_TEXT, ASK_AGAIN, NO_ANSWER_TEXT):
        c = check_medical_wording(txt)
        ok(c["ok"], f"话术 0 医疗判断：{txt}", str(c["hits"]))

    print("=" * 60)
    if fails:
        print(f"[FAIL] 未通过 {len(fails)} 项：{fails}")
        return 1
    print("[PASS] H-7 自测全部通过")
    return 0


def main():
    ap = argparse.ArgumentParser(description="H-7 跌倒/地滑安全告警")
    ap.add_argument("--selftest", action="store_true", help="跑召回/误报自测")
    ap.add_argument("--source", default="camera", choices=["camera", "sim"])
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--duration", type=float, default=0, help="跑几秒，0=一直跑")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    from sources import make_source
    from describe_frame import load_model
    model = load_model()
    src = make_source(args.source, interval=0,
                      **({"index": 0} if args.source == "camera" else {}))
    if not src.ok():
        print("[X] 帧源不可用")
        return 1

    print(f"[OK] 跌倒告警启动 | {src.describe()} | 阈值 宽高比≥{ASPECT_FALL} × {CONFIRM_FRAMES}拍")
    deadline = time.time() + args.duration if args.duration > 0 else None
    while True:
        if deadline and time.time() >= deadline:
            break
        ok, frame, _meta = src.read()
        if ok and frame is not None:
            r = model(frame, conf=CONF_MIN_TRIGGER, imgsz=640, verbose=False, device="cpu")[0]
            best = None
            for i in range(len(r.boxes)):
                if r.names[int(r.boxes.cls[i])] == "person":
                    conf = float(r.boxes.conf[i])
                    if best is None or conf > best[0]:
                        best = (conf, r.boxes.xyxy[i].tolist())
            del frame
            if best and best[0] >= CONF_MIN_TRIGGER:
                res = guard.feed(best[1], (720, 1280))
                if res["fallen"]:
                    print(f"[DETECT] {res['reason']}")
            else:
                # 没人 → 直立判定不成立，但也不该"取消"（人可能真的不在画面里）
                pass
        act = guard.tick()
        if act["action"] == "followup":
            print(f"[ASK] {act['text']}")
        elif act["action"] == "escalate":
            print(f"[ESCALATE] {act['reason']} -> 通知子女 + 提示120")
        time.sleep(max(0.0, args.interval - 0.1))
    src.release()
    print(f"[DONE] {guard.status()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

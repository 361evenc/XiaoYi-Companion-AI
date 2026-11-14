r"""T-1 端侧事件 → 云VLM 调度器（5 类事件闸门，平时 0 次 VLM）

设计决定（策略线 T-1，详见 docs/spike/T1_管线评测框架.md）：
    1. 平时 0 次 VLM —— 只有 5 类事件触发才调云 VLM（新物体首现/场景切换/
       找物查询/跌倒疑似/老人"记一下"），其余帧零 VLM 调用（省钱+省带宽）。
    2. 端侧 YOLO 做粗筛（7 组），云 VLM 做细描（只在事件时）——分层不重复花钱。
    3. 每次 VLM 调用都有冷却（同类事件 N 秒内不重复调），防刷屏。
    4. 调用失败诚实降级（VLM 不可用 → 用 YOLO 粗结果，vlm_called=False，不装细描）。

5 类事件（触发才调 VLM）：
    - new_object_first_seen: 新物体首现（端侧 YOLO 没见过的组）
    - scene_change:          场景切换（主场景变了）
    - find_object_query:     找物查询（老人问"我的X在哪"）
    - fall_suspected:        跌倒疑似（端侧检测到异常姿态/有人突然消失）
    - elder_remember:        老人"记一下"（老人说"记住这个"）

用法（模拟跑，验证 5 类闸门 + 冷却 + 降级）：
    python event_trigger.py --self-test
"""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
TRIGGER_STATE_FILE = DATA / "event_trigger_state.json"
VLM_LOG_FILE = DATA / "vlm_calls.jsonl"

# 5 类事件 + 冷却（同类事件 N 秒内不重复调 VLM，防刷屏）
EVENT_TYPES = {
    "new_object_first_seen": 3600,   # 新物体首现：1 小时冷却
    "scene_change": 7200,            # 场景切换：2 小时冷却
    "find_object_query": 300,        # 找物查询：5 分钟冷却（老人可能反复问）
    "fall_suspected": 60,            # 跌倒疑似：1 分钟冷却（紧急，短冷却）
    "elder_remember": 600,           # 老人"记一下"：10 分钟冷却
}

# 跌倒疑似：端侧检测阈值（连续 N 帧有人→无人，疑似跌倒/异常）
FALL_PERSON_DISAPPEAR_FRAMES = 3


class EventTrigger:
    """端侧事件 → 云VLM 调度器（平时 0 次 VLM，5 类事件闸门）。

    vlm_call: callable(image_b64_or_path, prompt) -> str；None = 诚实降级（不调 VLM）。
    clock:    时钟注入（可测试）。
    """

    def __init__(self, vlm_call=None, clock=time.time, state_path=None):
        self.vlm_call = vlm_call
        self.clock = clock
        self._state_path = state_path or TRIGGER_STATE_FILE
        self._last_call = {}     # {event_type: last_call_ts}
        self._call_count = 0
        self._person_frames = 0  # 连续"有人"帧计数（跌倒疑似用）
        self._load()

    # ---- 持久化（崩溃不丢冷却状态）----
    def _load(self):
        if self._state_path.exists():
            try:
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
                self._last_call = data.get("last_call", {})
                self._call_count = data.get("call_count", 0)
            except Exception:
                pass

    def _save(self):
        try:
            self._state_path.parent.mkdir(exist_ok=True)
            self._state_path.write_text(
                json.dumps({"last_call": self._last_call,
                            "call_count": self._call_count},
                           ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception:
            pass

    # ---- 闸门：该类事件是否该调 VLM（冷却判定）----
    def check(self, event_type, now=None):
        """返回 True（该调）/ False（冷却中，不调）。"""
        now = now or self.clock()
        cooldown = EVENT_TYPES.get(event_type, 3600)
        last = self._last_call.get(event_type, 0)
        return (now - last) >= cooldown

    # ---- 事件触发 → 调云 VLM 细描（平时 0 次 VLM，只有这里调）----
    def enrich(self, event, image=None, now=None):
        """返回 {**event, vlm_called, vlm_description/vlm_reason, ts}。
        冷却中 → vlm_called=False, vlm_reason=cooldown。
        VLM 不可用 → vlm_called=False, vlm_reason=no_vlm（诚实降级）。
        VLM 失败  → vlm_called=False, vlm_reason=error（绝不静默）。"""
        now = now or self.clock()
        event_type = event.get("type", "")
        if event_type not in EVENT_TYPES:
            return {**event, "vlm_called": False, "vlm_reason": "unknown_type", "ts": now}
        if not self.check(event_type, now=now):
            return {**event, "vlm_called": False, "vlm_reason": "cooldown", "ts": now}
        if self.vlm_call is None:
            return {**event, "vlm_called": False, "vlm_reason": "no_vlm", "ts": now}
        try:
            prompt = self._prompt_for(event)
            desc = self.vlm_call(image, prompt)
            self._call_count += 1
            self._last_call[event_type] = now
            self._save()
            self._log_vlm_call(event_type, desc, now)
            return {**event, "vlm_called": True, "vlm_description": desc, "ts": now}
        except Exception as e:
            return {**event, "vlm_called": False,
                    "vlm_reason": f"error: {e}", "ts": now}

    # ---- 端侧帧 → 事件判定（YOLO 粗筛，喂给 enrich）----
    def on_frame(self, rec, image=None, now=None):
        """每帧调一次（端侧 YOLO 粗筛结果 rec）。返回触发的 VLM 事件列表。
        rec: describe_frame 产出（含 objects/scene/summary）。
        平时 0 次 VLM —— 只有 5 类事件才 enrich。"""
        now = now or self.clock()
        fired = []
        groups = {o["group"] for o in rec.get("objects", [])}
        scene = rec.get("scene", "unknown")
        has_person = "人" in groups

        # 跌倒疑似：之前连续有人 → 突然无人（帧间突变）
        if self._person_frames >= FALL_PERSON_DISAPPEAR_FRAMES and not has_person:
            ev = {"type": "fall_suspected", "person_disappear": self._person_frames,
                  "scene": scene, "ts": now}
            fired.append(self.enrich(ev, image=image, now=now))
        self._person_frames = self._person_frames + 1 if has_person else 0

        # 其余 4 类事件由上层（perception_service.summarize 产出的 events）喂入，
        # 这里不重复判（新物体首现/场景切换在窗口汇总层判，避免每帧判）
        return fired

    # ---- 上层事件（summarize 产出）→ enrich ----
    def on_summary_events(self, events, image=None, now=None):
        """perception_service.summarize 产出的事件列表 → 逐条 enrich（5 类闸门）。
        返回 VLM 富化后的事件列表。"""
        out = []
        for e in events:
            et = e.get("type", "")
            if et == "new_group_first_seen":
                mapped = {"type": "new_object_first_seen",
                          "group": e.get("group"), "labels": e.get("labels")}
                out.append(self.enrich(mapped, image=image, now=now))
            elif et == "scene_change":
                mapped = {"type": "scene_change",
                          "from": e.get("from"), "to": e.get("to")}
                out.append(self.enrich(mapped, image=image, now=now))
            else:
                out.append(e)  # 非 5 类事件，原样透传
        return out

    def on_find_object_query(self, object_name, image=None, now=None):
        """老人找物查询 → enrich（5 类之③）。"""
        ev = {"type": "find_object_query", "object": object_name, "ts": now or self.clock()}
        return self.enrich(ev, image=image, now=now)

    def on_elder_remember(self, image=None, now=None):
        """老人"记一下" → enrich（5 类之⑤）。"""
        ev = {"type": "elder_remember", "ts": now or self.clock()}
        return self.enrich(ev, image=image, now=now)

    def _prompt_for(self, event):
        """按事件类型生成 VLM 细描 prompt（位置到家具级，符合 T-2 契约）。"""
        t = event.get("type", "")
        if t == "new_object_first_seen":
            return (f"画面里出现了新物体（组：{event.get('group')}，"
                    f"标签：{event.get('labels')}）。请细描它是什么、"
                    f"在哪个位置（到家具级，如'床头柜上'）。")
        if t == "scene_change":
            return (f"场景从'{event.get('from')}'切换到'{event.get('to')}'，"
                    f"请细描新场景里有什么（人/物/事/位置）。")
        if t == "find_object_query":
            return (f"老人在找'{event.get('object')}'，请细描画面里有没有这个物体、"
                    f"在哪个位置（到家具级）。")
        if t == "fall_suspected":
            return ("画面里之前有人、现在疑似没人（疑似跌倒或长时间不动），"
                    "请细描人的姿态和位置（用于确认是否跌倒）。")
        if t == "elder_remember":
            return "老人要求记住这个画面，请细描画面里有什么（人/物/事/位置，到家具级）。"
        return "请细描这个画面里有什么（人/物/事/位置，到家具级）。"

    def _log_vlm_call(self, event_type, desc, now):
        """VLM 调用日志（绝不静默，供 spike 评测引用）。"""
        try:
            DATA.mkdir(exist_ok=True)
            with open(VLM_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(
                    {"ts": datetime.now().isoformat(timespec="seconds"),
                     "type": event_type, "desc": (desc or "")[:200]},
                    ensure_ascii=False) + "\n")
        except Exception:
            pass

    def stats(self):
        """VLM 调用统计（供 spike go/no-go 判定：调用次数/命中率/降级率）。"""
        return {"total_calls": self._call_count,
                "last_call": dict(self._last_call)}


def self_test():
    """自检：5 类闸门 + 冷却 + 降级，不依赖真 VLM（用假 vlm_call）。"""
    import tempfile, os
    tmp = Path(tempfile.mkdtemp())
    state = tmp / "state.json"

    calls = []
    def fake_vlm(image, prompt):
        calls.append(prompt)
        return "细描：床头柜上有一个透明塑料盒（降压药）。"

    trig = EventTrigger(vlm_call=fake_vlm, clock=lambda: 1_000_000.0, state_path=state)
    ok = True

    # 1. 5 类事件各触发一次 VLM
    for ev in [
        {"type": "new_object_first_seen", "group": "餐具", "labels": ["bowl"]},
        {"type": "scene_change", "from": "卧室", "to": "客厅"},
        {"type": "find_object_query", "object": "降压药"},
        {"type": "fall_suspected", "person_disappear": 3, "scene": "卧室"},
        {"type": "elder_remember"},
    ]:
        r = trig.enrich(ev)
        assert r["vlm_called"] is True, f"应调 VLM: {ev}"
    assert len(calls) == 5, f"应调 5 次，实际 {len(calls)}"
    print("  [1] 5 类事件各触发 1 次 VLM  ✓")

    # 2. 冷却中不重复调（同类 1 秒后再调 → cooldown）
    r = trig.enrich({"type": "find_object_query", "object": "降压药"})
    assert r["vlm_called"] is False and r["vlm_reason"] == "cooldown"
    assert len(calls) == 5
    print("  [2] 冷却中同类事件不重复调  ✓")

    # 3. VLM 不可用 → 诚实降级
    trig2 = EventTrigger(vlm_call=None, clock=lambda: 1_000_000.0, state_path=tmp / "s2.json")
    r = trig2.enrich({"type": "scene_change", "from": "a", "to": "b"})
    assert r["vlm_called"] is False and r["vlm_reason"] == "no_vlm"
    print("  [3] VLM 不可用 → 诚实降级（no_vlm）  ✓")

    # 4. VLM 抛异常 → 诚实降级（error，绝不静默）
    def bad_vlm(image, prompt):
        raise RuntimeError("API down")
    trig3 = EventTrigger(vlm_call=bad_vlm, clock=lambda: 1_000_000.0, state_path=tmp / "s3.json")
    r = trig3.enrich({"type": "elder_remember"})
    assert r["vlm_called"] is False and "error" in r["vlm_reason"]
    print("  [4] VLM 抛异常 → 诚实降级（error）  ✓")

    # 5. 平时 0 次 VLM：非 5 类事件原样透传（不调 VLM）
    out = trig2.on_summary_events([{"type": "other", "x": 1}])
    assert out[0].get("vlm_called") is None  # 非 5 类事件不 enrich
    print("  [5] 非 5 类事件原样透传（0 次 VLM）  ✓")

    # 6. 帧级跌倒疑似：连续 3 帧有人 → 突然无人
    trig4 = EventTrigger(vlm_call=fake_vlm, clock=lambda: 2_000_000.0, state_path=tmp / "s4.json")
    for i in range(3):
        trig4.on_frame({"objects": [{"group": "人"}], "scene": "卧室"})
    r = trig4.on_frame({"objects": [], "scene": "卧室"})
    assert any(e.get("type") == "fall_suspected" for e in r), "应触发跌倒疑似"
    print("  [6] 帧级跌倒疑似（3 帧有人→无人）  ✓")

    stats = trig.stats()
    assert stats["total_calls"] == 5
    print(f"  [7] VLM 调用统计  total={stats['total_calls']}  ✓")

    import shutil
    shutil.rmtree(tmp, True)
    print("  PASS：T-1 事件闸门 7 项自检全部通过")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true", help="跑 5 类闸门自检")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

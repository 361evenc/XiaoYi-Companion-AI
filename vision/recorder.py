r"""H-2 持续本地录制管理 —— 常驻录制 + 真停帧 + 状态查询（电脑摄像头版）

这是找物主线的**数据源**。核心不是"能录"，而是两件更难的事：

  1. **说停就真停**（不是嘴上停）
     close() 之后摄像头必须被真正释放。本模块提供 verify_released()：
     关闭后再尝试打开摄像头 —— 打开成功 = 真停；设备报错 = 故障（不等于停）。
     前端 F-2 指示灯只信这个验证结果，不信任何"状态字段"。

  2. **原始帧零落盘**
     帧只在本进程内存里 → 检测 → 转成结构化条目 → 帧对象丢弃。
     全程不写图片、不写视频、不上传。代码里没有任何 imwrite/VideoWriter/PIL.save
     路径（见 tests/h2_privacy_check.py 的静态校验）。

用法（常驻）：
    venv\Scripts\python.exe recorder.py --source camera --interval 2

用法（自测，跑 2 分钟）：
    venv\Scripts\python.exe recorder.py --source sim --interval 0.5 --duration 0.03

给后端 / 前端用的接口：
    from recorder import controller
    controller.start(source="camera", interval=2)
    controller.stop()          # 真停：关闭摄像头 + 等线程退出
    controller.verify()        # 验证：真停 / 故障 / 仍在录
    controller.status()        # 状态快照（F-2 指示灯读这个）

设计决定：
    1. 录制循环跑在**独立线程**里 —— FastAPI 的请求线程不能被长循环占住
    2. **两个独立开关**：视觉（帧）和音频（听）。F-2 要求"别看了"只关视觉、
       "别听我的"只关音频。本卡只管视觉；音频开关留占位（audio_enabled），
       真实音频采集由后续卡接（当前 MVP 不采集音频，占位恒 False 也是合法状态）
    3. 关闭 = 释放硬件（cap.release + 删引用），不是暂停。因为"暂停的摄像头
       可能仍在缓冲" —— 只有释放才是可验证的
    4. **故障 != 停止**：设备被占用/驱动故障时，UI 必须说"我没法确定"，
       不能说"已停"。两者在 status() 里用不同状态值区分（released / fault）
    5. 结构化条目写 JSON Lines（与 perception_service 同一格式，下游不用改）
"""

import argparse
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DATA_DIR = HERE / "data"
FRAMES_FILE = DATA_DIR / "world_perception.json"
EVENTS_FILE = DATA_DIR / "recorder_events.json"      # 开关/故障审计日志

# 状态值（前端 F-2 按这些值选话术，不要自己另起一套）
ST_IDLE = "idle"                  # 从未启动
ST_RECORDING = "recording"        # 正在看
ST_RELEASED = "released"          # 确认真停（设备已释放）✅
ST_FAULT = "fault"                # 故障 —— 不能确定是否在录 ⚠️
ST_CLOSING = "closing"            # 正在关闭（过渡态，前端应等下一拍）


def append_jsonl(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _now():
    return datetime.now().isoformat(timespec="seconds")


class RecordingController:
    """录制状态机。一个进程内只有一个实例（模块底部 controller）。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._thread = None
        self._stop_flag = threading.Event()
        self._state = ST_IDLE
        self._detail = "尚未启动"
        self._source = None            # 当前帧源（None = 已释放）
        self._source_kind = "camera"
        self._interval = 2.0
        self._model = None
        self._n_frames = 0
        self._n_errors = 0
        self._last_frame_ts = None
        self._started_at = None
        self._stopped_at = None
        # 音频（听）独立开关：MVP 不采集音频，恒 False，接口先占位
        self.audio_enabled = False

    # ---------------- 状态查询（F-2 读这个） ----------------

    def status(self):
        with self._lock:
            return {
                "state": self._state,
                "detail": self._detail,
                # 三个布尔值分开给，前端不用自己拼判断逻辑
                "vision_on": self._state == ST_RECORDING,
                "audio_on": bool(self.audio_enabled),
                # 是否"可以确定摄像头没在录"—— 只有 released 才为 True
                "confirmed_off": self._state == ST_RELEASED,
                "frames": self._n_frames,
                "errors": self._n_errors,
                "last_frame_ts": self._last_frame_ts,
                "started_at": self._started_at,
                "stopped_at": self._stopped_at,
                "interval": self._interval,
                "source": self._source_kind,
                "ts": _now(),
            }

    def _set(self, state, detail):
        with self._lock:
            old = self._state
            self._state = state
            self._detail = detail
        if old != state:
            append_jsonl(EVENTS_FILE, {"ts": _now(), "event": "state_change",
                                       "from": old, "to": state, "detail": detail})

    # ---------------- 启动 / 停止 ----------------

    def start(self, source="camera", interval=2.0, camera_index=0):
        with self._lock:
            # 上一个线程必须真的退出了才允许再启动（失败后立刻重启的常见场景）
            if self._thread is not None and self._thread.is_alive():
                return {"ok": False, "reason": "already_running", "status": self.status()}
            self._stop_flag.clear()
            self._source_kind = source
            self._interval = float(interval)
            self._n_errors = 0
            self._n_frames = 0
            self._last_frame_ts = None
            self._stopped_at = None
            self._started_at = _now()
            self._thread = threading.Thread(
                target=self._run, args=(source, float(interval), int(camera_index)),
                name="xy-recorder", daemon=True,
            )
            self._state = ST_CLOSING
            self._detail = "正在启动"
            self._thread.start()
        # 等线程真正落到"在录"或"故障"（模型加载要几秒，给足 20s）。
        deadline = time.time() + 20
        while time.time() < deadline:
            with self._lock:
                st = self._state
            if st in (ST_RECORDING, ST_FAULT, ST_RELEASED):
                break
            time.sleep(0.05)
        return {"ok": self._state == ST_RECORDING, "status": self.status()}

    def stop(self, timeout=10.0):
        """真停：置停止标志 → 等线程退出 → 线程内释放摄像头。

        两个坑（都已处理）：
          1. 启动还在进行中（CLOSING）时调用 stop，如果直接置 stop_flag，
             线程自己会把 CLOSING 覆盖成 RELEASED —— 看起来"成功关闭"，
             实际启动从没完成过。所以 stop 记录"调用前状态"，收尾时按它判。
          2. 故障态（FAULT）下不许报"已停"：故障是"我没法确定"，
             不能说成"已经关掉了"，两者话术和风险都不同。
        """
        with self._lock:
            prev = self._state
            prev_detail = self._detail
            if prev == ST_IDLE:
                return {"ok": True, "status": self.status()}
            self._set(ST_CLOSING, "正在关闭")
            self._stop_flag.set()
        th = self._thread
        if th is not None:
            th.join(timeout)
            if th.is_alive():
                # 线程卡住 = 无法确认是否停 → 报故障，不报 released
                self._set(ST_FAULT, "关闭超时，线程未退出")
                return {"ok": False, "status": self.status()}
        self._stopped_at = _now()
        with self._lock:
            st = self._state
        # ① 启动没完成就关：不能算"已停"（从没真正开始过），只能算"没能启动"
        if prev == ST_CLOSING and st == ST_RELEASED:
            self._set(ST_FAULT, "启动未完成就被关闭，无法确认设备状态")
            return {"ok": False, "status": self.status()}
        # ② 调用前就是故障态：停机 = 尝试恢复。能重新打开才说明故障已过去，
        #    此时可以升级为 released；但"恢复"必须靠真验证，不能凭状态字段。
        if prev == ST_FAULT:
            v = self.verify(expected_off=False)
            if v["result"] == "released":
                self._set(ST_RELEASED, f"故障已恢复（原故障：{prev_detail}）")
            else:
                self._set(ST_FAULT, f"故障未解除：{v['detail']}（原故障：{prev_detail}）")
            return {"ok": self._state == ST_RELEASED, "status": self.status()}
        if st != ST_RELEASED:
            self.verify(expected_off=False)
        return {"ok": self._state == ST_RELEASED, "status": self.status()}

    # ---------------- 验证：真停 / 故障 / 仍在录 ----------------

    def verify(self, expected_off=True):
        """打开摄像头试一次，用硬件反应判断真实状态 —— 不信任何状态字段。

        返回 {"result": "released"|"recording"|"fault"|"unknown", "detail": str}
            released  : 能重新打开 → 说明刚才确实释放了 ✅（只有这种能让 UI 说"已停"）
            recording : 我们自己还在录（状态即为 recording）
            fault     : 打不开 / 能打开但读不到帧 / 异常 → 不能确定在不在录 ⚠️
            unknown   : 此前已经处于故障态，本次不覆盖原故障（原故障更可信）

        expected_off：调用方期望"已经关掉了"。若当时状态是 recording，
        说明调用方没先 stop —— 直接按 recording 返回，不去抢设备。

        注意：本函数会短暂占用摄像头，只在验证时调用，不要高频调。
        """
        with self._lock:
            state = self._state
            src = self._source
        if state == ST_RECORDING and src is not None:
            return {"result": "recording", "detail": "本服务仍在录制中"}
        if state == ST_FAULT and expected_off:
            # 故障态下 verify 抢到设备也没意义：抢到的是"这一次"，不是"刚才那台"。
            # 保留原故障结论，避免把"没法确定"洗成"已停"。
            return {"result": "fault", "detail": f"此前已处于故障态：{self._detail}"}

        if self._source_kind != "camera":
            # 模拟源没有硬件，释放即真停
            ok = self._state == ST_RELEASED
            return {"result": "released" if ok else "fault",
                    "detail": "模拟源无硬件，状态即释放状态" if ok else self._detail}

        try:
            import cv2
        except ImportError:
            return {"result": "fault", "detail": "没装 opencv，无法验证"}

        cap = None
        try:
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                # 打不开有两种原因，区分开：被占用 vs 驱动/硬件故障
                detail = "摄像头打不开（可能被其他程序占用，或驱动/硬件故障）"
                self._set(ST_FAULT, detail)
                return {"result": "fault", "detail": detail}
            ok_read, _ = cap.read()
            if not ok_read:
                self._set(ST_FAULT, "摄像头能打开但读不到帧")
                return {"result": "fault", "detail": "摄像头能打开但读不到帧"}
            self._set(ST_RELEASED, "验证通过：设备已释放，可被重新打开")
            return {"result": "released", "detail": "设备已释放（验证通过）"}
        except Exception as e:      # noqa: BLE001  硬件层什么都可能抛
            self._set(ST_FAULT, f"验证异常：{e}")
            return {"result": "fault", "detail": f"验证异常：{e}"}
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass

    # ---------------- 录制线程 ----------------

    def _load_model(self):
        if self._model is None:
            from describe_frame import load_model
            self._model = load_model()
        return self._model

    def _read_first_frame(self, src, timeout=5.0):
        """启动自检：真的读到一帧才算"在录"。读不到返回 None。

        返回 (frame, meta) —— meta 留着给录制循环复用，避免首帧被白白校验掉。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._stop_flag.is_set():
                return None
            ok, frame, meta = src.read()
            if ok and frame is not None:
                return (frame, meta)
            time.sleep(0.05)
        return None

    def _run(self, source, interval, camera_index):
        src = None
        pending = None
        try:
            from sources import make_source
            model = self._load_model()
            if source == "camera":
                src = make_source("camera", index=camera_index, interval=interval)
            elif source == "image":
                src = make_source("image", interval=interval)
            else:
                src = make_source("sim", interval=interval)

            if not src.ok():
                self._set(ST_FAULT, "帧源不可用")
                return

            # 首帧自检：能打开 != 能出帧。USB 松动/驱动异常时 VideoCapture
            # 经常"能打开但读不到帧"—— 那种情况下报 recording 就是假装在录。
            # 这里先真读一帧，读不到就落 fault（前端显示"我没法确定"）。
            first = self._read_first_frame(src)
            if first is None:
                if self._stop_flag.is_set():
                    self._set(ST_RELEASED, "启动过程中被停止")
                    return
                self._set(ST_FAULT, "摄像头能打开但第一帧读不到（设备可能被占用/故障）")
                return
            pending = first      # (frame, meta) —— 录制循环第一拍直接用掉

            # ★ 先 import 再报"在录"：describe_frame 首次 import 要拉 torch/ultralytics，
            # 几秒钟的正常开销。如果在 import 之前就报 recording，用户会看到
            # "小忆在看"但一帧都不出 —— 那是另一种形式的假装在录。
            from describe_frame import describe_frame

            with self._lock:
                self._source = src
                self._started_at = _now()
            self._set(ST_RECORDING, f"{src.describe()} | 每 {interval}s 一帧")

            while not self._stop_flag.is_set():
                t0 = time.time()
                try:
                    ok, frame, meta = src.read()
                    # 首帧自检已经读过一帧（读过就走源内部节流，通常这一拍拿不到）。
                    # 手里有"先头帧"时先用它，不丢数据，也保证启动即出帧。
                    if not ok and pending is not None:
                        frame, meta, ok = pending[0], pending[1], True
                        pending = None
                    if ok and frame is not None:
                        # ★ 帧只在这一行之内存在：检测完就丢，不落盘
                        rec = describe_frame(frame, meta, model)
                        rec["source"] = getattr(src, "kind", source)
                        append_jsonl(FRAMES_FILE, rec)   # 只写结构化条目，不写帧
                        del frame                        # 主动断引用，不留内存
                        with self._lock:
                            self._n_frames += 1
                            self._last_frame_ts = rec["ts"]
                except Exception as e:      # noqa: BLE001  单帧失败不杀循环
                    with self._lock:
                        self._n_errors += 1
                        n = self._n_errors
                    if n % 20 == 1:
                        print(f"[WARN] 录制循环异常（第 {n} 次）：{e}")
                # 间隔是"处理完之后再等"，保证下限；不足的补足
                elapsed = time.time() - t0
                wait = max(0.0, interval - elapsed)
                self._stop_flag.wait(wait)

        except SystemExit as e:
            # load_model() 缺权重时会 sys.exit(1)。SystemExit 继承 BaseException，
            # 不会被下面的 except Exception 捕获；以前它会直接漏到 finally，
            # 被 finally 兜底写成 released —— 启动明明失败了，却报"已停"。
            # 现在单独拦下来：启动阶段退出 = 没开起来，必须落 fault。
            self._set(ST_FAULT, f"录制线程异常退出（启动阶段退出，code={e.code}）")
        except Exception as e:      # noqa: BLE001  启动失败要落到状态里
            self._set(ST_FAULT, f"录制线程启动失败：{e}")
        finally:
            # ★ 真停的唯一实现：释放硬件 + 删引用
            if src is not None:
                try:
                    src.release()
                except Exception:
                    pass
            with self._lock:
                self._source = None
                stopping = self._stop_flag.is_set()
                st = self._state
            # ★ 只有"用户主动 stop"（stop_flag 已置）才算已停。
            #   其它任何退出（异常 / SystemExit / 未知原因）都不能写 released：
            #   没经过停止流程就报"已停"、confirmed_off=true，就是假装关了。
            if stopping:
                if st != ST_RELEASED:
                    self._set(ST_RELEASED, "已停止并释放设备")
            elif st != ST_FAULT:
                self._set(ST_FAULT, "录制线程意外退出，未确认设备已释放")


# 进程内单例
controller = RecordingController()


def main():
    ap = argparse.ArgumentParser(description="H-2 持续本地录制管理")
    ap.add_argument("--source", default="camera", choices=["sim", "camera", "image"])
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--camera-index", type=int, default=0)
    ap.add_argument("--duration", type=float, default=0, help="跑几小时，0=一直跑")
    args = ap.parse_args()

    r = controller.start(source=args.source, interval=args.interval,
                         camera_index=args.camera_index)
    print(f"[{'OK' if r['ok'] else 'X'}] 启动 -> {r['status']['state']} | {r['status']['detail']}")
    if not r["ok"]:
        return 1

    deadline = time.time() + args.duration * 3600 if args.duration > 0 else None
    try:
        while True:
            if deadline and time.time() >= deadline:
                print("[TIMEUP] 到达设定时长，正常停止")
                break
            time.sleep(5)
            s = controller.status()
            print(f"[HEART] {s['state']} | 帧 {s['frames']} | 错 {s['errors']} "
                  f"| 最近 {s['last_frame_ts']}")
    except KeyboardInterrupt:
        print("\n[STOP] Ctrl+C")

    res = controller.stop()
    print(f"[{'OK' if res['ok'] else 'X'}] 停止 -> {res['status']['state']} | {res['status']['detail']}")

    v = controller.verify()
    print(f"[VERIFY] {v['result']} | {v['detail']}")
    return 0 if v["result"] == "released" else 1


if __name__ == "__main__":
    raise SystemExit(main())

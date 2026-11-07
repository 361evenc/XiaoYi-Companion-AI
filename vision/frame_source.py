# -*- coding: utf-8 -*-
"""虚拟帧源（模拟摄像头）—— 机器视觉线 ①-A

为什么这么设计
--------------
1. read() 的形状和 cv2.VideoCapture 一致：(ok, frame)
   frame 是 BGR 顺序的 uint8 ndarray，H×W×3
   → 将来换真机，只把内部读取换成 cap.read()，下游代码一行不用改
2. 额外返回 meta（时段 / 天气）
   真实摄像头给不了"天气"，这是模拟环境独有的信号
   → 天气走 meta，不要指望检测模型去认天气
3. 帧按目录顺序轮询，不随机 —— 保证同样输入能复现同样结果

图集怎么放
----------
    本目录下建 frames/，放 10~30 张 .jpg/.png，建议命名 01_flower.jpg ...
    可选 frames/meta.json 控制模拟标签：
        {"01_street.jpg": {"weather": "rain", "scene": "street"}}

用法
----
    python frame_source.py                        # 2 秒一帧，一直跑
    python frame_source.py --interval 1 --limit 30
    python frame_source.py --dir frames --interval 3
"""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def slot_of_hour(h):
    """把钟点映射成时段标签（模拟'外部世界的时间变化'）"""
    if 5 <= h < 9:
        return "morning"
    if 9 <= h < 12:
        return "noon"
    if 12 <= h < 18:
        return "afternoon"
    if 18 <= h < 22:
        return "evening"
    return "night"


def letterbox(img, out_w, out_h, fill=(0, 0, 0)):
    """等比缩放后补黑边到固定尺寸 —— 模拟真实摄像头的固定输出分辨率"""
    w, h = img.size
    scale = min(out_w / w, out_h / h)
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    resized = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", (out_w, out_h), fill)
    canvas.paste(resized, ((out_w - nw) // 2, (out_h - nh) // 2))
    return canvas


class FrameSource:
    """模拟摄像头。接口对齐 cv2.VideoCapture：read() -> (ok, frame)。"""

    def __init__(self, frames_dir="frames", interval=2.0, out_size=(1280, 720)):
        self.dir = Path(frames_dir)
        self.interval = float(interval)
        self.out_size = out_size
        self.files = []
        if self.dir.exists():
            self.files = sorted(
                p for p in self.dir.iterdir()
                if p.is_file() and p.suffix.lower() in IMG_EXT
            )
        self.meta_map = {}
        meta_file = self.dir / "meta.json"
        if meta_file.exists():
            try:
                self.meta_map = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"[WARN] meta.json 解析失败，按默认标签走：{e}")
        self.idx = 0
        self.frame_id = 0
        self._last_ts = 0.0

    def ok(self):
        """图集是否可用"""
        return bool(self.files)

    def read(self):
        """没到间隔时间就返回 (False, None, None) —— 和 cv2 的行为一致。"""
        if not self.files:
            return False, None, None

        now = time.time()
        if now - self._last_ts < self.interval:
            return False, None, None
        self._last_ts = now

        path = self.files[self.idx % len(self.files)]
        self.idx += 1

        # PIL 读进来是 RGB；先统一到固定输出尺寸（像真摄像头），再翻成 BGR 对齐 cv2
        try:
            with Image.open(path) as im:
                img = im.convert("RGB")
            if self.out_size:
                img = letterbox(img, self.out_size[0], self.out_size[1])
            rgb = np.asarray(img, dtype=np.uint8)
            frame = rgb[:, :, ::-1].copy()
        except Exception as e:
            # 单张图坏掉不许拖垮长跑（24 小时验收会踩到这条）
            print(f"[WARN] 读帧失败 {path.name}: {e}")
            return False, None, None

        self.frame_id += 1  # 只在成功出帧时递增，坏图不占号
        extra = self.meta_map.get(path.name, {}) or {}
        meta = {
            "frame_id": self.frame_id,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "path": str(path),
            "slot": slot_of_hour(datetime.now().hour),
            "weather": extra.get("weather", "sunny"),
            "scene": extra.get("scene", "unknown"),
        }
        return True, frame, meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="frames", help="图集目录")
    ap.add_argument("--interval", type=float, default=2.0, help="推帧间隔（秒）")
    ap.add_argument("--limit", type=int, default=0, help="取够 N 帧就停；0=一直跑")
    ap.add_argument("--out", default="1280x720",
                    help="固定输出尺寸，如 1280x720；填 raw 保持原图尺寸")
    ap.add_argument("--log", default="", help="每帧追加到这个文件（24 小时长跑用）")
    ap.add_argument("--heartbeat", type=int, default=100,
                    help="带 --log 时，控制台每 N 帧报一次心跳")
    ap.add_argument("--duration", type=float, default=0,
                    help="跑多少小时自动停；0 = 一直跑")
    args = ap.parse_args()

    out_size = None
    if args.out.lower() != "raw":
        try:
            w, h = args.out.lower().split("x")
            out_size = (int(w), int(h))
        except Exception:
            print(f"[X] --out 格式不对：{args.out}（应为 1280x720 或 raw）")
            return 1

    src = FrameSource(args.dir, args.interval, out_size)
    if not src.ok():
        print(f"[X] 图集目录里没有图片：{Path(args.dir).resolve()}")
        print("    先放 10 张 .jpg/.png 进去，再重新运行。")
        return 1

    size_txt = f"{out_size[0]}x{out_size[1]}" if out_size else "原图"
    print(f"[OK] 图集 {len(src.files)} 张 | 间隔 {src.interval}s | 输出 {size_txt} | 开始推帧（Ctrl+C 退出）")

    log_fh = open(args.log, "a", encoding="utf-8") if args.log else None
    deadline = time.time() + args.duration * 3600 if args.duration > 0 else None
    if deadline:
        print(f"[OK] 将在 {args.duration} 小时后自动停止")
    if log_fh:
        log_fh.write(
            f"# START {datetime.now().isoformat(timespec='seconds')} | "
            f"图集 {len(src.files)} 张 | 间隔 {src.interval}s | 输出 {size_txt}\n"
        )
        log_fh.flush()

    t0 = time.time()
    n = 0
    try:
        while True:
            if deadline and time.time() >= deadline:
                print("[TIMEUP] 到达设定时长，正常停止")
                break
            ok, frame, meta = src.read()
            if ok:
                n += 1
                line = (
                    f"[{n:04d}] {meta['ts']}  id={meta['frame_id']}  "
                    f"shape={frame.shape}  slot={meta['slot']}  "
                    f"weather={meta['weather']}  file={Path(meta['path']).name}"
                )
                if log_fh:
                    log_fh.write(line + "\n")
                    log_fh.flush()
                    if n % max(1, args.heartbeat) == 0:
                        print(f"[HEART] 第 {n} 帧 | 已跑 {(time.time() - t0) / 60:.1f} 分钟")
                else:
                    print(line)
                if args.limit and n >= args.limit:
                    break
            time.sleep(0.2)  # 轮询节流，别空转烧 CPU
    except KeyboardInterrupt:
        print("\n[STOP] 收到 Ctrl+C，正常退出")

    dt = time.time() - t0
    fps = n / dt if dt > 0 else 0
    summary = (f"[DONE] 共 {n} 帧 | 用时 {dt:.1f}s（{dt / 3600:.2f} 小时）"
               f" | 平均 {fps:.2f} 帧/秒")
    print(summary)
    if log_fh:
        log_fh.write(f"# END {datetime.now().isoformat(timespec='seconds')} | {summary}\n")
        log_fh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

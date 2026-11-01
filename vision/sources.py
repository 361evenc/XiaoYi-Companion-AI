r"""⑥ 帧源切换：模拟 / 真机摄像头 / 单图上传 —— 三种源，同一个接口

**这就是"换真机只换帧源"的兑现。** 三个类的 read() 签名完全一致：

    ok, frame, meta = src.read()
        ok    : bool，这一拍有没有新帧
        frame : BGR uint8 ndarray (H, W, 3) —— 和 cv2 一致
        meta  : dict(frame_id, ts, slot, weather, scene, path)

下游（describe_frame / perception_service / 查询接口）**一行都不用改**。

用法：
    from sources import make_source
    src = make_source("sim",    frames_dir="frames", interval=2)
    src = make_source("camera", index=0, interval=2)
    src = make_source("image",  image_path="frames/01_sky.jpg", interval=2)

三者的差别（也是真机会暴露的问题）：

| 项 | 模拟源 | 真机摄像头 |
|---|---|---|
| frame | PIL 读图 → 补边到 1280×720 | cv2 直接给 BGR |
| weather | meta.json 里写死（晴天/雨） | **没有** → "unknown" |
| scene | meta.json 里写死（客厅/厨房） | **没有** → "unknown" |
| 分辨率 | 固定 1280×720（letterbox） | 由摄像头决定，需强制设定 |
| 帧率 | 靠 interval 节流 | 相机自身帧率 + interval 节流 |
"""

import time
from datetime import datetime
from pathlib import Path

import sys
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def slot_of_hour(h):
    if 5 <= h < 9:
        return "morning"
    if 9 <= h < 12:
        return "noon"
    if 12 <= h < 18:
        return "afternoon"
    if 18 <= h < 22:
        return "evening"
    return "night"


class SimSource:
    """模拟源：包一层 ① 的 FrameSource，接口对齐"""

    kind = "sim"

    def __init__(self, frames_dir="frames", interval=2.0, out_size=(1280, 720)):
        from frame_source import FrameSource
        self._src = FrameSource(str(HERE / frames_dir) if not Path(frames_dir).is_absolute()
                                else frames_dir, interval, out_size)

    def ok(self):
        return self._src.ok()

    def read(self):
        return self._src.read()

    def describe(self):
        return f"模拟帧源 | {len(self._src.files)} 张图 | 补边到 {self._src.out_size}"

    def release(self):
        pass


class CameraSource:
    """真机源：cv2.VideoCapture。没有摄像头时抛 RuntimeError（清晰报错，不崩栈）"""

    kind = "camera"

    def __init__(self, index=0, interval=2.0, width=1280, height=720):
        try:
            import cv2
        except ImportError as e:
            raise RuntimeError("没装 opencv：pip install opencv-python") from e

        self.cv2 = cv2
        self.cap = cv2.VideoCapture(int(index))
        if not self.cap.isOpened():
            raise RuntimeError(
                f"打不开摄像头 index={index}。"
                "可能原因：没接摄像头 / 被其他程序占用 / 需要权限。"
                "先用 --source sim 跑模拟源。"
            )
        # 强制分辨率：真机默认分辨率往往不是我们想要的，必须设
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.interval = float(interval)
        self.width, self.height = width, height
        self._last = 0.0
        self._fid = 0
        self._fail_streak = 0

    def ok(self):
        return self.cap.isOpened()

    def read(self):
        now = time.time()
        if now - self._last < self.interval:
            return False, None, None
        self._last = now

        ok, frame = self.cap.read()
        if not ok or frame is None:
            self._fail_streak += 1
            if self._fail_streak % 10 == 1:
                print(f"[WARN] 摄像头读帧失败（连续 {self._fail_streak} 次）")
            return False, None, None
        self._fail_streak = 0
        self._fid += 1

        return True, frame, {
            "frame_id": self._fid,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "slot": slot_of_hour(datetime.now().hour),
            # ★ 真机拿不到这两项 —— 这是模拟源和真机最大的差别
            "weather": "unknown",
            "scene": "unknown",
            "path": f"camera:{self.width}x{self.height}",
        }

    def describe(self):
        return f"真机摄像头 | {self.width}x{self.height}"

    def release(self):
        try:
            self.cap.release()
        except Exception:
            pass


class ImageSource:
    """单图源：一张图反复出。用于"照片上传"链路和单元调试"""

    kind = "image"

    def __init__(self, image_path, interval=2.0, out_size=(1280, 720)):
        from PIL import Image
        import numpy as np
        from frame_source import letterbox

        self.path = Path(image_path)
        if not self.path.exists():
            raise RuntimeError(f"图片不存在：{self.path}")
        img = Image.open(self.path).convert("RGB")
        if out_size:
            img = letterbox(img, *out_size)
        self._frame = np.asarray(img, dtype=np.uint8)[:, :, ::-1].copy()
        self.interval = float(interval)
        self._last = 0.0
        self._fid = 0
        self._meta_extra = {}
        meta_file = self.path.parent / "meta.json"
        if meta_file.exists():
            import json
            try:
                self._meta_extra = json.loads(meta_file.read_text(encoding="utf-8")).get(self.path.name, {})
            except Exception:
                pass

    def ok(self):
        return True

    def read(self):
        now = time.time()
        if now - self._last < self.interval:
            return False, None, None
        self._last = now
        self._fid += 1
        return True, self._frame, {
            "frame_id": self._fid,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "slot": slot_of_hour(datetime.now().hour),
            "weather": self._meta_extra.get("weather", "unknown"),
            "scene": self._meta_extra.get("scene", "unknown"),
            "path": str(self.path),
        }

    def describe(self):
        return f"单图源 | {self.path.name}"

    def release(self):
        pass


def make_source(kind, **kw):
    kind = (kind or "sim").lower()
    if kind == "sim":
        return SimSource(**kw)
    if kind == "camera":
        return CameraSource(**kw)
    if kind == "image":
        return ImageSource(**kw)
    raise ValueError(f"未知帧源类型：{kind}（可选 sim / camera / image）")

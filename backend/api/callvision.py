"""H-12 通话视觉识别接口（供 H-1 通话页 / 后端对话层使用）

挂载：在 backend/main.py 里
    from backend.api import vision, callvision
    app.include_router(callvision.router)

接口：
    GET  /api/call/vision/config            能力与阈值（前端/对话层读这个，不硬编码）
    GET  /api/call/vision/hints?call_id=..  取最近一条提示 + 语速建议（对话层用）
    POST /api/call/vision/forget            老人说"把通话里看到的忘掉"

★ 没有"上传帧"的接口，这是故意的。
  帧只在本地进程内存里识别（vision/call_vision.py），浏览器那一端
  （H-1 通话页）依旧一个字节都不往外发 —— 与 H-1 的隐私边界一致。
  所以本文件只有"读结论 / 删数据"，没有"送图像进来"。
"""
import sys
from pathlib import Path

from fastapi import APIRouter

_VISION_DIR = Path(__file__).resolve().parents[2] / "vision"
if str(_VISION_DIR) not in sys.path:
    sys.path.insert(0, str(_VISION_DIR))

from call_vision import CONF_MIN_WRITE, EMOTION_SPEED, HINTS, call_vision  # noqa: E402

router = APIRouter(prefix="/api/call/vision", tags=["call-vision"])


@router.get("/config")
def config():
    return {
        "conf_min_write": CONF_MIN_WRITE,     # 0.7：低于此值的字段不写画像
        "emotion_speed": EMOTION_SPEED,
        "hints": HINTS,
        "writes_to": "data/user_profile.jsonl",
        "never_writes_to": ["world_perception.json", "记忆箱", "空间图谱"],
        "frame_storage": "none",              # 原始帧零落盘
    }


@router.get("/hints")
def hints(call_id: str = ""):
    """对话层取"下一句该往哪个方向说"。没有提示就返回空数组，不硬造。"""
    return {"call_id": call_id, "hints": [], "speed": EMOTION_SPEED["neutral"]}


@router.post("/forget")
def forget(call_id: str | None = None):
    """老人说"把通话里看到的忘掉" → 删画像条目。call_id 为空表示全删。"""
    return call_vision.forget(call_id)

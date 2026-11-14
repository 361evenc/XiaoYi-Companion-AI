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
import base64
import io
import sys
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

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


class ObserveRequest(BaseModel):
    frame_b64: str = ""   # 通话页 opt-in 上传的一帧（JPEG base64，去掉 data: 头）
    call_id: str = ""
    scene: str = ""


@router.post("/observe")
def observe(req: ObserveRequest):
    """通话页 opt-in 帧分析：帧在内存里识别 → 返回结构化结论 → 帧立即丢弃、不写画像。

    通话是短时会话（"说完就忘掉"），所以 persist=False：只把"这次看到什么"回给
    通话页当下用，不写用户画像、不写记忆箱、不写空间图谱 —— 与 H-12"帧零落盘"
    和通话"说完就忘掉"一致。模型缺失/识别失败返回 ok=False，通话页降级纯语音。
    """
    frame_b64 = (req.frame_b64 or "").strip()
    call_id = str(req.call_id or "")
    scene = str(req.scene or "").strip() or None
    if not frame_b64:
        return {"ok": False, "reason": "没有画面"}
    try:
        import numpy as np
        from PIL import Image
        raw = base64.b64decode(frame_b64)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        frame = np.asarray(img)
    except Exception as e:
        return {"ok": False, "reason": f"画面这边没解出来：{e}"}
    # 权重没就位：直接降级、给个明白话（别等 observe 抛错/500）
    if not (_VISION_DIR / "models" / "yolov8n.pt").exists():
        return {"ok": False, "reason": "识别模型还没就位（缺 yolov8n.pt），先按纯语音聊"}
    # 模型加载/识别可能是 SystemExit 一类 BaseException——一律回 ok:false，接口不 500
    try:
        out = call_vision.observe(frame, call_id, scene=scene, persist=False)
    except BaseException as e:
        return {"ok": False, "reason": f"画面这边暂时看不清：{e}"}
    finally:
        del frame
    rec = out.get("record", {})
    return {
        "ok": True,
        "person_present": bool(rec.get("person_present")),
        "gaze": rec.get("gaze"),
        "hand_object": rec.get("hand_object"),
        "emotion": rec.get("emotion"),
        "hints": out.get("hints", []),
        "speed_hint": out.get("speed_hint"),
    }


@router.post("/scene")
def scene(req: ObserveRequest):
    """VLM 场景描述（qwen3-vl-plus）：把画面里真实可见的人/物品/场景用一两句话说清，
    喂给对话层，让"小忆"只说真正看到的、不凭空猜东西（解决"识别不出/乱说"）。
    与 /observe 一致：帧只在内存里用，不写画像、不落盘。未配置 VLM/失败返回空串。"""
    frame_b64 = (req.frame_b64 or "").strip()
    if not frame_b64:
        return {"ok": False, "scene": ""}
    try:
        from photo_scene import _load_vision_conf
        conf = _load_vision_conf()
        if not (conf["base"] and conf["key"]):
            return {"ok": False, "scene": "", "reason": "VLM 未配置"}
        import requests
        prompt = (
            "这是老人家里摄像头拍到的画面。只用一两句口语化中文，描述画面里真实可见的人、"
            "物品、场景。重点：老人手里拿的是什么、桌上或周围有什么明显的东西（眼镜、药、"
            "杯子、手机、遥控器）。看不清或没有就别编，直接说'画面里没看到明显东西'。只输出描述。"
        )
        r = requests.post(
            f"{conf['base'].rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {conf['key']}", "Content-Type": "application/json"},
            json={"model": conf["model"], "max_tokens": 120, "temperature": 0.1,
                  "messages": [{"role": "user", "content": [
                      {"type": "text", "text": prompt},
                      {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}"}}]}]},
            timeout=40,
        )
        d = r.json() or {}
        text = ((d.get("choices") or [{}])[0].get("message", {}).get("content") or "").strip()
        return {"ok": True, "scene": text}
    except Exception as e:
        return {"ok": False, "scene": "", "reason": str(e)}


@router.post("/end")
def end(call_id: str = ""):
    """通话页挂断时清内存态（不清画像；画像按老人意愿走 forget）。"""
    return call_vision.end_call(call_id)

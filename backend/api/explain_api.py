"""H-13 视频解释外界接口（电视 / 门口 / 天气，安全优先）

挂载：backend/main.py
    from backend.api import ..., explain_api
    app.include_router(explain_api.router)

接口：
    GET  /api/explain/config                能力状态 + 阈值
    POST /api/explain/tick                  {what?}  主循环每拍调：从当前帧解释一次
    GET  /api/explain/status                统计（含被安全告警抢占的次数）

★ 为什么 /tick 不接收帧：与 H-2 / H-12 / H-7 一致，帧在本地进程内产生和消费，
  不出进程。后端要"解释一下外面"，调 tick 即可，由 vision 侧抓帧。

★ 为什么没有"强制解释"参数：安全优先不能靠调用方自觉。要绕过告警去解释，
  物理上没有这个入口。
"""
import json
import sys
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

_VISION_DIR = Path(__file__).resolve().parents[2] / "vision"
if str(_VISION_DIR) not in sys.path:
    sys.path.insert(0, str(_VISION_DIR))

from explain_world import (  # noqa: E402
    SCENE_CONF_MIN, TV_CONF_MIN, explainer,
)

router = APIRouter(prefix="/api/explain", tags=["explain-world"])


class TickReq(BaseModel):
    what: str = "all"
    source: str = "camera"        # camera | sim
    interval: float = 5.0
    index: int = 0


@router.get("/config")
def config():
    return {
        "thresholds": {"tv": TV_CONF_MIN, "scene": SCENE_CONF_MIN},
        "priority": "safety_first",
        "note": "跌倒告警进行中时解释器不产生任何话术；冲突时安全无条件抢占",
        "privacy": {
            "door": "只登记'有人来过'，不记是谁（B-4）",
            "tv": "只给节目类型，不做内容复述",
            "weather": "室内不判天气（室内看不到天）",
        },
    }


@router.post("/tick")
def tick(req: TickReq):
    """从当前帧解释一次。若安全告警进行中，直接返回 suppressed。"""
    from sources import make_source
    kw = {"index": req.index} if req.source == "camera" else {}
    src = make_source(req.source, interval=0, **kw)
    try:
        ok, frame, meta = src.read()
    finally:
        try:
            src.release()
        except Exception:
            pass
    if not ok or frame is None:
        return {"ok": False, "reason": "no_frame"}

    r = explainer.explain(frame, req.what, meta={"source": req.source})
    del frame
    return r


@router.get("/status")
def status():
    return explainer.status()

"""H-7 跌倒/地滑安全告警接口（供 B-11 紧急转报 + 子女端 B-9 使用）

挂载：在 backend/main.py 里
    from backend.api import vision, callvision, fallsafety
    app.include_router(fallsafety.router)

接口：
    GET  /api/fall/status               当前状态 + 统计
    GET  /api/fall/poll                 取走待转报事件（取走即清，防重复通知子女）
    POST /api/fall/answer?text=...      老人应答（ASR 结果送进来）
    GET  /api/fall/config               阈值与话术（前端/对话层读这个）

★ 没有"喂帧"的接口：检测在主进程内跑（vision/fall_guard.py），
  与 H-1/H-12 一致——帧不出本地进程。

★ 告警是"确认 + 转达"，不是"诊断"：
  本模块所有对外文本都不含医疗判断词，`check_medical_wording()` 可被
  第二人 review 时直接调用来验（任务卡验收项：0 医疗诊断）。
"""
import sys
from pathlib import Path

from fastapi import APIRouter

_VISION_DIR = Path(__file__).resolve().parents[2] / "vision"
if str(_VISION_DIR) not in sys.path:
    sys.path.insert(0, str(_VISION_DIR))

from fall_guard import (  # noqa: E402
    ASK_AGAIN, ASK_TEXT, BOTTOM_MIN, CONF_MIN_TRIGGER, CONFIRM_FRAMES,
    NO_ANSWER_TEXT, TIMEOUT_ANSWER_S, VERTICALITY_FALL, check_medical_wording, guard,
)

router = APIRouter(prefix="/api/fall", tags=["fall-safety"])


@router.get("/status")
def status():
    return guard.status()


@router.get("/poll")
def poll():
    """B-11 紧急转报轮询入口。有事件返回事件，没有返回 {"event": null}。"""
    ev = guard.poll_escalation()
    return {"event": ev}


@router.post("/answer")
def answer(text: str = ""):
    return guard.answer(text)


@router.get("/config")
def config():
    return {
        "conf_min_trigger": CONF_MIN_TRIGGER,     # 0.5 低阈值保召回
        "aspect_fall": 1.15,
        "verticality_fall": VERTICALITY_FALL,     # 框顶比例，防"并躺"误报
        "bottom_min": BOTTOM_MIN,
        "confirm_frames": CONFIRM_FRAMES,
        "timeout_answer_s": TIMEOUT_ANSWER_S,
        "ask_text": ASK_TEXT,
        "ask_again": ASK_AGAIN,
        "no_answer_text": NO_ANSWER_TEXT,
        "medical_wording_ok": check_medical_wording(
            ASK_TEXT + ASK_AGAIN + NO_ANSWER_TEXT)["ok"],
        "auto_dial_120": False,                   # 诚实声明：不自主拨 120
    }

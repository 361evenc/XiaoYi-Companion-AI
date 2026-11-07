"""H-2 录制状态 API（供前端 F-2 指示灯/开关调用）

挂载方式：在 backend/main.py 里加两行
    from backend.api import vision
    app.include_router(vision.router)

接口（F-2 只调这三个，不要直接读 vision 的 data 文件）：
    GET  /api/vision/status     状态快照（指示灯）
    POST /api/vision/start      开（默认开）
    POST /api/vision/stop       关 -> 真停 + 验证
    GET  /api/vision/verify     主动验证（真停 / 故障 / 仍在录）

设计原则（来自任务卡 F-2 第 3 条）：
    "说停了实际还在录"是信任头号杀手。所以 stop 之后**必须**调用 verify，
    把验证结论放进响应里；前端按 verified 显示话术，不按 ok 显示。
    打不开摄像头 ≠ 已停 —— 前者是 fault，后者是 released，两种话术。
"""
import sys
from pathlib import Path

from fastapi import APIRouter

# vision/ 不在 backend 包内，用项目根下的相对路径挂上去
_VISION_DIR = Path(__file__).resolve().parents[2] / "vision"
if str(_VISION_DIR) not in sys.path:
    sys.path.insert(0, str(_VISION_DIR))

from recorder import controller  # noqa: E402

router = APIRouter(prefix="/api/vision", tags=["vision"])


@router.get("/status")
def status():
    return controller.status()


@router.post("/start")
def start(source: str = "camera", interval: float = 2.0, camera_index: int = 0):
    r = controller.start(source=source, interval=interval, camera_index=camera_index)
    return {"ok": r["ok"], "status": r["status"]}


@router.post("/stop")
def stop():
    r = controller.stop()
    # 停止后立刻验证："关了"必须是硬件层可验证的事实，不是状态字段的口头承诺
    v = controller.verify()
    return {
        "ok": r["ok"],
        "verified": v["result"],          # released / fault / recording
        "verify_detail": v["detail"],
        "status": controller.status(),
    }


@router.get("/verify")
def verify():
    v = controller.verify()
    return {"verified": v["result"], "detail": v["detail"], "status": controller.status()}

"""H-4 识图场景扩展接口（老照片人物 / 药品说明书）

挂载：backend/main.py
    from backend.api import ..., photo_scene_api
    app.include_router(photo_scene_api.router)

接口：
    GET  /api/photo/config                      两个场景各自的可用状态（OCR 装没装、VLM 配没配）
    POST /api/photo/elder                      {path, known_people?}  老照片 → 猜+确认
    POST /api/photo/elder/confirm              {path, accepted, name?, relation?}  确认/否认
    POST /api/photo/medicine                   {path}                 说明书 → 结构化 + 大字稿
    GET  /api/photo/medicine/check?text=...    越界话术自查（第二人 review 用）

★ 为什么只收 path 不收 base64：这两个场景处理的是**已经存在本地**的图（记忆箱里的
  老照片、刚拍完的说明书），不额外在接口层复制一份图数据。原始图不出本地进程，
  与 H-2/H-12 同一条线：帧/图不往上传，只有和 VLM 网关通信时才发（且药品优先走本地 OCR）。

★ 老照片不写库：/elder 只返回"猜 + 问句"，写入由 /confirm 确认后交给 B-5。
  这条是任务卡"猜+确认，不断言"的接口层体现——**没有"直接写入"的接口**。
"""
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

_VISION_DIR = Path(__file__).resolve().parents[2] / "vision"
if str(_VISION_DIR) not in sys.path:
    sys.path.insert(0, str(_VISION_DIR))

from photo_scene import (  # noqa: E402
    REQUIRED_FIELDS, RX_MARKERS, scene_engine, vision_ready,
)

router = APIRouter(prefix="/api/photo", tags=["photo-scene"])

# 记住最近的判定，供 /confirm 复用（不落盘：确认动作很快，进程重启丢了就再问一次）
_LAST = {}


class ElderReq(BaseModel):
    path: str
    known_people: list[dict] = []


class ConfirmReq(BaseModel):
    path: str
    accepted: bool
    name: str | None = None
    relation: str | None = None


class MedReq(BaseModel):
    path: str


def _check_path(p: str):
    fp = Path(p)
    if not fp.exists():
        raise HTTPException(404, f"图片不存在：{p}")
    if fp.stat().st_size > 20 * 1024 * 1024:
        raise HTTPException(413, "图片太大（>20MB）")
    return fp


@router.get("/config")
def config():
    return {
        "elder_photo": {
            "ready": True,
            "needs_confirm": True,
            "note": "猜+确认；不确认不写库。记忆库人物由 B-5 提供 known_people",
        },
        "medicine": {
            "local_ocr": scene_engine.med.ocr.local_available(),
            "vlm_fallback": vision_ready(),
            "required_fields": REQUIRED_FIELDS,
            "rx_markers": RX_MARKERS,
            "note": "本地 OCR 优先（药品不上云）；本地不可用且未配 VLM → 明确返回 ocr_unavailable",
        },
    }


@router.post("/elder")
def elder(req: ElderReq):
    fp = _check_path(req.path)
    out = scene_engine.elder_photo(fp, req.known_people)
    _LAST[str(fp)] = out
    return out


@router.post("/elder/confirm")
def elder_confirm(req: ConfirmReq):
    fp = _check_path(req.path)
    an = _LAST.get(str(fp))
    if an is None:
        raise HTTPException(409, "没有待确认的识别结果，请先调 /api/photo/elder")
    intent = scene_engine.photo.confirm(an, req.accepted, req.name, req.relation)
    _LAST.pop(str(fp), None)
    # 只返回"要写入什么"，不在此处写 B-5 —— 写入是 B-5 的职责，本接口不越界
    return {"ok": True, "intent": intent,
            "reply": ("好，我记住了。" if intent["action"] == "bind_person"
                      else "那我再问问您，别记错了。")}


@router.post("/medicine")
def medicine(req: MedReq):
    fp = _check_path(req.path)
    return scene_engine.medicine_label(fp)


@router.get("/medicine/check")
def medicine_check(text: str):
    hits = scene_engine.med.find_forbidden(text)
    return {"text": text, "forbidden": hits, "safe": not hits}

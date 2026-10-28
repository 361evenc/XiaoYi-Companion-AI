"""记忆/隐私接口：忘掉一件事 + 清空全部记忆。"""
from fastapi import APIRouter
from pydantic import BaseModel

from backend.services import memory_service

router = APIRouter(prefix="/api", tags=["memory"])


class ForgetRequest(BaseModel):
    text: str = ""


class WipeRequest(BaseModel):
    is_muted: bool = False


@router.post("/forget")
def api_forget(req: ForgetRequest):
    return memory_service.forget(req.text)


@router.delete("/memory/wipe")
def api_wipe(req: WipeRequest = None):
    # DELETE 带 body 不被部分客户端支持，允许缺省（默认不静音出提示音）
    return memory_service.wipe(bool(req.is_muted) if req else False)


@router.get("/memory")
def api_memory_box():
    """记忆箱：重要事件 + 照片 + 小忆的理解 + 用户画像。"""
    return memory_service.box()


@router.get("/emotion-chart")
def api_emotion_chart():
    """心情曲线图（数据不足时 image_url 为 null）。"""
    return memory_service.emotion_chart()

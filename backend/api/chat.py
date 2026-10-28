"""聊天接口：一次打包返回「回复 + 音频 + 会话id + 面板快照」。"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.services import chat_service

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    user_input: str = ""
    chat_history: List[Dict[str, Any]] = []
    surname: str = ""
    gender: str = Field("女", pattern="^(女|男)$")
    is_muted: bool = False
    personality: str = "踏实务实"
    conv_id: Optional[str] = None


class SetupRequest(BaseModel):
    surname: str = ""
    gender: str = Field("女", pattern="^(女|男)$")
    personality: str = "踏实务实"
    is_muted: bool = False


class PhotoRequest(BaseModel):
    photo_base64: str = ""
    mime: str = "image/jpeg"
    chat_history: List[Dict[str, Any]] = []
    surname: str = ""
    gender: str = Field("女", pattern="^(女|男)$")
    is_muted: bool = False
    personality: str = "踏实务实"
    conv_id: Optional[str] = None


class RecallRequest(BaseModel):
    conv_id: str = ""


class ProactiveRequest(BaseModel):
    is_muted: bool = False
    conv_id: Optional[str] = None


@router.post("/chat")
def api_chat(req: ChatRequest):
    return chat_service.chat(
        user_input=req.user_input,
        chat_history=req.chat_history,
        surname=req.surname,
        gender=req.gender,
        is_muted=req.is_muted,
        personality=req.personality,
        conv_id=req.conv_id,
    )


@router.post("/photo")
def api_photo(req: PhotoRequest):
    return chat_service.photo(
        photo_base64=req.photo_base64,
        mime=req.mime,
        chat_history=req.chat_history,
        surname=req.surname,
        gender=req.gender,
        is_muted=req.is_muted,
        personality=req.personality,
        conv_id=req.conv_id,
    )


@router.post("/recall")
def api_recall(req: RecallRequest):
    return chat_service.recall(req.conv_id)


@router.post("/proactive")
def api_proactive(req: ProactiveRequest):
    """主动关怀轮询：前端每 30 秒调一次，无触发时轻量返回 triggered=false。"""
    return chat_service.proactive(is_muted=req.is_muted, conv_id=req.conv_id)


@router.post("/setup")
def api_setup(req: SetupRequest):
    return chat_service.setup(
        surname=req.surname,
        gender=req.gender,
        personality=req.personality,
        is_muted=req.is_muted,
    )
"""语音接口：TTS 整段合成 + ASR 识别（F-8 起支持方言选择）。"""
from fastapi import APIRouter
from pydantic import BaseModel

from backend.services import voice_service

router = APIRouter(prefix="/api", tags=["voice"])


class TTSRequest(BaseModel):
    text: str = ""
    voice_type: str | None = None
    speed: float = 0.8
    pitch: float = 1.0
    volume: float = 1.3


class ASRRequest(BaseModel):
    audio_base64: str = ""
    # F-8：方言由前端切换，后端闸门决定「能不能听」；未上线的方言不调引擎
    dialect: str | None = None


@router.post("/tts")
def api_tts(req: TTSRequest):
    path = voice_service.tts(req.text, voice_type=req.voice_type,
                             speed=req.speed, pitch=req.pitch, volume=req.volume)
    return {"audio_url": voice_service.audio_url(path)}


@router.post("/asr")
def api_asr(req: ASRRequest):
    return voice_service.asr(req.audio_base64, dialect=req.dialect)

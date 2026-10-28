"""语音服务：TTS 整段合成 + ASR 识别（火山引擎）。

封装旧版 app.text_to_speech / app.transcribe_audio，只做参数规整。
"""
import base64
import os

from backend.core.state import engine


def tts(text, voice_type=None, speed=0.8, pitch=1.0, volume=1.3):
    """合成整段语音，返回音频文件路径（失败返回 None）。"""
    if not text or not str(text).strip():
        return None
    return engine.text_to_speech(str(text), voice_type=voice_type,
                                 speed=speed, pitch=pitch, volume=volume)


def audio_url(path):
    return "/audio/" + os.path.basename(path) if path else None


def asr(audio_base64):
    """base64 WAV → 识别文本（失败返回空串）。"""
    if not audio_base64:
        return ""
    try:
        wav_bytes = base64.b64decode(audio_base64)
    except Exception:
        return ""
    if len(wav_bytes) < 200:
        return ""
    return engine.transcribe_audio(wav_bytes)
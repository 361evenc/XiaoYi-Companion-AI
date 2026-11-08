"""语音服务：TTS 整段合成 + ASR 识别（火山引擎）。

封装旧版 app.text_to_speech / app.transcribe_audio，只做参数规整。
F-8：方言 ASR 先过 dialect_service 闸门——未上线方言不调引擎、不硬上，
直接回「我还没学会听X方言，等我学好了」。
"""
import base64
import os

from backend.core.state import engine
from backend.services import dialect_service


def tts(text, voice_type=None, speed=0.8, pitch=1.0, volume=1.3):
    """合成整段语音，返回音频文件路径（失败返回 None）。"""
    if not text or not str(text).strip():
        return None
    return engine.text_to_speech(str(text), voice_type=voice_type,
                                 speed=speed, pitch=pitch, volume=volume)


def audio_url(path):
    return "/audio/" + os.path.basename(path) if path else None


def asr(audio_base64, dialect=None):
    """base64 WAV → {text, refused, reply, dialect}（识别失败时 text 为空串）。

    方言闸门在后端算（不是前端能力）：
      - 普通话/未指定 → 照常识别
      - 上线方言     → 带后端选型给出的引擎参数识别
      - 其它         → refused=True + 固定诚实话术，不返回残文
    """
    gate = dialect_service.asr_gate(dialect)
    if gate.get("refused"):
        return {"text": "", "refused": True, "reply": gate.get("reply"),
                "dialect": gate.get("dialect"), "reason": gate.get("reason")}

    if not audio_base64:
        return {"text": "", "refused": False, "dialect": gate.get("dialect")}
    try:
        wav_bytes = base64.b64decode(audio_base64)
    except Exception:
        return {"text": "", "refused": False, "dialect": gate.get("dialect")}
    if len(wav_bytes) < 200:
        return {"text": "", "refused": False, "dialect": gate.get("dialect")}

    text = engine.transcribe_audio(wav_bytes, dialect_hint=gate.get("hint"))
    return {"text": text or "", "refused": False, "dialect": gate.get("dialect")}

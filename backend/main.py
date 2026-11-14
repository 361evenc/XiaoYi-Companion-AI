"""FastAPI 入口：加载旧版引擎 + 挂载路由 + 托管音频 + 启动后台线程。

启动方式（必须在项目根目录，单 worker）：
    uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.core import config  # noqa: F401  先切 sys.path 与 CWD
from backend.core.state import engine
from backend.core import background
from backend.api import (chat, voice, history, memory, reminder, ws, emergency,
                         vision, callvision, fallsafety, photo_scene_api, explain_api,
                         child_api, dialect_api, medication, cold_start)

app = FastAPI(title="小忆陪伴 · 后端服务")

# 单机单人开发阶段：放开跨域，方便 Vite 前端本地联调
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# TTS 音频文件落在临时目录，挂到 /audio 供前端 <audio> 播放
app.mount("/audio", StaticFiles(directory=engine.TEMP_AUDIO_DIR), name="audio")
# 记忆箱照片落在项目根 photos/，挂到 /photos 供记忆页缩略图展示
app.mount("/photos", StaticFiles(directory=engine.PHOTOS_DIR), name="photos")

app.include_router(chat.router)
app.include_router(voice.router)
app.include_router(history.router)
app.include_router(memory.router)
app.include_router(reminder.router)
app.include_router(emergency.router)
app.include_router(medication.router)  # T-4：用药闭环（余量自报/购药待办/未回应通知子女）
app.include_router(cold_start.router)  # T-3：冷启动编排（Day1-7 脚本，每天学一件事）
app.include_router(ws.router)
app.include_router(vision.router)   # H-2：录制状态/开关（F-2 指示灯用）
app.include_router(callvision.router)  # H-12：通话识别结论/删除（无上传帧接口）
app.include_router(fallsafety.router)  # H-7：跌倒告警状态/转报（无喂帧接口）
app.include_router(photo_scene_api.router)  # H-4：老照片猜+确认 / 说明书只读不判断
app.include_router(explain_api.router)  # H-13：解释外界（安全优先，无强制解释入口）
app.include_router(child_api.router)  # F-7：子女端只读摘要（无聊天/无会话路由）
app.include_router(dialect_api.router)  # F-8：方言闸门/采集表单（上线清单由后端算）


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "local_model_ok": engine.LOCAL_MODEL_OK,
        "personality": engine.UI_STATE.get("personality"),
    }


@app.on_event("startup")
def _startup():
    background.start()
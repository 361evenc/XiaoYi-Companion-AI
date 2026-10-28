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
from backend.api import chat, voice, history, memory, reminder, ws

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
app.include_router(ws.router)


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
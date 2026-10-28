"""主动关怀 WebSocket：把到点的提醒实时推给聊天页。

旧版靠 Gradio 定时轮询 reminder_queue；新版改为 WebSocket 推送：
到点提醒消息先落盘进当前对话，再推给前端（前端播放提示音 + 大字幕）。
单机单人：一个连接独立 drain 队列即可，无广播竞争问题。
"""
import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.core.state import engine

router = APIRouter()

# 每秒检查一次队列（与旧版 Gradio 1 秒轮询节奏一致）
POLL_SECONDS = 1.0


@router.websocket("/ws")
async def ws_reminder_push(websocket: WebSocket):
    """前端连接：ws://host/ws?conv_id=xxx。到点提醒实时下发。"""
    await websocket.accept()
    conv_id = (websocket.query_params.get("conv_id") or "").strip()
    try:
        while True:
            # 取走调度线程攒下的到点提醒（deque 在 CPython 下 append/popleft 原子）
            fired = []
            while engine.reminder_queue:
                fired.append(engine.reminder_queue.popleft())
            for item in fired:
                msg = str(item.get("message") or "").strip()
                if not msg:
                    continue
                _persist_reminder(conv_id, msg)
                await websocket.send_json({"type": "reminder", "text": msg})
            await asyncio.sleep(POLL_SECONDS)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        # 连接异常（断网/页面关闭）静默收尾，前端有自动重连
        print(f"[ws] 连接结束: {e}")


def _persist_reminder(conv_id, msg):
    """把提醒消息存进当前对话，刷新历史页也能看到（旧版 poll 同语义）。"""
    if not conv_id:
        return
    try:
        messages, _ = engine.get_conversation_messages_by_id(conv_id)
        if messages is None:
            messages = []
        messages = list(messages)
        messages.append({"role": "assistant", "content": msg})
        engine.save_conversation(conv_id, messages)
    except Exception as e:
        print(f"[ws] 提醒落盘失败: {e}")

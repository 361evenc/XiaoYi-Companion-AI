"""历史对话接口：列表 / 加载 / 新建 / 删除 / 搜索。"""
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from backend.core.state import engine

router = APIRouter(prefix="/api/conversations", tags=["history"])


def _conv_dict(c):
    return {
        "id": c.get("id"),
        "title": c.get("title", "新对话"),
        "created": c.get("created", 0),
        "updated": c.get("updated", 0),
        "time_str": datetime.fromtimestamp(c.get("updated", 0)).strftime("%m-%d %H:%M"),
    }


@router.get("")
def list_conversations(search: str | None = None):
    convs = engine.load_all_conversations()
    if search:
        convs = [c for c in convs if search in c.get("title", "")]
    return {"conversations": [_conv_dict(c) for c in convs[:50]]}


@router.get("/{conv_id}")
def get_conversation(conv_id: str):
    messages, _ = engine.get_conversation_messages_by_id(conv_id)
    if messages is None:
        return {"conv_id": conv_id, "messages": []}
    return {"conv_id": conv_id, "messages": messages}


class NewConversationRequest(BaseModel):
    pass


@router.post("")
def create_conversation(_req: NewConversationRequest | None = None):
    history, new_id, _ = engine.new_conversation()
    reply = ""
    for msg in reversed(history or []):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            reply = engine.extract_text(msg.get("content", "")).strip()
            break
    return {"conv_id": new_id, "chat_history": history, "reply": reply}


@router.delete("/{conv_id}")
def delete_conversation(conv_id: str):
    engine.delete_conversation_by_id(conv_id)
    return {"ok": True}
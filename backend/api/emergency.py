"""紧急联络接口（B-11）：状态 / 登记（三路径）/ 复述确认 / 主动呼叫。"""
from fastapi import APIRouter
from pydantic import BaseModel

from backend.services import emergency_service

router = APIRouter(prefix="/api/emergency", tags=["emergency"])


class SubmitRequest(BaseModel):
    name: str = ""
    phone: str = ""
    relation: str = ""
    source: str = "elder"      # elder(老人自报)/child(子女端代填)/staff(上门代填)


class ConfirmRequest(BaseModel):
    reply: str = ""


class CallRequest(BaseModel):
    target: str = "contact"     # contact / 120


@router.get("")
def api_status():
    return emergency_service.status()


@router.post("/contact/submit")
def api_submit(req: SubmitRequest):
    return emergency_service.submit(req.name, req.phone, req.relation, req.source)


@router.post("/contact/confirm")
def api_confirm(req: ConfirmRequest):
    return emergency_service.resolve_confirm(req.reply)


@router.post("/call")
def api_call(req: CallRequest):
    return emergency_service.request_call(req.target)

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


# ---- T-5 救命链路（item1-4：触发/AI确认/倒计时/路径决定）----

class RescueTriggerRequest(BaseModel):
    word: str = "救命"     # 救命 / 打120


class RescueReplyRequest(BaseModel):
    reply: str = ""


@router.get("/rescue/status")
def api_rescue_status():
    """救命链路状态总览（供前端轮询）。"""
    return emergency_service.rescue_status()


@router.post("/rescue/trigger")
def api_rescue_trigger(req: RescueTriggerRequest):
    """喊'救命'/'打120' 触发（item4 路径决定 + 30s可取消倒计时）。"""
    return emergency_service.rescue_trigger(req.word)


@router.post("/rescue/confirm")
def api_rescue_confirm(req: RescueReplyRequest):
    """AI确认答复（item2，含无应答分支）。"""
    return emergency_service.rescue_confirm_reply(req.reply)


@router.post("/rescue/countdown")
def api_rescue_countdown(req: RescueReplyRequest):
    """倒计时期间答复（item3，可取消）。"""
    return emergency_service.rescue_countdown_reply(req.reply)

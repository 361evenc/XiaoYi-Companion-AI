"""提醒接口：列表 / 完成 / 取消。"""
from fastapi import APIRouter

from backend.services import reminder_service

router = APIRouter(prefix="/api/reminders", tags=["reminders"])


@router.get("")
def api_list():
    return reminder_service.list()


@router.post("/{rid}/done")
def api_done(rid: str):
    return reminder_service.done(rid)


@router.post("/{rid}/cancel")
def api_cancel(rid: str):
    return reminder_service.cancel(rid)

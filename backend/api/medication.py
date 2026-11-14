"""用药闭环接口（T-4）：状态 / 余量自报 / 每天吃几次 / 购药待办 / 查询 / 删除。"""
from fastapi import APIRouter
from pydantic import BaseModel

from backend.services import medication_service

router = APIRouter(prefix="/api/medication", tags=["medication"])


class ReportRemainingRequest(BaseModel):
    med_name: str = ""
    remaining: int = 0
    unit: str = "片"


class DailyDoseRequest(BaseModel):
    med_name: str = ""
    daily_dose: int = 1


class MedNameRequest(BaseModel):
    med_name: str = ""


@router.get("")
def api_status():
    """状态总览（药品列表 + 余量低的药）。"""
    return medication_service.status()


@router.post("/report")
def api_report(req: ReportRemainingRequest):
    """余量自报。"""
    return medication_service.report_remaining(req.med_name, req.remaining, req.unit)


@router.post("/dose")
def api_dose(req: DailyDoseRequest):
    """更正每天吃几次。"""
    return medication_service.set_daily_dose(req.med_name, req.daily_dose)


@router.get("/low")
def api_low():
    """余量低的药 + 是否需要购药待办。"""
    return {"low_meds": medication_service.low_meds(),
            "need_purchase": medication_service.need_purchase_todo()}


@router.post("/purchase-todo")
def api_purchase_todo(req: MedNameRequest):
    """标记购药待办已创建（防重复）。"""
    return medication_service.mark_purchase_todo(req.med_name)


@router.post("/clear")
def api_clear(req: MedNameRequest):
    """删某个药。"""
    return medication_service.clear(req.med_name)

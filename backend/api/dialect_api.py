"""方言接口（F-8）：配置/闸门状态 + 通道③采集表单。

路由（只读为主，写入口只有「老人使用的方言」这一个采集字段）：
    GET  /api/dialect/config      闸门状态 + 选项 + 分布/WER 现状
    GET  /api/dialect/profile     已填的方言
    POST /api/dialect/profile     子女端/上门人员选填（只进统计，不直接决定 ASR）
    GET  /api/dialect/distribution 三通道统计原文（给报告/复核看）

★ 没有声音复刻接口，也没有「切方言就强制上线」的入口——上线清单只由后端闸门算。
"""
from fastapi import APIRouter
from pydantic import BaseModel

from backend.services import dialect_service

router = APIRouter(prefix="/api/dialect", tags=["dialect"])


class ProfileRequest(BaseModel):
    dialect: str = ""
    other: str = ""
    by: str = "child"


@router.get("/config")
def api_config():
    return dialect_service.config()


@router.get("/profile")
def api_profile():
    return {
        "ok": True,
        "profile": dialect_service.profile(),
        "options": dialect_service.form_options(),
    }


@router.post("/profile")
def api_set_profile(req: ProfileRequest):
    return dialect_service.set_profile(req.dialect, req.other, req.by)


@router.get("/distribution")
def api_distribution():
    return {"ok": True, **dialect_service.distribution()}

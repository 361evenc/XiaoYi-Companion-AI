"""子女端接口（F-7）：只读摘要 + 安心开关 + 首次告知 + 购药回执 + 老人通知。

路由白名单（权限测试第 2、4 条按这份清单验）：
    GET  /api/child/summary
    GET  /api/child/consent
    POST /api/child/consent
    POST /api/child/consent/notified
    POST /api/child/purchase/{rid}/receipt
    GET  /api/child/notices
    POST /api/child/notices/{nid}/ack

★ 没有会话/聊天路由，也没有任何写老人记忆的入口——B-9 落地后数据源替换，口径不变。
"""
from fastapi import APIRouter
from pydantic import BaseModel

from backend.services import child_service

router = APIRouter(prefix="/api/child", tags=["child"])


class ConsentRequest(BaseModel):
    enabled: bool = True


@router.get("/summary")
def api_summary():
    return child_service.summary()


@router.get("/consent")
def api_consent():
    return child_service.consent()


@router.post("/consent")
def api_set_consent(req: ConsentRequest):
    return child_service.set_consent(req.enabled)


@router.post("/consent/notified")
def api_notified():
    return child_service.mark_notified()


@router.post("/purchase/{rid}/receipt")
def api_purchase_receipt(rid: str):
    return child_service.purchase_receipt(rid)


@router.get("/notices")
def api_notices():
    return child_service.notices()


@router.post("/notices/{nid}/ack")
def api_ack_notice(nid: str):
    return child_service.ack_notice(nid)

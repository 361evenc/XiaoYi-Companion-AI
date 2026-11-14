"""冷启动编排接口（T-3）：状态 / 任务完成 / 任务跳过。"""
from fastapi import APIRouter
from pydantic import BaseModel

from backend.services import cold_start_service

router = APIRouter(prefix="/api/cold_start", tags=["cold_start"])


class TaskRequest(BaseModel):
    task_id: str = ""


@router.get("")
def api_status():
    """冷启动状态总览（第几天/主题/下一个任务/待办数）。"""
    return cold_start_service.status()


@router.post("/done")
def api_done(req: TaskRequest):
    """标记任务完成。"""
    return cold_start_service.mark_done(req.task_id)


@router.post("/skip")
def api_skip(req: TaskRequest):
    """标记任务跳过（essential 任务不跳过）。"""
    return cold_start_service.mark_skipped(req.task_id)

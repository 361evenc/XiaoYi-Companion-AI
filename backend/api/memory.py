"""记忆/隐私接口：忘掉一件事 + 清空全部记忆。"""
from fastapi import APIRouter
from pydantic import BaseModel

from backend.services import memory_service

router = APIRouter(prefix="/api", tags=["memory"])


class ForgetRequest(BaseModel):
    text: str = ""


class WipeRequest(BaseModel):
    is_muted: bool = False


@router.post("/forget")
def api_forget(req: ForgetRequest):
    return memory_service.forget(req.text)


@router.delete("/memory/wipe")
def api_wipe(req: WipeRequest = None):
    # DELETE 带 body 不被部分客户端支持，允许缺省（默认不静音出提示音）
    return memory_service.wipe(bool(req.is_muted) if req else False)


@router.get("/memory")
def api_memory_box():
    """记忆箱：重要事件 + 照片 + 小忆的理解 + 用户画像。"""
    return memory_service.box()


@router.get("/emotion-chart")
def api_emotion_chart():
    """心情曲线图（数据不足时 image_url 为 null）。"""
    return memory_service.emotion_chart()


class CorrectRequest(BaseModel):
    key: str
    new_value: str
    old_value: str = ""
    quote: str = ""


@router.get("/memory/search")
def api_memory_search(q: str = "", by: str = "object", k: int = 10):
    """统一记忆检索（B-5）：by ∈ person|object|scene。
    - person：人物档案优先；无档案的具名人物退回三类（resolved.kind=fallback_visitor，
      UI 只显"有客人来过"，不显示猜的人名）
    - object：物品（"X位置"规范键加权）
    - scene：场景（H-2 粗位置标签优先，无标签走词典+bigram）
    fact 结果带 history（旧值随检索可查）。"""
    return memory_service.search(q, by, k)


@router.post("/memory/correct")
def api_memory_correct(req: CorrectRequest):
    """显式更正（B-5）：值即时生效、旧值入 history 保留可查。
    返回 {ok, action: corrected|noop|added, key, value, previous}。"""
    return memory_service.correct(req.key, req.new_value, req.old_value, req.quote)


@router.get("/memory/health")
def api_memory_health():
    """记忆箱健康自检（B-4 audit，B-10 报告"记忆箱健康"项数据源）：
    条目数 / 视觉重复堆积 / 敏感词命中（只扫视觉条目）/ 拦截统计 / 免看清单。
    status ∈ healthy|attention；issues 非空即规则执行出问题，能被发现不是靠运气。"""
    return memory_service.health()


class FindObjectRequest(BaseModel):
    text: str


@router.post("/find_object")
def api_find_object(req: FindObjectRequest):
    """找物三级回答（B-3，契约按 B-1 PRD §4，F-1 按 source/stale/too_old 展示）：
    - graph：视觉条目（"我见过"），≤30 天直答，30–180 天 stale，>180 天不主动展示
    - text：记忆里"X位置"键（"您上次说"），>90 天 stale，>180 天不主动引用
    - none：诚实兜底（编造 0 容忍）
    has_clean_crop 恒 False（H-2 裁剪图二次检测未供）→ F-1 不展示图、只语音答位置。
    matched=False 表示不是找物提问，调用方转正常聊天。"""
    return memory_service.find_object(req.text)

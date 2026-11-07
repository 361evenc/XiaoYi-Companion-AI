"""紧急联络服务（B-11）：登记三路径 + 复述确认 + 主动呼叫 + 状态查询。

封装 memory_system.EmergencyContactStore，供 API 与口语入口共用。
MVP 呼叫通道 = caller callable（微信语音/电话 app 拉起），通道未接时诚实
说明并引导老人自己拨——绝不静默、绝不装打通。
"""
from backend.core.state import engine

# caller 通道占位：MVP 阶段未接微信/电话 app，先 None（诚实降级）。
# 接通时替换为 lambda phone: wechat_dial(phone) 之类的真实通道即可。
_CALLER = None


def _store():
    return engine.emergency_contact_store


def status():
    """状态总览：是否已设置 / 联系人 / 是否在等复述确认 / 最近一次呼叫。"""
    return _store().status()


def submit(name, phone, relation="", source="elder"):
    """提交联系人候选（登记第一步）→ 返回复述确认话术。

    source 三路径（B-11）：
      elder  = 老人自报（首选）
      child  = 子女端代填（F-7 调用，仍向老人复述——老人必须知道名单是谁）
      staff  = 社区/上门人员代填（老人失能时）
    """
    return _store().submit(str(name or ""), str(phone or ""),
                           relation=str(relation or ""), source=str(source or "elder"))


def resolve_confirm(reply_text):
    """复述确认答复（"对/没错"→落库；"不对"→重来）。"""
    return _store().resolve_confirm(str(reply_text or ""))


def request_call(target="contact"):
    """主动呼叫：contact=打给紧急联系人；120=诚实话术（MVP 不自主拨）。"""
    return _store().request_call(target=str(target or "contact"), caller=_CALLER)


def chat(text):
    """口语闭环（与 app.chat_response 走同一个 store；供前端兜底调用）。"""
    return _store().handle_chat(
        str(text or ""), engine.user_memory.get("call_name", "奶奶"))

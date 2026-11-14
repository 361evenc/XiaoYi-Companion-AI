"""紧急联络服务（B-11）：登记三路径 + 复述确认 + 主动呼叫 + 状态查询。

封装 memory_system.EmergencyContactStore，供 API 与口语入口共用。
MVP 呼叫通道 = caller callable（微信语音/电话 app 拉起），通道未接时诚实
说明并引导老人自己拨——绝不静默、绝不装打通。
"""
from backend.core.state import engine

# caller 通道：桌面微信语音拉起（B-11 注入位已接通）。
# 依赖/微信窗口缺失时 wechat_voice_call 返回 False，store 层诚实降级兜底话术。
from backend.services.wechat_caller import wechat_voice_call as _CALLER

import time


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


# ---- T-5 救命链路（item1-4：触发/AI确认/倒计时/路径决定）----

def _rescue():
    return engine.rescue_controller


def rescue_status():
    """救命链路状态总览（state/is_baseline/idle_secs/path/log）。"""
    return _rescue().status()


def rescue_trigger(word="救命"):
    """喊'救命'/'打120' 触发（item4 路径决定 + 30s可取消倒计时）。"""
    return _rescue().trigger_rescue(str(word or "救命"), now=time.time())


def rescue_confirm_reply(reply_text):
    """AI确认答复（item2，含无应答分支：60s超时追问1次仍无→直接进倒计时）。"""
    return _rescue().handle_confirm_reply(str(reply_text or ""), now=time.time())


def rescue_countdown_reply(reply_text):
    """倒计时期间答复（item3，可取消：'别打了/不用了'→取消+记录）。"""
    return _rescue().handle_countdown_reply(str(reply_text or ""), now=time.time())

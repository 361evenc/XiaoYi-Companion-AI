# -*- coding: utf-8 -*-
"""
提醒功能集成测试：抽取 app.py 中的提醒状态机（不加载模型、不起 Gradio），
桩掉 LLM 意图解析/语音/界面，模拟完整对话流。
运行：python test_reminder_integration.py
"""
import os
import re
import json
import time
import wave
import types
from datetime import datetime, timedelta
from collections import deque

import numpy as np

import reminder as R
from reminder import (ReminderStore, cn_to_int, parse_time_expr, humanize_ts,
                      humanize_repeat, describe_reminder, looks_like_reminder,
                      fallback_extract, match_confirmation, repeat_desc,
                      ack_set_message, trigger_message, reremind_message,
                      escalated_message, missed_message, confirm_ack_message,
                      later_ack_message, clarify_time_message, list_message,
                      select_message, confirm_cancel_message, modify_ask_message,
                      cancel_ack_message, abort_message, capability_message)

SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py"),
           encoding="utf-8").read()
BLOCK = SRC[SRC.index('# ========== 提醒系统：意图解析'):
            SRC.index('# 启动调度线程（daemon：随主进程退出）')]

FILES = []


def build_ns(llm_stub, store_file):
    FILES.append(store_file)
    ns = dict(re=re, json=json, time=time, datetime=datetime, timedelta=timedelta,
              deque=deque, np=np, wave=wave, cn_to_int=cn_to_int,
              parse_time_expr=parse_time_expr, humanize_ts=humanize_ts,
              humanize_repeat=humanize_repeat, describe_reminder=describe_reminder,
              looks_like_reminder=looks_like_reminder, fallback_extract=fallback_extract,
              match_confirmation=match_confirmation, repeat_desc=repeat_desc,
              ack_set_message=ack_set_message, trigger_message=trigger_message,
              reremind_message=reremind_message, escalated_message=escalated_message,
              missed_message=missed_message, confirm_ack_message=confirm_ack_message,
              later_ack_message=later_ack_message, clarify_time_message=clarify_time_message,
              list_message=list_message, select_message=select_message,
              confirm_cancel_message=confirm_cancel_message,
              modify_ask_message=modify_ask_message, cancel_ack_message=cancel_ack_message,
              abort_message=abort_message, capability_message=capability_message)
    ns.update(user_memory={"call_name": "王爷爷"},
              reminder_store=ReminderStore(store_file),
              UI_STATE={"personality": "暖心知心"},
              pending_reminder={"kind": None, "data": {}, "ts": 0.0},
              reminder_queue=deque(maxlen=20),
              PENDING_TTL=150, CONFIRM_WINDOW=6 * 3600,
              RE_REMIND_INTERVAL=120, MAX_RE_REMIND=2,
              save_conversation=lambda *a, **k: None,
              text_to_speech=lambda t: None,
              gr=types.SimpleNamespace(update=lambda **kw: ('update', kw)))
    exec(compile(BLOCK, 'app_block', 'exec'), ns)
    ns['llm_extract_intent'] = llm_stub  # exec 后覆盖，桩才生效
    return ns


def clean():
    for f in FILES:
        if os.path.exists(f):
            os.remove(f)


def run():
    ok = [0]

    def check(cond, name):
        assert cond, f"失败: {name}"
        ok[0] += 1
        print(f"  ✅ {name}")

    try:
        # ---- A. LLM 意图解析路径 ----
        nsA = build_ns(lambda x: {"intent": "set", "time": "五分钟后",
                                  "thing": "喝水", "target": ""}, "itA.json")
        print("== A. LLM 结构化输出路径 ==")
        msg = nsA['try_handle_reminder']("五分钟后提醒我喝水")
        check("喝水" in msg and len(nsA['reminder_store'].pending()) == 1, "A1 意图set+时间+事项")
        nsA2 = build_ns(lambda x: {"intent": "set", "time": "明天",
                                   "thing": "吃药", "target": ""}, "itA2.json")
        nsA2['try_handle_reminder']("明天提醒我吃药")
        check(nsA2['pending_reminder']['kind'] == 'clarify_time', "A2 缺钟点->一次澄清")

        # ---- B. 规则回退路径（LLM 不可用，等价于内部异常被捕获返回 None） ----
        ns = build_ns(lambda x: None, "itB.json")
        handle, store, pend = ns['try_handle_reminder'], ns['reminder_store'], ns['pending_reminder']
        saved = []
        ns['save_conversation'] = lambda cid, msgs: saved.append((cid, msgs))

        def say(t):
            return handle(t)

        print("== B. 规则回退路径 ==")
        say("每天早上八点吃药")
        r = store.pending()[0]
        check(r['repeat'] == {'type': 'daily', 'hour': 8, 'minute': 0} and '吃药' in r['thing'],
              "B1 周期提醒（无'提醒'字样）")
        store.cancel_all(); store.notices.clear()

        say("五分钟后提醒我喝水")
        say("我有什么提醒")
        check(len(store.pending()) == 1, "B2 一次性提醒+查看")

        r1 = store.pending()[0]
        store.mark_fired(r1['id'], time.time())
        say("喝完了")
        check(store.get(r1['id'])['log'][-1]['result'] == 'done', "B3 触发+确认闭环")

        store.add('吃药', time.time() + 3600)
        say("把提醒取消了"); say("是")
        check(store.pending() == [], "B4 取消（确认对话）")

        say("睡醒后提醒我量血压"); say("行")
        check('量血压' in store.pending()[0]['thing'], "B5 模糊锚点一次确认")

        say("明天提醒我复查"); say("早上八点")
        p = store.pending()[-1]
        check(datetime.fromtimestamp(p['time']).hour == 8
              and '复查' in p['thing'] and '明天' not in p['thing'],
              "B6 澄清补钟点+事项无日期残留")

        say("把提醒改到下午三点")
        in_select = pend['kind'] == 'select_modify'
        say("明天下午四点半")
        fixed = [x for x in store.pending() if '复查' in x['thing']]
        check(in_select and fixed and abs(fixed[0]['time'] -
              (datetime.now() + timedelta(days=1)).replace(hour=16, minute=30,
              second=0, microsecond=0).timestamp()) < 36 * 3600,
              "B7 多候选改期：日期词选中+直接应用新时间")

        store.cancel_all(); store.notices.clear()
        r2 = store.add('吃降压药', time.time() - 1)
        store.mark_fired(r2['id'], time.time())
        for t in (130, 260, 400):
            store.advance_awaiting(time.time() + t, 120, 2)
        say("吃了")
        check(len(store.notices) == 1
              and store.get(r2['id'])['log'][-1]['result'] == 'done',
              "B8 未确认->补提醒2次->升级记录->迟来确认仍记完成")

        r3 = store.add('喝水', time.time() - 1)
        store.mark_fired(r3['id'], time.time())
        say("还没呢，等会儿")
        check(r3['status'] == 'scheduled' and r3['time'] > time.time() + 500,
              "B9 '稍后再说'顺延10分钟")

        audio = ns['build_reminder_audio']('该吃药啦', True)
        check(audio is not None and len(audio[1]) > 5000, "B10 静音时到点仍有提示音")

        store.add('吃药', time.time() - 2)
        for r in store.due(time.time()):
            store.mark_fired(r['id'], time.time())
            ns['reminder_queue'].append(
                {"message": trigger_message("暖心知心", "王爷爷", r["thing"])})
        chat, audio, panel = ns['poll_reminder_events']([], True, 'it-conv')
        check(len(chat) == 1 and '吃药' in chat[0]['content']
              and saved and saved[-1][0] == 'it-conv',
              "B11 到点入队->轮询推进聊天+落库")

        check(handle("我今天挺好的") is None and handle("讲讲以前的事") is None
              and handle("我每天都散步") is None, "B12 闲聊不误触")
        check(handle("你能提醒我吗") is not None, "B13 能力询问")

        html = ns['get_reminders_panel_html']()
        check(isinstance(html, str), "B14 提醒面板HTML")
        print(f"\n🎉 集成测试 {ok[0]} 项全部通过")
    finally:
        clean()


if __name__ == "__main__":
    run()

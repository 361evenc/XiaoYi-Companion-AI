# -*- coding: utf-8 -*-
"""验证队友三项任务的实测脚本（独立进程，不触碰运行实例的数据文件）：
A. 任务5：三人格三音色——同文本三次合成，md5 应全不同
B. 任务1：短期记忆压缩——40 条窗口稳定、summary_until_index 正确累积、不溢出时不动
C. 任务1：summarize_conversation_segment 真实 DeepSeek 调用能返回非空摘要
"""
import os
import sys
import hashlib

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
os.chdir(BASE)
import app

passed, failed = [], []

def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"{'PASS' if cond else 'FAIL'} | {name} | {detail}")

# ---------- A. 三人格三音色 ----------
TEXT = "您好呀，今天过得怎么样？"
md5s = {}
for persona, voice in app.PERSONA_VOICE.items():
    path = app.text_to_speech(TEXT, voice_type=voice)
    if path and os.path.exists(path):
        with open(path, "rb") as f:
            data = f.read()
        md5s[persona] = hashlib.md5(data).hexdigest()
        print(f"  TTS {persona} | {voice} | {md5s[persona]} | {len(data)}B")
        os.remove(path)
    else:
        md5s[persona] = None
        print(f"  TTS {persona} | {voice} | 无音频")
check("A1 三人格都合成出音频", all(v is not None for v in md5s.values()),
      f"{sum(v is not None for v in md5s.values())}/3")
check("A2 三个 md5 全不同", len(set(v for v in md5s.values() if v)) == 3,
      f"distinct={len(set(v for v in md5s.values() if v))}")

# ---------- B. 短期记忆压缩逻辑（临时文件 + mock 摘要器） ----------
TMP_CONV = "_verify_conv_tmp.json"
real_sum = app.ai_services.summarize_conversation_segment
app.ai_services.summarize_conversation_segment = lambda old, seg, call_name="奶奶": "测试摘要XYZ"
app.HISTORY_FILE = TMP_CONV
with open(TMP_CONV, "w", encoding="utf-8") as f:
    f.write("[]")

def mk(n):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"消息{i}"} for i in range(n)]

app.save_conversation("tc1", mk(40))  # 真实场景：对话第一条消息起就已落盘
t1, s1 = app._maybe_compress_history("tc1", mk(45), "席爷爷")
c1 = next(c for c in app.load_all_conversations() if c["id"] == "tc1")
check("B1 45 条压到 40 条", len(t1) == 40, f"len={len(t1)}")
check("B2 摘要返回值正确", s1 == "测试摘要XYZ", f"summary={s1!r}")
check("B3 落盘 messages=40", len(c1["messages"]) == 40, f"msgs={len(c1['messages'])}")
check("B4 summary_until_index=5", c1.get("summary_until_index") == 5,
      f"sui={c1.get('summary_until_index')}")

t2, s2 = app._maybe_compress_history("tc1", t1 + mk(10), "席爷爷")  # 40+10=50 条
c2 = next(c for c in app.load_all_conversations() if c["id"] == "tc1")
check("B5 二次压缩后仍 40 条", len(t2) == 40, f"len={len(t2)}")
check("B6 sui 累积 5+10=15", c2.get("summary_until_index") == 15,
      f"sui={c2.get('summary_until_index')}")

t3, s3 = app._maybe_compress_history("tc1", mk(42), "席爷爷")
c3 = next(c for c in app.load_all_conversations() if c["id"] == "tc1")
check("B7 42 条（<=40+2）不压缩", t3 is not None and s3 == "" and len(c3["messages"]) == 40,
      f"summary={s3!r}, msgs={len(c3['messages'])}")

# ---------- C. 真实 DeepSeek 摘要 ----------
app.ai_services.summarize_conversation_segment = real_sum
real_out = real_sum("", [{"role": "user", "content": "我今年75岁，住在长沙"},
                         {"role": "assistant", "content": "好的，我记住了"}], "席爷爷")
check("C1 真实摘要非空", bool(real_out), f"len={len(real_out)}, head={real_out[:40]!r}")

try:
    os.remove(TMP_CONV)
except OSError:
    pass

print("=" * 50)
print(f"SUMMARY: {len(passed)} passed, {len(failed)} failed")
if failed:
    print("FAILED:", failed)
sys.exit(1 if failed else 0)

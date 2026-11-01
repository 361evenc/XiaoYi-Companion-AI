# -*- coding: utf-8 -*-
r"""阶段① 一键验收：跑完 6 项检查，生成 验收记录_阶段一.md

用法：
    cd D:\xiaoyi_vision
    D:\xiaoyi\XiaoYi-Companion-AI-main\venv\Scripts\python.exe run_acceptance.py

产出：
    logs/acc_A.log / acc_B1.log / acc_B2.log / acc_B3.log / acc_ctrlc.log
    验收记录_阶段一.md
"""

import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(ROOT, "frame_source.py")
LOGS = os.path.join(ROOT, "logs")
PY = sys.executable
os.makedirs(LOGS, exist_ok=True)

MANUAL_FILE = os.path.join(LOGS, "ctrlc_manual.txt")
manual_txt = ""
if os.path.exists(MANUAL_FILE):
    with open(MANUAL_FILE, encoding="utf-8") as f:
        manual_txt = f.read().strip()

FRAME_RE = re.compile(r"^\[\d{4}\].*$", re.M)
SHAPE_RE = re.compile(r"shape=\(([\d,\s]+)\)")


def run(args, timeout=600):
    p = subprocess.run([PY, SCRIPT] + args, cwd=ROOT,
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.stdout, p.stderr, p.returncode


def shapes_of(out):
    got = []
    for m in SHAPE_RE.findall(out):
        parts = [x.strip() for x in m.split(",")]
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            got.append(tuple(int(x) for x in parts))
    return got


def save(name, text):
    path = os.path.join(LOGS, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def run_ctrlc():
    """起一个不限次数的进程，出几帧后发 CTRL+BREAK，验证能否干净退出"""
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
    p = subprocess.Popen([PY, SCRIPT, "--interval", "1"], cwd=ROOT,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace",
                         creationflags=flags)
    time.sleep(4.5)
    try:
        p.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
    except Exception:
        pass
    try:
        out, _ = p.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate()
    return out, p.returncode


results = []

# ---------- ①-A ----------
print(">>> ①-A：20 帧 + Ctrl+C")
a_out, a_err, a_rc = run(["--interval", "2", "--limit", "20"])
save("acc_A.log", a_out + (("\n[STDERR]\n" + a_err) if a_err.strip() else ""))
a_frames = FRAME_RE.findall(a_out)
a_shapes = shapes_of(a_out)

results.append({
    "id": "①-A-1", "name": "20 条帧记录，字段齐全",
    "ok": len(a_frames) == 20 and all(k in a_out for k in ["slot=", "weather=", "file=", "id="]),
    "detail": f"实际 {len(a_frames)} 条",
})
results.append({
    "id": "①-A-2", "name": "shape 为三通道",
    "ok": bool(a_shapes) and all(s[2] == 3 for s in a_shapes),
    "detail": f"共 {len(a_shapes)} 条，均为 3 通道" if a_shapes else "无 shape",
})
results.append({
    "id": "①-A-3", "name": "跑满自动停 + [DONE]",
    "ok": "[DONE]" in a_out and a_rc == 0,
    "detail": ([l for l in a_out.splitlines() if l.startswith("[DONE]")] or ["无"])[-1],
})

c_out, c_rc = run_ctrlc()
save("acc_ctrlc.log", c_out)
ctrlc_ok = ("[STOP]" in c_out) and ("Traceback" not in c_out)
ctrlc_src = "自动捕获到 [STOP]"
if not ctrlc_ok and manual_txt and "[STOP]" in manual_txt and "Traceback" not in manual_txt:
    ctrlc_ok = True
    ctrlc_src = "人工确认（日志见 logs/ctrlc_manual.txt）"
results.append({
    "id": "①-A-4", "name": "Ctrl+C 干净退出（无 Traceback）",
    "ok": ctrlc_ok,
    "manual": not ctrlc_ok,
    "detail": ctrlc_src if ctrlc_ok else "自动发信号在 Windows 不可靠 → 需人工确认（见文末）",
})

# ---------- ①-B ----------
print(">>> ①-B：1280x720 / raw / 640x480")
b1_out, b1_err, b1_rc = run(["--interval", "0.5", "--limit", "12"])
save("acc_B1.log", b1_out)
b1_shapes = set(shapes_of(b1_out))
results.append({
    "id": "①-B-1", "name": "启动行显示 21 张 + 输出 1280x720",
    "ok": "图集 21 张" in b1_out and "输出 1280x720" in b1_out,
    "detail": (b1_out.splitlines() or [""])[0],
})
results.append({
    "id": "①-B-2", "name": "12 帧尺寸完全一致 (720,1280,3)",
    "ok": b1_shapes == {(720, 1280, 3)},
    "detail": f"出现过的尺寸：{sorted(b1_shapes)}",
})

b2_out, _, _ = run(["--interval", "0.5", "--limit", "3", "--out", "raw"])
save("acc_B2.log", b2_out)
b2_shapes = set(shapes_of(b2_out))
results.append({
    "id": "①-B-3", "name": "--out raw 回到原图尺寸",
    "ok": "输出 原图" in b2_out and len(b2_shapes) > 1,
    "detail": f"出现过的尺寸：{sorted(b2_shapes)}",
})

b3_out, _, _ = run(["--interval", "0.5", "--limit", "3", "--out", "640x480"])
save("acc_B3.log", b3_out)
b3_shapes = set(shapes_of(b3_out))
results.append({
    "id": "①-B-4", "name": "--out 640x480 生效",
    "ok": "输出 640x480" in b3_out and b3_shapes == {(480, 640, 3)},
    "detail": f"出现过的尺寸：{sorted(b3_shapes)}",
})

# ---------- 生成报告 ----------
passed = sum(1 for r in results if r["ok"])
lines = []
lines.append("# 阶段① 验收记录（虚拟帧源）")
lines.append("")
lines.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
lines.append(f"- 运行环境：{sys.version.split()[0]} | {sys.platform}")
lines.append(f"- 图集：`frames/`（21 张）| 输出：1280x720（letterbox）")
lines.append(f"- **自动检查结果：{passed} / {len(results)} 项通过**")
lines.append("")
lines.append("## 检查项")
lines.append("")
lines.append("| 编号 | 检查项 | 结果 | 实测 |")
lines.append("|---|---|---|---|")
for r in results:
    if r.get("manual") and not r["ok"]:
        mark = "⬜"
    else:
        mark = "✅" if r["ok"] else "❌"
    detail = str(r["detail"]).replace("|", "\\|")[:120]
    lines.append(f"| {r['id']} | {r['name']} | {mark} | {detail} |")
lines.append("")
lines.append("## 关键输出摘录")
lines.append("")
lines.append("### ①-A 启动行 + 收尾")
a_lines = a_out.splitlines()
lines.append("```")
for l in a_lines[:2] + a_lines[-2:]:
    lines.append(l)
lines.append("```")
lines.append("")
lines.append("### ①-B 三种输出尺寸")
for tag, txt in [("默认 1280x720", b1_out), ("--out raw", b2_out), ("--out 640x480", b3_out)]:
    ls = txt.splitlines()
    lines.append(f"**{tag}**")
    lines.append("```")
    lines.append(ls[0] if ls else "(空)")
    lines.append(ls[1] if len(ls) > 1 else "")
    lines.append("```")
    lines.append("")
lines.append("## 原始日志")
lines.append("")
for f in ["acc_A.log", "acc_B1.log", "acc_B2.log", "acc_B3.log", "acc_ctrlc.log"]:
    lines.append(f"- `logs/{f}`")
lines.append("")
lines.append("## 人工确认项")
lines.append("")
lines.append("### ①-A-4 Ctrl+C 干净退出")
lines.append("")
lines.append("自动发信号在 Windows 上不可靠（子进程无控制台时 `CTRL_BREAK` 收不到），这一项必须人工跑：")
lines.append("")
lines.append("```powershell")
lines.append("cd D:\\xiaoyi_vision")
lines.append("D:\\xiaoyi\\XiaoYi-Companion-AI-main\\venv\\Scripts\\python.exe frame_source.py --interval 2")
lines.append("```")
lines.append("")
lines.append("出到 `[0005]` 左右按 `Ctrl + C`，应出现下面两行，且**没有** `Traceback`：")
lines.append("")
lines.append("```")
lines.append("[STOP] 收到 Ctrl+C，正常退出")
lines.append("[DONE] 共 N 帧 | 用时 ...s（0.00 小时） | 平均 ... 帧/秒")
lines.append("```")
lines.append("")
lines.append("**实际输出：**")
lines.append("")
lines.append("```")
if manual_txt:
    lines.append(manual_txt)
else:
    lines.append("（待填：把 Ctrl+C 后的输出存到 logs/ctrlc_manual.txt，再重跑本脚本）")
lines.append("```")
lines.append("")
lines.append("> 复核方式：重新运行 `run_acceptance.py` 即可重新生成本记录。")

report = os.path.join(ROOT, "验收记录_阶段一.md")
with open(report, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print(f"\n完成：{passed}/{len(results)} 项通过")
print("报告：", report)
for r in results:
    print(("  OK  " if r["ok"] else "  FAIL") + f"  {r['id']}  {r['name']}")

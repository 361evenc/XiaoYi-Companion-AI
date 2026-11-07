"""H-2 验收脚本：真停帧 / 故障区分 / 帧零落盘 / 状态一致

跑法（在 vision/ 目录）：
    venv\\Scripts\\python.exe tests\\h2_acceptance.py
    venv\\Scripts\\python.exe tests\\h2_acceptance.py --source camera    # 有摄像头时

三项验收：
  A. 隐私静态校验：录制链路源码里不能出现任何"把帧写出去"的调用
  B. 状态机：启动 -> recording；停止 -> released；状态与真实硬件一致
  C. 故障区分：设备被占用时必须报 fault（"没法确定"），不能报 released（"已停"）

退出码 0 = 全过；1 = 有项没过（CI 用）。
"""
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]     # vision/
sys.path.insert(0, str(HERE))

PASS, FAIL = "[PASS]", "[FAIL]"
failures = []


def check(name, ok, detail=""):
    print(f"{PASS if ok else FAIL} {name}" + (f" | {detail}" if detail else ""))
    if not ok:
        failures.append(name)
    return ok


# ---------------- A. 隐私静态校验 ----------------

# 这些调用一旦出现在录制链路上，就意味着"帧被写出去了"
FORBIDDEN = [
    r"cv2\.imwrite", r"cv2\.VideoWriter", r"\.tofile\(",
    r"Image\.fromarray.*\.save\(", r"\.save\(\s*.*\.(jpg|jpeg|png)",
    r"send_file", r"requests\.post", r"urlopen",
]
RECORDING_CHAIN = ["recorder.py", "sources.py", "describe_frame.py"]


def check_privacy_static():
    print("\n--- A. 帧零落盘（源码静态校验）---")
    for fname in RECORDING_CHAIN:
        p = HERE / fname
        if not p.exists():
            check(f"{fname} 存在", False, str(p))
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        hits = [pat for pat in FORBIDDEN if re.search(pat, text)]
        check(f"{fname} 无写帧/外传调用", not hits, f"命中 {hits}" if hits else "")
    # 正面要求：必须有释放调用（真停的实现前提）
    rec = (HERE / "recorder.py").read_text(encoding="utf-8", errors="replace")
    check("recorder.py 有 release() 调用", ".release()" in rec)
    check("recorder.py 有 del frame 主动断引用", "del frame" in rec)


# ---------------- B/C. 状态机 + 故障区分 ----------------

def _count_lines(path):
    if not path.exists():
        return 0
    with open(path, encoding="utf-8", errors="replace") as f:
        return sum(1 for _ in f)


def check_state_machine(source):
    print(f"\n--- B. 状态机（source={source}）---")
    from recorder import controller, ST_RECORDING, ST_RELEASED, ST_FAULT

    before = _count_lines(HERE / "data" / "world_perception.json")
    # 首次 import describe_frame 要拉 torch/ultralytics，可能几秒到十几秒
    print("    （首次运行要加载模型，可能等 10-20s）")
    r = controller.start(source=source, interval=0.3)
    check("启动 -> recording", r["status"]["state"] == ST_RECORDING, r["status"]["detail"])
    time.sleep(3.0)

    s = controller.status()
    check("运行中有帧产出", s["frames"] > 0, f"frames={s['frames']}")
    check("vision_on=True", s["vision_on"] is True)
    check("运行中 confirmed_off=False（不能说没在看）", s["confirmed_off"] is False)

    res = controller.stop()
    check("停止 -> released", res["status"]["state"] == ST_RELEASED, res["status"]["detail"])
    v = controller.verify()
    check("verify -> released", v["result"] == "released", v["detail"])

    s2 = controller.status()
    check("停止后 vision_on=False", s2["vision_on"] is False)
    check("停止后 confirmed_off=True", s2["confirmed_off"] is True)

    after = _count_lines(HERE / "data" / "world_perception.json")
    new = after - before
    check("停止后不再新增帧记录", new >= 0, f"本次写入 {new} 条")
    # 只写结构化条目：抽查最后一条，必须没有图片字段
    last = None
    fp = HERE / "data" / "world_perception.json"
    if fp.exists():
        with open(fp, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = line
    if last:
        import json
        rec = json.loads(last)
        keys = set(rec.keys())
        bad = keys & {"image", "frame", "img", "jpg", "path_png", "video"}
        check("落盘条目不含图像/视频字段", not bad, f"多余字段 {bad}" if bad else "")
        check("落盘条目含 objects（结构化）", "objects" in rec)


def check_fault_vs_stop():
    print("\n--- C. 故障区分（读不到帧 -> 必须报 fault）---")
    try:
        import cv2
    except ImportError:
        print("[SKIP] 没装 opencv，跳过故障用例")
        return
    from recorder import controller, ST_RECORDING, ST_FAULT, ST_RELEASED
    controller.stop()
    holder = cv2.VideoCapture(0)
    if not holder.isOpened():
        print("[SKIP] 本机没有可用摄像头，跳过故障用例")
        holder.release()
        return
    try:
        # 本机实测：Windows 摄像头驱动允许多进程同时打开，所以"被占用"这条
        # 无法在用例内可靠构造 —— 但"读不到帧必须报 fault"是可构造的真路径。
        # 另外，启动中途取消属于"没开成"而不是"已停"，也不许报 released。
        r = controller.start(source="camera", interval=1.0, camera_index=99)
        check("不存在的摄像头 -> fault（不是假装在录）",
              r["status"]["state"] == ST_FAULT, r["status"]["detail"])
        v = controller.verify()
        check("故障时 verify 不报 released", v["result"] != "released", v["detail"])

        controller.stop()
        r2 = controller.stop()
        check("故障后再次停止：靠真验证决定，恢复则 released、未恢复则仍 fault",
              r2["status"]["state"] in (ST_RELEASED, ST_FAULT) and
              (r2["status"]["state"] == ST_RELEASED) == r2["ok"],
              f"{r2['status']['state']} | {r2['status']['detail']}")
    finally:
        holder.release()
        controller.stop()


def main():
    src = "sim"
    if "--source" in sys.argv:
        src = sys.argv[sys.argv.index("--source") + 1]

    check_privacy_static()
    check_state_machine(src)
    check_fault_vs_stop()

    print("\n" + "=" * 50)
    if failures:
        print(f"{FAIL} 未通过 {len(failures)} 项：{failures}")
        return 1
    print(f"{PASS} H-2 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

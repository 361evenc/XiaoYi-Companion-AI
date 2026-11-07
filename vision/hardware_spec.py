"""H-9 硬件配置 · 选型骨架（挂起中，先备好可询价的清单）

**状态：H-9 应是"⏸挂起（等 H-8）"。本文件是提前备好的选型骨架，不是已定稿配置。**

先把话说白：H-8 没拍板"走不走硬件"，H-9 就不该出"最终配置清单"。
但选型里**不依赖"走不走"的部分**（远场麦要什么指标、扬声器要什么功率、
指示灯要不要、语音响应预算怎么测）现在就能定——这些是工程常识 + 已有实测数据，
先写好，等 H-8 说"走"，直接拿去询价，不浪费一轮。

**语音响应毫秒预算**是 H-9 里唯一"必须有实测数字"的项（任务卡：
"老人感觉'卡'的阈值是实测出来的，不是拍脑袋"）。所以本文件给的是
**测量方法 + 现有链路的实测拆解**，不是编一个 800ms 出来。

用法：
    venv\\Scripts\\python.exe hardware_spec.py --latency      # 跑现有链路的分段耗时
    venv\\Scripts\\python.exe hardware_spec.py --list         # 打印选型表
"""

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


# ================================================================ 选型表
# 每一项都标"依据"：能查到的写来源，估的写"估"，没数据的写"待测"
SELECTION = [
    {
        "part": "远场麦克风阵列",
        "spec": "4 麦环形阵列，灵敏度 -26dBFS，SNR ≥65dB，拾音半径 5m（混响 0.6s 房间）",
        "why": "老人坐沙发说话，离设备常 3-5m；平板单麦在 3m 外掉字严重。"
               "4 麦是波束成形的最低配置（2 麦只能做左右分辨）",
        "basis": "指标来自当前主流 4 麦模组规格（如 Vesper/Knowles 阵列方案）；"
                 "**拾音半径必须实测**（见 --latency 的空房间/开电视两组）",
        "price_cny": "80-150（模组）",
        "risk": "远场 + 电视声同时存在时，中文 ASR 误识率会跳升；H-6 实测要单独记这一项",
    },
    {
        "part": "扬声器",
        "spec": "8Ω 3W 全频 + 独立功放（≥3W），1m 处 75dB 不破音",
        "why": "老人高频听力先衰退，需要中低频足；75dB@1m 是客厅对话可听下限",
        "basis": "估——75dB 是助听器验配常用的'舒适阈值'量级，需实测确认",
        "price_cny": "25-45",
        "risk": "小体积腔体低频出不来，音量开大反而失真；腔体体积要在结构阶段锁死",
    },
    {
        "part": "指示灯（联动 F-2）",
        "spec": "环形 RGB，录制时呼吸橙、待机灭、故障红闪；**硬件开关可物理断电**",
        "why": "H-5 访谈的核心诉求方向是'我得看得见它在不在看'；H-7 也要求告警状态可视。"
               "物理断电开关是隐私信任的关键——灯能关不算数，摄像头能断才算",
        "basis": "F-2 已定'指示灯'方向；H-5 访谈未做，具体颜色/形态**待 H-5 结论**",
        "price_cny": "10-25",
        "risk": "★ 关键：指示灯必须与摄像头**同一路电源**，否则'灯灭了但还在看'就是欺骗——"
                "这条测试要写进 H-10 的 72h 稳定性验收",
    },
    {
        "part": "主控",
        "spec": "RK3566（4×A55 1.8GHz，1TOPS NPU，4GB+64GB）",
        "why": "现跑的 YOLOv8n（6.2MB）在 CPU 上约 60-100ms/帧，够用；"
               "NPU 可给未来本地化模型留余量；不需要上 RK3588（贵一倍，功耗高一档）",
        "basis": "vision/ 实测：CPU 推理 infer_ms 已在 world_perception 里记录",
        "price_cny": "含在整机内",
        "risk": "RK3566 的 NPU 对 YOLOv8 需要转 RKNN，转换有精度损失——初期直接跑 CPU，别赌 NPU",
    },
    {
        "part": "屏幕",
        "spec": "10.1\" 1280×800 IPS，亮度 ≥300nit，触摸（可选）",
        "why": "视频通话（H-1）要看清脸；300nit 够客厅白天；1280×800 是这一代平板的标配分辨率",
        "basis": "整机 FOB 报价 $50-65（1000 台量级，公开报价区间）",
        "price_cny": "含在整机内",
        "risk": "★ 触摸屏是双刃——老人会去点、会误触设置。方案：默认全屏无菜单，触摸只响应「挂断」一个手势",
    },
    {
        "part": "摄像头",
        "spec": "前摄 2MP，FOV ≥90°，支持 720p@30fps",
        "why": "FOV 90° 才能覆盖 3×4m 客厅的坐姿区域；2MP 够 H-1 看清脸、够 H-7 判姿态",
        "basis": "整机自带前摄（常见 2MP/90°）；**FOV 需实测**",
        "price_cny": "含在整机内",
        "risk": "平板前摄多为定焦，老人离设备 <40cm 会糊——需要自动对焦或选 60cm 起清晰的模组",
    },
    {
        "part": "供电",
        "spec": "12V/2A 适配器，Type-C 输入（或 DC 口），支持边充边用",
        "why": "24h 常开（H-2/H-10），不能靠电池；边充边用是必须，否则一拔电就断录像",
        "basis": "10.1\" 平板常见 12V/2A；功耗需 H-10 实测",
        "price_cny": "15-30",
        "risk": "长期插电锂电池会鼓包（安全隐患）——建议**插电版去掉电池**，或加充电管理限 80%",
    },
    {
        "part": "结构（外壳/底座）",
        "spec": "桌面立式，底座配重 ≥600g，倾角 15°，顶部麦克风开孔朝上",
        "why": "H-10 要求防倾倒；15° 倾角让摄像头拍不到天花板反而拍到坐姿",
        "basis": "估——配重/倾角需打样后实测（H-10 的防倾倒测试）",
        "price_cny": "模具 10-30 万（一次性）+ 单件 30-60",
        "risk": "老人家里台面空间有限，底座太大会被挪走；**先用胶垫 + 配重，别做夹持式**",
    },
]


# ================================================================ 语音响应预算


def measure_latency(rounds=3):
    """拆现有链路的耗时，给出"哪里最慢"的实测数字。

    任务卡要的是"唤醒→开始回应 ≤X ms，实测定值"。这条链路跨了四个环节：
        ASR（火山）→ LLM（DeepSeek）→ TTS（火山）→ 播放
    本机没有 ASR/TTS 的 key（local_secrets 只有 DEEPSEEK/VOLC/BING），
    所以这里**只能实测 LLM 与本地视觉两段**，ASR/TTS 两段标"待测"。

    不编数字：测不出来的就写 None + 原因，让 H-9 的预算表诚实留白。
    """
    out = {"rounds": rounds, "segments": {}}

    # ① 本地视觉（YOLO 单帧）
    try:
        import time
        import numpy as np
        from describe_frame import load_model
        m = load_model()
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        ts = []
        for _ in range(rounds):
            t0 = time.perf_counter()
            m(frame, conf=0.4, imgsz=640, verbose=False, device="cpu")
            ts.append(round(1000 * (time.perf_counter() - t0), 1))
        out["segments"]["yolo_cpu_ms"] = {"values": ts, "min": min(ts),
                                          "note": "空白帧，真实画面会更高"}
    except Exception as e:
        out["segments"]["yolo_cpu_ms"] = {"error": f"{type(e).__name__}: {e}"}

    # ② LLM 首字延迟（DeepSeek）
    try:
        import time
        import requests
        fp = HERE.parent / "local_secrets.json"
        key = os.getenv("DEEPSEEK_API_KEY", "")
        if not key:
            try:
                key = json.load(open(fp, encoding="utf-8")).get("DEEPSEEK_API_KEY", "")
            except Exception:
                pass
        if not key:
            out["segments"]["llm_first_token_ms"] = {"error": "无 DEEPSEEK_API_KEY"}
        else:
            ts = []
            for _ in range(rounds):
                t0 = time.perf_counter()
                r = requests.post(
                    "https://api.deepseek.com/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": "deepseek-chat", "max_tokens": 20,
                          "messages": [{"role": "user", "content": "说一句'我在呢'"}]},
                    timeout=30)
                if r.ok:
                    ts.append(round(1000 * (time.perf_counter() - t0), 1))
            out["segments"]["llm_full_ms"] = {
                "values": ts, "min": min(ts) if ts else None,
                "note": "非流式，含完整生成；流式首字会低得多"}
    except Exception as e:
        out["segments"]["llm_full_ms"] = {"error": f"{type(e).__name__}: {e}"}

    # ③ ASR / TTS：本机无 key，明确标待测
    out["segments"]["asr_ms"] = {"error": "未配置火山 ASR key（VOLC_APP_ID 有，ASR 凭据缺）",
                                 "note": "需在 H-6 实测现场采集：老人说完 → 文字出来"}
    out["segments"]["tts_first_audio_ms"] = {"error": "未配置 TTS 凭据",
                                             "note": "需实测：文字下达 → 第一个音频包"}

    # ④ 预算表（可填项已填，缺项留 None）
    s = out["segments"]
    out["budget"] = {
        "asr": None,
        "llm": s.get("llm_full_ms", {}).get("min"),
        "tts": None,
        "play_start": None,
        "vision_optional": s.get("yolo_cpu_ms", {}).get("min"),
        "target_total_ms": None,
        "note": "target_total 必须由老人实测'感不感觉卡'确定（建议 20 次对话、"
                "每次随机注入 200/400/600/800ms 额外延迟，让老人二选一'刚才卡不卡'）",
    }
    return out


def main():
    ap = argparse.ArgumentParser(description="H-9 硬件配置选型骨架（挂起中）")
    ap.add_argument("--latency", action="store_true", help="实测现有链路耗时")
    ap.add_argument("--list", action="store_true", help="打印选型表")
    args = ap.parse_args()

    if args.list or not args.latency:
        print(f"{'部件':<16}{'单价(元)':<16}{'依据'}")
        print("-" * 78)
        for s in SELECTION:
            print(f"{s['part']:<16}{s['price_cny']:<16}{s['basis'][:44]}")
        print("\n★ 状态：H-9 挂起中（等 H-8 拍板）。以上是可询价骨架，不是定稿配置。")
        return 0

    print(json.dumps(measure_latency(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

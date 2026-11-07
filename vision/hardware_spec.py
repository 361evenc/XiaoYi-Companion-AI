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
# ★ 2026-11-07 H-8 已拍板：C 音箱形态（发起人本人）。
# 屏/喇叭/结构三项已按音箱形态修订；屏幕的具体方案待发起人在
# H-8 决策包第六节的三个选项（4寸屏/纯语音/电视联动）里拍一个。
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
        "spec": "8Ω 5W 全频 + 独立功放（≥5W），1m 处 78dB 不破音（C 形态主卖点就是听，比平板档加一档）",
        "why": "老人高频听力先衰退，需要中低频足；78dB@1m 留出织布外壳的吸声损耗；"
               "音箱形态没有屏可依赖，语音清晰度就是产品本体",
        "basis": "估——75dB 是助听器验配常用的'舒适阈值'量级，需实测确认；5W 对齐主流智能音箱",
        "price_cny": "35-60",
        "risk": "★ 织布外壳吸声 + 小腔体低频差是双重坑；腔体体积和布面开孔率要在结构阶段锁死，"
                "功放 5W 持续输出的散热同步进 H-10",
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
        "part": "屏幕（C 形态：三选一，待发起人拍板）",
        "spec": "选项1: 4.0\" 480×800 IPS 竖屏（约+60~90元）｜选项2: 无屏｜选项3: 无屏+HDMI 输出（约+30~50元）",
        "why": "H-8 已定 C 音箱形态，原 10.1\" 平板屏作废。三选项的应用蓝图见 "
               "H8_硬件形态决策包.md 第六节——核心差异是视频通话放哪：音箱小屏 / 手机 / 老人已有电视",
        "basis": "H-8 第六节蓝图与对比表；4 寸屏价格按小尺寸串口屏行情估",
        "price_cny": "0（选项2）/ 60-90（选项1）/ 30-50（选项3）",
        "risk": "★ 无论哪个选项，触摸/按钮交互都要锁死：老人只能「接听/挂断/确认提醒」，不存在菜单——"
                "这条要写进 H-1 通话页的硬件适配版",
    },
    {
        "part": "摄像头（C 形态：顶部外露 + 物理遮挡滑盖）",
        "spec": "2MP 定焦模组，FOV ≥90°，720p@30fps，位于圆柱顶部；**配物理滑盖**（不是电子开关）",
        "why": "FOV 90° 覆盖 3×4m 客厅坐姿区域；音箱无屏可藏摄像头，物理滑盖是老人能看见摸到的"
               "隐私保证（比指示灯更强一档）；H-13 电视解释、H-7 跌倒检测都靠它",
        "basis": "C 形态定义（H-8 第二节）；滑盖是主流智能音箱摄像头方案（如某些带屏音箱的遮挡设计）",
        "price_cny": "25-45（模组）+ 滑盖结构件",
        "risk": "★ 滑盖关闭状态软件必须识别并停止采集（防滑盖没盖严）；顶部朝上安装角"
                "要重新验证 FOV 覆盖（原来按平板 15° 倾角算的）",
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
        "part": "结构（C 形态：圆柱织布外壳）",
        "spec": "直径约 10cm 圆柱，高约 18cm，织布包身 + 顶部塑料盖（摄像头/滑盖/指示灯集成在盖内）；"
                "配重底座 ≥800g，桌面防倾倒",
        "why": "发起人选定的音箱形态；织布是家居融入度的主流解；摄像头+滑盖+灯集中顶盖"
               "让「看没看我」一眼可判，与隐私红线（物理断电/遮挡）直接呼应",
        "basis": "估——对标主流智能音箱尺寸；布面开孔率与腔体需打样实测（H-10 散热+H-9 声学）",
        "price_cny": "模具 15-35 万（一次性）+ 单件 40-70（织布包覆比 ABS 贵）",
        "risk": "★ 织布外壳是本形态最大结构风险：吸声（影响麦克风拾音与喇叭音质）+ "
                "散热差（5W 功放持续输出，H-10 必须实测布面温升）；开孔率是声学与散热的双刃参数",
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

# vision —— 机器视觉线（端侧感知）

小忆的「眼睛」。把摄像头/图集的画面转成**结构化感知结果**，再交给对话层使用。

本目录是视觉线的**独立交付物**，代码不依赖 `backend/`，可单独运行、单独验收；
与小忆的对接只走三个入口（见文末「与小忆的对接点」）。

---

## 目录结构

```
vision/
├── *.py                  # 全部代码（12 个脚本，扁平放在本目录）
│   ├── frame_source.py        ① 虚拟帧源（图集当摄像头，接口对齐 cv2）
│   ├── sources.py             ⑥ 三源统一：SimSource / CameraSource / ImageSource
│   ├── describe_frame.py      ② 单帧 → 结构化描述（YOLO + 7 大组归类）
│   ├── perception_service.py  ③ 主服务：循环取帧 → 描述 → 落库 + 窗口汇总
│   ├── perception_query.py    ③ 查询接口（import 即用，不起服务）
│   ├── perception_bridge.py   ③ 感知事件 → 小忆记忆库（sidecar，默认不写主库）
│   ├── proactive_signal.py    ④ 感知事件 → 主动消息候选（含三层冷却）
│   ├── clip_zeroshot.py       ⑤ CLIP 零样本：室内外 / 场景 / 天气
│   ├── eval_perception.py     ⑦ 评测（按图算召回精确）
│   ├── detect_one.py          ②-A 单图检测 + 画框
│   ├── detect_batch.py        ②-A 批量检测 + 出 contact sheet
│   ├── run_acceptance.py      ① 一键自动验收，产出验收记录
│   └── start_24h.bat          ①-C 24 小时长稳跑启动脚本
├── data/                 # 样例数据（文件名带 .sample.，运行时不会被覆盖）
│   ├── world_perception.sample.jsonl     逐帧感知结果（JSON Lines，一行一帧）
│   ├── perception_summary.sample.json    窗口汇总（默认 30 分钟一条）
│   ├── proactive_queue.sample.jsonl      主动消息候选队列
│   ├── proactive_state.sample.json       冷却状态（水位线）
│   └── annotations.json                  ⑦ 评测标注（评测脚本直接读这个）
│
│   > 运行时程序会生成同名的**不带** `.sample` 的文件
│   > （`world_perception.json` 等），那是实时数据，不要提交。
├── models/               # 权重目录（放入 yolov8n.pt，见该目录 README）
├── frames/               # 21 张测试图集（室内外混合，含明暗/远近/多目标）
├── docs/                 # 任务卡 + 映射表 + 真机预案 + 评测报告 + 验收记录
└── logs/
    └── run_24h.log.gz    # ①-C 24 小时原始日志（42,123 行，压缩后 388KB）
```

> **脚本必须和 `data/`、`frames/` 同级。** 代码里用
> `HERE = Path(__file__).resolve().parent` 定位数据目录，把 `.py` 挪进子目录会找不到图集。
> 如果确实要分目录，需要同步改每个脚本里的 `HERE`。

---

## 快速开始

```bash
# 1) 独立环境（不要装进小忆主环境，理由见 requirements.txt 顶部）
python -m venv venv
venv\Scripts\pip install -r vision/requirements.txt
# 首次运行会自动下载 yolov8n.pt（6.2MB）到当前工作目录

# 2) 最短路径：看图集跑一分多钟，落库 + 出汇总
#    注意 --duration 单位是【小时】，0.02 ≈ 72 秒；0 表示一直跑
cd vision
..\venv\Scripts\python.exe perception_service.py --source sim --interval 2 --duration 0.02 --summary-every 30

# 3) 查最新一条感知结果
..\venv\Scripts\python.exe perception_query.py --latest

# 4) 真机摄像头（--duration 0.02 约 72 秒）
..\venv\Scripts\python.exe perception_service.py --source camera --duration 0.02

# 5) 单图看效果（会画框存到 out/）
..\venv\Scripts\python.exe detect_one.py frames/09_meal.jpg
```

> 所有脚本的 `--help` 都有完整参数。默认输出尺寸 1280×720（letterbox 等比 + 黑边，
> 不拉伸变形）；`--out raw` 回原图尺寸。

---

## 数据格式（跨人接口，已定版）

一行一帧，JSON Lines，追加写（崩溃只丢半行，不会毁整个文件）：

```json
{
  "ts": "2026-10-28T11:16:42",
  "frame_id": 1,
  "scene": "outdoor_sky",
  "weather": "sunny",
  "slot": "noon",
  "objects": [{"group": "餐具", "label": "bowl", "conf": 0.79, "bbox": [x1,y1,x2,y2]}],
  "summary": "餐厅看到3处人，还有3处餐具",
  "infer_ms": 31.6,
  "source": "sim"
}
```

**三条设计约束（后续改动不得违背，来自 ②-B 实测）：**

1. `scene` / `weather` **取 meta 标签，不由 YOLO 产出**。COCO 80 类里没有天空/街道/草地，
   21 张里有 5 张纯空检出，硬用检测推场景必然出错。
2. 27 个 COCO 细类归并为 **7 大组**（人/动物/植物/餐具/家具/家电/其他），
   映射写死在 `describe_frame.py` 的 `GROUP_MAP`，详见 `docs/02_业务词到COCO类映射表.md`。
3. `conf < 0.40` 丢弃。0.25 时误检明显（圆盆多肉被当成 4 个 bowl，置信 0.83）。

---

## 验收状态

| 项 | 内容 | 结果 |
|---|---|---|
| ① | 虚拟帧源（接口/尺寸/异常/Ctrl+C/24h） | ✅ 8/8 自动检查 + 24.00 小时长稳跑通过 |
| ② | YOLO 检测 + 结构化描述 | ✅ 稳态 28–33 ms/帧（CPU） |
| ③ | 落库 / 汇总 / 查询 / 事件 | ✅ 实测通过；接小忆记忆库有一处待对方支持 |
| ④ | 主动消息候选 + 冷却 | ✅ 三层冷却实测生效 |
| ⑤ | CLIP 零样本 | ⚠️ 室内外 20/21 可用；**天气 58%，不可用** |
| ⑥ | 真机切换 | ✅ 本机摄像头实跑通过；无自动重连/看门狗 |
| ⑦ | 评测 | ⚠️ 召回 96%（22/23）· 精确 71%（22/31），仅 21 张 |

**①-C 24 小时长稳跑**（`logs/run_24h.log.gz`）：

```
# START 2026-10-27T21:54:56 | 图集 21 张 | 间隔 2.0s | 输出 1280x720
# END   2026-10-28T21:54:56 | [DONE] 共 42121 帧 | 用时 86400.1s（24.00 小时） | 平均 0.49 帧/秒
```

`Traceback` / `读帧失败` / `ERROR` 均为 **0**；最长空档 18 秒；每小时 1,729–1,773 帧，
无一小时跌破 1,700（可证全程未休眠）。帧数 42,121 而非 43,200 是因为脚本
「处理完再 sleep 2s」，2 秒是下限不是定频，实测平均间隔 2.052s —— 帧率 0.49 与目标 0.5 一致。

---

## 已知限制（交付时必须一起说明，不要只报好的）

1. **⑦ 的标注是 AI 目视预标，未经人工复核**，真值本身有误差；且只有 21 张（目标 30 张，
   Commons 接口 503 未补齐）。**召回 96% 只能当参考，不能当承诺。**
2. **天气不要用 CLIP。** 58% 正确率，且会把餐盘判成 snow、盆栽判成 rain。
   已加保护：室内置信 ≥0.60 时天气直接输出 `unknown`。天气应走天气 API 或固定窗外视角。
3. **真机无自动重连与看门狗。** `CameraSource` 有连续失败计数但不会重启进程；
   正式部署需要外部守护（计划任务/服务化）。
4. **`scene` 依赖 meta 标签。** 真机摄像头没有 meta，`sources.py` 会输出 `unknown`，
   摘要句退化为「画面里看到人」这种不带场景的说法 —— 这是刻意设计，不硬编造场景。
5. **`perception_bridge.py` 写进小忆会渲染成「老人提过」。** 小忆记忆系统只有 `field`
   没有 `source`，情节一律按「老人说的」渲染。所以默认只写本地 sidecar，
   真正接入前需要小忆侧先加 `source` 字段（改法见该文件末尾「已知缺陷」）。
6. 视觉线独占一份 venv，不要装进小忆主环境：`ultralytics` 会动 `numpy`/`pillow` 版本。

---

## 与小忆的对接点

视觉线不改小忆一行代码，只通过三个入口对接：

| 入口 | 方向 | 现状 |
|---|---|---|
| `perception_query.py` 的 `latest_perception()` | 小忆 → 视觉 | ✅ 可直接 import |
| `perception_bridge.build_events()` | 视觉 → 小忆记忆 | ⚠️ 等小忆加 `source` 字段 |
| `proactive_signal.candidates()` | 视觉 → 小忆主动播报 | ⚠️ 等对话层接入（跨人） |

主动消息的冷却参数（写在 `proactive_signal.py` 顶部，可直接调）：

- 全局最小间隔 **20 分钟**
- 同类消息最小间隔 **2 小时**
- 每日上限 **6 条**
- 静默时段 **22:00–07:00**

---

## 分工说明

按 10.23 重新分配的任务清单，本线承担「①-④ 模拟感知管线」+ ⑤⑥⑦。
清单把这部分归给「硬件实体负责人」，但**实际 7 项里 6 项是软件/算法**，
硬件职责（选型/供电/安装固定/散热/看门狗/日志轮转）在清单中是缺失的。
这一职责边界问题已写在 `docs/01_任务卡_10.27重规划.md` 第七节，待与队长确认。

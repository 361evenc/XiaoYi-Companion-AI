# models/

这里放 YOLOv8n 权重，文件名必须是 `yolov8n.pt`。

**本目录不进版本库**：`.gitattributes` 里 `*.pt` 走 Git LFS，为避免占团队 LFS 配额，
权重按需自取。代码在权重缺失时会直接报错并给出补法，不会静默联网下载
（github.com 不通的网络里，ultralytics 的自动下载会静默挂起数分钟）。

## 获取方式（任选其一）

```bash
# 1) 已装过 ultralytics，能联网
python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"
# 然后把生成的 yolov8n.pt 移到本目录

# 2) 直接下（约 6.2MB）
#   官方地址：https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt
#   国内网络建议用镜像站，下完同样放本目录
```

放好后应该是：

```
vision/models/yolov8n.pt    约 6.2MB
```

## 校验

```bash
python -c "from ultralytics import YOLO; m=YOLO('models/yolov8n.pt'); print(len(m.names), 'classes')"
# 预期输出：80 classes
```

80 类 = COCO 预训练权重。若输出不是 80，说明拿错了模型文件。

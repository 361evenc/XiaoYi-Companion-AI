"""
最小 QLoRA 验证脚本（本地模式，不下载模型）
目标：确认 4-bit 量化 + LoRA + 训练循环 能跑通
"""
import torch, gc, os
os.environ["CUDA_LAUNCH_BLOCKING"] = "1"

# 1. 检查 CUDA
assert torch.cuda.is_available(), "CUDA 不可用！"
print(f"✅ CUDA: {torch.cuda.get_device_name(0)}")
print(f"   VRAM: {torch.cuda.get_device_properties(0).total_memory/1024**3:.1f} GB")
print(f"   空闲: {torch.cuda.mem_get_info()[0]/1024**3:.1f} GB")

# 2. 用随机张量模拟一个迷你模型 + 4-bit 量化
from bitsandbytes.nn import Linear4bit

print("\n⏳ 创建模拟 4-bit 模型...")
# 创建一个小的 4-bit 线性层 + 全连接网络
class Mini4BitModel(torch.nn.Module):
    def __init__(self):
        super().__init__()
        # 用 bitsandbytes 的 4-bit 线性层
        self.layer1 = Linear4bit(128, 256, bias=True, compute_dtype=torch.float16)
        self.layer2 = Linear4bit(256, 128, bias=True, compute_dtype=torch.float16)
        self.layer3 = Linear4bit(128, 64, bias=True, compute_dtype=torch.float16)
        self.drop = torch.nn.Dropout(0.1)
    
    def forward(self, x):
        x = torch.relu(self.layer1(x))
        x = self.drop(x)
        x = torch.relu(self.layer2(x))
        x = self.layer3(x)
        return x

model = Mini4BitModel().cuda().half()
print(f"✅ 4-bit 模型创建成功！参数量: {sum(p.numel() for p in model.parameters())}")

# 3. 用 LoRA 包装
from peft import LoraConfig, get_peft_model

lora_config = LoraConfig(
    r=8,
    lora_alpha=16,
    target_modules=["layer1", "layer2", "layer3"],
    lora_dropout=0.05,
    bias="none",
)

model = get_peft_model(model, lora_config)
model.print_trainable_parameters()

# 4. 生成模拟数据 + 跑一步训练
print("\n⏳ 生成模拟数据并训练...")
X = torch.randn(4, 128).cuda().half()
y = torch.randn(4, 64).cuda().half()

optimizer = torch.optim.AdamW(model.parameters(), lr=2e-4)
loss_fn = torch.nn.MSELoss()

model.train()
optimizer.zero_grad()
output = model(X)
loss = loss_fn(output, y)
loss.backward()
optimizer.step()

print(f"   Loss: {loss.item():.6f}")
print(f"   峰值显存: {torch.cuda.max_memory_allocated()/1024**3:.2f} GB")
print("\n--- 4-bit QLoRA 训练流程验证通过！---")
print("（等下载模型时换成 transformers 的 from_pretrained 即可）")

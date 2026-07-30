"""
极简模型加载测试：只加载 Qwen2.5-7B-Instruct 4-bit，不训练
"""
import torch, gc, os, sys

os.environ["HF_HOME"] = os.path.expanduser("~/.cache/huggingface")
print("释放显存...")
gc.collect()
torch.cuda.empty_cache()
print(f"初始空闲显存: {torch.cuda.mem_get_info()[0]/1024**3:.1f} GB")

from transformers import AutoConfig
from transformers import BitsAndBytesConfig, AutoModelForCausalLM

print("配置 4-bit...")
bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype=torch.float16,
)

print("开始加载模型...")
sys.stdout.flush()

try:
    model = AutoModelForCausalLM.from_pretrained(
        "Qwen/Qwen2.5-7B-Instruct",
        quantization_config=bnb_config,
        device_map="auto",
        max_memory={0: "6GiB", "cpu": "32GiB"},
        torch_dtype=torch.float16,
        low_cpu_mem_usage=True,
        local_files_only=True,
        use_safetensors=True,
        trust_remote_code=True,
    )
    print(f"✅ 加载成功! 参数量: {model.num_parameters()/1e9:.2f}B")
    print(f"最终显存: {torch.cuda.mem_get_info()[0]/1024**3:.1f} GB 空闲")
except Exception as e:
    print(f"❌ 失败: {type(e).__name__}: {str(e)[:500]}")

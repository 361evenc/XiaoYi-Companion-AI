"""下载 Qwen2.5-3B-Instruct 到本地文件夹"""
import ssl, os
ssl._create_default_https_context = ssl._create_unverified_context
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
from huggingface_hub import snapshot_download

local_dir = r"D:\新建文件夹 (2)\XiaoYi-Companion-AI-main\qwen3b_local"
os.makedirs(local_dir, exist_ok=True)

print("下载 Qwen2.5-3B-Instruct 到本地文件夹...")
print(f"目标: {local_dir}")
snapshot_download("Qwen/Qwen2.5-3B-Instruct", local_dir=local_dir, local_dir_use_symlinks=False)
print("下载完成！")

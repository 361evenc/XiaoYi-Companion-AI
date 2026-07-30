"""下载 Qwen2.5-3B-Instruct 模型"""
import ssl
ssl._create_default_https_context = ssl._create_unverified_context
from huggingface_hub import snapshot_download
import os

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
print("下载 Qwen2.5-3B-Instruct（约 6GB）...")
snapshot_download("Qwen/Qwen2.5-3B-Instruct")
print("下载完成！")

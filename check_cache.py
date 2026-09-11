"""诊断 huggingface 缓存中的模型"""
import os, glob

cache_dir = os.path.expanduser(r"~/.cache/huggingface/hub")
print(f"缓存根目录: {cache_dir}")
print(f"目录存在: {os.path.exists(cache_dir)}")

# 列出所有 models-- 目录
if os.path.exists(cache_dir):
    items = os.listdir(cache_dir)
    model_dirs = [d for d in items if d.startswith("models--")]
    print(f"模型缓存数: {len(model_dirs)}")
    for d in model_dirs:
        print(f"\n  {d}")
        snapshots = os.path.join(cache_dir, d, "snapshots")
        if os.path.exists(snapshots):
            snaps = os.listdir(snapshots)
            print(f"    快照数: {len(snaps)}")
            for s in snaps:
                snap_dir = os.path.join(snapshots, s)
                files = os.listdir(snap_dir)
                print(f"    快照 {s[:12]}...: {len(files)} 个文件")
                # 显示文件大小
                total_size = sum(os.path.getsize(os.path.join(snap_dir, f)) for f in files) / 1024**3
                print(f"      总大小: {total_size:.1f} GB")
                # 列出关键文件是否存在
                for key_file in ["config.json", "tokenizer.json", "model.safetensors", "model-00001-of-00002.safetensors"]:
                    path = os.path.join(snap_dir, key_file)
                    exists = os.path.exists(path)
                    if exists:
                        print(f"      ✅ {key_file}")
                break  # 只看第一个快照
        else:
            print(f"    ❌ snapshots 目录不存在")
else:
    print("缓存目录不存在")

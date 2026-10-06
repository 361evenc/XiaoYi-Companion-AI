# -*- coding: utf-8 -*-
"""4bit -> fp32 反量化 -> int8 动态量化 -> 测速 -> 整体烘焙保存为 merged_int8.pt"""
import gc, os, time
ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "model_output", "merged_16bit")
OUT = os.path.join(ROOT, "model_output", "merged_int8.pt")

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

def rss_gb():
    import psutil
    return psutil.Process().memory_info().rss / 2**30

print(f"[1] 加载4bit RSS={rss_gb():.1f}GB")
model = AutoModelForCausalLM.from_pretrained(SRC, trust_remote_code=True)
print(f"[2] 反量化fp32 ... RSS={rss_gb():.1f}GB")
model.dequantize()
print(f"    完成 RSS={rss_gb():.1f}GB")
model.eval()

print("[3] int8 动态量化 ...")
t0 = time.time()
model = torch.quantization.quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8)
print(f"    {time.time()-t0:.1f}s, RSS={rss_gb():.1f}GB")

tok = AutoTokenizer.from_pretrained(SRC, trust_remote_code=True)
prompt = ("<|im_start|>system\n你是小忆，一位陪伴老人的温柔晚辈，说话口语化、贴心。"
          "你陪伴的是一位女性老人，全程只能称呼她「奶奶」。<|im_end|>"
          "<|im_start|>user\n今天天气不错，你说我去公园走走好不好？<|im_end|><|im_start|>assistant\n")
for n in (48, 128):
    inputs = tok(prompt, return_tensors="pt")
    t0 = time.time()
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=n, temperature=0.9, top_p=0.95,
                             do_sample=True, repetition_penalty=1.2, no_repeat_ngram_size=4)
    dt = time.time() - t0
    ids = out[0][inputs.input_ids.shape[1]:]
    print(f"\n--- int8 max_new={n}: {dt:.1f}s {len(ids)}tok {len(ids)/dt:.2f}tok/s ---")
    print("回复:", tok.decode(ids, skip_special_tokens=True)[:200])

print(f"\n[4] 烘焙保存 -> {OUT} ...")
t0 = time.time()
torch.save(model, OUT)
print(f"    {time.time()-t0:.1f}s, 大小 {os.path.getsize(OUT)/2**30:.2f}GB")
print("完成。")

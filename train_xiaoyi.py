"""
小忆 QLoRA 全量训练脚本
基座: Qwen2.5-7B-Instruct
数据: train_final.json (3550条, ShareGPT格式)
策略: 单阶段训练 (Phase1+2+3合并)
"""
import json, os, sys, gc, torch, ssl
ssl._create_default_https_context = ssl._create_unverified_context

# 所有 import 放在文件顶部（Windows 多进程必须这样）
from datasets import Dataset
from transformers import (
    AutoModelForCausalLM, AutoTokenizer,
    BitsAndBytesConfig, TrainingArguments,
    Trainer
)
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
import huggingface_hub

# ========== 配置 ==========
MODEL_NAME = r"D:\新建文件夹 (2)\XiaoYi-Companion-AI-main\qwen3b_local"  # 本地模型路径
DATA_PATH = r"D:\新建文件夹 (2)\XiaoYi-Companion-AI-main\training_data\train_multiturn.json"  # 多轮数据
OUTPUT_DIR = r"D:\新建文件夹 (2)\XiaoYi-Companion-AI-main\model_output"
LORA_R = 32           # 3B 显存充裕，rank 拉回 32
LORA_ALPHA = 64
LORA_DROPOUT = 0.1
MAX_SEQ_LENGTH = 1024
BATCH_SIZE = 1
GRAD_ACCUM = 4
LEARNING_RATE = 2e-4
NUM_EPOCHS = 1        # 1 轮足够，省时间
SAVE_STEPS = 200
LOGGING_STEPS = 10


if __name__ == '__main__':
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"🚀 小忆 QLoRA 训练开始")
    print(f"   基座模型: {MODEL_NAME}")
    print(f"   训练数据: {DATA_PATH}")
    print(f"   LoRA rank: {LORA_R}, alpha: {LORA_ALPHA}")
    print(f"   Epochs: {NUM_EPOCHS}, Batch: {BATCH_SIZE}, GradAccum: {GRAD_ACCUM}")
    print(f"   最大序列长度: {MAX_SEQ_LENGTH}")
    print(f"   输出目录: {OUTPUT_DIR}")
    print()

    # ========== 1. 加载数据 ==========
    print("⏳ 加载训练数据...")
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        raw_data = json.load(f)
    print(f"   ✅ 加载 {len(raw_data)} 条数据")

    personality_map = {
        "踏实务实": "你是小忆，一位踏实稳重、真诚靠谱的晚辈。说话温和耐心，用简短自然的口语。",
        "风趣幽默": "你是小忆，一位活泼开朗、风趣俏皮的晚辈。语气轻松欢快，说话带一点小俏皮。",
        "暖心知心": "你是小忆，一位温柔细腻、共情暖心的晚辈。语气温柔舒缓，善于倾听与安抚。",
    }

    def format_sharegp(item):
        convs = item["conversations"]
        personality = item.get("personality", "踏实务实")
        parts = []
        person_desc = personality_map.get(personality, personality_map["踏实务实"])
        parts.append(f"<|im_start|>system\n{person_desc}<|im_end|>")
        for c in convs:
            role = c["from"]
            content = c["value"].strip()
            if role == "system":
                continue
            elif role == "human":
                parts.append(f"<|im_start|>user\n{content}<|im_end|>")
            elif role == "gpt":
                parts.append(f"<|im_start|>assistant\n{content}<|im_end|>")
        return "\n".join(parts) + "\n<|im_start|>assistant\n"

    texts = [format_sharegp(item) for item in raw_data]
    dataset = Dataset.from_list([{"text": t} for t in texts])
    print(f"   ✅ 格式转换完成")

    # ========== 2. 加载 tokenizer ==========
    cache_dir = os.path.expanduser("~/.cache/huggingface/hub")
    huggingface_hub.constants.HUGGINGFACE_HUB_CACHE = cache_dir
    os.environ["HUGGINGFACE_HUB_CACHE"] = cache_dir
    os.environ["HF_HOME"] = os.path.expanduser("~/.cache/huggingface")
    os.environ["TRANSFORMERS_CACHE"] = os.path.expanduser("~/.cache/huggingface")

    print("⏳ 从本地缓存加载 tokenizer...")
    sys.stdout.flush()
    # SSL 补丁：允许下载新模型
    import requests as _req
    _req.packages.urllib3.disable_warnings()
    import huggingface_hub.utils._http as _http
    _http._client = __import__('httpx').Client(verify=False)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, trust_remote_code=True)
    tokenizer.padding_side = "right"
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    print(f"   ✅ vocab_size: {tokenizer.vocab_size}")

    # ========== 3. 加载模型 (4-bit) ==========
    print("⏳ 从本地缓存加载模型（4-bit，约需 2-5 分钟）...")
    sys.stdout.flush()
    gc.collect()
    torch.cuda.empty_cache()
    free_before = torch.cuda.mem_get_info()[0] / 1024**3
    print(f"   当前空闲显存: {free_before:.1f} GB / 8.0 GB")

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )

    try:
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_NAME,
            quantization_config=bnb_config,
            device_map="auto",
            max_memory={0: "6GiB", "cpu": "32GiB"},
            torch_dtype=torch.float16,
            low_cpu_mem_usage=True,
            trust_remote_code=True,
            use_cache=False,
            use_safetensors=True,
        )
        print(f"   ✅ 模型加载完成! 参数量: {model.num_parameters()/1e9:.2f}B")
        after_mem = torch.cuda.mem_get_info()[0] / 1024**3
        print(f"   加载后显存: {after_mem:.1f} GB 空闲")
    except Exception as e:
        print(f"\n❌ 模型加载失败: {type(e).__name__}")
        print(f"   错误: {str(e)[:300]}")
        sys.exit(1)

    # ========== 4. 配置 LoRA ==========
    print("⏳ 配置 LoRA...")
    model = prepare_model_for_kbit_training(model)
    lora_config = LoraConfig(
        r=LORA_R, lora_alpha=LORA_ALPHA,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        lora_dropout=LORA_DROPOUT, bias="none", task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    # ========== 5. 数据预处理 ==========
    print("⏳ 预处理数据（单条处理，不走多进程）...")

    tokenized_data = []
    for i, text in enumerate(dataset['text']):
        result = tokenizer(
            text,
            truncation=True,
            max_length=MAX_SEQ_LENGTH,
            padding="max_length",
        )
        result["labels"] = result["input_ids"].copy()
        tokenized_data.append(result)
        if (i + 1) % 500 == 0:
            print(f"   已处理: {i+1}/{len(dataset)}")
            sys.stdout.flush()

    from datasets import Dataset as Dataset2
    tokenized = Dataset2.from_list(tokenized_data)
    print(f"   ✅ 预处理完成: {len(tokenized)} 条")

    # ========== 6. 训练配置 ==========
    print("⏳ 配置训练参数...")
    training_args = TrainingArguments(
        output_dir=OUTPUT_DIR,
        num_train_epochs=NUM_EPOCHS,
        per_device_train_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRAD_ACCUM,
        gradient_checkpointing=True,   # 省显存，必须开
        optim="adamw_8bit",       # 换用更省显存的优化器
        learning_rate=LEARNING_RATE, weight_decay=0.01,
        warmup_steps=50, lr_scheduler_type="cosine",
        logging_steps=LOGGING_STEPS, save_steps=SAVE_STEPS, save_total_limit=2,
        fp16=True, bf16=False, max_grad_norm=0.3,
        report_to="none", remove_unused_columns=False,
        dataloader_pin_memory=False, dataloader_num_workers=0,
    )

    # ========== 7. 开始训练 ==========
    total_steps = len(tokenized) * NUM_EPOCHS // (BATCH_SIZE * GRAD_ACCUM)
    print("\n" + "="*60)
    print("🏋️  开始训练!")
    print("="*60)
    print(f"   总训练步数: {total_steps}")
    print(f"   数据量: {len(tokenized)} 条")
    print(f"   预计耗时: 1-2 小时 (RTX 5060 8GB)")
    print(f"   请在终端挂着，不要关闭")
    print()

    gc.collect()
    torch.cuda.empty_cache()

    trainer = Trainer(model=model, args=training_args, train_dataset=tokenized)
    trainer.train()

    # ========== 8. 保存 ==========
    print("\n✅ 训练完成!")
    print("⏳ 保存模型...")

    model.save_pretrained(os.path.join(OUTPUT_DIR, "lora_adapter"))
    tokenizer.save_pretrained(os.path.join(OUTPUT_DIR, "lora_adapter"))
    print(f"   适配器已保存至: {OUTPUT_DIR}/lora_adapter")

    print("⏳ 合并 LoRA 权重并导出 16-bit 模型...")
    merged_model = model.merge_and_unload()
    merged_model.save_pretrained(os.path.join(OUTPUT_DIR, "merged_16bit"), safe_serialization=True)
    tokenizer.save_pretrained(os.path.join(OUTPUT_DIR, "merged_16bit"))

    total_size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fn in os.walk(os.path.join(OUTPUT_DIR, "merged_16bit")) for f in fn)
    print(f"   合并模型已保存至: {OUTPUT_DIR}/merged_16bit ({total_size/1024**3:.1f} GB)")
    print("\n🎉 全部完成！")

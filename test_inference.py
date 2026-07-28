"""
验证微调后的模型能否正常推理
测试三种人格输出是否不同
"""
import torch, ssl, os
ssl._create_default_https_context = ssl._create_unverified_context
os.environ["TOKENIZERS_PARALLELISM"] = "false"

from transformers import AutoModelForCausalLM, AutoTokenizer

model_path = r"D:\新建文件夹 (2)\XiaoYi-Companion-AI-main\model_output\merged_16bit"

print("⏳ 加载模型...")
model = AutoModelForCausalLM.from_pretrained(
    model_path,
    device_map="auto",
    torch_dtype=torch.float16,
    trust_remote_code=True,
)
tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
if tokenizer.pad_token is None:
    tokenizer.pad_token = tokenizer.eos_token
print(f"✅ 加载完成! 参数量: {model.num_parameters()/1e9:.2f}B")
print(f"   显存: {torch.cuda.mem_get_info()[0]/1024**3:.1f} GB 空闲")

# 三种人格的描述（和训练时一致）
personalities = {
    "踏实务实": "你是小忆，一位踏实稳重、真诚靠谱的晚辈。说话温和耐心，用简短自然的口语。",
    "风趣幽默": "你是小忆，一位活泼开朗、风趣俏皮的晚辈。语气轻松欢快，说话带一点小俏皮。",
    "暖心知心": "你是小忆，一位温柔细腻、共情暖心的晚辈。语气温柔舒缓，善于倾听与安抚。",
}

test_questions = [
    "小忆，我今天有点头疼",
    "小忆，我儿子好久没来看我了",
    "小忆，降压药是饭前吃还是饭后吃？",
]

def generate(person_desc, question):
    messages = [
        {"role": "system", "content": person_desc},
        {"role": "user", "content": question},
    ]
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    
    inputs = tokenizer(text, return_tensors="pt").to(model.device)
    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=100,
            temperature=0.7,
            top_p=0.9,
            do_sample=True,
            repetition_penalty=1.05,
        )
    
    full_response = tokenizer.decode(outputs[0], skip_special_tokens=True)
    # 提取 assistant 的回答
    answer = full_response.split("assistant")[-1].strip()
    return answer

print("\n" + "="*60)
print("🗣️  三种人格对话测试")
print("="*60)

for question in test_questions:
    print(f"\nQ: {question}")
    print("-" * 40)
    for pname, pdesc in personalities.items():
        answer = generate(pdesc, question)
        print(f"[{pname}] {answer}")
    print("=" * 50)

print("\n✅ 测试完成！")
free_mem = torch.cuda.mem_get_info()[0] / 1024**3
print(f"最终显存: {free_mem:.1f} GB 空闲")

"""
将 mixed_partially_shuffled.json (Alpaca格式) 转为 ShareGPT 格式
输入: instruction / input / output
输出: conversations: [{from:"human", value:...}, {from:"gpt", value:...}]
"""
import json

# 读取 mixed 数据
with open(r"C:\Users\ASUS\Downloads\mixed_partially_shuffled.json", "r", encoding="utf-8") as f:
    mixed_data = json.load(f)

print(f"原始 mixed 数据: {len(mixed_data)} 条")
print(f"格式: {list(mixed_data[0].keys())}")

# 转换为 ShareGPT 格式
converted = []
for item in mixed_data:
    human_text = item.get("instruction", "").strip()
    gpt_text = item.get("output", "").strip()
    
    # 如果有 input（非空），追加到 instruction 后面
    input_text = item.get("input", "").strip()
    if input_text:
        human_text = human_text + "\n" + input_text
    
    if not human_text or not gpt_text:
        continue
    
    conversations = [
        {"from": "human", "value": human_text},
        {"from": "gpt", "value": gpt_text}
    ]
    
    converted.append({
        "conversations": conversations,
        "personality": "踏实务实",
        "category": "general",
        "source": "mixed_public"
    })

print(f"转换后: {len(converted)} 条 (丢弃 {len(mixed_data)-len(converted)} 条空数据)")

# 保存
output_path = r"C:\Users\ASUS\Downloads\mixed_converted.json"
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(converted, f, ensure_ascii=False, indent=2)

print(f"已保存至: {output_path}")
print(f"示例:")
print(json.dumps(converted[0], ensure_ascii=False, indent=2)[:300])

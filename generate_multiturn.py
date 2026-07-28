"""
将单轮训练数据拼接为多轮对话数据
输入: train_final.json (3550条单轮)
输出: train_multiturn.json (多轮拼接)
"""
import json, random
random.seed(42)

data_path = r"D:\新建文件夹 (2)\XiaoYi-Companion-AI-main\training_data\train_final.json"

with open(data_path, "r", encoding="utf-8") as f:
    data = json.load(f)

print(f"原始数据: {len(data)} 条")

# 按人格+类别分组
groups = {}
for item in data:
    key = (item.get("personality", "踏实务实"), item.get("category", "general"))
    if key not in groups:
        groups[key] = []
    groups[key].append(item)

print(f"分组数: {len(groups)}")
for k, v in groups.items():
    print(f"  {k}: {len(v)} 条")

# 拼接多轮对话
personality_map = {
    "踏实务实": "你是小忆，一位踏实稳重、真诚靠谱的晚辈。说话温和耐心，用简短自然的口语。",
    "风趣幽默": "你是小忆，一位活泼开朗、风趣俏皮的晚辈。语气轻松欢快，说话带一点小俏皮。",
    "暖心知心": "你是小忆，一位温柔细腻、共情暖心的晚辈。语气温柔舒缓，善于倾听与安抚。",
}

multiturn_data = []

for (personality, category), items in groups.items():
    # 随机打乱同一组内的数据
    random.shuffle(items)
    
    # 每2-4条拼接成一个多轮对话
    i = 0
    while i < len(items):
        # 随机选2-4条拼一起
        turn_count = min(random.randint(2, 4), len(items) - i)
        batch = items[i:i+turn_count]
        i += turn_count
        
        convs = []
        person_desc = personality_map.get(personality, personality_map["踏实务实"])
        convs.append({"from": "system", "value": person_desc})
        
        for item in batch:
            original_convs = item["conversations"]
            for c in original_convs:
                role = c["from"]
                value = c["value"].strip()
                if role == "system":
                    continue  # 跳过原有system，用上面统一的人格描述
                if value:
                    convs.append({"from": role, "value": value})
        
        multiturn_data.append({
            "conversations": convs,
            "personality": personality,
            "category": category,
            "source": "multiturn_augmented"
        })

print(f"\n多轮数据: {len(multiturn_data)} 条")

# 统计轮数分布
turn_counts = {}
for item in multiturn_data:
    # 去掉system，算实际对话轮数
    dialogue_turns = [c for c in item["conversations"] if c["from"] != "system"]
    human_turns = sum(1 for c in dialogue_turns if c["from"] == "human")
    key = f"{human_turns}轮对话"
    turn_counts[key] = turn_counts.get(key, 0) + 1

print("轮数分布:")
for k, v in sorted(turn_counts.items()):
    print(f"  {k}: {v} 条")

# 和原始数据合并
all_data = data + multiturn_data
print(f"\n合并后总数据: {len(all_data)} 条")
print(f"  单轮: {len(data)} 条")
print(f"  多轮: {len(multiturn_data)} 条")

# 保存
output = r"D:\新建文件夹 (2)\XiaoYi-Companion-AI-main\training_data\train_multiturn.json"
with open(output, "w", encoding="utf-8") as f:
    json.dump(all_data, f, ensure_ascii=False, indent=2)

print(f"已保存至: {output}")
print(f"文件大小: {os.path.getsize(output)/1024/1024:.1f} MB")

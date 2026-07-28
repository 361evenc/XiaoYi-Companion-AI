"""
数据集质量校验 + 合并为最终训练集
读取三个数据集，校验格式，合并去重，输出最终训练文件
"""
import json
from collections import Counter

def load_json(path, name):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"\n=== {name} ===")
    print(f"  条数: {len(data)}")
    if len(data) == 0:
        print(f"  ⚠️ 空数据集！")
        return data
    print(f"  键: {list(data[0].keys())}")
    
    # 校验每条数据的 conversations 字段
    valid = 0
    invalid = 0
    empty_gpt = 0
    for item in data:
        convs = item.get("conversations", [])
        if not convs or not isinstance(convs, list):
            invalid += 1
            continue
        # 检查是否至少有一轮 human→gpt
        has_human = any(c.get("from") == "human" and c.get("value","").strip() for c in convs)
        has_gpt = any(c.get("from") == "gpt" and c.get("value","").strip() for c in convs)
        if not has_human or not has_gpt:
            invalid += 1
            if has_human and not has_gpt:
                empty_gpt += 1
            continue
        valid += 1
    print(f"  有效: {valid}, 无效: {invalid}, (缺回复: {empty_gpt})")
    
    # 统计 personality 分布
    pers = Counter(item.get("personality", "unknown") for item in data)
    for k, v in pers.most_common():
        print(f"    人格 [{k}]: {v} 条")
    
    # 统计 category 分布
    cats = Counter(item.get("category", "unknown") for item in data)
    if len(cats) > 0:
        print(f"  类别: {dict(cats.most_common(10))}")
    
    return data

# 加载三个数据集
self_built = load_json(r"C:\Users\ASUS\Downloads\self_built_data.json", "self_built (自建数据)")
xiaoyi = load_json(r"C:\Users\ASUS\Downloads\xiaoyi_training_data.json", "xiaoyi_training (人格数据)")
mixed = load_json(r"C:\Users\ASUS\Downloads\mixed_converted.json", "mixed_converted (公开数据)")

# 合并所有有效数据
all_data = []
for data in [self_built, xiaoyi, mixed]:
    for item in data:
        convs = item.get("conversations", [])
        if not convs or not isinstance(convs, list):
            continue
        has_human = any(c.get("from") == "human" and c.get("value","").strip() for c in convs)
        has_gpt = any(c.get("from") == "gpt" and c.get("value","").strip() for c in convs)
        if has_human and has_gpt:
            all_data.append(item)

print(f"\n{'='*50}")
print(f"总有效数据: {len(all_data)} 条")

# 去重（基于 conversations 的 JSON 字符串去重）
seen = set()
deduped = []
for item in all_data:
    key = json.dumps(item["conversations"], ensure_ascii=False)
    if key not in seen:
        seen.add(key)
        deduped.append(item)

print(f"去重后: {len(deduped)} 条 (去除 {len(all_data)-len(deduped)} 条重复)")

# 最终统计
pers_final = Counter(item.get("personality", "unknown") for item in deduped)
print(f"\n最终人格分布:")
for k, v in pers_final.most_common():
    print(f"  [{k}]: {v} 条")

# 保存最终训练集
output = r"D:\新建文件夹 (2)\XiaoYi-Companion-AI-main\training_data\train_final.json"
import os
os.makedirs(os.path.dirname(output), exist_ok=True)

with open(output, "w", encoding="utf-8") as f:
    json.dump(deduped, f, ensure_ascii=False, indent=2)

print(f"\n已保存至: {output}")
print(f"文件大小: {os.path.getsize(output)/1024/1024:.1f} MB")
print("\n✅ 数据集准备完毕，可以开始训练！")

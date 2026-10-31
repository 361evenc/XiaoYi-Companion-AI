# -*- coding: utf-8 -*-
"""健康知识库（反馈11 / AI工程师⑨）：100 条适老健康常识，本地检索 top-3 注入。
设计约束：
- 只依赖标准库 + memory_system.similarity（字符 bigram），离线可用，无循环导入
- health_knowledge.json 缺失/损坏时静默降级为空库，不影响主流程
- is_health_query 供 app.py 判断是否注入；HEALTH_QUERY_RE 独立定义于此，
  app.py 的联网搜索触发词复用本模块的判断，两处不重复维护
"""
import json
import os
import re

from memory_system import similarity

_DIR = os.path.dirname(os.path.abspath(__file__))
_KB_PATH = os.path.join(_DIR, "health_knowledge.json")

# 健康问题识别：症状 / 慢病 / 用药 / 就医 / 饮食锻炼（管"注入本地知识库"；
# 网络搜索由 app.py 的 SEARCH_TRIGGERS 补充触发，两者可叠加）
HEALTH_QUERY_RE = re.compile(
    r"血压|血糖|心脏|冠心病|心梗|脑梗|中风|糖尿病|尿酸|痛风|骨质|骨质疏松"
    r"|关节|膝盖|腰疼|腿疼|腰椎|头晕|头疼|失眠|睡不好|便秘|感冒|咳嗽|发烧"
    r"|吃药|服药|药量|漏服|漏吃|降压药|降糖药|胰岛素|体检|复查|挂号|看病"
    r"|忌口|能吃|不能吃|吃什么|喝什么|锻炼|散步|运动|减肥|体重")


def _load():
    try:
        with open(_KB_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict) and d.get("text")]
    except Exception:
        pass
    return []


_ITEMS = _load()


def is_health_query(text):
    """这句话是不是健康类问题（决定要不要注入知识库上下文）"""
    return bool(HEALTH_QUERY_RE.search(str(text or "")))


def retrieve_context(query, top_k=3):
    """健康问题 -> 相关知识 top-k 注入块；非健康问题或无命中返回空串。
    检索用 memory_system 的字符 bigram 相似度（与记忆检索同一套度量）。"""
    if not _ITEMS or not is_health_query(query):
        return ""
    q = str(query or "")
    scored = sorted(_ITEMS, key=lambda d: -similarity(q, d.get("text", "")))
    picked = [d for d in scored if similarity(q, d.get("text", "")) > 0][:top_k]
    if not picked:
        return ""
    lines = ["[健康常识参考（用一两句口语自然带出来，别照本宣科，别一次全说完）]"]
    for d in picked:
        lines.append(f"- {d['text']}")
    return "\n".join(lines)

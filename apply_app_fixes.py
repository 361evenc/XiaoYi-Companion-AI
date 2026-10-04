# -*- coding: utf-8 -*-
"""一次性精确修补 app.py（混合换行文件，Edit 工具无法匹配，改用字节级替换）。
每处替换都要求唯一匹配（count==1），否则中止，防止误改。"""
import sys

path = "app.py"
with open(path, "rb") as f:
    data = f.read().decode("utf-8")


def rep(old_block, new_block, tag):
    global data
    old = "\r\n".join(old_block.strip("\n").split("\n"))
    new = "\r\n".join(new_block.strip("\n").split("\n"))
    n = data.count(old)
    if n != 1:
        print(f"❌ {tag}: 匹配到 {n} 处（应为 1），中止")
        sys.exit(1)
    data = data.replace(old, new)
    print(f"✅ {tag}")


# ---------- A. Bug③补强：抱怨/质问优先于一切提醒状态机 ----------
rep('''
    # 0) 过期的待澄清/待选择直接失效
    if pending_reminder["kind"] and now - pending_reminder["ts"] > PENDING_TTL:
        pending_reminder["kind"] = None

    # 1) 上一轮澄清/选择的答复
''', '''
    # 0) 过期的待澄清/待选择直接失效
    if pending_reminder["kind"] and now - pending_reminder["ts"] > PENDING_TTL:
        pending_reminder["kind"] = None

    # 0') Bug③补强：抱怨/质问优先于一切状态机——"你怎么还没提醒我"不是"等会儿"。
    #    旧顺序里它会被第2步待确认拦截的"还没"误判为顺延10分钟（Searle 言语行为理论：
    #    表达类责备 ≠ 指令类答复）。抱怨句式永远不会是澄清/确认/选择的合法答复，
    #    故提到最前：一律道歉+立即补提醒，绝不反问时间、绝不顺延。
    if is_complaint_about_reminder(user_input):
        return handle_reminder_complaint(user_input, now, call, persona)

    # 1) 上一轮澄清/选择的答复
''', "A. Bug③ 抱怨优先级")

# ---------- B. 本地模型优雅降级：缺失/未装torch/加载失败都能启动 ----------
rep('''
# ========== 本地模型配置（训练好的小忆 3B 模型，只做推理，无需训练） ==========
import torch, ssl
ssl._create_default_https_context = ssl._create_unverified_context
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_output", "merged_16bit")
ON_GPU = torch.cuda.is_available()
print(f"⏳ 加载本地模型（{'GPU' if ON_GPU else 'CPU 推理模式，无需训练'}）...")
if ON_GPU:
    _model = AutoModelForCausalLM.from_pretrained(
        MODEL_PATH, device_map="auto", torch_dtype=torch.float16, trust_remote_code=True,
    )
else:
    # 无显卡的电脑：不走 device_map（会挂起），直接加载进内存
    _model = AutoModelForCausalLM.from_pretrained(
        MODEL_PATH, dtype=torch.float32, trust_remote_code=True,
    )
_tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=True)
if _tokenizer.pad_token is None:
    _tokenizer.pad_token = _tokenizer.eos_token
print(f"✅ 模型加载完成")
''', '''
# ========== 本地模型配置（训练好的小忆 3B 模型，只做推理，无需训练） ==========
# "模型不需要本地训练，只需要能调用"：模型文件缺失 / 未装 torch / 加载失败时
# 优雅降级——聊天走 DeepSeek API，意图解析与记忆抽取走规则回退，应用照常可用。
import ssl
ssl._create_default_https_context = ssl._create_unverified_context
try:
    import torch
except Exception:           # 没装 torch 也能启动（DeepSeek 兜底）
    torch = None

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_output", "merged_16bit")
ON_GPU = False
_model = None
_tokenizer = None

def _try_load_local_model():
    """加载本地小忆 3B（纯推理，不训练）。成功返回 True；任何失败都降级 DeepSeek，应用照常启动。"""
    global _model, _tokenizer, ON_GPU
    if torch is None:
        print("⚠️ 未安装 torch，跳过本地模型，聊天走 DeepSeek API")
        return False
    if not os.path.exists(os.path.join(MODEL_PATH, "model.safetensors")):
        print(f"⚠️ 未找到本地模型文件（{MODEL_PATH}），聊天走 DeepSeek API")
        return False
    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer
        ON_GPU = torch.cuda.is_available()
        print(f"⏳ 加载本地模型（{'GPU' if ON_GPU else 'CPU 推理模式，无需训练'}）...")
        if ON_GPU:
            _model = AutoModelForCausalLM.from_pretrained(
                MODEL_PATH, device_map="auto", torch_dtype=torch.float16, trust_remote_code=True,
            )
        else:
            # 无显卡的电脑：不走 device_map（会挂起），直接加载进内存
            _model = AutoModelForCausalLM.from_pretrained(
                MODEL_PATH, dtype=torch.float32, trust_remote_code=True,
            )
        _tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=True)
        if _tokenizer.pad_token is None:
            _tokenizer.pad_token = _tokenizer.eos_token
        print("✅ 模型加载完成")
        return True
    except Exception as e:
        _model, _tokenizer = None, None
        print(f"⚠️ 本地模型加载失败（{e}），聊天走 DeepSeek API")
        return False

LOCAL_MODEL_OK = _try_load_local_model()
''', "B. 本地模型优雅降级")

# ---------- C. memory_llm：本地模型缺失时不再 NameError ----------
rep('''
    txt = deepseek_chat_msgs([{"role": "user", "content": prompt}], max_tokens=500)
    if txt:
        return txt
    try:
''', '''
    txt = deepseek_chat_msgs([{"role": "user", "content": prompt}], max_tokens=500)
    if txt:
        return txt
    if _model is None:
        return ""            # 本地模型缺失：调用方自动降级关键词抽取
    try:
''', "C. memory_llm 守卫")

# ---------- D1. 聊天主路径：本地模型缺失时走 DeepSeek ----------
rep('''
    bot_reply = ""
    if search_context:
        # DeepSeek 结合搜索资料回答（本地 3B 难以可靠利用搜索结果）
        try:
''', '''
    bot_reply = ""
    if search_context or _model is None:
        # DeepSeek 结合搜索资料回答（本地 3B 难以可靠利用搜索结果）；
        # 本地模型缺失/加载失败时整体降级 DeepSeek——"模型不需要本地训练，能调用就行"
        try:
''', "D1. 聊天 DeepSeek 降级条件")

# ---------- D2. 本地推理只在模型可用时进入 ----------
rep('''
    try:
        if not bot_reply:
            prompt = f"<|im_start|>system\\n{system_content + search_context}<|im_end|>"
''', '''
    if not bot_reply and _model is not None:
        try:
            prompt = f"<|im_start|>system\\n{system_content + search_context}<|im_end|>"
''', "D2. 本地推理守卫")

# ---------- D3. 异常分支收尾：先清空再走统一兜底话术 ----------
rep('''
    except Exception as e:
        print(f"推理错误: {e}")
        bot_reply = f"{call_name}，我有点听不清楚，您再说一遍好吗？"
''', '''
        except Exception as e:
            print(f"推理错误: {e}")
            bot_reply = ""

    if not bot_reply:
        bot_reply = f"{call_name}，我有点听不清楚，您再说一遍好吗？"
''', "D3. 统一兜底话术")

# ---------- E. llm_extract_intent：本地模型缺失直接放弃 ----------
rep('''
    Bug⑥b 后仅作断网兜底（DeepSeek 优先），避免与聊天生成抢本地模型锁。"""
    try:
''', '''
    Bug⑥b 后仅作断网兜底（DeepSeek 优先），避免与聊天生成抢本地模型锁。"""
    if _model is None:
        return None  # 本地模型缺失：DeepSeek/规则已覆盖，放弃本地兜底
    try:
''', "E. 意图解析本地兜底守卫")

with open(path, "wb") as f:
    f.write(data.encode("utf-8"))
print("全部替换完成")

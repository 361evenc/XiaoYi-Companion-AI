"""全局单例访问层。

旧版 app.py 用模块级变量承载单机状态（user_memory / UI_STATE / memory_bank /
reminder_store / pending_reminder / reminder_queue），并在 import 时启动
本地模型加载、记忆抽取队列、提醒调度、天气刷新等后台线程。

这里 import app 一次，后续服务层统一从这里取 engine 与单例，
避免各处重复 import（重复 import 不会重复初始化，但集中一处更清晰）。
"""
from backend.core import config  # noqa: F401  # 先切 sys.path 与 CWD
import app as engine

# 单例引用（单机单人 + uvicorn workers=1，全局共享一份即可）
user_memory = engine.user_memory
UI_STATE = engine.UI_STATE
memory_bank = engine.memory_bank
reminder_store = engine.reminder_store
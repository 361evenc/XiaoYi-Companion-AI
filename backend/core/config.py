"""后端配置。

旧版 app.py 大量使用相对路径（HISTORY_FILE="conversations.json" / memory_bank.json 等），
且这些相对路径依赖"工作目录=项目根"。为保证无论从哪个目录启动 uvicorn 都能正确定位
数据文件，并让 `import app` 能找到项目根下的旧版引擎，这里在 import 时：
1) 把项目根插入 sys.path；
2) 把工作目录切到项目根（与 Gradio 旧版共用同一份数据）。
"""
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../backend
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)  # .../XiaoYi-Companion-AI

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 单机单人应用：直接切到项目根读写数据，保证和旧版共用 conversations.json / memory_bank.json。
os.chdir(PROJECT_ROOT)
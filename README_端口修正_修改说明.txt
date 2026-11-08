README 端口不一致修正 说明
============================

时间：2026-11-08
文件：README.md（仅「🚀 运行项目」一节）

修改原因：
README 仍描述早期 Gradio 单进程版本的启动方式与端口（python app.py、7861），
与当前实际架构（FastAPI 后端 + Vite 前端、python run.py 单入口）不一致，
按 README 操作会启动失败或访问到错误地址。

修改内容：
1. 启动命令：`python app.py` → `python run.py`（项目根目录执行）。
2. 访问地址：`http://localhost:7861` → 前端 `http://localhost:5173`
   （Vite 端口被占用时自动顺延，以终端输出为准）。
3. 新增说明：后端服务为 `http://127.0.0.1:8010`，健康检查 `/api/health`。
4. 新增说明：可双击 `启动小忆.bat` 一键启动并自动打开浏览器；
   Ctrl+C 或关闭窗口会同时停止前后端。

依据：
- run.py 中 uvicorn 端口为 8010、vite.config.js 中 Vite 端口为 5173
- 启动小忆.bat 与 backend/main.py 的路由挂载方式

备注：
本次只改端口/启动相关表述，README 中其余仍指向旧架构的内容
（如「项目结构」里的 app.py 描述、技术栈表格中的 Gradio）未改动，
后续需要时可单独修订。

# -*- coding: utf-8 -*-
"""小忆 · 单入口启动器：起后端（FastAPI 8010）时自动拉起前端（Vite dev）。

用法（项目根目录）：
    python run.py

行为：
  1. 先把前端 Vite dev server 作为子进程拉起（5173 被占时 Vite 自动换端口，
     以控制台打印为准）
  2. 再启动 FastAPI 后端（uvicorn，必须项目根目录 + 单 worker）
  3. Ctrl+C 或窗口关闭时，一并杀掉前端子进程（taskkill /T 杀进程树）

注意：这是开发模式启动器；生产部署请用 frontend/dist + uvicorn 静态托管。
"""
import atexit
import os
import signal
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)  # 后端依赖项目根作为 CWD（数据文件/相对路径）

frontend_proc = None


def _frontend_dev_cmd():
    """构造前端 dev 命令：优先系统 npm；找不到则退回 node 自带的 npm-cli.js。
    （本机情形：node 在 PATH 但 npm/npm.cmd 不在，只有 node 旁的 node_modules/npm。）"""
    import shutil
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm:
        return f'"{npm}" run dev'
    node = shutil.which("node") or shutil.which("node.exe") or "node"
    npm_cli = os.path.join(os.path.dirname(os.path.abspath(node)),
                           "node_modules", "npm", "bin", "npm-cli.js")
    if os.path.exists(npm_cli):
        return f'"{node}" "{npm_cli}" run dev'
    return "npm run dev"   # 兜底


def start_frontend():
    """以子进程方式启动 Vite（npm run dev）。已在跑则跳过。"""
    global frontend_proc
    try:
        # 已有 Vite 在跑（5173/5174 任一存活）就不重复起
        import socket
        for port in (5173, 5174, 5175):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    print(f"[run] 检测到前端已在 {port} 端口运行，跳过前端启动")
                    return None
    except Exception:
        pass

    cwd = os.path.join(ROOT, "frontend")
    try:
        # 命令由 _frontend_dev_cmd 自适应：npm 在 PATH 用 npm，否则退回 node 自带 npm-cli.js
        cmd = _frontend_dev_cmd()
        frontend_proc = subprocess.Popen(
            cmd, cwd=cwd, shell=True,
            stdout=sys.stdout, stderr=sys.stderr,
        )
        print(f"[run] 前端 Vite 已启动（{cmd}；端口见上方 vite 输出，默认 http://localhost:5174/）")
    except Exception as e:
        print(f"[run] 前端启动失败（不影响后端）: {e}")
    return frontend_proc


def stop_frontend():
    """杀掉前端子进程树（npm → node → vite）。"""
    global frontend_proc
    if frontend_proc is None:
        return
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(frontend_proc.pid)],
            capture_output=True,
        )
        print("[run] 前端已停止")
    except Exception:
        pass
    frontend_proc = None


def _on_exit(*_):
    stop_frontend()
    sys.exit(0)


def main():
    atexit.register(stop_frontend)
    # 窗口被直接关闭（SIGBREAK）时也要清理前端
    try:
        signal.signal(signal.SIGBREAK, _on_exit)
    except (AttributeError, ValueError):
        pass

    print("=" * 56)
    print("  小忆 · 单入口启动：后端 8010 + 前端 Vite")
    print("  浏览器访问: http://localhost:5174/ （以 Vite 打印端口为准）")
    print("  按 Ctrl+C 或关闭本窗口 = 前后端一起停止")
    print("=" * 56)

    start_frontend()

    # 后端（阻塞运行；import 放这里，避免前端没起时也白等引擎加载）
    import uvicorn
    from backend.main import app

    try:
        uvicorn.run(app, host="127.0.0.1", port=8010, workers=1, log_level="info")
    except KeyboardInterrupt:
        pass
    finally:
        stop_frontend()


if __name__ == "__main__":
    main()

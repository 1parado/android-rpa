# -*- coding: utf-8 -*-
"""人为打断控制：INTERRUPT 文件存在 = 请求停止。

双击 stop_agent.bat 创建该文件即可打断任何正在跑的批量任务 / MCP 操作；
双击 resume_agent.bat 删除该文件即可恢复。
"""
import os
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INTERRUPT_FILE = os.path.join(ROOT, "INTERRUPT")


def is_interrupted() -> bool:
    return os.path.exists(INTERRUPT_FILE)


def request_stop(reason: str = "manual"):
    with open(INTERRUPT_FILE, "w", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {reason}\n")


def clear_stop():
    if os.path.exists(INTERRUPT_FILE):
        os.remove(INTERRUPT_FILE)

# -*- coding: utf-8 -*-
"""Android RPA MCP Server

让任意支持 MCP 的 Agent（WorkBuddy / Claude Code / Cursor 等）实时
查看手机屏幕并操作手机。所有写操作（点击/滑动/输入/按键）执行前
都会检查人为打断开关（INTERRUPT 文件）。

运行：由 MCP 客户端以 stdio 方式拉起，无需手动运行。
手动测试：python mcp_server.py（会等待 stdio 输入）
"""
import os
import time

import uiautomator2 as u2
from mcp.server.fastmcp import FastMCP

import config
from core.control import INTERRUPT_FILE, clear_stop, is_interrupted, request_stop

mcp = FastMCP("android-rpa")

LOGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
_d = None


# ---------- 内部 ----------
def _device():
    global _d
    if _d is None:
        _d = u2.connect(config.DEVICE_SERIAL or None)
    return _d


def _guard():
    """人为打断守卫：INTERRUPT 文件存在时拒绝一切操作"""
    if is_interrupted():
        raise RuntimeError(
            f"⛔ 人为打断已触发（检测到 {INTERRUPT_FILE}），已拒绝执行。"
            f"请删除该文件（或双击 resume_agent.bat）后重试。")


def _shot(path: str) -> str:
    d = _device()
    last = None
    for _ in range(3):  # HarmonyOS screencap 偶发坏帧
        try:
            d.screenshot(path)
            return path
        except Exception as e:
            last = e
            time.sleep(1)
    raise RuntimeError(f"截图连续 3 次失败: {last}")


# ---------- 只读工具（查看，不受打断影响，随时可用） ----------
@mcp.tool()
def phone_screenshot() -> str:
    """实时截取手机屏幕。返回 PNG 文件路径，用 Read 工具即可查看图片。"""
    os.makedirs(LOGS, exist_ok=True)
    p = os.path.join(LOGS, f"mcp_shot_{int(time.time())}.png")
    return _shot(p)


@mcp.tool()
def phone_ui_texts() -> str:
    """读取手机当前界面上所有可见文本及其坐标（比截图更适合定位控件）。
    每行格式：`文本 | (x1,y1)-(x2,y2)`。坐标可直接用于 phone_tap。"""
    import re
    import xml.etree.ElementTree as ET
    xml = _device().dump_hierarchy()
    root = ET.fromstring(xml)
    lines = []
    for n in root.iter("node"):
        t = (n.get("text") or "").strip()
        desc = (n.get("content-desc") or "").strip()
        label = t or desc
        if not label:
            continue
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.get("bounds", ""))
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        if x2 - x1 <= 0 or y2 - y1 <= 0:
            continue
        lines.append(f"{label} | ({x1},{y1})-({x2},{y2})")
    return "\n".join(lines) if lines else "(当前界面没有可读文本)"


@mcp.tool()
def phone_current_app() -> str:
    """查看手机当前前台应用（包名 + activity）。"""
    cur = _device().app_current()
    return f"package={cur.get('package')} activity={cur.get('activity')}"


@mcp.tool()
def phone_screen_size() -> str:
    """查看手机屏幕分辨率。"""
    info = _device().info
    return f"{info['displayWidth']}x{info['displayHeight']}"


# ---------- 写操作工具（受人为打断守卫保护） ----------
@mcp.tool()
def phone_tap(x: int, y: int) -> str:
    """点击手机屏幕坐标 (x, y)。坐标可从 phone_screenshot 截图或 phone_ui_texts 获取。"""
    _guard()
    _device().click(int(x), int(y))
    return f"已点击 ({x}, {y})"


@mcp.tool()
def phone_tap_text(text: str) -> str:
    """按文本/描述查找屏幕上的控件并点击其中心（找不到会明确报错）。"""
    _guard()
    d = _device()
    for sel in ({"text": text}, {"textContains": text}, {"description": text}):
        el = d(**sel)
        if el.wait(timeout=2):
            b = el.info["visibleBounds"]
            cx, cy = (b["left"] + b["right"]) // 2, (b["top"] + b["bottom"]) // 2
            d.click(cx, cy)
            return f"已点击「{text}」于 ({cx}, {cy})"
    return f"未找到文本为「{text}」的控件"


@mcp.tool()
def phone_swipe(direction: str, distance: float = 0.4) -> str:
    """滑动手机屏幕。direction: up/down/left/right；distance: 滑动距离占屏比(0~1)。"""
    _guard()
    d = _device()
    w, h = d.info["displayWidth"], d.info["displayHeight"]
    cx, cy = w // 2, h // 2
    dx = int(w * distance) // 2
    dy = int(h * distance) // 2
    moves = {
        "up": (cx, cy + dy, cx, cy - dy),
        "down": (cx, cy - dy, cx, cy + dy),
        "left": (cx + dx, cy, cx - dx, cy),
        "right": (cx - dx, cy, cx + dx, cy),
    }
    if direction not in moves:
        return f"无效方向: {direction}（可选 up/down/left/right）"
    x1, y1, x2, y2 = moves[direction]
    d.swipe(x1, y1, x2, y2, duration=0.4)
    return f"已向{direction}滑动"


@mcp.tool()
def phone_input_text(text: str) -> str:
    """向当前焦点输入框输入文字（支持中文）。会临时切换 ADB 键盘并在结束后还原原输入法。"""
    _guard()
    d = _device()
    prev = d.shell("settings get secure default_input_method").output.strip()
    d.send_keys(text, clear=False)
    time.sleep(0.5)
    cur = d.shell("settings get secure default_input_method").output.strip()
    if cur != prev and "uiautomator" in cur and prev:
        d.shell(f"ime set {prev}")  # 还原用户输入法
    return f"已输入: {text[:50]}"


@mcp.tool()
def phone_key(key: str) -> str:
    """按手机按键。key: back(返回)/home(桌面)/enter(回车)/recent(多任务)。"""
    _guard()
    if key not in ("back", "home", "enter", "recent"):
        return f"无效按键: {key}"
    _device().press(key)
    return f"已按 {key}"


@mcp.tool()
def phone_start_app(package: str) -> str:
    """启动指定包名的 App（如 com.hpbr.bosszhipin / com.tencent.mm）。"""
    _guard()
    _device().app_start(package)
    time.sleep(3)
    return f"已启动 {package}"


@mcp.tool()
def phone_wait(seconds: float) -> str:
    """等待指定秒数（0~30），用于等待页面加载或模拟人类停顿。"""
    seconds = max(0.0, min(30.0, seconds))
    time.sleep(seconds)
    return f"已等待 {seconds}s"


# ---------- 打断开关管理 ----------
@mcp.tool()
def phone_request_stop(reason: str = "") -> str:
    """人为打断：设置停止开关。之后所有写操作会被拒绝，正在跑的批量任务会在下一步自动停下。"""
    request_stop(reason or "manual")
    return f"已设置打断开关: {INTERRUPT_FILE}"


@mcp.tool()
def phone_resume() -> str:
    """清除人为打断开关，恢复可操作状态。"""
    clear_stop()
    return "打断已清除，恢复可操作"


if __name__ == "__main__":
    mcp.run()

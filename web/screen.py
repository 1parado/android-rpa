# -*- coding: utf-8 -*-
"""实时手机屏幕预览：adb 截屏 -> MJPEG 流 -> 浏览器 <img>。

用法：
    python web/screen.py              # 默认 http://127.0.0.1:8766
    python web/screen.py --port 9001  # 换端口
    python web/screen.py --fps 5      # 降低刷新率（默认 2）
    python web/screen.py --mirror     # 拉起 scrcpy 窗口（60fps 高帧率投屏，可选）

思路参考 iPhone-use 的 MJPEG 独立通道：浏览器断开即停止截屏，
不依赖 uiautomator2 会话，只读不写，不干扰正在跑的 RPA 任务。
"""
import argparse
import io
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from core.device import connect_wifi_devices, adb_devices


class ScreenStream:
    """后台线程按固定间隔抓帧，浏览器各自消费最新 JPEG。"""

    def __init__(self, serial=None, fps=2):
        self.serial = serial
        self.interval = 1.0 / max(fps, 1)
        self._frame = b""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._clients = 0

    def _grab(self):
        cmd = [config.ADB_PATH, "-s", self.serial, "exec-out", "screencap", "-p"]
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=5).stdout
        except subprocess.SubprocessError:
            return
        if out.startswith(b"\x89PNG"):
            with self._lock:
                self._frame = out

    def _loop(self):
        while not self._stop.is_set():
            start = time.monotonic()
            self._grab()
            elapsed = time.monotonic() - start
            self._stop.wait(max(0.05, self.interval - elapsed))

    def start(self):
        connect_wifi_devices()
        devices = adb_devices()
        if self.serial is None:
            if not devices:
                raise RuntimeError("未检测到 Android 设备，请先 adb connect 或插线开调试")
            self.serial = devices[0]
        elif self.serial not in devices:
            raise RuntimeError(f"设备 {self.serial} 不在线，当前在线: {devices}")
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True, name="adb-screen")
            self._thread.start()

    def stop(self):
        self._stop.set()

    def client_connected(self):
        with self._lock:
            self._clients += 1

    def client_disconnected(self):
        with self._lock:
            self._clients -= 1
            if self._clients <= 0:
                self._clients = 0
                self.stop()

    def frame(self):
        with self._lock:
            return self._frame


STREAM = None


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self._send(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/stream":
            self._stream()
        elif self.path == "/frame":
            body = STREAM.frame()
            if body:
                self._send(200, body, "image/png")
            else:
                self._send(503, b"no frame yet", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        STREAM.client_connected()
        try:
            last = None
            while True:
                frame = STREAM.frame()
                if frame and frame != last:
                    last = frame
                    self.wfile.write(
                        b"--frame\r\n"
                        b"Content-Type: image/png\r\n\r\n" + frame + b"\r\n"
                    )
                time.sleep(0.1)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            STREAM.client_disconnected()

    def log_message(self, fmt, *args):
        pass


HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>实时屏幕 · Android RPA</title>
<style>
body{background:#171B1E;color:#EFF1EE;font:15px/1.6 "Segoe UI",sans-serif;
  display:flex;flex-direction:column;align-items:center;padding:40px 20px}
img{max-width:420px;width:100%;border:1px solid #444;border-radius:8px;background:#000}
.meta{font:12px/1.8 Consolas,monospace;color:#8a9298;margin-top:10px}
</style>
</head>
<body>
<img src="/stream" alt="手机实时屏幕">
<div class="meta">adb exec-out screencap | MJPEG | 127.0.0.1 only</div>
</body>
</html>"""


def main():
    global STREAM
    ap = argparse.ArgumentParser(description="Android 实时屏幕预览")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--serial", default=config.DEVICE_SERIAL or None)
    ap.add_argument("--fps", type=int, default=2)
    ap.add_argument("--mirror", action="store_true",
                    help="用 scrcpy 拉起高帧率投屏窗口（需要安装 scrcpy）")
    args = ap.parse_args()

    if args.mirror:
        _launch_mirror(args.serial)

    STREAM = ScreenStream(serial=args.serial, fps=args.fps)
    STREAM.start()
    print(f"[OK] 实时屏幕: http://127.0.0.1:{args.port}  （Ctrl-C 停止）")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


def _launch_mirror(serial):
    """检测 scrcpy 并拉起高帧率投屏窗口；未安装则提示但不中断浏览器模式"""
    if not config.SCRCPY_PATH:
        print("[!] 未检测到 scrcpy，跳过高帧率投屏。")
        print("    下载: https://github.com/Genymobile/scrcpy/releases")
        print("    配置: 设置环境变量 ANDROID_RPA_SCRCPY 指向 scrcpy.exe，")
        print("          或解压到 %LOCALAPPDATA%\\scrcpy\\ 或加入 PATH")
        return
    cmd = [config.SCRCPY_PATH]
    if serial:
        cmd += ["-s", serial]
    print(f"[OK] 启动 scrcpy 投屏: {config.SCRCPY_PATH}")
    try:
        subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE
                         if os.name == "nt" else 0)
    except OSError as e:
        print(f"[X] scrcpy 启动失败: {e}")


if __name__ == "__main__":
    main()

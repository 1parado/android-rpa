# -*- coding: utf-8 -*-
"""设备连接管理：adb 检测 + uiautomator2 会话"""
import subprocess
import sys

from adbutils import adb

import config


def adb_devices() -> list:
    """返回当前连接的设备序列号列表"""
    out = subprocess.run([config.ADB_PATH, "devices"],
                         capture_output=True, text=True, timeout=10)
    devices = []
    for line in out.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    return devices


def connect(serial: str = None):
    """连接设备，返回 uiautomator2 设备对象"""
    devices = adb_devices()
    if not devices:
        print("[X] 未检测到设备。请检查：")
        print("    1. 手机已开启 开发者选项 -> USB 调试")
        print("    2. 插线后手机弹窗点了『允许 USB 调试』")
        print(f"    3. 手动验证: {config.ADB_PATH} devices")
        print("    4. 多台设备时: 设置环境变量 ANDROID_RPA_SERIAL 指定序列号")
        sys.exit(1)
    if serial is None:
        serial = config.DEVICE_SERIAL or None
    if serial is None:
        serial = devices[0]
        if len(devices) > 1:
            print(f"[!] 检测到多台设备 {devices}，使用第一台: {serial}")
    print(f"[OK] 连接设备: {serial}")

    import uiautomator2 as u2
    d = u2.connect(serial)
    info = d.info
    sdk = d.device_info.get("sdk_int") or d.shell("getprop ro.build.version.sdk").output.strip()
    print(f"[OK] 屏幕: {info['displayWidth']}x{info['displayHeight']} | SDK {sdk}")
    return d

# -*- coding: utf-8 -*-
"""全局配置：包名、话术模板、频率控制、目标列表

移植指南（其他用户 clone 后只需要改这里 + WECHAT/BOSS 的业务配置）：
1. ADB_PATH 一般不用改：按 环境变量 > PATH > 常见安装位置 自动发现
2. DEVICE_SERIAL 一般不用改：留空自动连接唯一设备；多台设备时用
   环境变量 ANDROID_RPA_SERIAL 或填序列号
3. 打招呼语建议在 BOSS App 内设置（我的->设置->消息设置->打招呼语），
   脚本会直接复用；未设置时可用弹窗模板
"""
import os
import shutil


def _find_adb() -> str:
    """按 环境变量 > PATH > 常见安装位置 自动发现 adb"""
    env = os.environ.get("ANDROID_RPA_ADB")
    if env and os.path.isfile(env):
        return env
    found = shutil.which("adb")
    if found:
        return found
    for p in (
        # Android Studio / SDK Manager 在 Windows 的默认安装位置
        os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
        r"C:\platform-tools\adb.exe",
        r"C:\Android\platform-tools\adb.exe",
        r"C:\Users\Public\platform-tools\adb.exe",
        "/usr/bin/adb",
        "/usr/local/bin/adb",
        "/opt/homebrew/bin/adb",
    ):
        if os.path.isfile(p):
            return p
    return "adb"   # 兜底：直接赌 PATH 里有


# adb 可执行文件路径（自动发现，特殊环境可用环境变量 ANDROID_RPA_ADB 覆盖）
ADB_PATH = _find_adb()

# 设备序列号：留空 = 自动连接唯一已连接设备；多设备时填 adb devices 里的序列号
# （也可用环境变量 ANDROID_RPA_SERIAL 覆盖，无需改代码）
DEVICE_SERIAL = os.environ.get("ANDROID_RPA_SERIAL", "")

# 无线 adb 设备（"IP:端口" 列表），如 ["192.168.1.100:5555"]
# 支持 "IP" 简写（默认端口 5555）。也支持环境变量 ANDROID_RPA_WIFI（逗号分隔）
# 手机端：开发者选项 -> 无线调试（Android 11+ 记得先配对），
# 或先插一次 USB 执行 `adb tcpip 5555` 后拔线。
WIFI_DEVICES = [
    s for s in os.environ.get("ANDROID_RPA_WIFI", "").replace("：", ":").split(",") if s.strip()
]

# 目标 App 包名：加新 App 只需在这里加一行 "别名": "包名"，
# 然后在 apps/ 下参照 boss_zhipin.py 写一个 run(r) 流程模块即可
# （包名可用 adb shell pm list packages | grep 关键词 查询）
PACKAGES = {
    "boss": "com.hpbr.bosszhipin",
    "wechat": "com.tencent.mm",
    "qq": "com.tencent.mobileqq",
}

# ---------- 频率控制（风控核心：慢就是快） ----------
RATE = {
    "min_delay": 3.0,        # 每次点击后最小等待（秒）
    "max_delay": 8.0,        # 最大等待（秒），实际随机取区间值
    "pause_probability": 0.18,  # 概率性「分神」：像人一样突然停下来看一会儿
    "pause_min": 8.0,           # 分神最短时长（秒）
    "pause_max": 18.0,          # 分神最长时长（秒）
    "max_ops_per_session": 150,  # 单次会话操作上限（每次投递约消耗 6 步）
    "session_cooldown": 600,     # 会话之间冷却（秒）
}

# ---------- BOSS直聘 ----------
BOSS = {
    # 打招呼话术（仅供参考）：推荐在 BOSS App 内设置「自动打招呼语」，
    # 设置后点「立即沟通」会直接发出该话术，脚本无需输入任何文字。
    # App 内未设置时，脚本会走「选模板 + 发送并开聊」的弹窗流程。
    "greetings": [
        "您好，我对这个职位很感兴趣，希望有机会进一步沟通，谢谢！",
    ],
    "max_greetings": 150,  # 单次运行最多打招呼数量（BOSS 免费用户每日上限 150）
    # 要打招呼的职位名关键字（java 不分大小写；AI 按独立词匹配）。
    # 推荐用法：在 App 里手动搜索/筛选好职位列表，脚本按当前页面顺序投递。
    "job_keywords": [
        # "java",
        # "AI",
    ],
}

# ---------- 微信 ----------
WECHAT = {
    # 要发消息的联系人列表：{联系人备注名: 消息}
    # 注意：先小规模测试，确认话术无误再放量
    "targets": {
        # "文件传输助手": "来自 PC RPA 脚本的测试消息",
    },
}

# ---------- 本地私有配置（可选，不提交到 git） ----------
# 复制下面内容为 config_local.py 即可覆盖任意配置，适合放个人序列号/路径：
#   ANDROID_RPA_ADB = r"C:\your\adb.exe"
#   DEVICE_SERIAL = "YOUR_SERIAL"
#   WIFI_DEVICES = ["192.168.1.100:5555"]   # 无线 adb 设备
try:
    from config_local import *   # noqa: F401,F403
except ImportError:
    pass

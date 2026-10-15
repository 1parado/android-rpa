# -*- coding: utf-8 -*-
"""一键环境自检与安装：git clone 后只需 `python bootstrap.py`

做五件事：
1. 检查 Python 版本（>=3.10）
2. 检测/创建虚拟环境并安装依赖（--install 时）
3. 发现 adb（环境变量 > PATH > 常见位置）
4. 列出当前连接的安卓设备
5. 打印剩余的手动步骤提示

用法：
    python bootstrap.py            # 只自检，给出结论
    python bootstrap.py --install  # 自检 + 创建 .venv 并安装依赖
"""
import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY_MIN = (3, 10)


def step(msg):
    print(f"\n== {msg} ==")


def check_python() -> bool:
    step("1/5 Python 版本")
    v = sys.version_info
    ok = v >= PY_MIN
    print(f"[{'OK' if ok else 'X'}] Python {v.major}.{v.minor}.{v.micro}"
          + ("" if ok else f"（需要 >= {PY_MIN[0]}.{PY_MIN[1]}）"))
    return ok


def install_deps() -> bool:
    step("2/5 依赖安装")
    venv_dir = os.path.join(ROOT, ".venv")
    if os.path.isdir(venv_dir):
        py = (os.path.join(venv_dir, "Scripts", "python.exe") if os.name == "nt"
              else os.path.join(venv_dir, "bin", "python"))
        print(f"[OK] 已有虚拟环境: {venv_dir}")
    else:
        print("创建虚拟环境 .venv ...")
        if subprocess.call([sys.executable, "-m", "venv", venv_dir]) != 0:
            print("[X] venv 创建失败")
            return False
        py = (os.path.join(venv_dir, "Scripts", "python.exe") if os.name == "nt"
              else os.path.join(venv_dir, "bin", "python"))
    print("安装依赖 requirements.txt ...")
    r = subprocess.call([py, "-m", "pip", "install", "-r",
                         os.path.join(ROOT, "requirements.txt")])
    print(f"[{'OK' if r == 0 else 'X'}] 依赖安装"
          + ("完成" if r == 0 else "失败（可换源: pip install -i "
              "https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt）"))
    return r == 0


def check_adb() -> str:
    step("3/5 adb 发现")
    env = os.environ.get("ANDROID_RPA_ADB")
    adb = (env if env and os.path.isfile(env) else None) or shutil.which("adb")
    for p in (r"C:\platform-tools\adb.exe", "/usr/bin/adb",
              "/usr/local/bin/adb", "/opt/homebrew/bin/adb"):
        if adb is None and os.path.isfile(p):
            adb = p
    if adb is None:
        # 回退：复用 config.py / config_local.py 里的发现结果
        try:
            sys.path.insert(0, ROOT)
            import config
            if shutil.which(config.ADB_PATH) or os.path.isfile(config.ADB_PATH):
                adb = config.ADB_PATH
        except Exception:
            pass
    if adb:
        print(f"[OK] adb: {adb}")
    else:
        print("[X] 未找到 adb。解决方式任选其一：")
        print("    a. 下载 Android platform-tools，解压后把 adb 加入 PATH")
        print("    b. 设置环境变量 ANDROID_RPA_ADB 指向 adb 可执行文件")
    return adb or ""


def check_devices(adb: str) -> list:
    step("4/5 设备检测")
    if not adb:
        print("[!] 跳过（没有 adb）")
        return []
    try:
        out = subprocess.run([adb, "devices"], capture_output=True,
                             text=True, timeout=10).stdout
    except Exception as e:
        print(f"[X] adb 执行失败: {e}")
        return []
    devices = [ln.split()[0] for ln in out.splitlines()[1:]
               if len(ln.split()) >= 2 and ln.split()[1] == "device"]
    if devices:
        print(f"[OK] 已连接设备: {devices}")
    else:
        print("[X] 未检测到设备。请确认手机已开 USB 调试并点了授权弹窗")
    return devices


def next_steps(devices: list):
    step("5/5 下一步")
    if not devices:
        print("接好手机后重新运行本脚本确认，然后：")
        return
    print("1. 激活虚拟环境并初始化手机端组件（免 root）：")
    act = (".venv\\Scripts\\activate" if os.name == "nt"
           else "source .venv/bin/activate")
    print(f"   {act}")
    print("   python -m uiautomator2 init")
    print("2. 空跑验证（不点击）：python main.py --task boss")
    print("3. 真实执行：python main.py --task boss --live")
    print("4. 可选：复制 config_local.example.py 为 config_local.py 定制个人配置")


def main():
    ap = argparse.ArgumentParser(description="Android RPA 环境自检与安装")
    ap.add_argument("--install", action="store_true",
                    help="创建 .venv 并安装依赖（默认只自检）")
    args = ap.parse_args()

    ok = check_python()
    if args.install:
        ok = install_deps() and ok
    else:
        step("2/5 依赖安装")
        print("[!] 跳过（加 --install 可自动创建 .venv 并安装依赖）")
    adb = check_adb()
    devices = check_devices(adb)
    next_steps(devices)
    print("\n== 结论 ==")
    print("环境就绪，可以开始使用" if ok and devices else "请按上面 [X] 项修正后重试")


if __name__ == "__main__":
    main()

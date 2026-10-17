# Android RPA（uiautomator2 版）

PC 端 Python 脚本通过 adb 控制安卓手机，自动执行 **BOSS直聘打招呼**、**微信发消息** 等 RPA 任务。

## 快速接入你自己的手机

本项目**零硬编码**：adb 路径自动发现、设备序列号自动识别、坐标全部按屏幕比例计算，
clone 后只需三步：

1. **一键环境自检/安装**
   ```bash
   git clone <this-repo> && cd android-rpa
   python bootstrap.py --install   # 自动创建 .venv + 装依赖 + 找 adb + 检测手机
   ```
   脚本会逐项检查 Python 版本、依赖、adb、设备连接，缺什么提示什么。
   也可以手动来（效果等价）：
   ```bash
   python -m venv .venv
   .venv\Scripts\activate        # Windows；macOS/Linux 用 source .venv/bin/activate
   pip install -r requirements.txt
   # 手机开启「开发者选项 -> USB 调试」插线后，验证：
   adb devices                    # 需要 adb 在 PATH；或设环境变量 ANDROID_RPA_ADB 指向 adb.exe
   python -m uiautomator2 init    # 向手机安装控制端组件（免 root，见下节）
   ```

2. **按需改 `config.py`**（全部可留空/用默认值）
   | 配置 | 默认 | 说明 |
   |---|---|---|
   | `ADB_PATH` | 自动发现 | 环境变量 `ANDROID_RPA_ADB` 可覆盖 |
   | `DEVICE_SERIAL` | 空=自动 | 多台设备时填序列号，或用 `ANDROID_RPA_SERIAL` |
   | `WIFI_DEVICES` | 空 | 无线 adb 设备列表，如 `["192.168.1.100:5555"]`，见下 |
   | `PACKAGES` | 已填常用 App | 换目标 App 改这里 |
   | `BOSS.greetings` | 通用示例 | 建议在 BOSS App 内设置「自动打招呼语」，脚本直接复用 |
   | `WECHAT.targets` | 空 | `{联系人备注: 消息}` |
   | `RATE` | 已调好 | 频率/分神概率/单轮上限 |

3. **先 dry-run 再 --live**
   ```bash
   python main.py --task boss     # 只找控件不点击，验证选择器
   python main.py --task boss --live
   ```

> **多设备 / adb 不在 PATH**：复制 `config_local.example.py` 为 `config_local.py`
> 填入 `ADB_PATH` / `DEVICE_SERIAL`（该文件被 git 忽略，不会提交）；或用环境变量
> `ANDROID_RPA_ADB` / `ANDROID_RPA_SERIAL`。

> **无线 adb（免插线）**：手机与电脑连同一 Wi-Fi，两种方式任选：
> - Android 11+「开发者选项 -> 无线调试」：端口随机，先在电脑上 `adb pair IP:配对端口` 配对
>   （配对码在手机无线调试页面），之后把「IP:端口」填入 `WIFI_DEVICES`；
> - 任意 Android 版本：插一次 USB 执行 `adb tcpip 5555` 后拔线，填 `手机IP` 即可
>   （重启手机后失效，需重新执行）。
>
> 环境变量方式：`ANDROID_RPA_WIFI="192.168.1.100:5555,192.168.1.101"`（逗号分隔，端口可省略默认 5555）。
> 任务启动时会自动 `adb connect` 列表里的设备，连接失败不影响 USB 设备。

## 手机端装了什么 & 如何卸载

`uiautomator2 init` 会向手机安装两个组件（免 root，uiautomator2 v3.x 方案）：

| 组件 | 位置 | 作用 |
|---|---|---|
| `com.github.uiautomator` 应用（桌面「ATX」图标） | 普通应用 | uiautomator server + AdbKeyboard（中文输入用） |
| `u2.jar` | `/data/local/tmp/u2.jar` | 经 `app_process` 拉起的服务 |

不用了？一键清干净：

```bash
adb uninstall com.github.uiautomator
adb shell rm /data/local/tmp/u2.jar
```

> 老教程里的 `atx-agent`（Go 常驻代理）是 uiautomator2 v2.x 的机制，v3 已移除，本项目不依赖。

## 实时屏幕预览 / 投屏

两种方式按需选：

| 方式 | 启动 | 帧率 | 用途 |
|---|---|---|---|
| 浏览器预览（内置） | `python web/screen.py` | 2-3fps | 盯一眼手机当前画面，零额外依赖 |
| scrcpy 投屏（可选） | `python web/screen.py --mirror` | 60fps | 高帧率观察动画/滑动，需装 scrcpy |

**浏览器预览**：纯 Python + adb，启动后打开 `http://127.0.0.1:8766`，
只读不写、不干扰 RPA 任务；浏览器关掉页面采集自动停止。磁盘占用为 0（帧在内存里流转）。
常用参数：`--port 9001` 换端口、`--fps 5` 提高刷新率、`--serial xxx` 指定设备。

**scrcpy 投屏**：`--mirror` 会自动找 scrcpy 并拉起独立投屏窗口（60fps、可直接鼠标操作手机），
找不到时会打印下载地址和配置方法。安装任选其一：

1. 从 [scrcpy releases](https://github.com/Genymobile/scrcpy/releases) 下载 zip，
   解压到 `%LOCALAPPDATA%\scrcpy\`（自动识别）
2. 或解压到任意位置后设置环境变量 `ANDROID_RPA_SCRCPY` 指向 `scrcpy.exe`
3. 或把 scrcpy.exe 所在目录加入 `PATH`

> scrcpy 是独立软件，不装它只用浏览器预览即可，不影响本项目其他功能。
> 无线设备同样支持：`adb connect 手机IP:端口` 后，`--serial 手机IP:端口`。

## MCP：让 Agent 直接看屏 + 操作手机

本项目内置 stdio MCP Server（`mcp_server.py`），任何支持 MCP 的客户端
（WorkBuddy / Claude Code / Cursor 等）都能让 Agent 直接控制手机。
在其他客户端注册只需一段 JSON（路径换成你的项目位置）：

```json
{
  "mcpServers": {
    "android-rpa": {
      "command": "/path/to/android-rpa/.venv/Scripts/python.exe",
      "args": ["/path/to/android-rpa/mcp_server.py"]
    }
  }
}
```

（Windows 下 command 形如 `E:/android-rpa/.venv/Scripts/python.exe`）

**提供的工具：**

| 工具 | 说明 |
|---|---|
| `phone_screenshot` | 实时截图（返回 PNG 路径，Read 即可看图） |
| `phone_ui_texts` | 读取当前界面所有可见文本 + 坐标 |
| `phone_current_app` / `phone_screen_size` | 前台应用 / 分辨率 |
| `phone_tap` / `phone_tap_text` | 按坐标 / 按文本点击 |
| `phone_swipe` | 上下左右滑动 |
| `phone_input_text` | 输入文字（自动还原输入法） |
| `phone_key` | 返回 / Home / 回车 / 多任务 |
| `phone_start_app` / `phone_wait` | 启动 App / 等待 |
| `phone_request_stop` / `phone_resume` | 设置 / 清除人为打断 |

**典型用法**：直接对 Agent 说「帮我看看手机现在什么页面」「帮我在微信里给 XX 发条消息」，
Agent 会 截图 → 看内容 → 点坐标/文本 → 再截图验证，形成感知-操作闭环。

## 目录结构

```
android-rpa/
├── bootstrap.py         # 一键环境自检/安装（clone 后先跑这个）
├── main.py              # 入口
├── config.py            # 包名 / 话术 / 频率控制 / 联系人列表（唯一需要按需修改的文件）
├── config_local.example.py  # 个人配置模板（复制为 config_local.py 使用，git 忽略）
├── mcp_server.py        # MCP Server：让 Agent 实时看屏 + 操作手机
├── stop_agent.bat/.sh   # 【双击/运行】人为打断一切任务
├── resume_agent.bat/.sh # 【双击/运行】清除打断，恢复运行
├── core/
│   ├── device.py        # adb 检测 + u2 连接
│   ├── control.py       # 人为打断开关（INTERRUPT 文件）
└── actions.py       # Robot：人味延迟、查找、点击/长按/双击、滑动/手势/拖拽、输入、截图、操作预算
├── apps/
│   ├── boss_zhipin.py   # BOSS直聘自动打招呼
│   └── wechat.py        # 微信自动发消息
└── logs/                # 运行日志 + 异常截图（git 忽略）
```

## 人为打断（随时急停）

- **Windows**：双击 `stop_agent.bat` 急停，双击 `resume_agent.bat` 恢复
- **macOS/Linux**：`./stop_agent.sh` / `./resume_agent.sh`（或手动 `touch INTERRUPT` / `rm INTERRUPT`）
- 正在跑的批量任务会在下一步（点击前 / 延迟中）立即停止；MCP 的所有写操作
  （点击/滑动/输入）会被拒绝。
- 截图/读文本等**只读操作不受打断影响**，急停后仍可查看手机状态。

## 运行

```bash
# 1. 先空跑：只找控件不点击，验证选择器是否匹配当前 App 版本
python main.py --task boss
python main.py --task wechat

# 2. 确认日志无 [X]/未找到 后，真实执行
python main.py --task boss --live
python main.py --task wechat --live
```

## 核心设计

- **dry-run 优先**：默认只定位控件不操作，日志能看到每一步找到什么，避免盲点误触。
- **风控三板斧**（`config.RATE`）：
  - 每次点击后随机延迟 3–8 秒，另有 18% 概率「分神」停顿 8–18 秒（像人）
  - 单次会话最多 150 个操作，超了主动停
  - 选择器用文本/描述而非控件 id，App 更新不易失效
- **异常留痕**：找不到预期控件时自动截图到 `logs/`，方便对照修正。
- **跨设备适配**：坐标按屏幕比例计算，控件找不到时有多级兜底（文本 → 描述 → 已知 id → 相对坐标）。

## 手势能力（Robot 通用原语）

`core/actions.py` 的 `Robot` 封装了与具体 App 无关的操作原语，扩展新任务直接复用：

| 方法 | 说明 |
|---|---|
| `tap(el_or_xy)` / `tap_text(text)` | 点击控件或坐标 / 按文字查找后点击 |
| `long_press(el_or_xy, duration)` | 长按（控件或坐标），默认 1 秒 |
| `double_tap(el_or_xy)` | 双击（控件或坐标） |
| `swipe_direction("up" \|"down" \|"left" \|"right", scale)` | 按屏幕比例的方向滑动（翻页/下拉刷新） |
| `gesture_path([(x,y), ...], duration)` | 多点连续手势：曲线滑动、手势解锁轨迹等 |
| `drag(start, end, duration)` | 拖拽（按下-移动-抬起时序，区别于滑动） |
| `input_text(el, text)` | 输入中文（ADB 键盘，自动还原原输入法） |
| `find/find(...)` | 按 文本/资源id/描述/模糊文本 查找控件 |

所有手势都受 dry-run、操作预算与人为打断保护。底层为 uiautomator2 单指手势；
多指/画布类界面需图像识别方案，不在当前范围内。

**加一个新 App 任务**：`config.py` 的 `PACKAGES` 加一行 `"别名": "包名"`，
再在 `apps/` 下参照 `boss_zhipin.py` 写一个 `run(r)` 流程模块、在 `main.py` 挂上即可。

## 元素定位调试

App 控件 id/hint 文字随版本会变。用 weditor 在浏览器里实时看控件树：

```bash
pip install weditor
python -m weditor    # 打开 http://localhost:17310
```

选中控件后把它的 `text` / `resource-id` / `description` 更新到对应 `apps/*.py` 的 `find(...)` 里即可。

## 风险提醒

- BOSS直聘 / 微信都有自动化行为检测：**保持低频、用小号先试、话术别群发同一句**。
- 账号被限制的代价远大于省下的时间，`--live` 模式务必从小批量开始。

---

学AI，上[L站](https://linux.do/)


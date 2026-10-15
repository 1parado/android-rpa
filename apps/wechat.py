# -*- coding: utf-8 -*-
"""微信：自动给指定联系人发消息

已于 2026-10-15 在华为 JER-AN10 (HarmonyOS) + 微信实机跑通：
  启动 -> 点右上角放大镜(meb) -> 搜索框输入联系人 -> 点结果
  -> 聊天输入框输入 -> 点发送(自绘控件,坐标兜底)

实测要点：
- 微信新版把搜索图标放右上角，resource-id=meb（混淆 id 随版本会变）
- 搜索结果第一个 text 匹配是输入框里的查询词，必须过滤 y>300 的
- 「发送」按钮是自绘控件，控件树读不到，用屏幕右下角坐标兜底
"""
import time

import config
from core.actions import Robot, log

PACKAGE = config.PACKAGES["wechat"]


def run(r: Robot):
    d = r.d
    w, h = d.info["displayWidth"], d.info["displayHeight"]
    r.start_app(PACKAGE)
    sent = 0

    for contact, message in config.WECHAT["targets"].items():
        log.info(f"=== 目标: {contact} ===")

        # 1. 打开搜索：文本/描述/已知 resource-id 逐个尝试，最后右上角坐标兜底
        #    （微信混淆 id 随版本变化，candidate 列表便于按版本补充）
        search = None
        for kw in ({"desc": "搜索"}, {"rid": "com.tencent.mm:id/meb"},
                   {"rid": "com.tencent.mm:id/icon_search"}):
            search = r.find(timeout=3, **kw)
            if search:
                break
        if search:
            r.tap(search, "搜索图标")
        else:
            log.warning("未找到搜索图标，按坐标兜底（右上角）")
            d.click(int(w * 0.86), int(h * 0.085))

        # 2. 输入联系人
        box = d(className="android.widget.EditText")
        if not box.count:
            log.error("找不到搜索输入框")
            r.screenshot("wx_no_box")
            continue
        box[0].click()
        time.sleep(0.8)
        d.send_keys(contact, clear=True)
        time.sleep(2.5)

        # 3. 点搜索结果（过滤掉输入框里的查询词：必须低于顶部输入框区域）
        top_line = int(h * 0.12)
        target = None
        for el in d(text=contact):
            b = el.info["visibleBounds"]
            if b["top"] > top_line:
                target = b
                break
        if target is None:
            log.warning(f"搜索结果里没有『{contact}』")
            r.screenshot("wx_no_result")
            r.press_back()
            continue
        d.click((target["left"] + target["right"]) // 2,
                (target["top"] + target["bottom"]) // 2)
        time.sleep(2.5)
        if "ChattingUI" not in d.app_current()["activity"]:
            log.error("未进入聊天界面")
            r.screenshot("wx_not_chat")
            continue

        # 4. 输入消息
        chat_box = d(className="android.widget.EditText")
        if not chat_box.count:
            log.error("找不到聊天输入框")
            r.screenshot("wx_no_chatbox")
            r.press_back()
            continue
        chat_box[0].click()
        time.sleep(0.8)
        d.send_keys(message, clear=True)
        time.sleep(1.5)

        # 5. 发送：优先找控件，自绘按钮则按右下角坐标兜底
        send = r.find(text="发送", timeout=3)
        if send:
            r.tap(send, "发送")
        else:
            log.info("发送按钮为自绘控件，按坐标点击")
            d.click(int(w * 0.9), int(h * 0.965))
        sent += 1
        log.info(f"已发送给 {contact} ({sent})")
        time.sleep(2)

        r.press_back()   # 退出聊天
        r.press_back()   # 退出搜索

    log.info(f"完成：共发送 {sent} 条消息")
    r.stop_app(PACKAGE)
    return sent

# -*- coding: utf-8 -*-
"""通用操作库：带随机延迟的『人味』点击、查找、输入、截图"""
import logging
import os
import random
import time
from datetime import datetime

import config
from core.control import is_interrupted

log = logging.getLogger("rpa")

# 项目根目录下的 logs/（跨平台路径，不再依赖 config.__file__ 字符串切分）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS_DIR = os.path.join(ROOT, "logs")


class Interrupted(RuntimeError):
    """人为打断（INTERRUPT 文件存在）"""


def setup_logging():
    os.makedirs(LOGS_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                os.path.join(
                    LOGS_DIR, f"run_{datetime.now():%Y%m%d_%H%M%S}.log"),
                encoding="utf-8"),
        ],
    )


class Robot:
    """uiautomator2 设备的语义化封装"""

    def __init__(self, d, dry_run: bool = True):
        self.d = d
        self.dry_run = dry_run
        self.op_count = 0
        # 会话操作预算：默认取配置值，批量任务可按数量动态调大
        self.op_budget = config.RATE["max_ops_per_session"]

    # ---------- 基础 ----------
    def human_delay(self):
        """随机人味延迟：基础区间 + 概率性「分神」长停顿，风控的基本功"""
        if is_interrupted():
            raise Interrupted("人为打断：检测到 INTERRUPT 文件，任务已停止")
        t = random.uniform(config.RATE["min_delay"], config.RATE["max_delay"])
        if random.random() < config.RATE.get("pause_probability", 0):
            t = random.uniform(config.RATE.get("pause_min", 8),
                               config.RATE.get("pause_max", 18))
            log.info(f"分神停顿 {t:.1f}s ...")
        else:
            log.info(f"等待 {t:.1f}s ...")
        # 分段睡眠，打断能秒级生效
        end = time.time() + t
        while time.time() < end:
            if is_interrupted():
                raise Interrupted("人为打断：检测到 INTERRUPT 文件，任务已停止")
            time.sleep(min(0.5, max(0, end - time.time())))

    def check_budget(self):
        if is_interrupted():
            raise Interrupted("人为打断：检测到 INTERRUPT 文件，任务已停止")
        self.op_count += 1
        if self.op_count > self.op_budget:
            raise RuntimeError("已达单次会话操作上限，主动停止以保护账号")

    # ---------- 查找 ----------
    def find(self, text=None, rid=None, desc=None, text_contains=None, timeout=8):
        """按 文本/resource-id/content-desc/模糊文本 查找控件，找不到返回 None"""
        sel = {}
        if text is not None:
            sel["text"] = text
        if rid is not None:
            sel["resourceId"] = rid
        if desc is not None:
            sel["description"] = desc
        if text_contains is not None:
            sel["textContains"] = text_contains
        el = self.d(**sel)
        if el.wait(timeout=timeout):
            return el
        return None

    def exists(self, timeout=1.5, **kw) -> bool:
        return self.find(timeout=timeout, **kw) is not None

    # ---------- 动作 ----------
    def tap(self, el, label=""):
        self.check_budget()
        if self.dry_run:
            log.info(f"[DRY] 点击 {label or el.info.get('text', '')}")
            return True
        log.info(f"点击: {label}")
        el.click()
        self.human_delay()
        return True

    def tap_text(self, text, timeout=8, label=None):
        el = self.find(text=text, timeout=timeout)
        if el is None:
            log.warning(f"未找到控件: text={text}")
            return False
        return self.tap(el, label or text)

    def long_press(self, el_or_xy, duration=1.0, label=""):
        """长按。el_or_xy 传控件对象或 (x, y) 坐标；duration 单位秒"""
        self.check_budget()
        if hasattr(el_or_xy, "click"):          # 控件对象
            target = el_or_xy
            name = label or el_or_xy.info.get("text", "")
        else:                                    # (x, y) 坐标
            x, y = el_or_xy
            target = None
            name = label or f"({x},{y})"
        if self.dry_run:
            log.info(f"[DRY] 长按 {name}")
            return True
        log.info(f"长按 {duration:.1f}s: {name}")
        if target is not None:
            target.long_click(duration=duration)
        else:
            self.d.long_click(x, y, duration=duration)
        self.human_delay()
        return True

    def double_tap(self, el_or_xy, label=""):
        """双击。控件对象或 (x, y) 坐标"""
        self.check_budget()
        if hasattr(el_or_xy, "click"):
            target = el_or_xy
            name = label or el_or_xy.info.get("text", "")
        else:
            x, y = el_or_xy
            target = None
            name = label or f"({x},{y})"
        if self.dry_run:
            log.info(f"[DRY] 双击 {name}")
            return True
        log.info(f"双击: {name}")
        if target is not None:
            target.double_click()
        else:
            self.d.double_click(x, y)
        self.human_delay()
        return True

    def swipe_direction(self, direction, scale=0.9, label=""):
        """按屏幕比例方向滑动：direction 取 up / down / left / right，
        scale 为滑动幅度占屏幕的比例 (0, 1]。适合翻页、下拉刷新等模糊滑动"""
        self.check_budget()
        if self.dry_run:
            log.info(f"[DRY] 滑动 {direction} scale={scale}")
            return True
        log.info(f"滑动 {direction} scale={scale} {label}")
        self.d.swipe_ext(direction, scale=scale)
        self.human_delay()
        return True

    def gesture_path(self, points, duration=0.5, label=""):
        """多点连续手势：points 为 [(x, y), ...] 至少两点，逐点注入。
        可做曲线滑动、手势解锁轨迹等复杂手势（底层单指）"""
        self.check_budget()
        if len(points) < 2:
            log.warning("gesture_path 至少需要两个点")
            return False
        if self.dry_run:
            log.info(f"[DRY] 手势路径 {len(points)} 点 {label}")
            return True
        log.info(f"手势路径 {len(points)} 点 {label}")
        self.d.swipe_points(points, duration=duration)
        self.human_delay()
        return True

    def drag(self, start, end, duration=0.5, label=""):
        """拖拽：按住 start 拖到 end（与滑动不同，drag 有明确的按下-移动-抬起时序）"""
        self.check_budget()
        if self.dry_run:
            log.info(f"[DRY] 拖拽 {start}->{end} {label}")
            return True
        log.info(f"拖拽 {start}->{end} {label}")
        self.d.drag(start[0], start[1], end[0], end[1], duration=duration)
        self.human_delay()
        return True

    def input_text(self, el, text, label="输入框"):
        self.check_budget()
        if self.dry_run:
            log.info(f"[DRY] 输入到 {label}: {text[:20]}...")
            return True
        log.info(f"输入到 {label}")
        el.click()
        time.sleep(0.5)
        el.clear_text()
        # 记住原输入法，send_keys 会临时切到 ADB 键盘
        prev_ime = self.d.shell(
            "settings get secure default_input_method").output.strip()
        # set_fastinput_ime 走 ADB 键盘，支持中文且更快
        self.d.send_keys(text, clear=True)
        self.human_delay()
        # 打完字还原用户输入法，否则手机上会没输入法可用
        cur_ime = self.d.shell(
            "settings get secure default_input_method").output.strip()
        if cur_ime != prev_ime and "uiautomator" in cur_ime and prev_ime:
            self.d.shell(f"ime set {prev_ime}")
            log.info(f"已还原输入法: {prev_ime.split('/')[-1]}")
        return True

    def press_back(self):
        self.check_budget()
        log.info("按返回键")
        self.d.press("back")
        self.human_delay()

    def screenshot(self, name="shot"):
        os.makedirs(LOGS_DIR, exist_ok=True)
        path = os.path.join(LOGS_DIR, f"{name}_{datetime.now():%H%M%S}.png")
        # HarmonyOS 的 screencap 偶发返回坏数据，重试兜底
        for i in range(3):
            try:
                self.d.screenshot(path)
                log.info(f"截图: {path}")
                return path
            except Exception as e:
                log.warning(f"第{i+1}次截图失败: {e}")
                time.sleep(1)
        return None

    def start_app(self, package):
        self.check_budget()
        log.info(f"启动 App: {package}")
        self.d.app_start(package)
        time.sleep(5)

    def stop_app(self, package):
        log.info(f"关闭 App: {package}")
        self.d.app_stop(package)

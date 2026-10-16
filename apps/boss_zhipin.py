# -*- coding: utf-8 -*-
"""BOSS直聘：按当前页面列表顺序批量打招呼（用户手动筛选职位范围）

流程（2026-10-15 实机验证）：
  扫描当前列表页职位卡（以薪资行为锚点向上找标题）
  → 点标题进详情 → 点「立即沟通」
  → 开了自动打招呼语时：直接发送并进聊天（无弹窗）
     未开时：弹窗点「发送并开聊」
  → 返回列表 → 标记已投 → 下一个；本屏没有新的就翻页

防重复：
- 会话内 seen 集合记录处理过的标题
- BOSS 自身对已沟通职位显示「继续沟通」，脚本识别后跳过
- 滚动用 adb shell input（绕过 HarmonyOS 的 INJECT_EVENTS 限制）
"""
import re
import time
import xml.etree.ElementTree as ET

import config
from core import state as app_state
from core.actions import Robot, log

PACKAGE = config.PACKAGES["boss"]

SALARY_RE = re.compile(r"\d+K|元/[天日月]|·\d+薪|\d+-\d+元|^面议$")
UI_LABELS = {"推荐", "最新", "附近", "城市", "职位", "发现", "消息", "我的",
             "继续沟通", "更多筛选", "寻求其他机会"}


def _center(b):
    return (b["left"] + b["right"]) // 2, (b["top"] + b["bottom"]) // 2


def _nodes_with_bounds(root):
    out = []
    for n in root.iter("node"):
        t = (n.get("text") or "").strip()
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.get("bounds", ""))
        if t and m:
            x1, y1, x2, y2 = map(int, m.groups())
            out.append((t, x1, y1, x2, y2))
    return out


def _scan_cards(r: Robot, seen: set):
    """扫描可见职位卡：与薪资行垂直重叠、且位于其左侧的文本即职位标题"""
    xml = r.d.dump_hierarchy()
    nodes = _nodes_with_bounds(ET.fromstring(xml))
    titles = []
    for t, x1, y1, x2, y2 in nodes:
        if not SALARY_RE.search(t) or (y2 - y1) > 90:
            continue
        best, best_top = None, 10**9
        for t2, a1, b1, a2, b2 in nodes:
            if t2 == t or not (4 <= len(t2) <= 28) or t2 in UI_LABELS or t2 in seen:
                continue
            if SALARY_RE.search(t2):
                continue
            # 标题与薪资行垂直重叠，且起点在薪资左侧
            if b1 < y2 and b2 > y1 and a1 < x1:
                if b1 < best_top:
                    best, best_top = (t2, a1, b1, a2, b2), b1
        if best:
            titles.append((best[0], {"left": best[1], "top": best[2],
                                     "right": best[3], "bottom": best[4]}))
    titles.sort(key=lambda x: x[1]["top"])
    uniq, used = [], set()
    for t, b in titles:
        if t not in used:
            used.add(t)
            uniq.append((t, b))
    return uniq


def _scroll(r: Robot):
    """滚动翻页：按屏幕比例计算坐标，适配任意分辨率"""
    w, h = r.d.info["displayWidth"], r.d.info["displayHeight"]
    r.d.shell(f"input swipe {w // 2} {int(h * 0.77)} {w // 2} {int(h * 0.30)} 400")
    time.sleep(2.5)


def greet_current(r: Robot) -> str:
    """在已进入的详情页打招呼。返回 ok / skip / fail"""
    d = r.d
    w, h = d.info["displayWidth"], d.info["displayHeight"]
    d.click(w // 2, int(h * 0.51))   # 关可能的新手引导浮层
    time.sleep(1)

    btn = r.find(text="立即沟通", timeout=4)
    if btn is None:
        if r.find(text="继续沟通", timeout=2):
            return "skip"
        log.warning("详情页没有沟通按钮")
        r.screenshot("boss_no_btn")
        return "fail"
    r.tap(btn, "立即沟通")
    time.sleep(3)

    if not r.dry_run:
        # 情况 A：开了自动打招呼语 → 直接进聊天（话术已发出）
        if (r.find(text_contains="由你发起的沟通", timeout=2)
                or d(className="android.widget.EditText").count):
            log.info("打招呼语已自动发送，进入聊天窗")
            r.press_back()
            r.press_back()
            return "ok"
        # 情况 B：弹窗确认
        send = r.find(text="发送并开聊", timeout=3)
        if send:
            r.tap(send, "发送并开聊")
            time.sleep(2)
            r.press_back()
            r.press_back()
            return "ok"
        log.warning("既没进聊天也没弹确认框")
        r.screenshot("boss_unknown_state")
    else:
        log.info("[DRY] 到达沟通入口，停止")
    r.press_back()
    return "dry" if r.dry_run else "fail"


LIST_ACTIVITIES = ("GeekSearchActivity", "MainActivity")


def _ensure_list(r: Robot):
    """兜底：不管当前停在详情/聊天/公司页，按返回直到回到职位列表"""
    for _ in range(4):
        act = r.d.app_current()["activity"]
        if any(a in act for a in LIST_ACTIVITIES):
            return
        log.info(f"不在列表页({act.split('.')[-1]})，按返回")
        r.d.press("back")
        time.sleep(1.5)


def run(r: Robot, keywords: list = None, max_greet: int = None, fresh: bool = False):
    """按当前页面列表顺序批量打招呼（keywords 参数保留但不再用于筛选）

    断点续跑：默认加载 logs/state.json 里的 seen/greeted 跳过已处理职位；
    正常跑完一轮自动清理状态；--fresh 忽略历史从头扫。
    """
    max_greet = max_greet or config.BOSS["max_greetings"]
    # 每条投递约 3 步（点击沟通 + 2 次返回）+ 启动/兜底余量
    r.op_budget = max_greet * 4 + 30
    r.start_app(PACKAGE)
    time.sleep(3)
    _ensure_list(r)   # 可能上次停在公司页/详情页，先回到列表

    state = {"seen": set(), "greeted": set(), "updated_at": time.time()}
    if not fresh:
        state = app_state.load()
        if state["seen"]:
            log.info(f"断点续跑：跳过历史已处理职位 {len(state['seen'])} 个")
    seen = state["seen"]
    done, empty = 0, 0
    while done < max_greet and empty < 3:
        cards = _scan_cards(r, seen)
        if not cards:
            empty += 1
            log.info(f"本屏没有未处理的职位，翻页 ({empty}/3)")
            _scroll(r)
            continue
        empty = 0

        title, b = cards[0]
        seen.add(title)
        log.info(f">>> 候选职位: {title} ({done}/{max_greet})")
        r.d.click(*_center(b))
        time.sleep(3.5)

        result = greet_current(r)
        if result == "ok":
            done += 1
            log.info(f"[OK] 已打招呼 ({done}/{max_greet}): {title}")
            app_state.record(state, title, greeted=True)
            r.human_delay()
        elif result == "skip":
            log.info(f"跳过已沟通: {title}")
            app_state.record(state, title, greeted=False)
        elif result in ("fail", "dry"):
            app_state.record(state, title, greeted=False)
            r.press_back()
            time.sleep(1.5)
        _ensure_list(r)

    log.info(f"本轮结束：成功打招呼 {done} 次（处理职位 {len(seen)} 个）")
    if done >= max_greet or (not r.dry_run and empty >= 3):
        app_state.clear()
    return done

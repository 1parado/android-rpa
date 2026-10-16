# -*- coding: utf-8 -*-
"""选题雷达：B 站三源采集 + LLM 选题日报（全程只读，PRD 见 docs/PRD_选题雷达.md）

流程：
  启动 App → 弹窗清理 → F1 热榜（搜索页）
           → F2 推荐流（预热后翻屏采集，跨日 seen 去重）
           → F3 分区排行（P1，找不到跳过）
           → F5 全部落盘 data/bili_radar/ → F4 LLM 日报（无 key 降级跳过）

红线：不点击视频卡片、不点赞、不关注；tap 白名单仅限搜索框/tab/弹窗关闭。
"""
import csv
import hashlib
import json
import os
import re
import time
from datetime import datetime

import config
from core.actions import Robot, log

PACKAGE = config.PACKAGES["bili"]
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "bili_radar")
SEEN_FILE = os.path.join(DATA_DIR, "seen_titles.json")
SEEN_TTL = 7 * 24 * 3600

# 集中选择器（App 改版只改这里）
SELECTORS = {
    # 首页搜索框：占位文案是动态推荐词，只能靠 desc（「搜索」）兜底
    "search_box": ["搜索"],
    "hot_section": ["bilibili热搜", "b站热搜", "B站热搜", "热搜"],
    "feed_tab": ["推荐"],
    "hot_tab": ["热门"],
    "popup_close": ["跳过", "我知道了", "稍后再说", "关闭", "取消", "以后再说"],
    "login_hint": ["登录", "验证", "扫码登录"],
}
# 推荐流卡片噪声：这些短文本不可能是视频标题
UI_LABELS = {"推荐", "热门", "直播", "关注", "动态", "消息", "我的", "首页",
             "番剧", "影视", "大会员", "游戏", "会员购", "全部", "排行榜"}
PLAY_RE = re.compile(r"([\d.]+\s*[万亿]?)\s*(?:播放|观看)")

TODAY = datetime.now().strftime("%Y%m%d")

# 会话内共享的 seen 去重库（run() 加载，采集函数增量更新）
_SEEN: dict = {}


# ---------- F5 数据留存 ----------
def _save_csv(name: str, rows: list, fields: list):
    """UTF-8 带 BOM 落盘（Excel 直开不乱码）"""
    if not rows:
        return None
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{name}_{TODAY}.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    log.info(f"落盘 {path}（{len(rows)} 行）")
    return path


def _load_seen() -> dict:
    """seen 去重库：{title_hash: {"title":..., "count":N, "ts":...}}，TTL 7 天"""
    try:
        data = json.loads(open(SEEN_FILE, encoding="utf-8").read())
        now = time.time()
        fresh = {k: v for k, v in data.items()
                 if now - v.get("ts", 0) < SEEN_TTL}
        if len(fresh) < len(data):
            log.info(f"seen 库过期清理 {len(data) - len(fresh)} 条")
        return fresh
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"seen 库读取失败，重建: {e}")
        return {}


def _save_seen(seen: dict):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(seen, f, ensure_ascii=False, indent=1)
    except OSError as e:
        log.warning(f"seen 库写入失败: {e}")


def _seen_update(seen: dict, title: str) -> bool:
    """记录标题，返回是否为首次见到（跨日去重 + 升温统计基础）"""
    h = hashlib.md5(title.encode("utf-8")).hexdigest()[:16]
    first = h not in seen
    entry = seen.setdefault(h, {"title": title, "count": 0, "ts": time.time()})
    entry["count"] += 1
    entry["ts"] = time.time()
    return first


# ---------- 通用 ----------
def _clear_popups(r: Robot):
    """弹窗清理：按文本白名单关闭，最多 3 轮"""
    for _ in range(3):
        hit = None
        for t, bounds in r.dump_texts():
            if t in SELECTORS["popup_close"]:
                hit = (t, bounds)
                break
        if not hit:
            return
        b = hit[1]
        x = (b["left"] + b["right"]) // 2
        y = (b["top"] + b["bottom"]) // 2
        log.info(f"清理弹窗: {hit[0]}")
        r.d.click(x, y)
        time.sleep(1.5)


def _check_login(r: Robot) -> bool:
    """检测未登录/验证页：命中即终止，提示人工登录"""
    texts = {t for t, _ in r.dump_texts()}
    for hint in SELECTORS["login_hint"]:
        if hint in texts and any("登录" in t for t in texts):
            log.error("检测到登录/验证页面，请人工在手机上登录 B 站后重跑")
            r.screenshot("bili_need_login")
            return True
    return False


def _find_by_texts(r: Robot, candidates: list, timeout: float = 1.0):
    """精确文本 → 包含文本 → desc，多级查找，返回控件或 None"""
    for c in candidates:
        el = r.find(text=c, timeout=timeout)
        if el:
            return el
    for c in candidates:
        el = r.find(text_contains=c, timeout=timeout)
        if el:
            return el
    for c in candidates:
        el = r.find(desc=c, timeout=timeout)
        if el:
            return el
    for c in candidates:
        el = r.find(desc_contains=c, timeout=timeout)
        if el:
            return el
    return None


# ---------- F1 热榜 ----------
def collect_hot_search(r: Robot) -> list:
    """搜索页 B 站热搜榜。返回 [{rank, title, heat}]，失败返回 []"""
    box = _find_by_texts(r, SELECTORS["search_box"])
    if not box:
        log.warning("找不到搜索框，跳过热榜采集")
        r.screenshot("bili_no_search")
        return []
    r.tap(box, "搜索框")
    time.sleep(2.5)
    # 注意：不能用 press_back 收键盘——实测会把整个搜索页退回首页。
    # 热搜列表在键盘上方（y<1500），无需收键盘直接 dump 即可。

    texts = r.dump_texts()
    hot_idx = -1
    for i, (t, _) in enumerate(texts):
        if any(h in t for h in SELECTORS["hot_section"]):
            hot_idx = i
            break
    if hot_idx < 0:
        log.warning("搜索页未见热搜区域，跳过热榜采集")
        r.screenshot("bili_no_hotsearch")
        if not r.dry_run:
            r.press_back()
        return []

    # 布局：双列标题、无排名无热度值；「完整榜单」与区头相邻必须跳过，
    # section 以「搜索发现」结束
    rows, seen_local = [], set()
    for t, _ in texts[hot_idx + 1:]:
        if t == "搜索发现":
            break
        if t == "完整榜单" or t in UI_LABELS or t in seen_local or len(t) < 2:
            continue
        seen_local.add(t)
        rows.append({"rank": len(rows) + 1, "title": t, "heat": "",
                     "collected_at": datetime.now().isoformat(timespec="seconds")})
        if len(rows) >= 50:
            break
    if not r.dry_run:
        r.press_back()   # 收键盘（若在）再退回首页
        r.press_back()
    log.info(f"热榜采集 {len(rows)} 条")
    return rows


# ---------- F2 推荐流 ----------
def collect_feed(r: Robot, screens: int = None) -> list:
    """首页推荐流翻屏采集。返回 [{title, uploader, play_count}]，会话内去重"""
    screens = screens or config.BILI["feed_screens"]
    tab = _find_by_texts(r, SELECTORS["feed_tab"])
    if tab:
        r.tap(tab, "推荐 tab")

    warm = config.BILI["warmup_seconds"]
    if warm > 0:
        log.info(f"预热停留 {warm}s（模拟真人浏览）...")
        end = time.time() + warm
        while time.time() < end:
            if r.op_count > r.op_budget:
                break
            time.sleep(min(2, max(0, end - time.time())))

    rows, seen_local = [], set()
    w = r.d.info["displayWidth"]
    h = r.d.info["displayHeight"]
    for s in range(screens):
        if r.op_count > r.op_budget:
            log.warning("操作预算耗尽，提前结束推荐流采集")
            break
        nodes = r.dump_texts()
        for t, b in nodes:
            if t in seen_local or t in UI_LABELS:
                continue
            # 标题：足够长、不全是数字/符号、位于内容区（避开状态栏/导航栏）
            if len(t) < 6 or len(t) > 60:
                continue
            if b["top"] < 120 or b["bottom"] > h - 180:
                continue
            if re.fullmatch(r"[\d\s.:/%万千亿+-]+", t):
                continue
            if PLAY_RE.search(t) or re.search(r"\d+(/\d+)?\s*(万)?弹幕", t):
                continue   # 「xx万播放 xx弹幕」元信息行，不是标题
            seen_local.add(t)
            first = _seen_update(_SEEN, t)
            rows.append({"title": t,
                         "uploader": "",          # MVP: 元信息常与标题合并，LLM 侧容错
                         "play_count": "",
                         "collected_at": datetime.now().isoformat(timespec="seconds"),
                         "seen_count": _SEEN[hashlib.md5(t.encode("utf-8")).hexdigest()[:16]]["count"]})
            if first:
                log.debug(f"新标题: {t[:30]}")
        log.info(f"推荐流第{s+1}/{screens}屏，累计 {len(rows)} 条")
        if s < screens - 1:
            r.swipe_direction("up", scale=0.85, label="推荐流翻屏")
            time.sleep(config.BILI["feed_swipe_min"])
    log.info(f"推荐流采集 {len(rows)} 条（会话内去重）")
    return rows


# ---------- F3 分区排行（P1） ----------
def collect_ranking(r: Robot) -> list:
    """热门 tab → 排行榜入口。入口命中才出数，找不到跳过"""
    tab = _find_by_texts(r, SELECTORS["hot_tab"])
    if not tab:
        log.info("找不到「热门」tab，分区排行跳过（P1 降级）")
        return []
    r.tap(tab, "热门 tab")
    rank_entry = _find_by_texts(r, ["排行榜"])
    if not rank_entry:
        log.info("热门页未见排行榜入口，分区排行跳过")
        if not r.dry_run:
            r.press_back()
        return []
    r.tap(rank_entry, "排行榜入口")
    time.sleep(3)

    rows = []
    nodes = r.dump_texts()
    # 排行榜是单一榜单：直接按 y 顺序取标题类文本
    current_rank = 0
    for t, b in sorted(nodes, key=lambda x: x[1]["top"]):
        if len(t) < 6 or len(t) > 80 or b["top"] < 200:
            continue
        if PLAY_RE.search(t) or re.fullmatch(r"[\d\s.:/%万千亿+-]+", t):
            continue
        current_rank += 1
        rows.append({"region": "", "rank": current_rank, "title": t,
                     "play_count": "",
                     "collected_at": datetime.now().isoformat(timespec="seconds")})
        if current_rank >= 20:
            break
    if not r.dry_run:
        r.press_back()
    log.info(f"排行采集 {len(rows)} 条")
    return rows


# ---------- F4 LLM 日报 ----------
def generate_report(r: Robot, feed_rows: list, hot_rows: list, ranking_rows: list) -> str:
    """汇总近 3 天数据出日报。LLM key 缺失时降级：仅返回提示，不出报告"""
    from datetime import timedelta
    import urllib.request

    llm = config.LLM
    if not llm.get("api_key"):
        log.warning("未配置 LLM api_key（环境变量 ANDROID_RPA_LLM_KEY），"
                    "跳过日报生成，采集数据已照常落盘")
        return ""

    # 汇总近 3 天历史 CSV（升温对比需要频次趋势）
    history = []
    for i in range(2, -1, -1):
        day = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
        for name in ("feed", "hot_search", "ranking"):
            p = os.path.join(DATA_DIR, f"{name}_{day}.csv")
            if os.path.isfile(p):
                try:
                    with open(p, encoding="utf-8-sig") as f:
                        history.append(f"# {name} {day}\n" + f.read())
                except OSError:
                    pass

    # seen 库中的升温线索（3 天内出现 ≥2 次的标题）
    rising = [{"title": v["title"], "count": v["count"]}
              for v in _load_seen().values() if v.get("count", 0) >= 2]

    prompt = (
        "你是B站内容选题分析师。以下是近3天采集的数据（热榜/推荐流/排行榜）和重复出现标题。\n"
        "请输出 Markdown 日报，包含三部分：\n"
        "1. 交叉话题：热榜与推荐流都出现的题材（强信号）\n"
        "2. 升温类型：3天内频次上升的内容方向\n"
        "3. 选题建议：3-5 条具体选题（含参考标题结构与切入角度）\n\n"
        f"重复出现标题（升温线索）: {json.dumps(rising, ensure_ascii=False)}\n\n"
        f"历史数据:\n{chr(10).join(history)[:20000]}"
    )
    body = json.dumps({
        "model": llm["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.6,
    }).encode("utf-8")
    req = urllib.request.Request(
        llm["base_url"].rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {llm['api_key']}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            text = json.loads(resp.read())["choices"][0]["message"]["content"]
    except Exception as e:
        log.error(f"LLM 请求失败: {e}")
        return ""

    os.makedirs(os.path.join(DATA_DIR, "reports"), exist_ok=True)
    path = os.path.join(DATA_DIR, "reports", f"topic_radar_{TODAY}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    log.info(f"日报已生成: {path}")
    return path


# ---------- 主入口 ----------
def run(r: Robot, screens: int = None):
    """选题雷达主流程：三源采集 → 落盘 → LLM 日报"""
    r.op_budget = config.BILI["max_ops"]
    r.start_app(PACKAGE)
    time.sleep(3)
    _clear_popups(r)
    if _check_login(r):
        return

    global _SEEN
    _SEEN = _load_seen()

    hot_rows = collect_hot_search(r)
    feed_rows = collect_feed(r, screens)
    ranking_rows = collect_ranking(r) if not r.dry_run else []
    _save_csv("hot_search", hot_rows,
              ["rank", "title", "heat", "collected_at"])
    _save_csv("feed", feed_rows,
              ["title", "uploader", "play_count", "collected_at", "seen_count"])
    _save_csv("ranking", ranking_rows,
              ["region", "rank", "title", "play_count", "collected_at"])
    _save_seen(_SEEN)
    log.info(f"seen 库 {len(_SEEN)} 条已保存")

    report = generate_report(r, feed_rows, hot_rows, ranking_rows)
    log.info(f"选题雷达完成：热榜 {len(hot_rows)} / 推荐流 {len(feed_rows)} / "
             f"排行 {len(ranking_rows)}，日报 {'✓ ' + report if report else '跳过'}")

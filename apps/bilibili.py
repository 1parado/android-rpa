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


# ---------- UP 主观测（--task bili_up） ----------
# 深链拉起任意 UP 主页：bilibili://space/{mid}，无需搜索导航
NUM_RE = re.compile(r"([\d.]+\s*[万亿]?)")
# 列表页每条视频的元信息行，如「12.3万 2452 3天前」或分行：「12.3万」「2452」「3天前」
META_TIME_RE = re.compile(r"^(\d+)\s*(秒|分钟|小时|天|周|个月|月|年)前$")


def _parse_num(s: str) -> str:
    """「12.3万」→「12.3万」原样留空格规整；非数字返回空"""
    m = NUM_RE.fullmatch(s.replace(" ", ""))
    return m.group(1) if m else ""


def _goto_up(r: Robot, mid: int, note: str):
    """深链打开 UP 主页，返回 True/False"""
    log.info(f"打开 UP 主页: {note} (mid={mid})")
    r.check_budget()
    # 深链用 am start（该版 uiautomator2 的 app_start 不支持 data）
    r.d.shell(f'am start -a android.intent.action.VIEW '
              f'-d "bilibili://space/{mid}" {PACKAGE}')
    time.sleep(5)
    _clear_popups(r)
    # 简单校验：页面应有「投稿」或「关注」类按钮文本
    texts = {t for t, _ in r.dump_texts()}
    if not any(("投稿" in t or "关注" in t or "粉丝" in t) for t in texts):
        log.warning(f"UP 主页可能未加载成功: {note}")
        r.screenshot("bili_up_fail")
        return False
    return True


def _back_to_list(r: Robot, times: int = 2):
    """从视频详情页退回列表页"""
    for _ in range(times):
        r.press_back()
        time.sleep(1.5)


def _load_up_csv(note: str) -> list:
    """读取今天已有的 up_{note}_{今天}.csv（增量采集用），无则返回 []"""
    day = datetime.now().strftime("%Y%m%d")
    path = os.path.join(DATA_DIR, f"up_{note}_{day}.csv")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def collect_up_videos(r: Robot, mid: int, note: str, detail: int = 0,
                      screens: int = None) -> dict:
    """采集 UP 主最近视频：列表页（标题/播放/发布时间）+ 可选详情页（点赞/投币/收藏）。
    返回 {up, mid, rows: [...]}"""
    if not _goto_up(r, mid, note):
        return {"up": note, "mid": mid, "rows": []}

    # 切到「投稿/视频」tab（有些 UP 默认落在动态页）
    for cand in ("投稿", "视频"):
        el = _find_by_texts(r, [cand])
        if el:
            r.tap(el, f"{note} 的 {cand} tab")
            break

    # 翻屏收集列表项。投稿 tab 的卡片布局（实测 dump）：
    #   标题行 x≈450 宽到 1044，高 ~112；其上方一行：时长(x≈318) 日期(x≈450) 播放(x≈450) 弹幕(x≈681)
    seen_local, raw = set(), []
    screens = screens if screens is not None else config.BILI.get("up_screens", 3)
    # 日期文本实测有：9月29日 / 9-29 / 昨天 17:00 / 今天 / 3天前 / 5小时前
    DATE_RE = re.compile(
        r"^\d{1,2}月\d{1,2}日$|^\d{1,2}-\d{1,2}$|^\d{4}-\d{1,2}-\d{1,2}"
        r"|^昨天|^今天|^\d+天前$|^\d+小时前$|^\d+分钟前$|^刚刚$")
    for s in range(screens):
        if r.op_count > r.op_budget:
            log.warning("操作预算耗尽，提前结束 UP 采集")
            break
        nodes = r.dump_texts()
        # 投稿列表是自绘控件，dump 偶尔拿不到文本节点；轻滚 1px 触发重渲染后重试
        retry = 0
        while not any(t for t, _ in nodes) or len(nodes) < 15:
            if retry >= 2:
                break
            retry += 1
            log.info(f"列表 dump 为空，轻滚重试 {retry}/2")
            r.d.swipe(540, 1500, 540, 1498)
            time.sleep(1.5)
            nodes = r.dump_texts()
        h = r.d.info["displayHeight"]
        # 标题判定：x 起点在 430~480、文本足够长、位于列表区
        # 标题按页面顺序处理；元信息节点「用过即划走」，避免相邻卡片串行（错位根因）
        titles = sorted(
            [(t, b) for t, b in nodes
             if 430 <= b["left"] <= 480 and 6 <= len(t) <= 60
             and b["top"] >= 430 and b["bottom"] < h - 160
             and not re.fullmatch(r"[\d\s.:/%万千亿+-]+", t)
             and not DATE_RE.match(t)],   # 「昨天 17:00」等日期文本 x 也在标题列
            key=lambda x: x[1]["top"])
        used = set()
        for t, tb in titles:
            if t in seen_local:
                continue
            seen_local.add(t)
            # 元信息行：标题下方到下一张卡片之间（实测：标题 bottom≈2055，
            # 其下方依次是 日期 top2061 → 播放/弹幕 top2111；下一条标题 top≈2380）
            nxt = next((b2["top"] for t2, b2 in titles if b2["top"] > tb["top"]), h)
            pub, play, danmaku = "", "", ""
            for j, (t2, b2) in enumerate(nodes):
                if id(b2) in used or b2 is tb:
                    continue
                if not (tb["bottom"] - 8 <= b2["top"] < min(nxt, tb["bottom"] + 240)):
                    continue
                if DATE_RE.match(t2):
                    pub = t2
                    used.add(id(b2))
                elif 430 <= b2["left"] <= 480 and _parse_num(t2) and not play:
                    play = _parse_num(t2)
                    used.add(id(b2))
                elif b2["left"] > 640 and _parse_num(t2) and not danmaku:
                    danmaku = _parse_num(t2)
                    used.add(id(b2))
            raw.append({"title": t, "play": play, "danmaku": danmaku,
                        "pub": pub,
                        "collected_at": datetime.now().isoformat(timespec="seconds")})
        log.info(f"UP「{note}」列表第{s+1}/{screens}屏，累计 {len(raw)} 条")
        if s < screens - 1:
            r.swipe_direction("up", scale=0.8, label="UP 视频列表翻屏")
            time.sleep(2.5)

    # 排序：按页面 y 顺序即发布顺序（新的在上）
    rows = raw[:40]
    _first = True
    for i, row in enumerate(rows):
        row["seq"] = i + 1

    # 增量：读取当天已有数据，旧条目直接沿用（详情字段也带过来），只补采缺失的
    prev = {row["title"]: row for row in _load_up_csv(note)}
    for row in rows:
        old = prev.get(row["title"])
        if old:
            for k in ("play", "danmaku", "pub", "like", "coin", "fav", "comment", "bvid"):
                if old.get(k) and not row.get(k):
                    row[k] = old[k]

    # 点进全部视频采详情（BV/点赞/投币/收藏/评论数 + 热评）——增量跳过已有数据的
    detail = detail if detail is not None else len(rows)
    n_comments = max(1, config.BILI.get("up_comments", 40))
    if not r.dry_run and rows:
        need = [row for row in rows[:detail]
                if not row.get("like") or not row.get("bvid")
                or (n_comments > 0 and not row.get("top_comments"))]
        if need:
            log.info(f"详情需补采 {len(need)}/{min(detail, len(rows))} 条（其余已有数据，跳过）")
            if _back_to_space(r, mid, note):
                _collect_details(r, need, comments=n_comments)
        else:
            log.info("详情数据全部已有，跳过详情页采集")

    _save_csv(f"up_{note}", rows,
              ["seq", "title", "play", "danmaku", "pub", "bvid",
               "like", "coin", "fav", "comment", "top_comments",
               "collected_at"])
    # 热评明细单独存一份 JSON（CSV 单元格放不下）：data/bili_radar/up_comments_{note}_{day}.json
    with_c = [x for x in rows if x.get("top_comments")]
    if with_c:
        cpath = os.path.join(DATA_DIR, f"up_comments_{note}_{datetime.now().strftime('%Y%m%d')}.json")
        try:
            old = json.load(open(cpath, encoding="utf-8")) if os.path.isfile(cpath) else {}
        except (OSError, json.JSONDecodeError):
            old = {}
        old.update({x["bvid"] or x["title"]: {"title": x["title"], "bvid": x.get("bvid", ""),
                                              "comments": x["top_comments"]} for x in with_c})
        json.dump(old, open(cpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        log.info(f"热评明细已写入 {os.path.basename(cpath)}（{len(old)} 个视频）")
    log.info(f"UP「{note}」采集 {len(rows)} 条")
    return {"up": note, "mid": mid, "rows": rows}


def _scroll_top(r: Robot, steps: int = 5):
    """列表滚回顶部：从列表内容区（避开顶部横幅/收藏入口）半屏上滑"""
    for _ in range(steps):
        if "authorspace" not in r.d.app_current()["activity"].lower():
            log.warning("已离开 UP 主页，停止回滚")
            return
        r.d.swipe(540, 1200, 540, 1900, duration=0.3)   # 半屏上滑，起点避开卡片点击
        time.sleep(1)
    time.sleep(1.5)


def _back_to_space(r: Robot, mid: int, note: str):
    """确保回到 UP 主页（误入子页面时返回；已不在 App 内则重走深链）"""
    for _ in range(4):
        act = r.d.app_current()["activity"].lower()
        if "authorspace" in act:
            _scroll_top(r)
            return True
        if "bilibili" not in act:
            return _goto_up(r, mid, note)
        r.d.press("back")
        time.sleep(1.5)
    return "authorspace" in r.d.app_current()["activity"].lower()


def _collect_details(r: Robot, rows: list, comments: int = 0):
    """点进视频详情页补采 BV号/点赞/投币/收藏/评论数（+可选评论内容）。
    底栏操作区是图标按钮、数字在图标下方且无文字标签（实测 dump）：
    同一 y 行（±8px）内按 x 排序依次为 赞 / [不喜欢=文本] / 币 / 藏 / 分享。
    BV 号在「展开更多」展开后的简介文本里（实测 BV1t6ex66EKs）。
    评论数在顶部「评论」tab 标签右侧（实测 top≈782, left≈426）。"""
    log.info(f"进入详情页补采 {len(rows)} 条（可见卡片采完后自动下滑列表继续找）...")
    pending = list(rows)
    stale = 0          # 连续整屏下滑都没找到新卡片的次数
    while pending and stale < 3:
        row = pending[0]
        el = _find_by_texts(r, [row["title"][:15]])
        if not el:
            # 当前屏找不到 → 下滑一屏再找；滚到底反复找不到则放弃
            log.info(f"当前屏未找到「{row['title'][:20]}」，下滑列表继续")
            act = r.d.app_current()["activity"].lower()
            if "authorspace" not in act:
                log.warning("已离开 UP 主页，终止详情补采")
                break
            r.d.swipe(540, 1700, 540, 800, duration=0.4)
            time.sleep(2)
            stale += 1
            # 下滑后再试，若找到会清零 stale
            el = _find_by_texts(r, [row["title"][:15]])
            if not el:
                continue
        stale = 0
        pending.pop(0)
        r.tap(el, f"视频: {row['title'][:20]}")
        time.sleep(4)
        nodes = r.dump_texts()
        # 评论数：「评论」标签右侧紧邻的数字
        for i, (t, b) in enumerate(nodes):
            if t == "评论":
                cand = [(_parse_num(t2), b2)
                        for t2, b2 in nodes[i + 1:i + 5]
                        if _parse_num(t2)
                        and abs(b2["top"] - b["top"]) <= 30 and b2["left"] > b["left"]]
                if cand:
                    row["comment"] = cand[0][0]
                break
        # 找操作区数字行：同一 y 上有 ≥3 个数字节点（赞/币/藏/分享）
        by_row = {}
        for t, b in nodes:
            if _parse_num(t) and b["top"] > 600:   # 排除标题区/播放数区
                key = b["top"] // 8
                by_row.setdefault(key, []).append((b["left"], _parse_num(t)))
        best = max(by_row.values(), key=len, default=[])
        best.sort()
        nums = [v for _, v in best]
        if len(nums) >= 4:
            row["like"], row["coin"], row["fav"] = nums[0], nums[-3], nums[-2]
        elif len(nums) == 3:
            row["like"], row["coin"], row["fav"] = nums
        # BV 号：简介区「展开更多」→ 展开后的文本里含 BVxxxxxxxxxx
        if r.d(descriptionContains="展开更多").click_exists():
            time.sleep(1.5)
            nodes = r.dump_texts()
        for t, b in nodes:
            m = re.search(r"BV[0-9A-Za-z]{10}", t)
            if m:
                row["bvid"] = m.group(0)
                break
        log.info(f"详情 {row['seq']}: BV={row.get('bvid','?')} 赞={row.get('like','?')} "
                 f"币={row.get('coin','?')} 藏={row.get('fav','?')} 评={row.get('comment','?')}")
        # 可选：采热评（B站默认按热度排，前 N 条即高价值评论）
        if comments > 0 and not r.dry_run:
            _collect_comments(r, row, comments)
        _back_to_list(r, times=1)


def _collect_comments(r: Robot, row: dict, limit: int):
    """采集当前视频的热门评论。评论流结构（实测 dump）：
    评论正文 → 点赞数 / 用户名「9月16日 IP属地：X」/「xxx: 回复」/「共N条回复」。
    只采顶层热评不展开回复，数量用 limit 封顶（热评排序下前 N 条即最有价值）。"""
    if not r.d(text="评论").click_exists():
        return
    time.sleep(2)
    r.d.swipe(540, 1400, 540, 900, duration=0.3)   # 小滑进入评论流
    time.sleep(2)
    got, seen = [], set()
    max_screens = max(1, limit // 15)   # 每屏约 15 条，封顶滑动次数
    for _ in range(max_screens):
        for t, b in r.dump_texts():
            flat = t.replace("​", "").strip()   # 去零宽字符
            if t in seen or len(flat) < 4 or _parse_num(flat):
                continue
            if "IP属地" in t or "共" in t and "回复" in t or t in ("简介", "评论"):
                continue
            if ": " in t or "：回复" in t or "顶部" in t or "置顶" in t:
                continue
            if "回复" in t:
                continue
            # 过滤纯计数/时间串（含带换行的 "19.3\nK/s" 网速指示器）
            if re.fullmatch(r"[\d\s.:/%万千亿KkSs+-]+", flat):
                continue
            if len(t) >= 6:   # 评论正文一般较长（过滤时间/计数等短串）
                seen.add(t)
                got.append(t)
        if len(got) >= limit:
            break
        act = r.d.app_current()["activity"].lower()
        if "unitedbizdetails" not in act:   # 已离开详情页
            break
        r.d.swipe(540, 1700, 540, 800, duration=0.4)   # 翻下一屏评论
        time.sleep(2)
    row["top_comments"] = got[:limit]
    log.info(f"  评论 {row['seq']}: 采到 {len(got[:limit])} 条热评")


def run_up(r: Robot, up_names: list = None, detail: int = None,
           screens: int = None):
    """--task bili_up 入口：采集 config.BILI.up_list 中的 UP（或指定备注名子集）"""
    up_list = config.BILI.get("up_list") or {}
    if not up_list:
        log.error("config.BILI.up_list 为空，请先添加 {备注名: UID}")
        return
    r.op_budget = config.BILI["max_ops"] * max(1, len(up_list))
    names = up_names or list(up_list)
    results = []
    for note in names:
        mid = up_list.get(note)
        if not mid:
            log.warning(f"up_list 中找不到 {note}，跳过")
            continue
        results.append(collect_up_videos(r, mid, note, detail=detail,
                                         screens=screens))
    total = sum(len(x["rows"]) for x in results)
    log.info(f"UP 观测完成：{len(results)} 个 UP，共 {total} 条视频记录")

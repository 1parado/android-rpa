# -*- coding: utf-8 -*-
"""选题雷达可视化面板：本地起一个只读 HTTP 服务，浏览 data/bili_radar/ 采集数据

用法：
    python web/dashboard.py            # 默认 http://localhost:8765
    python web/dashboard.py --port 9000

只监听 127.0.0.1（不对外网开放），纯标准库无第三方依赖。
"""
import argparse
import csv
import json
import os
import re
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data", "bili_radar")

# CSV 文件名前缀 → 标题（顺带决定面板里的顺序）
SOURCES = [
    ("hot_search", "🔥 热搜榜"),
    ("ranking", "🏆 排行榜"),
    ("feed", "📺 推荐流"),
]

UP_RE = re.compile(r"^up_(.+?)_(\d{8})\.csv$")   # up_{备注名}_{日期}.csv

# 统计卡片要显示的图题数（每源 top N 高频标题）
TOP_N = 15


def list_days() -> list:
    """扫描数据目录里有哪些日期（从文件名提取，倒序）"""
    days = set()
    if os.path.isdir(DATA_DIR):
        for f in os.listdir(DATA_DIR):
            m = re.search(r"_(\d{8})\.csv$", f)
            if m:
                days.add(m.group(1))
    return sorted(days, reverse=True)


def read_csv(name: str, day: str) -> list:
    """读某天某源的 CSV，返回 list[dict]；文件不存在返回 []"""
    path = os.path.join(DATA_DIR, f"{name}_{day}.csv")
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_seen() -> dict:
    """seen 库：升温线索（count>=2 的标题）"""
    path = os.path.join(DATA_DIR, "seen_titles.json")
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def list_ups() -> list:
    """扫描有哪些 UP（从 up_{name}_{day}.csv 文件名提取，按名字排序）"""
    ups = set()
    if os.path.isdir(DATA_DIR):
        for f in os.listdir(DATA_DIR):
            m = UP_RE.match(f)
            if m:
                ups.add(m.group(1))
    return sorted(ups)


def build_up_data(name: str, day: str) -> dict:
    """聚合某个 UP 某天的观测数据"""
    days = [d for d in list_days()
            if os.path.isfile(os.path.join(DATA_DIR, f"up_{name}_{d}.csv"))]
    if day not in days:
        day = days[0] if days else ""
    rows = read_csv(f"up_{name}", day) if day else []

    def num(s):
        """'108.5万' → 1085000，用于排序/占比条"""
        s = (s or "").strip()
        m = re.fullmatch(r"([\d.]+)([万亿]?)", s)
        if not m:
            return 0
        v = float(m.group(1))
        return int(v * {"万": 1e4, "亿": 1e8}.get(m.group(2), 1))

    for i, row in enumerate(rows):
        row["_seq"] = i + 1
        row["_play_n"] = num(row.get("play"))
        row["_like_n"] = num(row.get("like"))

    return {
        "name": name,
        "ups": list_ups(),
        "day": day,
        "days": days,
        "rows": rows,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def build_data(day: str) -> dict:
    """聚合某天数据为前端需要的结构"""
    sources, rising = {}, []
    seen = load_seen()
    seen_total = len(seen)

    # 升温榜：出现次数 >=2 的标题，按 count 降序
    for v in seen.values():
        if v.get("count", 0) >= 2:
            rising.append({"title": v["title"], "count": v["count"]})
    rising.sort(key=lambda x: (-x["count"], x["title"]))

    for name, label in SOURCES:
        rows = read_csv(name, day)
        # 标题频次统计（同一标题多天累计走 seen；单日只看本文件）
        freq = {}
        for row in rows:
            t = row.get("title", "").strip()
            if t:
                freq[t] = freq.get(t, 0) + 1
        top = sorted(freq.items(), key=lambda x: -x[1])[:TOP_N]
        sources[name] = {
            "label": label,
            "rows": rows,
            "total": len(rows),
            "top": [{"title": t, "count": c} for t, c in top],
        }

    return {
        "day": day,
        "days": list_days(),
        "sources": sources,
        "rising": rising[:TOP_N],
        "seen_total": seen_total,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif parsed.path == "/up":
            self._send(200, UP_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif parsed.path == "/api/data":
            from urllib.parse import parse_qs
            day = (parse_qs(parsed.query).get("day") or [""])[0]
            if not re.fullmatch(r"\d{8}", day):
                days = list_days()
                day = days[0] if days else ""
            data = build_data(day)
            self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif parsed.path == "/api/up":
            from urllib.parse import parse_qs
            q = parse_qs(parsed.query)
            name = (q.get("name") or [""])[0]
            day = (q.get("day") or [""])[0]
            ups = list_ups()
            if name not in ups:
                name = ups[0] if ups else ""
            if not re.fullmatch(r"\d{8}", day or ""):
                day = ""
            data = build_up_data(name, day)
            self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def log_message(self, fmt, *args):
        pass   # 静默访问日志


def main():
    ap = argparse.ArgumentParser(description="选题雷达可视化面板")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    if not list_days():
        print(f"[!] {DATA_DIR} 下暂无采集数据，先跑 python main.py --task bili --live")
    url = f"http://{args.host}:{args.port}"
    print(f"[OK] 面板已启动: {url}  （Ctrl-C 停止）")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>选题雷达 · B站</title>
<style>
:root{
  --paper:#EFF1EE;      /* 冷灰纸：观测台底色 */
  --ink:#171B1E;        /* 墨青：正文 */
  --pink:#FB7299;       /* 信号粉：只用在信号本身 */
  --teal:#1F4E5F;       /* 深测蓝：刻度、次级结构 */
  --dim:#7A8288;        /* 灰显：标签 */
  --line:#D5D9D4;       /* 细线 */
  --mono:"Consolas","SF Mono","Cascadia Mono",monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{background:var(--paper);color:var(--ink);
  font:15px/1.7 "Segoe UI","Microsoft YaHei",sans-serif;
  padding:40px 28px 80px;max-width:1180px;margin:0 auto}
::selection{background:var(--pink);color:#fff}

/* ---------- 顶部：铭牌式抬头 ---------- */
header{display:flex;align-items:flex-end;justify-content:space-between;
  flex-wrap:wrap;gap:16px;border-bottom:2px solid var(--ink);padding-bottom:18px}
.brand .eyebrow{font-family:var(--mono);font-size:11px;letter-spacing:.35em;
  color:var(--teal);text-transform:uppercase}
.brand h1{font-size:30px;letter-spacing:.14em;font-weight:700;margin-top:2px}
.brand h1 b{color:var(--pink)}
.meta{font-family:var(--mono);font-size:12px;color:var(--dim);text-align:right;line-height:1.9}
.meta select{font-family:var(--mono);font-size:13px;color:var(--ink);
  background:transparent;border:1px solid var(--ink);padding:3px 10px;
  border-radius:0;cursor:pointer;margin-bottom:4px}
.meta select:focus-visible{outline:2px solid var(--pink);outline-offset:2px}

/* ---------- 雷达盘：签名元素 ---------- */
.radar-wrap{display:grid;grid-template-columns:minmax(300px,440px) 1fr;gap:40px;
  align-items:center;margin:44px 0 56px}
@media(max-width:860px){.radar-wrap{grid-template-columns:1fr}}
.radar{position:relative;width:100%;aspect-ratio:1;user-select:none}
.radar svg{width:100%;height:100%;display:block}
.radar .readout{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
  text-align:center;pointer-events:none;transition:opacity .25s}
.radar .readout .n{font-family:var(--mono);font-size:44px;font-weight:700;line-height:1}
.radar .readout .l{font-size:12px;color:var(--dim);letter-spacing:.25em;margin-top:4px}
.radar.hovering .readout{opacity:0}
.radar .tip{position:absolute;pointer-events:none;opacity:0;
  background:var(--ink);color:var(--paper);font-size:12px;line-height:1.5;
  padding:6px 10px;border-radius:4px;transform:translate(-50%,-115%);
  white-space:nowrap;max-width:280px;overflow:hidden;text-overflow:ellipsis;
  transition:opacity .15s;z-index:5}
.radar .tip.show{opacity:1}
.radar .tip .c{font-family:var(--mono);color:var(--pink)}

/* ---------- 分区：刻度编号（热搜榜是真实有序的榜单） ---------- */
section{margin-top:44px}
.sec-head{display:flex;align-items:baseline;gap:14px;margin-bottom:14px}
.sec-head .tick{font-family:var(--mono);font-size:13px;color:var(--teal);
  letter-spacing:.2em;white-space:nowrap}
.sec-head .tick::before{content:"—— "}
.sec-head h2{font-size:19px;letter-spacing:.08em}
.sec-head .sub{color:var(--dim);font-size:13px}

table{width:100%;border-collapse:collapse;font-size:14px}
th{font-family:var(--mono);font-size:11px;color:var(--dim);text-align:left;
  font-weight:400;letter-spacing:.15em;padding:6px 10px;border-bottom:1px solid var(--ink)}
td{padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
tbody tr{transition:background .15s}
tbody tr:hover{background:#E4E8E2}
.rank{font-family:var(--mono);color:var(--teal);width:44px;font-size:13px}
.rank.top3{color:var(--pink);font-weight:700}
.count{font-family:var(--mono);font-size:13px;color:var(--teal);white-space:nowrap}
.time{font-family:var(--mono);font-size:12px;color:var(--dim);white-space:nowrap}
.freqbar{height:4px;background:var(--line);border-radius:2px;margin-top:5px;
  width:110px;overflow:hidden}
.freqbar i{display:block;height:100%;background:var(--pink);border-radius:2px;
  transform-origin:left;animation:grow .7s cubic-bezier(.2,.8,.2,1) backwards}
@keyframes grow{from{transform:scaleX(0)}}
.empty{color:var(--dim);text-align:center;padding:32px 0;font-size:14px}
.empty code{font-family:var(--mono);font-size:13px;background:#E4E8E2;padding:2px 8px}
.jump{display:inline-block;margin-top:14px;font-family:var(--mono);font-size:13px;
  color:var(--teal);text-decoration:none;border:1px solid var(--teal);
  padding:7px 16px;letter-spacing:.15em;transition:all .18s}
.jump:hover,.jump:focus-visible{background:var(--teal);color:var(--paper);outline:none}

/* ---------- 页脚 ---------- */
footer{margin-top:64px;border-top:1px solid var(--line);padding-top:14px;
  font-family:var(--mono);font-size:12px;color:var(--dim);display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}

@media (prefers-reduced-motion: reduce){
  *{animation:none!important;transition:none!important}
  html{scroll-behavior:auto}
}
</style>
</head>
<body>
<header>
  <div class="brand">
    <div class="eyebrow">Topic Radar / Bilibili</div>
    <h1>选题<b>雷达</b></h1>
  </div>
  <div class="meta">
    <select id="day" onchange="load()" aria-label="选择日期"></select><br>
    <span id="meta"></span>
  </div>
</header>

<!-- 签名：升温雷达盘 -->
<div class="radar-wrap">
  <div class="radar" id="radar">
    <svg viewBox="0 0 400 400" aria-label="升温雷达图：标题出现次数越多，光点越靠外越大" role="img">
      <g id="grid"></g>
      <g id="sweep"></g>
      <g id="blips"></g>
    </svg>
    <div class="readout"><div class="n" id="r_n">0</div><div class="l">升温信号</div></div>
    <div class="tip" id="tip"></div>
  </div>
  <div>
    <div class="sec-head"><span class="tick">RISING</span><h2>升温榜</h2><span class="sub">跨日出现 ≥2 次的标题</span></div>
    <div id="rising"></div>
    <a class="jump" href="/up">◉ UP 主观测 →</a>
  </div>
</div>

<section>
  <div class="sec-head"><span class="tick">CH-01</span><h2>热搜榜</h2><span class="sub" id="hot_sub"></span></div>
  <div id="hot"></div>
</section>
<section>
  <div class="sec-head"><span class="tick">CH-02</span><h2>排行榜</h2><span class="sub" id="rank_sub"></span></div>
  <div id="rank"></div>
</section>
<section>
  <div class="sec-head"><span class="tick">CH-03</span><h2>推荐流</h2><span class="sub" id="feed_sub"></span></div>
  <div id="feed"></div>
</section>

<footer>
  <span>DATA // data/bili_radar</span>
  <span>LOCAL ONLY // 127.0.0.1</span>
</footer>

<script>
const esc = s => (s||'').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const NS = 'http://www.w3.org/2000/svg';
const CX=200, CY=200;
const polar = (r, deg) => {
  const a = (deg-90) * Math.PI/180;
  return [CX + r*Math.cos(a), CY + r*Math.sin(a)];
};

/* 雷达底盘：同心圆 + 十字刻度，画一次即可 */
function drawGrid(){
  const g = document.getElementById('grid');
  for (const r of [60, 110, 160, 196]) {
    const c = document.createElementNS(NS,'circle');
    c.setAttribute('cx',CX); c.setAttribute('cy',CY); c.setAttribute('r',r);
    c.setAttribute('fill','none');
    c.setAttribute('stroke', r===196 ? '#171B1E' : '#C9CEC8');
    c.setAttribute('stroke-width', r===196 ? 1.5 : 1);
    g.appendChild(c);
  }
  for (let deg=0; deg<360; deg+=30) {
    const [x1,y1] = polar(186,deg), [x2,y2] = polar(196,deg);
    const l = document.createElementNS(NS,'line');
    l.setAttribute('x1',x1); l.setAttribute('y1',y1);
    l.setAttribute('x2',x2); l.setAttribute('y2',y2);
    l.setAttribute('stroke','#171B1E'); l.setAttribute('stroke-width',1.5);
    g.appendChild(l);
  }
  const [cx1,cy1]=polar(196,0),[cx2,cy2]=polar(196,180),[cy3x,cy3y]=polar(196,90),[cx4,cy4]=polar(196,270);
  for (const [x1,y1,x2,y2] of [[cx1,cy1,cx2,cy2],[cy3x,cy3y,cx4,cy4]]) {
    const l = document.createElementNS(NS,'line');
    l.setAttribute('x1',x1); l.setAttribute('y1',y1);
    l.setAttribute('x2',x2); l.setAttribute('y2',y2);
    l.setAttribute('stroke','#D5D9D4'); l.setAttribute('stroke-width',1);
    g.appendChild(l);
  }
}

/* 扫描线：开盘扫一圈（reduced-motion 下 CSS 已禁用动画，静止为一条指向 12 点的线） */
function drawSweep(){
  const g = document.getElementById('sweep');
  const l = document.createElementNS(NS,'line');
  l.setAttribute('x1',CX); l.setAttribute('y1',CY);
  l.setAttribute('x2',CX); l.setAttribute('y2',4);
  l.setAttribute('stroke','#FB7299'); l.setAttribute('stroke-width',2);
  l.setAttribute('stroke-linecap','round');
  const style = document.createElementNS(NS,'style');
  style.textContent = `#sweep line{transform-origin:200px 200px;animation:sweep 2.4s cubic-bezier(.3,0,.2,1) 1 forwards}
@keyframes sweep{from{transform:rotate(0)}to{transform:rotate(360deg)}}`;
  g.appendChild(style); g.appendChild(l);
}

/* 光点：count 越大越靠外、越大；扫描线扫到才亮（错峰显现） */
function drawBlips(rising){
  const g = document.getElementById('blips');
  g.innerHTML = '';
  const max = rising[0] ? rising[0].count : 1;
  rising.forEach((x,i) => {
    const t = max===1 ? .5 : (x.count-1)/(max-1)*.8 + .2;   // 0.2~1.0 半径比例
    const deg = (i * 137.508) % 360;                         // 黄金角均匀散布
    const [x1,y1] = polar(30 + t*160, deg);
    const dot = document.createElementNS(NS,'circle');
    dot.setAttribute('cx',x1); dot.setAttribute('cy',y1);
    dot.setAttribute('r', 4 + t*7);
    dot.setAttribute('fill','#FB7299');
    dot.setAttribute('opacity','0');
    dot.setAttribute('style', `animation:blip .4s ease-out ${0.5 + i*0.22}s forwards;cursor:pointer`);
    dot.addEventListener('mouseenter', e => showTip(dot, x));
    dot.addEventListener('mouseleave', hideTip);
    g.appendChild(dot);
  });
  if (!document.getElementById('blipKf')) {
    const st = document.createElementNS(NS,'style'); st.id='blipKf';
    st.textContent = '@keyframes blip{from{opacity:0;r:0}to{opacity:.9}}';
    document.getElementById('blips').appendChild(st);
  }
}
function showTip(dot, x){
  document.getElementById('radar').classList.add('hovering');
  const tip = document.getElementById('tip');
  const rb = document.getElementById('radar').getBoundingClientRect();
  const db = dot.getBoundingClientRect();
  tip.style.left = (db.left + db.width/2 - rb.left) + 'px';
  tip.style.top = (db.top - rb.top) + 'px';
  tip.innerHTML = `${esc(x.title)}<br><span class="c">× ${x.count} 次</span>`;
  tip.classList.add('show');
}
function hideTip(){
  document.getElementById('radar').classList.remove('hovering');
  document.getElementById('tip').classList.remove('show');
}

function tbl(rows, cols){
  if (!rows.length) return '<div class="empty">当天无数据</div>';
  return `<table><thead><tr>${cols.map(c=>`<th>${c.h}</th>`).join('')}</tr></thead><tbody>` +
    rows.map((x,i)=>`<tr>${cols.map(c=>`<td class="${c.k||''}">${c.f(x,i)}</td>`).join('')}</tr>`).join('') +
    '</tbody></table>';
}

async function load(){
  const day = document.getElementById('day').value || '';
  const d = await (await fetch('/api/data?day='+day)).json();
  const sel = document.getElementById('day');
  if (sel.options.length !== d.days.length)
    sel.innerHTML = d.days.map(x=>`<option ${x===d.day?'selected':''}>${x}</option>`).join('');
  document.getElementById('meta').textContent =
    `${d.generated_at} · SEEN ${d.seen_total}`;

  /* 雷达 + 升温榜 */
  drawBlips(d.rising);
  document.getElementById('r_n').textContent = d.rising.length;
  const maxC = d.rising[0] ? d.rising[0].count : 1;
  document.getElementById('rising').innerHTML = d.rising.length
    ? tbl(d.rising, [
        {h:'TITLE', f:x=>esc(x.title)},
        {h:'FREQ', k:'count', f:x=>`${x.count} 次<div class="freqbar"><i style="width:${x.count/maxC*100}%"></i></div>`}
      ])
    : '<div class="empty">还没有重复出现的标题。<br>连续几天运行 <code>main.py --task bili --live</code> 后这里会出现升温信号。</div>';

  /* 三个频道 */
  const S = d.sources;
  document.getElementById('hot_sub').textContent = `${S.hot_search.total} 条`;
  document.getElementById('rank_sub').textContent = `${S.ranking.total} 条`;
  document.getElementById('feed_sub').textContent = `${S.feed.total} 条（会话内已去重）`;
  const mk = (src, withTime) => tbl(S[src].rows.slice(0,50), [
    {h:'RANK', k:'rank'+(src==='hot_search'?' top3':''), f:(_,i)=>String(i+1).padStart(2,'0')},
    {h:'TITLE', f:x=>esc(x.title)},
    withTime
      ? {h:'TIME', k:'time', f:x=>(x.collected_at||'').split('T')[1]||''}
      : {h:'—', k:'count', f:x=>x.heat||'—'}
  ]);
  document.getElementById('hot').innerHTML = mk('hot_search');
  document.getElementById('rank').innerHTML = mk('ranking');
  document.getElementById('feed').innerHTML = mk('feed', true);
}

drawGrid();
drawSweep();
load();
</script>
</body>
</html>"""

UP_HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UP 主观测 · 选题雷达</title>
<style>
:root{
  --paper:#EFF1EE; --ink:#171B1E; --pink:#FB7299; --teal:#1F4E5F;
  --dim:#7A8288; --line:#D5D9D4;
  --mono:"Consolas","SF Mono","Cascadia Mono",monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{background:var(--paper);color:var(--ink);
  font:15px/1.7 "Segoe UI","Microsoft YaHei",sans-serif;
  padding:40px 28px 80px;max-width:1180px;margin:0 auto}
::selection{background:var(--pink);color:#fff}
header{display:flex;align-items:flex-end;justify-content:space-between;
  flex-wrap:wrap;gap:16px;border-bottom:2px solid var(--ink);padding-bottom:18px}
.brand .eyebrow{font-family:var(--mono);font-size:11px;letter-spacing:.35em;
  color:var(--teal);text-transform:uppercase}
.brand h1{font-size:30px;letter-spacing:.14em;font-weight:700;margin-top:2px}
.brand h1 b{color:var(--pink)}
.brand .eyebrow a{color:var(--teal);text-decoration:none}
.meta{font-family:var(--mono);font-size:12px;color:var(--dim);text-align:right;line-height:1.9}
.meta select{font-family:var(--mono);font-size:13px;color:var(--ink);
  background:transparent;border:1px solid var(--ink);padding:3px 10px;
  border-radius:0;cursor:pointer;margin-bottom:4px}
.meta select:focus-visible{outline:2px solid var(--pink);outline-offset:2px}

section{margin-top:36px}
.sec-head{display:flex;align-items:baseline;gap:14px;margin-bottom:14px;flex-wrap:wrap}
.sec-head .tick{font-family:var(--mono);font-size:13px;color:var(--teal);
  letter-spacing:.2em;white-space:nowrap}
.sec-head .tick::before{content:"—— "}
.sec-head h2{font-size:19px;letter-spacing:.08em}
.sec-head .sub{color:var(--dim);font-size:13px}

/* 概览卡：三格刻度 */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
  gap:1px;background:var(--ink);border:1px solid var(--ink);margin-top:32px}
.stat{background:var(--paper);padding:16px 18px}
.stat .n{font-family:var(--mono);font-size:30px;font-weight:700;line-height:1.15}
.stat .n b{color:var(--pink)}
.stat .l{font-family:var(--mono);font-size:11px;color:var(--dim);letter-spacing:.2em;margin-top:3px}

table{width:100%;border-collapse:collapse;font-size:14px}
th{font-family:var(--mono);font-size:11px;color:var(--dim);text-align:left;
  font-weight:400;letter-spacing:.15em;padding:6px 10px;border-bottom:1px solid var(--ink)}
td{padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
tbody tr{transition:background .15s}
tbody tr:hover{background:#E4E8E2}
.seq{font-family:var(--mono);color:var(--teal);width:40px;font-size:13px}
.num{font-family:var(--mono);font-size:13px;color:var(--teal);white-space:nowrap}
.num.hot{color:var(--pink);font-weight:700}
.time{font-family:var(--mono);font-size:12px;color:var(--dim);white-space:nowrap}
.bar{height:4px;background:var(--line);border-radius:2px;margin-top:5px;
  width:110px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--pink);border-radius:2px;
  transform-origin:left;animation:grow .7s cubic-bezier(.2,.8,.2,1) backwards}
@keyframes grow{from{transform:scaleX(0)}}
.empty{color:var(--dim);text-align:center;padding:32px 0;font-size:14px}
.empty code{font-family:var(--mono);font-size:13px;background:#E4E8E2;padding:2px 8px}
footer{margin-top:64px;border-top:1px solid var(--line);padding-top:14px;
  font-family:var(--mono);font-size:12px;color:var(--dim);
  display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}
@media (prefers-reduced-motion: reduce){
  *{animation:none!important;transition:none!important}
  html{scroll-behavior:auto}
}
</style>
</head>
<body>
<header>
  <div class="brand">
    <div class="eyebrow"><a href="/">← Topic Radar /</a> Bilibili UP Watch</div>
    <h1>UP 主<b>观测</b></h1>
  </div>
  <div class="meta">
    <select id="name" onchange="load()" aria-label="选择 UP"></select>
    <select id="day" onchange="load()" aria-label="选择日期"></select><br>
    <span id="meta"></span>
  </div>
</header>

<div class="stats">
  <div class="stat"><div class="n" id="s_total">0</div><div class="l">观测视频 / 条</div></div>
  <div class="stat"><div class="n" id="s_play">0</div><div class="l">最近一条播放</div></div>
</div>

<section>
  <div class="sec-head"><span class="tick">WATCH-01</span><h2>视频列表</h2>
    <span class="sub">最近发布优先 · 点赞/投币/收藏来自详情页（--detail 开启才有）</span></div>
  <div id="tbl"></div>
</section>

<footer>
  <span>DATA // data/bili_radar/up_*.csv</span>
  <span><a href="/" style="color:var(--teal)">← 返回选题雷达</a> · LOCAL ONLY</span>
</footer>

<script>
const esc = s => (s||'').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function parseNum(s){   /* '108.5万' → 1085000，用于排序 */
  const m = /^([\d.]+)([万亿]?)$/.exec((s||'').replace(/\s/g,''));
  if (!m) return 0;
  return parseFloat(m[1]) * ({'万':1e4,'亿':1e8}[m[2]] || 1);
}

async function load(){
  const name = document.getElementById('name').value || '';
  const day = document.getElementById('day').value || '';
  const d = await (await fetch(`/api/up?name=${encodeURIComponent(name)}&day=${encodeURIComponent(day)}`)).json();

  const selN = document.getElementById('name'), selD = document.getElementById('day');
  if (selN.options.length !== d.ups.length)
    selN.innerHTML = d.ups.map(x=>`<option ${x===d.name?'selected':''}>${esc(x)}</option>`).join('');
  selD.innerHTML = d.days.map(x=>`<option ${x===d.day?'selected':''}>${x}</option>`).join('');
  document.getElementById('meta').textContent = d.generated_at;

  /* 概览 */
  const rows = d.rows;
  document.getElementById('s_total').textContent = rows.length;
  document.getElementById('s_play').innerHTML = rows[0]
    ? esc(rows[0].play || '—') : '0';

  /* 表 */
  const el = document.getElementById('tbl');
  if (!rows.length){
    el.innerHTML = '<div class="empty">暂无该 UP 的观测数据。<br>先在 <code>config.py</code> 的 <code>BILI.up_list</code> 里加 UP，再跑 <code>main.py --task bili_up --live</code></div>';
    return;
  }
  const maxPlay = Math.max(...rows.map(x=>x._play_n), 1);
  const hasDetail = rows.some(x=>x.like || x.coin || x.fav);
  const cols = [
    {h:'#', k:'seq', f:x=>String(x._seq).padStart(2,'0')},
    {h:'TITLE', f:x=>esc(x.title)},
    {h:'播放', f:x=>{const p=parseNum(x.play);
      return `${esc(x.play)||'—'}<div class="bar"><i style="width:${Math.round(p/maxPlay*100)}%"></i></div>`}},
    {h:'弹幕', k:'num', f:x=>esc(x.danmaku)||'—'},
    {h:'发布', k:'time', f:x=>esc(x.pub)||'—'},
  ];
  if (hasDetail){
    cols.push(
      {h:'赞', k:'num hot', f:x=>esc(x.like)||'<span style="color:var(--dim)">—</span>'},
      {h:'币', k:'num', f:x=>esc(x.coin)||'<span style="color:var(--dim)">—</span>'},
      {h:'藏', k:'num', f:x=>esc(x.fav)||'<span style="color:var(--dim)">—</span>'},
      {h:'评论', k:'num', f:x=>esc(x.comment)||'<span style="color:var(--dim)">—</span>'},
    );
  }
  cols.push({h:'TIME', k:'time', f:x=>(x.collected_at||'').replace('T',' ').slice(5,16)});
  el.innerHTML = `<table><thead><tr>${cols.map(c=>`<th>${c.h}</th>`).join('')}</tr></thead><tbody>` +
    rows.map(x=>`<tr>${cols.map(c=>`<td class="${c.k||''}">${c.f(x)}</td>`).join('')}</tr>`).join('') +
    '</tbody></table>';
}

load();
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()


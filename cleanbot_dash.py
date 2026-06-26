#!/usr/bin/env python3
"""Standalone professional CleanBot dashboard.
Reads clean_bot_state.json + clean_bot.log → live metrics (P&L, win rate, trades,
equity curve, open positions). Single file, no deps beyond Flask. Port 8095."""
import json, re, time
try:
    import httpx
except Exception:
    httpx = None
from pathlib import Path
from flask import Flask, jsonify, Response, request

BOT = Path(__file__).resolve().parent
STATE = BOT / "clean_bot_state.json"
LOG = BOT / "clean_bot.log"
app = Flask(__name__)

RE_RES = re.compile(
    r'(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| \[(WIN|LOSS)\] (\w+) (UP|DOWN) @ (\d+)c '
    r'-> (UP|DOWN) \| ([+-][\d.]+) \| bankroll \$([\d.]+) \| day net ([+-][\d.]+)')
RE_ENTER = re.compile(
    r'(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| \[ENTER\] (\w+) (UP|DOWN) '
    r'drift=([+-][\d.]+)% ask=(\d+)c')
RE_ADAPT = re.compile(r'\[ADAPT\] rolling WR (\d+)% \(last (\d+)\) -> drift bar ([\d.]+)bps \(base (\d+)\)')


_ER_CACHE = {}
ER_TREND = 0.32


def efficiency_ratio(coin):
    """Kaufman ER over the last hour (12x 5m candles): ~1 trend, ~0 chop. Cached 60s."""
    now = time.time()
    c = _ER_CACHE.get(coin)
    if c and now - c[0] < 60:
        return c[1]
    er = None
    if httpx:
        sym = {"ETH": "ETHUSDT", "SOL": "SOLUSDT", "BTC": "BTCUSDT"}.get(coin)
        for base in ("https://api.binance.us/api/v3/klines",
                     "https://data-api.binance.vision/api/v3/klines"):
            try:
                r = httpx.get(base, params={"symbol": sym, "interval": "5m", "limit": 13},
                              timeout=6, trust_env=False)
                if r.status_code == 200:
                    cl = [float(k[4]) for k in r.json()]
                    if len(cl) >= 4:
                        net = abs(cl[-1] - cl[0])
                        path = sum(abs(cl[i] - cl[i - 1]) for i in range(1, len(cl)))
                        er = round((net / path) if path > 0 else 0.0, 2)
                    break
            except Exception:
                continue
    _ER_CACHE[coin] = (now, er)
    return er


def compute_adapt(recent):
    """Live rolling WR + adaptive drift bar from the bot's recent_trades (matches the
    bot's _eff_drift: base 10, target 0.60, +35bps per WR-deficit point, cap 20)."""
    r = recent[-15:]
    if len(r) < 5:
        return None
    wr = sum(r) / len(r)
    bar = min(10 + max(0.0, 0.60 - wr) * 35, 20)
    return {"wr": round(wr * 100), "n": len(r), "bar": round(bar, 1), "base": 10}


def latest_adapt():
    """Most recent [ADAPT] line from the log -> the bot's own rolling WR + drift bar."""
    try:
        for ln in reversed(LOG.read_text(errors="ignore").splitlines()):
            m = RE_ADAPT.search(ln)
            if m:
                return {"wr": int(m.group(1)), "n": int(m.group(2)),
                        "bar": float(m.group(3)), "base": int(m.group(4))}
    except Exception:
        pass
    return None


def read_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def parse_log():
    trades, equity, enters = [], [], {}
    try:
        lines = LOG.read_text(errors="ignore").splitlines()
    except Exception:
        lines = []
    for ln in lines:
        e = RE_ENTER.search(ln)
        if e:
            enters[(e.group(2), e.group(3), e.group(5))] = {
                "drift": float(e.group(4)), "ask": int(e.group(5))}
        m = RE_RES.search(ln)
        if m:
            ts, res, coin, d, entry, outcome, pnl, bk, daynet = m.groups()
            en = enters.get((coin, d, entry), {})
            trades.append({"ts": ts, "result": res, "coin": coin, "dir": d,
                           "entry": int(entry), "outcome": outcome,
                           "pnl": float(pnl), "bankroll": float(bk),
                           "drift": en.get("drift")})
            equity.append({"ts": ts, "v": float(bk)})
    return trades, equity


def metrics(trades):
    wins = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    n = len(trades)
    total = sum(t["pnl"] for t in trades)
    streak = 0
    for t in reversed(trades):
        if t == trades[-1] or (trades and t["result"] == trades[-1]["result"]):
            streak += 1
        else:
            break
    return {
        "n": n, "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / n * 100, 1) if n else 0,
        "total_pnl": round(total, 2),
        "avg_win": round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0,
        "best": round(max((t["pnl"] for t in trades), default=0), 2),
        "worst": round(min((t["pnl"] for t in trades), default=0), 2),
        "streak": streak, "streak_type": trades[-1]["result"] if trades else "",
    }


@app.route("/api/data")
def data():
    st = read_state()
    trades, equity = parse_log()
    today = st.get("day", "")
    today_trades = [t for t in trades if t["ts"][:10] == today]
    pos = st.get("positions", {}) or {}
    open_pos = [{"key": k, **p} for k, p in pos.items() if p.get("status") == "filled"]
    running = False
    try:
        import subprocess
        out = subprocess.run(["pgrep", "-f", "clean_bot.py"],
                             capture_output=True, text=True, timeout=3)
        running = bool(out.stdout.strip())
    except Exception:
        try:
            running = (time.time() - LOG.stat().st_mtime) < 300
        except Exception:
            pass
    return jsonify({
        "state": {
            "bankroll": st.get("bankroll"), "day": today, "mode": st.get("mode"),
            "version": st.get("version"),
            "day_net": round(st.get("wins", 0) - st.get("losses", 0), 2),
        },
        "metrics": metrics(trades),
        "today": metrics(today_trades),
        "trades": trades[-60:][::-1],
        "equity": equity[-240:],
        "open_positions": open_pos,
        "adapt": compute_adapt(st.get("recent_trades", [])),
        "recent": st.get("recent_trades", [])[-20:],
        "regime": {c: efficiency_ratio(c) for c in ("BTC", "ETH", "SOL")},
        "er_trend": ER_TREND,
        "running": running,
        "ts": time.time(),
    })


@app.route("/api/logs")
def logs():
    try:
        n = min(int(request.args.get("n", 600)), 4000)
    except Exception:
        n = 600
    try:
        lines = LOG.read_text(errors="ignore").splitlines()[-n:]
    except Exception:
        lines = []
    return jsonify({"lines": lines, "ts": time.time()})


@app.route("/")
def index():
    return Response(HTML, mimetype="text/html")


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>CleanBot · Live</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
:root{
  --bg:#070b16; --bg2:#0c1322; --card:#0f1830; --card2:#131d38; --line:#1e2a4a;
  --txt:#e8edf7; --mut:#8a98b8; --grn:#16c784; --red:#ff4d5d; --blu:#4d8dff;
  --pur:#9b6bff; --gold:#f5b94a;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  background:radial-gradient(1200px 600px at 80% -10%,#10204a 0%,var(--bg) 55%);
  color:var(--txt);min-height:100vh;padding:22px 26px 60px;
  -webkit-font-smoothing:antialiased}
.num{font-variant-numeric:tabular-nums;font-feature-settings:'tnum'}
.wrap{max-width:1320px;margin:0 auto}
header{display:flex;align-items:center;justify-content:space-between;margin-bottom:24px;flex-wrap:wrap;gap:12px}
.brand{display:flex;align-items:center;gap:13px}
.logo{width:42px;height:42px;border-radius:12px;display:grid;place-items:center;font-size:22px;
  background:linear-gradient(135deg,var(--blu),var(--pur));box-shadow:0 6px 20px rgba(77,141,255,.35)}
.brand h1{font-size:20px;font-weight:700;letter-spacing:.3px}
.brand .sub{font-size:12px;color:var(--mut);margin-top:1px}
.pill{display:flex;align-items:center;gap:8px;background:var(--card);border:1px solid var(--line);
  padding:8px 14px;border-radius:30px;font-size:13px;font-weight:600}
.dot{width:9px;height:9px;border-radius:50%;background:var(--grn);box-shadow:0 0 0 0 rgba(22,199,132,.6);
  animation:pulse 1.8s infinite}
.dot.off{background:var(--red);animation:none}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(22,199,132,.55)}70%{box-shadow:0 0 0 9px rgba(22,199,132,0)}100%{box-shadow:0 0 0 0 rgba(22,199,132,0)}}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:18px}
.card{background:linear-gradient(180deg,var(--card2),var(--card));border:1px solid var(--line);
  border-radius:18px;padding:20px 22px;position:relative;overflow:hidden}
.card .lbl{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:1px;font-weight:600;margin-bottom:10px}
.card .big{font-size:32px;font-weight:750;letter-spacing:-.5px;line-height:1}
.card .meta{font-size:12.5px;color:var(--mut);margin-top:9px}
.card .ico{position:absolute;right:16px;top:16px;font-size:20px;opacity:.5}
.grn{color:var(--grn)}.red{color:var(--red)}.blu{color:var(--blu)}.gold{color:var(--gold)}.mut{color:var(--mut)}
.grid2{display:grid;grid-template-columns:1.55fr 1fr;gap:16px}
.panel{background:linear-gradient(180deg,var(--card2),var(--card));border:1px solid var(--line);border-radius:18px;padding:18px 20px}
.panel h3{font-size:13px;text-transform:uppercase;letter-spacing:1px;color:var(--mut);font-weight:700;margin-bottom:14px;display:flex;justify-content:space-between;align-items:center}
.chartbox{height:230px;position:relative}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{text-align:left;color:var(--mut);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.6px;padding:8px 6px;border-bottom:1px solid var(--line)}
td{padding:9px 6px;border-bottom:1px solid rgba(30,42,74,.5)}
tbody tr:hover{background:rgba(77,141,255,.05)}
.badge{display:inline-flex;align-items:center;gap:5px;padding:3px 9px;border-radius:7px;font-size:11.5px;font-weight:700}
.b-up{background:rgba(22,199,132,.13);color:var(--grn)}
.b-dn{background:rgba(255,77,93,.13);color:var(--red)}
.res{font-weight:700;font-size:12px;padding:2px 8px;border-radius:6px}
.res.W{background:rgba(22,199,132,.15);color:var(--grn)}
.res.L{background:rgba(255,77,93,.15);color:var(--red)}
.stat{display:flex;justify-content:space-between;padding:10px 2px;border-bottom:1px solid rgba(30,42,74,.5);font-size:13.5px}
.stat:last-child{border:none}.stat .k{color:var(--mut)}
.dot-w{width:14px;height:14px;border-radius:3px;background:var(--grn)}
.dot-l{width:14px;height:14px;border-radius:3px;background:var(--red)}
.openpos{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:11px 13px;margin-bottom:9px;display:flex;justify-content:space-between;align-items:center;font-size:13px}
.empty{color:var(--mut);font-size:13px;text-align:center;padding:18px}
.foot{text-align:center;color:var(--mut);font-size:12px;margin-top:22px}
#logbox{height:460px;overflow-y:auto;background:#05080f;border:1px solid var(--line);border-radius:12px;
  padding:12px 14px;font-family:ui-monospace,'SF Mono','Cascadia Code',Menlo,Consolas,monospace;font-size:12px;line-height:1.6}
#logbox::-webkit-scrollbar{width:9px}#logbox::-webkit-scrollbar-thumb{background:#22304f;border-radius:5px}
.logln{white-space:pre-wrap;word-break:break-all;padding:1px 5px;border-radius:3px;color:#9fb0d0}
.logln:hover{background:rgba(255,255,255,.04)}
.l-win{color:#1fe096;background:rgba(22,199,132,.07)}
.l-loss{color:#ff7b86;background:rgba(255,77,93,.08)}
.l-enter{color:#6aa6ff}.l-fill{color:#4d8dff}.l-strike{color:#b98bff}
.l-watch{color:#647596}.l-exit{color:#f5b94a}.l-hold{color:#3fd6c8}
.l-warn{color:#ff9d4d;background:rgba(255,157,77,.07)}
.l-stop{color:#f5b94a;background:rgba(245,185,74,.07)}
.wr-ring{display:flex;align-items:center;gap:14px}
.ring{--p:0;width:54px;height:54px;border-radius:50%;background:conic-gradient(var(--grn) calc(var(--p)*1%),#22304f 0);display:grid;place-items:center}
.ring i{width:42px;height:42px;border-radius:50%;background:var(--card);display:grid;place-items:center;font-size:13px;font-weight:700;font-style:normal}
@media(max-width:960px){.cards{grid-template-columns:repeat(2,1fr)}.grid2{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="brand">
      <div class="logo">⚡</div>
      <div><h1>CleanBot <span class="mut" style="font-weight:500;font-size:15px" id="ver"></span></h1>
        <div class="sub">Polymarket 15m crypto · Chainlink · hold-to-settlement</div></div>
    </div>
    <div class="pill"><span class="dot" id="dot"></span><span id="status">connecting…</span>
      <span class="mut" style="font-weight:500;margin-left:6px" id="upd"></span></div>
  </header>

  <div class="cards">
    <div class="card"><div class="ico">💰</div><div class="lbl">Bankroll</div>
      <div class="big num" id="bankroll">—</div><div class="meta" id="mode"></div></div>
    <div class="card"><div class="ico">📈</div><div class="lbl">Today P&L</div>
      <div class="big num" id="today">—</div><div class="meta" id="today_n"></div></div>
    <div class="card"><div class="ico">🎯</div><div class="lbl">Win Rate</div>
      <div class="wr-ring"><div class="ring" id="ring"><i id="wr">—</i></div>
        <div><div class="big num" style="font-size:22px" id="wl">—</div><div class="meta">all-time</div></div></div></div>
    <div class="card"><div class="ico">∑</div><div class="lbl">Total P&L</div>
      <div class="big num" id="total">—</div><div class="meta" id="streak"></div></div>
  </div>

  <div class="panel" style="margin-bottom:16px">
    <h3>🎯 Market Regime <span class="mut" style="text-transform:none;letter-spacing:0;font-weight:500">efficiency ratio · &lt;0.32 = chop (bot demands stronger signals) · ≥0.32 = trend (trades freely)</span></h3>
    <div id="regime" style="display:flex;gap:26px;flex-wrap:wrap"></div>
  </div>

  <div class="grid2">
    <div class="panel">
      <h3>Equity Curve <span class="mut num" id="ntrades"></span></h3>
      <div class="chartbox"><canvas id="chart"></canvas></div>
    </div>
    <div class="panel">
      <h3>Performance</h3>
      <div class="stat"><span class="k">Total trades</span><span class="num" id="s_n">—</span></div>
      <div class="stat"><span class="k">Wins · Losses</span><span class="num" id="s_wl">—</span></div>
      <div class="stat"><span class="k">Avg win</span><span class="num grn" id="s_aw">—</span></div>
      <div class="stat"><span class="k">Avg loss</span><span class="num red" id="s_al">—</span></div>
      <div class="stat"><span class="k">Best · Worst</span><span class="num" id="s_bw">—</span></div>
      <h3 style="margin-top:18px">🧠 Adaptive Accuracy</h3>
      <div class="stat"><span class="k">Rolling win rate</span><span class="num" id="a_wr">—</span></div>
      <div class="stat"><span class="k">Drift bar (adaptive)</span><span class="num" id="a_bar">—</span></div>
      <div id="a_dots" style="display:flex;gap:4px;flex-wrap:wrap;padding:10px 2px 4px"></div>
      <div class="mut" style="font-size:11px">recent trades · green=win red=loss — bar rises when losing, drops when winning</div>
      <h3 style="margin-top:18px">Open Positions <span class="mut num" id="nopen"></span></h3>
      <div id="open"></div>
    </div>
  </div>

  <div class="panel" style="margin-top:16px">
    <h3>Trade History</h3>
    <div style="overflow-x:auto">
    <table><thead><tr>
      <th>Time</th><th>Market</th><th>Side</th><th>Entry</th><th>Drift</th><th>Settled</th><th>Result</th><th style="text-align:right">P&L</th><th style="text-align:right">Balance</th>
    </tr></thead><tbody id="rows"></tbody></table>
    </div>
  </div>
  <div class="panel" style="margin-top:16px">
    <h3>Live Log
      <span style="display:flex;align-items:center;gap:12px;font-size:12px;text-transform:none;letter-spacing:0">
        <label class="mut" style="display:flex;align-items:center;gap:5px;cursor:pointer">
          <input type="checkbox" id="autoscroll" checked> auto-scroll</label>
        <input id="filter" placeholder="filter… (e.g. WIN, ENTER, ETH)"
          style="background:var(--bg2);border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:6px 10px;font-size:12px;width:200px;outline:none">
        <span class="mut num" id="logcount"></span>
      </span>
    </h3>
    <div id="logbox"></div>
  </div>
  <div class="foot">Live — log refreshes every 2s · metrics every 4s · CleanBot dashboard</div>
</div>

<script>
let chart;
const $=id=>document.getElementById(id);
const money=v=>(v>=0?'+':'')+'$'+Math.abs(v).toFixed(2);
const bal=v=>'$'+Number(v).toFixed(2);
function sign(v){return v>0?'grn':v<0?'red':'mut';}

async function load(){
  let d;
  try{ d=await (await fetch('/api/data')).json(); }
  catch(e){ $('status').textContent='offline'; $('dot').classList.add('off'); return; }
  const s=d.state,m=d.metrics,t=d.today;
  $('ver').textContent='v'+(s.version||'');
  $('status').textContent=d.running?'LIVE':'idle';
  $('dot').classList.toggle('off',!d.running);
  $('upd').textContent=new Date(d.ts*1000).toLocaleTimeString();
  $('bankroll').textContent=bal(s.bankroll);
  $('mode').textContent=(s.mode||'')+' · '+(s.day||'');
  $('today').textContent=money(t.total_pnl); $('today').className='big num '+sign(t.total_pnl);
  $('today_n').textContent=t.n+' trades · '+t.wins+'W '+t.losses+'L';
  $('wr').textContent=m.win_rate+'%';
  $('ring').style.setProperty('--p',m.win_rate);
  $('wl').textContent=m.wins+'W · '+m.losses+'L';
  $('total').textContent=money(m.total_pnl); $('total').className='big num '+sign(m.total_pnl);
  $('streak').textContent=m.streak>0?(m.streak+' '+(m.streak_type==='WIN'?'win':'loss')+' streak'):'';
  $('ntrades').textContent=m.n+' trades';
  $('s_n').textContent=m.n;
  $('s_wl').textContent=m.wins+' · '+m.losses;
  $('s_aw').textContent=money(m.avg_win);
  $('s_al').textContent=money(m.avg_loss);
  $('s_bw').innerHTML='<span class="grn">'+money(m.best)+'</span> · <span class="red">'+money(m.worst)+'</span>';

  const ad=d.adapt;
  $('a_wr').textContent=ad?(ad.wr+'% (last '+ad.n+')'):'learning…';
  $('a_wr').className='num '+(ad?(ad.wr>=60?'grn':ad.wr>=50?'gold':'red'):'mut');
  $('a_bar').innerHTML=ad?(ad.bar.toFixed(1)+'bps <span class="mut">(base '+ad.base+')</span> '
    +(ad.bar>ad.base+0.05?'<span class="gold">▲ tightened</span>':'<span class="grn">● open</span>')):'—';
  $('a_dots').innerHTML=(d.recent||[]).map(x=>'<span class="'+(x?'dot-w':'dot-l')+'"></span>').join('')
    ||'<span class="mut" style="font-size:12px">no trades yet</span>';

  const reg=d.regime||{}, ert=d.er_trend||0.32;
  $('regime').innerHTML=['BTC','ETH','SOL'].map(c=>{
    const er=reg[c];
    if(er==null) return '<div style="min-width:150px"><b>'+c+'</b> <span class="mut">—</span></div>';
    const trend=er>=ert, col=trend?'#16c784':'#ff5d6c', pct=Math.min(100,Math.round(er*100));
    return '<div style="min-width:155px"><div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:6px">'+
      '<b>'+c+'</b><span class="num" style="color:'+col+';font-weight:700">'+er.toFixed(2)+' · '+(trend?'TREND':'CHOP')+'</span></div>'+
      '<div style="height:9px;background:#1a2440;border-radius:5px;overflow:hidden">'+
      '<div style="height:100%;width:'+pct+'%;background:'+col+';transition:width .4s"></div></div></div>';
  }).join('');

  $('nopen').textContent=d.open_positions.length?('('+d.open_positions.length+')'):'';
  $('open').innerHTML=d.open_positions.length?d.open_positions.map(p=>
    '<div class="openpos"><span><b>'+p.coin+'</b> <span class="badge '+(p.dir==='UP'?'b-up':'b-dn')+'">'+p.dir+'</span></span>'+
    '<span class="num mut">@ '+Math.round(p.entry*100)+'c × '+p.shares+'</span></div>').join('')
    :'<div class="empty">No open positions</div>';

  $('rows').innerHTML=d.trades.map(r=>{
    const W=r.result==='WIN';
    return '<tr><td class="mut num">'+r.ts.slice(11,16)+'</td>'+
      '<td><b>'+r.coin+'</b></td>'+
      '<td><span class="badge '+(r.dir==='UP'?'b-up':'b-dn')+'">'+r.dir+'</span></td>'+
      '<td class="num">'+r.entry+'c</td>'+
      '<td class="num mut">'+(r.drift!=null?(r.drift>0?'+':'')+r.drift.toFixed(2)+'%':'—')+'</td>'+
      '<td class="num">'+r.outcome+'</td>'+
      '<td><span class="res '+(W?'W':'L')+'">'+(W?'WIN':'LOSS')+'</span></td>'+
      '<td class="num '+(W?'grn':'red')+'" style="text-align:right;font-weight:700">'+money(r.pnl)+'</td>'+
      '<td class="num mut" style="text-align:right">'+bal(r.bankroll)+'</td></tr>';
  }).join('')||'<tr><td colspan="9" class="empty">No trades yet</td></tr>';

  const eq=d.equity;
  const labels=eq.map(e=>e.ts.slice(11,16)), vals=eq.map(e=>e.v);
  if(!chart){
    const ctx=$('chart').getContext('2d');
    const g=ctx.createLinearGradient(0,0,0,230);
    g.addColorStop(0,'rgba(22,199,132,.35)');g.addColorStop(1,'rgba(22,199,132,0)');
    chart=new Chart(ctx,{type:'line',data:{labels,datasets:[{data:vals,borderColor:'#16c784',
      backgroundColor:g,fill:true,tension:.35,borderWidth:2,pointRadius:0,pointHoverRadius:4}]},
      options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},
        tooltip:{callbacks:{label:c=>'$'+c.parsed.y.toFixed(2)}}},
        scales:{x:{grid:{display:false},ticks:{color:'#8a98b8',maxTicksLimit:8,font:{size:10}}},
          y:{grid:{color:'rgba(30,42,74,.5)'},ticks:{color:'#8a98b8',font:{size:10},callback:v=>'$'+v}}}}});
  }else{chart.data.labels=labels;chart.data.datasets[0].data=vals;chart.update('none');}
}
load();setInterval(load,4000);

function logClass(l){
  if(/\[WIN\]/.test(l))return'l-win';
  if(/\[LOSS\]/.test(l))return'l-loss';
  if(/EXIT FAIL|ORDER FAIL|WARNING|\berror\b|Error|Traceback|FAIL/.test(l))return'l-warn';
  if(/\[EXIT/.test(l))return'l-exit';
  if(/\[ENTER\]/.test(l))return'l-enter';
  if(/\[FILLED\]|\[GTC\]|\[CANCEL\]/.test(l))return'l-fill';
  if(/\[STRIKE/.test(l))return'l-strike';
  if(/\[WATCH\]/.test(l))return'l-watch';
  if(/\[HOLD\]/.test(l))return'l-hold';
  if(/\[STOP\]|\[BREAKER\]/.test(l))return'l-stop';
  return'';
}
const esc=s=>s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
async function loadLogs(){
  let d; try{ d=await (await fetch('/api/logs?n=1000')).json(); }catch(e){return;}
  const f=$('filter').value.trim().toLowerCase();
  let lines=f?d.lines.filter(l=>l.toLowerCase().includes(f)):d.lines;
  $('logcount').textContent=lines.length+' lines';
  const box=$('logbox');
  box.innerHTML=lines.map(l=>'<div class="logln '+logClass(l)+'">'+esc(l)+'</div>').join('');
  if($('autoscroll').checked) box.scrollTop=box.scrollHeight;
}
loadLogs();setInterval(loadLogs,2000);
</script>
</body>
</html>"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8095)

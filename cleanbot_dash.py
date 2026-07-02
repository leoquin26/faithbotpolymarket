#!/usr/bin/env python3
"""CleanBot Dashboard v2 — real-time monitoring console.
Single file, Flask + vanilla JS + Chart.js. Port 8095.

v2 upgrades:
- Incremental log tailing (parses only appended bytes; cheap 3s polling)
- Chain-truth equity (RECONCILED/SYNC lines), not the optimistic ledger
- Live open-position status: strike vs live price -> WINNING/LOSING + countdown
- Guard activity panel (counter-trend / regime / day-trend / corr / rev-cooldown)
- Shadow-coin verifier gate progress (BTC/XRP: n/80, WR vs break-even, z, EV)
- Active stop detection (profit-lock / daily loss / breaker) + bot-down alerts
- UP/DOWN + per-coin splits, 7-day history, deduped colorized log viewer
"""
import csv, json, math, re, subprocess, threading, time
from pathlib import Path
try:
    import httpx
except Exception:
    httpx = None
from flask import Flask, jsonify, Response, request

BOT = Path(__file__).resolve().parent
STATE = BOT / "clean_bot_state.json"
LOG = BOT / "clean_bot.log"
RESEARCH = BOT / "clean_bot_research.csv"
STRIKES = BOT / "data" / "strike_cache.json"
app = Flask(__name__)

# ── regexes (only full-date lines; the log carries short-format duplicates) ──
RE_DATED = re.compile(r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d \| ')
RE_RES = re.compile(
    r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| \[(WIN|LOSS)\] (\w+) (UP|DOWN) @ (\d+)c '
    r'-> (UP|DOWN) \| ([+-][\d.]+) \| bankroll \$([\d.]+) \| day net ([+-][\d.]+)')
RE_ENTER = re.compile(
    r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| \[ENTER\] (\w+) (UP|DOWN) '
    r'drift=([+-][\d.]+)% ask=(\d+)c.*?(?:prob=([\d.]+))?\s*T=(\d+)s')
RE_RECON = re.compile(
    r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| \[RECONCILED\] bankroll \$([\d.]+) '
    r'\(chain truth\) \| session net ([+-][\d.]+)')
RE_SYNC = re.compile(
    r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| \[BANKROLL SYNC\] \$[\d.]+ -> \$([\d.]+)')
RE_ALIVE = re.compile(
    r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| .*alive scan#(\d+) open=(\d+) '
    r'positions=(\d+) bankroll=\$([\d.]+) day_net=([+-][\d.]+)')
RE_STOP = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| \[STOP\] (PROFIT-LOCK|daily loss|give-back)')
RE_BANNER = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| === CleanBot v([\d.]+) start \| (LIVE|DRY)')
RE_ADAPT = re.compile(r'\[ADAPT\] rolling WR (\d+)% \(last (\d+)\) -> drift bar ([\d.]+)bps \(base (\d+)\)')
RE_SKIP = re.compile(r'-> SKIP:(\w+)')
GUARD_TAGS = ["COUNTER-TREND SKIP", "REGIME SKIP", "DAY-TREND SKIP", "MOM SKIP",
              "REV COOLDOWN", "CORR DIVERGE", "CORR SKIP", "CORR HALF",
              "FLOW SKIP", "NO CONFIRM", "FILLED-RACE"]

# ── incremental log parser (thread-safe; re-reads only appended bytes) ──────
class LogCache:
    def __init__(self):
        self.lock = threading.Lock()
        self.pos = 0
        self.trades = []          # resolved WIN/LOSS
        self.equity = []          # chain-truth points (RECONCILED + SYNC + trade bankrolls)
        self.enters = []          # ENTER events
        self.guards = []          # (ts, tag)
        self.skips = []           # (ts, reason) from WATCH lines
        self.stops = []           # (ts, kind)
        self.banners = []         # (ts, version, mode)
        self.alive = None         # last heartbeat dict
        self.adapt = None         # last ADAPT dict

    def _ingest(self, ln):
        if not RE_DATED.match(ln):
            return
        m = RE_RES.match(ln)
        if m:
            ts, res, coin, d, entry, outcome, pnl, bk, daynet = m.groups()
            self.trades.append({"ts": ts, "result": res, "coin": coin, "dir": d,
                                "entry": int(entry), "outcome": outcome,
                                "pnl": float(pnl), "bankroll": float(bk),
                                "day_net": float(daynet)})
            return
        m = RE_RECON.match(ln)
        if m:
            self.equity.append({"ts": m.group(1), "v": float(m.group(2)), "src": "recon"})
            return
        m = RE_SYNC.match(ln)
        if m:
            self.equity.append({"ts": m.group(1), "v": float(m.group(2)), "src": "sync"})
            return
        m = RE_ALIVE.match(ln)
        if m:
            self.alive = {"ts": m.group(1), "scan": int(m.group(2)), "open": int(m.group(3)),
                          "positions": int(m.group(4)), "bankroll": float(m.group(5)),
                          "day_net": float(m.group(6))}
            return
        m = RE_ENTER.match(ln)
        if m:
            self.enters.append({"ts": m.group(1), "coin": m.group(2), "dir": m.group(3),
                                "drift": float(m.group(4)), "ask": int(m.group(5)),
                                "prob": float(m.group(6)) if m.group(6) else None,
                                "t": int(m.group(7))})
            return
        m = RE_STOP.match(ln)
        if m:
            self.stops.append({"ts": m.group(1), "kind": m.group(2)})
            return
        m = RE_BANNER.match(ln)
        if m:
            self.banners.append({"ts": m.group(1), "version": m.group(2), "mode": m.group(3)})
            return
        m = RE_ADAPT.search(ln)
        if m:
            self.adapt = {"wr": int(m.group(1)), "n": int(m.group(2)),
                          "bar": float(m.group(3)), "base": int(m.group(4))}
        for tag in GUARD_TAGS:
            if "[" + tag.split()[0] in ln and tag in ln:
                self.guards.append({"ts": ln[:19], "tag": tag})
                return
        m = RE_SKIP.search(ln)
        if m:
            self.skips.append({"ts": ln[:19], "reason": m.group(1)})

    def refresh(self):
        with self.lock:
            try:
                size = LOG.stat().st_size
            except Exception:
                return
            if size < self.pos:                       # rotated/truncated -> full reparse
                self.pos = 0
                self.__init__()
            if size == self.pos:
                return
            with open(LOG, "rb") as f:
                f.seek(self.pos)
                chunk = f.read()
                self.pos = f.tell()
            for ln in chunk.decode("utf-8", errors="ignore").splitlines():
                try:
                    self._ingest(ln)
                except Exception:
                    pass
            # bound memory
            for attr, keep in (("equity", 5000), ("enters", 800), ("guards", 3000),
                               ("skips", 4000), ("stops", 300), ("trades", 3000)):
                v = getattr(self, attr)
                if len(v) > keep:
                    setattr(self, attr, v[-keep:])

CACHE = LogCache()

# ── live prices / ER (direct to binance, cached) ────────────────────────────
_PX = {}
_ER = {}
SYMS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}

def live_price(coin, ttl=4):
    now = time.time()
    c = _PX.get(coin)
    if c and now - c[0] < ttl:
        return c[1]
    px = None
    if httpx:
        for base in ("https://api.binance.us/api/v3/ticker/price",
                     "https://data-api.binance.vision/api/v3/ticker/price"):
            try:
                r = httpx.get(base, params={"symbol": SYMS.get(coin, "")}, timeout=4, trust_env=False)
                if r.status_code == 200:
                    px = float(r.json().get("price", 0)) or None
                    break
            except Exception:
                continue
    _PX[coin] = (now, px)
    return px

def efficiency_ratio(coin, ttl=60):
    now = time.time()
    c = _ER.get(coin)
    if c and now - c[0] < ttl:
        return c[1]
    er = None
    if httpx:
        for base in ("https://api.binance.us/api/v3/klines",
                     "https://data-api.binance.vision/api/v3/klines"):
            try:
                r = httpx.get(base, params={"symbol": SYMS.get(coin, ""), "interval": "5m",
                                            "limit": 13}, timeout=6, trust_env=False)
                if r.status_code == 200:
                    cl = [float(k[4]) for k in r.json()]
                    if len(cl) >= 4:
                        net = abs(cl[-1] - cl[0])
                        path = sum(abs(cl[i] - cl[i-1]) for i in range(1, len(cl)))
                        er = round((net / path) if path > 0 else 0.0, 2)
                    break
            except Exception:
                continue
    _ER[coin] = (now, er)
    return er

# ── shadow-coin verifier gate (from research CSV; cached 120s) ──────────────
_SHADOW = {"ts": 0, "data": None}

def shadow_gate():
    now = time.time()
    if _SHADOW["data"] is not None and now - _SHADOW["ts"] < 120:
        return _SHADOW["data"]
    out = {}
    try:
        rows = [r for r in csv.DictReader(open(RESEARCH, encoding="utf-8", errors="ignore"))
                if r.get("drift_correct") in ("0", "1")]
        def fl(r, k):
            try: return float(r[k])
            except Exception: return None
        for coin in ("BTC", "XRP", "ETH", "SOL"):
            sub = [r for r in rows if r["coin"] == coin and fl(r, "fav_ask")
                   and 55 <= fl(r, "fav_ask") <= 74 and fl(r, "drift_pct") is not None
                   and abs(fl(r, "drift_pct") * 100) >= 5]
            n = len(sub)
            if n == 0:
                out[coin] = {"n": 0}
                continue
            w = sum(int(r["drift_correct"]) for r in sub)
            wr = w / n
            be = sum(fl(r, "fav_ask") / 100 for r in sub) / n
            se = math.sqrt(be * (1 - be) / n) if n else 0
            z = (wr - be) / se if se > 0 else 0
            ev = sum(((1 - fl(r, "fav_ask")/100) / (fl(r, "fav_ask")/100)) if int(r["drift_correct"])
                     else -1.0 for r in sub) / n
            out[coin] = {"n": n, "wr": round(wr*100, 1), "be": round(be*100, 1),
                         "z": round(z, 2), "ev": round(ev, 3),
                         "gate": bool(n >= 80 and z >= 1.64 and ev > 0)}
    except Exception:
        pass
    _SHADOW.update(ts=now, data=out)
    return out

# ── helpers ──────────────────────────────────────────────────────────────────
def read_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def strike_for(coin, ws):
    try:
        cache = json.loads(STRIKES.read_text(encoding="utf-8"))
        e = cache.get(f"{coin.lower()}-updown-15m-{int(ws)}")
        return float(e["strike"]) if e else None
    except Exception:
        return None

def metrics(trades):
    wins = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    n = len(trades)
    total = sum(t["pnl"] for t in trades)
    streak = 0
    if trades:
        last = trades[-1]["result"]
        for t in reversed(trades):
            if t["result"] == last:
                streak += 1
            else:
                break
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    return {"n": n, "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins)/n*100, 1) if n else 0,
            "total_pnl": round(total, 2),
            "avg_win": round(gw/len(wins), 2) if wins else 0,
            "avg_loss": round(-gl/len(losses), 2) if losses else 0,
            "profit_factor": round(gw/gl, 2) if gl else None,
            "streak": streak, "streak_type": trades[-1]["result"] if trades else ""}

def splits(trades):
    out = {}
    for key, pred in (("UP", lambda t: t["dir"] == "UP"), ("DOWN", lambda t: t["dir"] == "DOWN"),
                      ("ETH", lambda t: t["coin"] == "ETH"), ("SOL", lambda t: t["coin"] == "SOL"),
                      ("BTC", lambda t: t["coin"] == "BTC")):
        g = [t for t in trades if pred(t)]
        if g:
            w = sum(1 for t in g if t["result"] == "WIN")
            out[key] = {"n": len(g), "w": w, "wr": round(w/len(g)*100),
                        "pnl": round(sum(t["pnl"] for t in g), 2)}
    return out

def day_history(trades, days=7):
    byday = {}
    for t in trades:
        byday.setdefault(t["ts"][:10], []).append(t)
    hist = []
    for d in sorted(byday)[-days:]:
        g = byday[d]
        w = sum(1 for t in g if t["result"] == "WIN")
        hist.append({"day": d, "n": len(g), "w": w, "l": len(g)-w,
                     "wr": round(w/len(g)*100) if g else 0,
                     "pnl": round(sum(t["pnl"] for t in g), 2)})
    return hist

# ── routes ───────────────────────────────────────────────────────────────────
@app.route("/api/data")
def data():
    CACHE.refresh()
    st = read_state()
    now = time.time()
    today = time.strftime("%Y-%m-%d")
    trades = CACHE.trades
    today_trades = [t for t in trades if t["ts"][:10] == today]

    # process status: pgrep + heartbeat freshness
    running = False
    try:
        out = subprocess.run(["pgrep", "-f", "clean_bot.py"], capture_output=True, text=True, timeout=3)
        running = bool(out.stdout.strip())
    except Exception:
        pass
    hb_age = None
    if CACHE.alive:
        try:
            hb_age = int(now - time.mktime(time.strptime(CACHE.alive["ts"], "%Y-%m-%d %H:%M:%S")))
        except Exception:
            pass
    status = "DOWN" if not running else ("STALE" if (hb_age is None or hb_age > 240) else "LIVE")

    # active stop: last STOP within 6 min and after last banner
    active_stop = None
    if CACHE.stops:
        s = CACHE.stops[-1]
        try:
            age = now - time.mktime(time.strptime(s["ts"], "%Y-%m-%d %H:%M:%S"))
            after_restart = (not CACHE.banners) or s["ts"] >= CACHE.banners[-1]["ts"]
            if age < 360 and after_restart:
                active_stop = s["kind"]
        except Exception:
            pass

    # open positions w/ live status
    open_pos = []
    for k, p in (st.get("positions", {}) or {}).items():
        if p.get("status") != "filled":
            continue
        coin, ws = p.get("coin"), p.get("ws", 0)
        strike = strike_for(coin, ws)
        px = live_price(coin)
        drift = round((px - strike) / strike * 1e4, 1) if (px and strike) else None
        winning = None
        if drift is not None:
            winning = (drift > 0) == (p.get("dir") == "UP")
        open_pos.append({"coin": coin, "dir": p.get("dir"), "entry": p.get("entry"),
                         "shares": p.get("shares"), "ws": ws,
                         "t_left": max(0, int(ws + 900 - now)),
                         "strike": strike, "px": px, "drift_bps": drift, "winning": winning})

    # guards today
    gtoday = {}
    for g in CACHE.guards:
        if g["ts"][:10] == today:
            gtoday[g["tag"]] = gtoday.get(g["tag"], 0) + 1
    stoday = {}
    for s in CACHE.skips:
        if s["ts"][:10] == today:
            stoday[s["reason"]] = stoday.get(s["reason"], 0) + 1

    version = CACHE.banners[-1]["version"] if CACHE.banners else st.get("version")
    chain_bk = CACHE.equity[-1]["v"] if CACHE.equity else st.get("bankroll")
    day_pnl = round(sum(t["pnl"] for t in today_trades), 2)

    return jsonify({
        "status": status, "running": running, "hb_age": hb_age,
        "version": version, "active_stop": active_stop,
        "scan": CACHE.alive["scan"] if CACHE.alive else None,
        "bankroll": st.get("bankroll"), "chain_bankroll": chain_bk,
        "day_net_ledger": round(st.get("wins", 0) - st.get("losses", 0), 2),
        "day_pnl": day_pnl,
        "metrics_all": metrics(trades), "metrics_today": metrics(today_trades),
        "splits_today": splits(today_trades),
        "day_history": day_history(trades),
        "trades": trades[-40:][::-1],
        "equity": CACHE.equity[-800:],
        "open_positions": open_pos,
        "adapt": CACHE.adapt,
        "recent": (st.get("recent_trades") or [])[-30:],
        "guards_today": gtoday, "skips_today": stoday,
        "regime": {c: efficiency_ratio(c) for c in ("BTC", "ETH", "SOL", "XRP")},
        "er_trend": 0.32,
        "shadow": shadow_gate(),
        "last_enter": CACHE.enters[-1] if CACHE.enters else None,
        "ts": now,
    })

@app.route("/api/logs")
def logs():
    """Incremental: pass ?since=<offset> to get only new bytes' lines + new offset."""
    try:
        since = int(request.args.get("since", -1))
    except Exception:
        since = -1
    try:
        size = LOG.stat().st_size
        if since < 0 or since > size:
            start = max(0, size - 120_000)          # first load: last ~120KB
        else:
            start = since
        with open(LOG, "rb") as f:
            f.seek(start)
            chunk = f.read()
            offset = f.tell()
        lines = [l for l in chunk.decode("utf-8", errors="ignore").splitlines()
                 if RE_DATED.match(l)]
        if since < 0 and lines:
            lines = lines[1:]                        # drop possibly-partial first line
        return jsonify({"lines": lines[-1200:], "offset": offset, "ts": time.time()})
    except Exception:
        return jsonify({"lines": [], "offset": 0, "ts": time.time()})

@app.route("/")
def index():
    return Response(HTML, mimetype="text/html")


HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CleanBot Console</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
:root{--bg:#0b0f17;--panel:#111827;--panel2:#0f1522;--line:#1f2a3c;--txt:#e5eaf3;--dim:#8494ab;
--green:#22c55e;--red:#ef4444;--amber:#f59e0b;--blue:#3b82f6;--cyan:#06b6d4;--vio:#8b5cf6}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font:14px/1.45 'Segoe UI',system-ui,sans-serif}
a{color:var(--blue)}
.wrap{max-width:1500px;margin:0 auto;padding:14px}
header{display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding:10px 14px;background:var(--panel);
border:1px solid var(--line);border-radius:12px;position:sticky;top:8px;z-index:50}
h1{font-size:17px;letter-spacing:.4px}
.pill{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;font-size:12px;font-weight:600}
.pill .dot{width:8px;height:8px;border-radius:50%}
.LIVE{background:#052e1a;color:var(--green)}.LIVE .dot{background:var(--green);animation:pulse 1.6s infinite}
.STALE{background:#3a2a05;color:var(--amber)}.STALE .dot{background:var(--amber)}
.DOWN{background:#3a0a0a;color:var(--red)}.DOWN .dot{background:var(--red);animation:pulse .8s infinite}
@keyframes pulse{0%{opacity:1}50%{opacity:.35}100%{opacity:1}}
.hstat{margin-left:auto;display:flex;gap:18px;align-items:center;flex-wrap:wrap}
.hstat b{font-size:19px}
.banner{margin-top:10px;padding:10px 14px;border-radius:10px;font-weight:600;display:none}
.banner.warn{display:block;background:#3a2a05;color:var(--amber);border:1px solid #6b4e0a}
.banner.err{display:block;background:#3a0a0a;color:var(--red);border:1px solid #7a1a1a}
.grid{display:grid;gap:12px;margin-top:12px}
.cards{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.card .lbl{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.8px}
.card .val{font-size:22px;font-weight:700;margin-top:3px}
.card .sub{color:var(--dim);font-size:12px;margin-top:2px}
.pos{color:var(--green)}.neg{color:var(--red)}.dim{color:var(--dim)}
.cols{grid-template-columns:2fr 1fr}
@media(max-width:1100px){.cols{grid-template-columns:1fr}}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}
.panel h2{font-size:13px;color:var(--dim);text-transform:uppercase;letter-spacing:.8px;margin-bottom:10px;
display:flex;align-items:center;gap:8px}
.panel h2 .chip{margin-left:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th{color:var(--dim);text-align:left;font-weight:600;padding:5px 8px;border-bottom:1px solid var(--line);font-size:11px;text-transform:uppercase}
td{padding:5px 8px;border-bottom:1px solid #16202f}
tr:last-child td{border-bottom:none}
.chip{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:700}
.cW{background:#052e1a;color:var(--green)}.cL{background:#3a0a0a;color:var(--red)}
.cU{background:#052236;color:var(--blue)}.cD{background:#2b0b36;color:var(--vio)}
.cN{background:#1a2333;color:var(--dim)}
.bar{height:8px;background:#1a2333;border-radius:5px;overflow:hidden}
.bar>i{display:block;height:100%;border-radius:5px}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.kv{display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #16202f;font-size:13px}
.kv:last-child{border-bottom:none}
.kv b{font-variant-numeric:tabular-nums}
#logbox{background:var(--panel2);border:1px solid var(--line);border-radius:10px;height:420px;overflow-y:auto;
padding:10px;font:12px/1.55 Consolas,Menlo,monospace;white-space:pre-wrap;word-break:break-all}
.lg-t{color:#9ae6b4}.lg-l{color:#feb2b2}.lg-g{color:#fbd38d}.lg-s{color:#63b3ed}.lg-x{color:#718096}
.lg-e{color:#fc8181;font-weight:700}
.toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px}
.btn{background:#1a2333;color:var(--txt);border:1px solid var(--line);border-radius:8px;
padding:5px 12px;font-size:12px;cursor:pointer;font-weight:600}
.btn.on{background:var(--blue);border-color:var(--blue);color:#fff}
input[type=text]{background:var(--panel2);border:1px solid var(--line);border-radius:8px;color:var(--txt);
padding:5px 10px;font-size:12px;width:170px}
.flash{animation:flash 1.2s}
@keyframes flash{0%{background:#1d3a5f}100%{background:transparent}}
canvas{max-height:280px}
.countdown{font-variant-numeric:tabular-nums;font-weight:700}
footer{color:var(--dim);text-align:center;font-size:11px;margin:16px 0}
</style></head><body><div class="wrap">

<header>
  <h1>🤖 CleanBot Console</h1>
  <span id="status" class="pill LIVE"><span class="dot"></span><span id="statusTxt">…</span></span>
  <span class="chip cN" id="ver">v?</span>
  <span class="chip cN" id="scan">scan —</span>
  <div class="hstat">
    <div><span class="dim">Wallet (chain)</span><br><b id="bank">$—</b></div>
    <div><span class="dim">Day P&L</span><br><b id="dayPnl">—</b></div>
    <div><span class="dim">Today</span><br><b id="todayWr">—</b></div>
    <div><span class="dim">Rolling WR</span><br><b id="rollWr">—</b></div>
  </div>
</header>
<div id="alert" class="banner"></div>

<div class="grid cards" id="cards">
  <div class="card"><div class="lbl">Today Trades</div><div class="val" id="tN">—</div><div class="sub" id="tWL">—</div></div>
  <div class="card"><div class="lbl">Day P&L (resolved)</div><div class="val" id="tPnl">—</div><div class="sub" id="tPf">PF —</div></div>
  <div class="card"><div class="lbl">Avg Win / Loss</div><div class="val" id="tAvg">—</div><div class="sub">per trade</div></div>
  <div class="card"><div class="lbl">Streak</div><div class="val" id="tStreak">—</div><div class="sub" id="tStreakT">—</div></div>
  <div class="card"><div class="lbl">All-time WR</div><div class="val" id="aWr">—</div><div class="sub" id="aN">—</div></div>
  <div class="card"><div class="lbl">Drift Bar</div><div class="val" id="aBar">—</div><div class="sub" id="aBase">adaptive</div></div>
</div>

<div class="grid cols">
  <div class="panel"><h2>💰 Equity — chain truth <span class="chip cN" id="eqRangeLbl"></span>
    <span style="margin-left:auto;display:flex;gap:6px">
      <button class="btn" data-r="today">Today</button>
      <button class="btn on" data-r="3d">3D</button>
      <button class="btn" data-r="all">All</button></span></h2>
    <canvas id="eqChart"></canvas>
  </div>
  <div class="panel"><h2>📡 Open Positions <span class="chip cN" id="opN"></span></h2>
    <div id="openPos"><div class="dim">none</div></div>
    <h2 style="margin-top:16px">🧭 Regime (ER, trend ≥ 0.32)</h2>
    <div id="regime"></div>
  </div>
</div>

<div class="grid cols">
  <div class="panel"><h2>📜 Recent Trades</h2>
    <div style="max-height:330px;overflow-y:auto"><table id="tradesTbl">
      <thead><tr><th>Time</th><th>Coin</th><th>Dir</th><th>Entry</th><th>Result</th><th>P&L</th><th>Wallet</th></tr></thead>
      <tbody></tbody></table></div>
  </div>
  <div class="panel">
    <h2>🛡️ Guards Today <span class="chip cN">what the bot avoided</span></h2>
    <div id="guards"></div>
    <h2 style="margin-top:16px">🧪 Shadow Coins — verifier gate</h2>
    <div id="shadow"></div>
  </div>
</div>

<div class="grid cols">
  <div class="panel"><h2>📅 Last 7 Days</h2>
    <table id="histTbl"><thead><tr><th>Day</th><th>Trades</th><th>W–L</th><th>WR</th><th>P&L</th></tr></thead><tbody></tbody></table>
    <h2 style="margin-top:16px">🔀 Today Splits</h2>
    <div id="splits" class="g2"></div>
  </div>
  <div class="panel"><h2>🎯 Last 30 Outcomes</h2>
    <div id="dots" style="display:flex;gap:5px;flex-wrap:wrap"></div>
    <h2 style="margin-top:16px">🕐 Last Signal</h2>
    <div id="lastEnter" class="dim">—</div>
  </div>
</div>

<div class="panel" style="margin-top:12px">
  <h2>🖥️ Live Log</h2>
  <div class="toolbar">
    <button class="btn on" data-f="all">All</button>
    <button class="btn" data-f="trade">Trades</button>
    <button class="btn" data-f="guard">Guards</button>
    <button class="btn" data-f="sys">System</button>
    <button class="btn" data-f="err">Errors</button>
    <input type="text" id="logSearch" placeholder="search…">
    <button class="btn" id="pauseBtn">⏸ Pause</button>
    <button class="btn on" id="scrollBtn">⤓ Auto-scroll</button>
    <span class="dim" id="logInfo"></span>
  </div>
  <div id="logbox"></div>
</div>

<footer>CleanBot Console v2 · polls 4s data / 3s logs · chain-truth accounting</footer>
</div>

<script>
const $=id=>document.getElementById(id);
const fmt=(v,d=2)=>v==null?'—':(+v).toFixed(d);
const money=v=>v==null?'—':(v<0?'-$':'$')+Math.abs(v).toFixed(2);
const sgn=v=>v==null?'—':(v>=0?'+$':'-$')+Math.abs(v).toFixed(2);
const cls=v=>v>0?'pos':v<0?'neg':'dim';

let eqChart=null, eqRange='3d', lastTradeTs=null, allEquity=[];

function drawEquity(){
  const now=Date.now(), day0=new Date(); day0.setHours(0,0,0,0);
  let pts=allEquity;
  if(eqRange==='today') pts=pts.filter(p=>new Date(p.ts)>=day0);
  else if(eqRange==='3d') pts=pts.filter(p=>now-new Date(p.ts).getTime()<3*864e5);
  const labels=pts.map(p=>p.ts.slice(5,16)), data=pts.map(p=>p.v);
  $('eqRangeLbl').textContent=pts.length+' pts';
  if(!eqChart){
    const ctx=$('eqChart').getContext('2d');
    const grad=ctx.createLinearGradient(0,0,0,260);
    grad.addColorStop(0,'rgba(34,197,94,.28)');grad.addColorStop(1,'rgba(34,197,94,0)');
    eqChart=new Chart(ctx,{type:'line',data:{labels,datasets:[{data,fill:true,backgroundColor:grad,
      borderColor:'#22c55e',borderWidth:2,pointRadius:0,tension:.25}]},
      options:{animation:false,plugins:{legend:{display:false},tooltip:{mode:'index',intersect:false}},
      scales:{x:{ticks:{color:'#8494ab',maxTicksLimit:8},grid:{color:'#16202f'}},
              y:{ticks:{color:'#8494ab',callback:v=>'$'+v},grid:{color:'#16202f'}}}}});
  } else { eqChart.data.labels=labels; eqChart.data.datasets[0].data=data; eqChart.update('none'); }
}
document.querySelectorAll('[data-r]').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('[data-r]').forEach(x=>x.classList.remove('on'));
  b.classList.add('on'); eqRange=b.dataset.r; drawEquity();});

function chip(t,c){return `<span class="chip ${c}">${t}</span>`}

async function poll(){
  try{
    const r=await fetch('/api/data'); const d=await r.json();
    // header
    const st=$('status'); st.className='pill '+d.status; $('statusTxt').textContent=
      d.status+(d.hb_age!=null?` · hb ${d.hb_age}s`:'');
    $('ver').textContent='v'+(d.version||'?');
    $('scan').textContent='scan '+(d.scan??'—');
    $('bank').textContent=money(d.chain_bankroll);
    const dp=$('dayPnl'); dp.textContent=sgn(d.day_pnl); dp.className=cls(d.day_pnl);
    const mt=d.metrics_today||{};
    $('todayWr').textContent=mt.n?`${mt.win_rate}% (${mt.wins}W/${mt.losses}L)`:'—';
    $('rollWr').textContent=d.adapt?`${d.adapt.wr}% (n${d.adapt.n})`:'—';
    document.title=`${money(d.chain_bankroll)} ${d.day_pnl>=0?'▲':'▼'} CleanBot`;
    // alert banner
    const al=$('alert');
    if(d.status==='DOWN'){al.className='banner err';al.textContent='⛔ BOT PROCESS NOT RUNNING — no trading is happening';}
    else if(d.status==='STALE'){al.className='banner warn';al.textContent='⚠️ Heartbeat stale ('+(d.hb_age??'?')+'s) — bot may be hung';}
    else if(d.active_stop){al.className='banner warn';al.textContent='🔒 STOP ACTIVE: '+d.active_stop+' — no new entries (auto-clears at midnight or restart)';}
    else al.className='banner';
    // cards
    $('tN').textContent=mt.n??0; $('tWL').textContent=`${mt.wins??0}W / ${mt.losses??0}L`;
    const tp=$('tPnl'); tp.textContent=sgn(mt.total_pnl); tp.className='val '+cls(mt.total_pnl);
    $('tPf').textContent='PF '+(mt.profit_factor??'—');
    $('tAvg').innerHTML=`<span class="pos">${sgn(mt.avg_win)}</span> / <span class="neg">${sgn(mt.avg_loss)}</span>`;
    $('tStreak').textContent=mt.streak||0;
    $('tStreakT').textContent=mt.streak_type==='WIN'?'wins in a row 🔥':mt.streak_type==='LOSS'?'losses in a row':'—';
    const ma=d.metrics_all||{};
    $('aWr').textContent=(ma.win_rate??'—')+'%'; $('aN').textContent=(ma.n??0)+' trades all-time';
    $('aBar').textContent=d.adapt?d.adapt.bar+'bps':'—';
    $('aBase').textContent=d.adapt?('base '+d.adapt.base+'bps · adaptive'):'adaptive';
    // equity
    allEquity=d.equity||[]; drawEquity();
    // open positions
    const op=d.open_positions||[]; $('opN').textContent=op.length;
    $('openPos').innerHTML=op.length?op.map(p=>{
      const w=p.winning==null?chip('?','cN'):p.winning?chip('WINNING','cW'):chip('LOSING','cL');
      const mm=Math.floor(p.t_left/60),ss=(p.t_left%60+'').padStart(2,'0');
      return `<div class="kv"><span>${chip(p.coin,'cN')} ${chip(p.dir,p.dir==='UP'?'cU':'cD')} @${Math.round(p.entry*100)}c ×${p.shares}</span>
        <span>${p.drift_bps==null?'':`<b class="${p.drift_bps>0?'pos':'neg'}">${p.drift_bps>0?'+':''}${p.drift_bps}bps</b> `}${w}
        <b class="countdown"> ${mm}:${ss}</b></span></div>`;}).join('')
      :'<div class="dim">none — scanning for setups</div>';
    // regime
    $('regime').innerHTML=Object.entries(d.regime||{}).map(([c,v])=>{
      const trend=v!=null&&v>=d.er_trend;
      const pct=v==null?0:Math.min(100,Math.round(v*100));
      return `<div class="kv"><span>${c} ${v==null?'':trend?chip('TREND','cW'):chip('CHOP','cL')}</span>
        <span style="flex:1;margin:6px 12px"><span class="bar"><i style="width:${pct}%;background:${trend?'var(--green)':'var(--red)'}"></i></span></span>
        <b>${v??'—'}</b></div>`;}).join('');
    // trades table + flash on new
    const tb=$('tradesTbl').querySelector('tbody');
    tb.innerHTML=(d.trades||[]).map(t=>`<tr>
      <td class="dim">${t.ts.slice(5,16)}</td><td>${t.coin}</td>
      <td>${chip(t.dir,t.dir==='UP'?'cU':'cD')}</td><td>${t.entry}c</td>
      <td>${chip(t.result,t.result==='WIN'?'cW':'cL')}</td>
      <td class="${cls(t.pnl)}">${sgn(t.pnl)}</td><td class="dim">$${fmt(t.bankroll)}</td></tr>`).join('');
    const newest=(d.trades||[])[0];
    if(newest&&lastTradeTs&&newest.ts!==lastTradeTs){tb.rows[0]&&tb.rows[0].classList.add('flash');}
    if(newest)lastTradeTs=newest.ts;
    // guards
    const G={'COUNTER-TREND SKIP':'⛔ counter-trend','REGIME SKIP':'🌀 chop regime','DAY-TREND SKIP':'☀️ day no-trend',
      'MOM SKIP':'📉 momentum','REV COOLDOWN':'↩️ reversal cooldown','CORR DIVERGE':'🔗 divergent pair',
      'CORR SKIP':'🔗 corr duplicate','CORR HALF':'🔗 corr half-size','FLOW SKIP':'🌊 flow','NO CONFIRM':'🤝 no confirm','FILLED-RACE':'⚠️ cancel-race fill'};
    const g=d.guards_today||{},s=d.skips_today||{};
    let gh=Object.entries(g).map(([k,v])=>`<div class="kv"><span>${G[k]||k}</span><b>${v}</b></div>`).join('');
    gh+=`<div class="kv"><span class="dim">weak drift (no signal)</span><b class="dim">${s.weak_drift||0}</b></div>`;
    gh+=`<div class="kv"><span class="dim">price out of band</span><b class="dim">${s.ask_out_of_zone||0}</b></div>`;
    $('guards').innerHTML=gh||'<div class="dim">none yet</div>';
    // shadow coins
    $('shadow').innerHTML=Object.entries(d.shadow||{}).filter(([c])=>c==='BTC'||c==='XRP').map(([c,v])=>{
      if(!v.n)return `<div class="kv"><span>${c}</span><span class="dim">no data</span></div>`;
      const prog=Math.min(100,Math.round(v.n/80*100));
      const ok=v.gate?chip('GATE PASS ✓','cW'):(v.ev>0&&v.z>=1.64?chip(`needs n≥80 (${v.n})`,'cN'):chip('failing','cL'));
      return `<div style="margin-bottom:10px"><div class="kv" style="border:none"><span><b>${c}</b> · WR ${v.wr}% vs BE ${v.be}% · z ${v.z} · EV ${v.ev>0?'+':''}${v.ev}</span>${ok}</div>
        <div class="bar"><i style="width:${prog}%;background:${v.ev>0?'var(--cyan)':'var(--red)'}"></i></div>
        <div class="dim" style="font-size:11px;margin-top:2px">${v.n}/80 samples</div></div>`;}).join('');
    // history
    $('histTbl').querySelector('tbody').innerHTML=(d.day_history||[]).map(h=>`<tr>
      <td>${h.day.slice(5)}</td><td>${h.n}</td><td>${h.w}–${h.l}</td><td>${h.wr}%</td>
      <td class="${cls(h.pnl)}">${sgn(h.pnl)}</td></tr>`).join('');
    // splits
    const sp=d.splits_today||{};
    $('splits').innerHTML=Object.entries(sp).map(([k,v])=>`<div class="card" style="padding:8px 10px">
      <div class="lbl">${k}</div><div style="font-size:15px;font-weight:700">${v.wr}% <span class="dim">(${v.w}/${v.n})</span></div>
      <div class="${cls(v.pnl)}" style="font-size:12px">${sgn(v.pnl)}</div></div>`).join('')||'<span class="dim">no trades yet</span>';
    // dots
    $('dots').innerHTML=(d.recent||[]).map(x=>`<span style="width:14px;height:14px;border-radius:4px;display:inline-block;
      background:${x?'var(--green)':'var(--red)'}"></span>`).join('');
    // last signal
    const le=d.last_enter;
    $('lastEnter').innerHTML=le?`${le.ts.slice(5,16)} — ${le.coin} ${le.dir} @${le.ask}c, drift ${le.drift>0?'+':''}${le.drift}%`+
      (le.prob?`, prob ${le.prob}`:'')+`, T=${le.t}s`:'—';
  }catch(e){ const st=$('status'); st.className='pill DOWN'; $('statusTxt').textContent='API ERROR'; }
}

// ── live log ──
let logOffset=-1, paused=false, autoscroll=true, filter='all', search='';
function lineClass(l){
  if(/\[(WIN|LOSS|ENTER|FILLED|GTC|CANCEL)/.test(l))return /LOSS/.test(l)?'lg-l':'lg-t';
  if(/(SKIP|COOLDOWN|DIVERGE|CORR|STOP|BREAKER)/.test(l))return 'lg-g';
  if(/(RECONCILED|SYNC|STRIKE|PRUNE|ADAPT|start |model|feed)/.test(l))return 'lg-s';
  if(/(ERROR|FAIL|Traceback|error)/i.test(l))return 'lg-e';
  return 'lg-x';}
function lineCat(l){
  if(/\[(WIN|LOSS|ENTER|FILLED|GTC|CANCEL)/.test(l))return 'trade';
  if(/(SKIP|COOLDOWN|DIVERGE|CORR HALF|STOP|BREAKER|NO CONFIRM)/.test(l))return 'guard';
  if(/(ERROR|FAIL|Traceback)/i.test(l))return 'err';
  return 'sys';}
const buf=[];
async function pollLogs(){
  if(paused)return;
  try{
    const r=await fetch('/api/logs?since='+logOffset); const d=await r.json();
    logOffset=d.offset;
    for(const l of d.lines){buf.push(l); if(buf.length>1500)buf.shift();}
    renderLog();
  }catch(e){}
}
function renderLog(){
  const box=$('logbox');
  const vis=buf.filter(l=>(filter==='all'||lineCat(l)===filter)&&(!search||l.toLowerCase().includes(search)));
  box.innerHTML=vis.map(l=>`<div class="${lineClass(l)}">${l.replace(/</g,'&lt;')}</div>`).join('');
  $('logInfo').textContent=vis.length+' lines';
  if(autoscroll)box.scrollTop=box.scrollHeight;
}
document.querySelectorAll('[data-f]').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('[data-f]').forEach(x=>x.classList.remove('on'));
  b.classList.add('on'); filter=b.dataset.f; renderLog();});
$('logSearch').oninput=e=>{search=e.target.value.toLowerCase();renderLog();};
$('pauseBtn').onclick=()=>{paused=!paused;$('pauseBtn').textContent=paused?'▶ Resume':'⏸ Pause';
  $('pauseBtn').classList.toggle('on',paused);};
$('scrollBtn').onclick=()=>{autoscroll=!autoscroll;$('scrollBtn').classList.toggle('on',autoscroll);};

poll(); pollLogs();
setInterval(poll,4000); setInterval(pollLogs,3000);
setInterval(()=>{ // tick countdowns locally between polls
  document.querySelectorAll('.countdown').forEach(el=>{
    const p=el.textContent.trim().split(':'); if(p.length!==2)return;
    let t=(+p[0])*60+(+p[1]); if(t>0)t--;
    el.textContent=' '+Math.floor(t/60)+':'+(t%60+'').padStart(2,'0');});
},1000);
</script></body></html>"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8095, threaded=True)

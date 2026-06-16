"""Control-center API for the v3 dashboard.

Adds the capabilities the base dashboard lacks:
  - GET/POST /api/v3/config   read + safely WRITE whitelisted .env knobs
  - POST     /api/v3/control/<target>/<action>  restart faithbot / sniper / dashboard
  - GET      /api/v3/perf     REAL account performance (chain-reconciled) + equity
  - GET      /api/v3/sniper   window-reset sniper dry-run status + recent events
  - GET      /control         the new control-center single-page UI

Registered from app.py via control_api.register(app). Secrets are never read or
written — only the curated KNOBS list is editable. If DASH_TOKEN is set in the
env, write/control endpoints require ?token= (read endpoints stay open).
"""
from __future__ import annotations
import os, re, csv, json, time, subprocess, datetime
from pathlib import Path
from collections import defaultdict
from flask import jsonify, request, render_template

BOT_DIR = Path("/home/ubuntu/v3-bot")
ENV = BOT_DIR / ".env"

# (key, label, type, group) — the ONLY keys the dashboard may edit. No secrets.
KNOBS = [
    ("BANKROLL_BALANCE", "Bankroll $", "num", "Risk"),
    ("SIZE_MAX_USD", "Max $ / bet", "num", "Sizing"),
    ("SIZE_GLOBAL_MULT", "Global size ×", "num", "Sizing"),
    ("COIN_MULT_BTC", "BTC size ×", "num", "Sizing"),
    ("COIN_MULT_ETH", "ETH size ×", "num", "Sizing"),
    ("COIN_MULT_SOL", "SOL size ×", "num", "Sizing"),
    ("COIN_MULT_XRP", "XRP size ×", "num", "Sizing"),
    ("MIN_EDGE_THRESHOLD", "Min edge (0-1)", "num", "Gates"),
    ("MIN_WIN_PROB", "Min win prob (0-1)", "num", "Gates"),
    ("ROC60_DESIZE_ON", "roc60 chase de-size", "bool", "Gates"),
    ("FRESH_OVERSHOOT_ON", "fresh-overshoot guard", "bool", "Gates"),
    ("EMP_SHRINK_ON", "empirical shrink", "bool", "Gates"),
    ("DAILY_RECONCILE_ON", "chain P&L reconcile", "bool", "Gates"),
    ("SNIPE_MAX_ENTRY", "Sniper max entry (≤)", "num", "Sniper"),
    ("SNIPE_SHARP_DIST", "Sniper sharp dist", "num", "Sniper"),
    ("SNIPE_WINDOW", "Sniper window (s)", "num", "Sniper"),
]
KNOB_KEYS = {k for k, *_ in KNOBS}


def _auth_ok() -> bool:
    tok = os.getenv("DASH_TOKEN", "")
    if not tok:
        return True
    return request.args.get("token") == tok or request.headers.get("X-Dash-Token") == tok


def _read_env() -> dict:
    d = {}
    if ENV.exists():
        for ln in ENV.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = ln.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                d[k.strip()] = v.strip()
    return d


def _write_env_key(key: str, val: str):
    if key not in KNOB_KEYS:
        raise ValueError(f"{key} is not editable")
    val = str(val).strip().replace("\n", "").replace("\r", "")
    if not re.fullmatch(r"[A-Za-z0-9_.+\-]{1,32}", val):
        raise ValueError("invalid value")
    lines = ENV.read_text(encoding="utf-8", errors="ignore").splitlines() if ENV.exists() else []
    out, found = [], False
    for ln in lines:
        s = ln.strip()
        if s and not s.startswith("#") and "=" in s and s.split("=", 1)[0].strip() == key:
            out.append(f"{key}={val}")
            found = True
        else:
            out.append(ln)
    if not found:
        out.append(f"{key}={val}")
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    (BOT_DIR / f".env.bak_dash_{stamp}").write_text("\n".join(lines), encoding="utf-8")
    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")


def _pids(pattern: str):
    try:
        out = subprocess.check_output(["pgrep", "-f", pattern], text=True).strip()
        return [int(x) for x in out.splitlines() if x]
    except subprocess.CalledProcessError:
        return []


TARGETS = {
    "faithbot": (r"^python3 -u run_bot\.py", "setsid nohup python3 -u run_bot.py >> v3_bot.log 2>&1 < /dev/null &"),
    "sniper": (r"sniper_dryrun\.py", "setsid nohup python3 -u sniper_dryrun.py >> logs/sniper_dryrun_stdout.log 2>&1 < /dev/null &"),
}


def _control(target: str, action: str) -> dict:
    if target not in TARGETS:
        return {"ok": False, "msg": f"unknown target {target}"}
    pat, startcmd = TARGETS[target]
    pids = _pids(pat)
    if action in ("stop", "restart"):
        for pid in pids:
            try:
                os.kill(pid, 15)
            except Exception:
                pass
        time.sleep(2)
    if action in ("start", "restart"):
        # never run two faithbots
        if target == "faithbot" and _pids(pat):
            return {"ok": False, "msg": "faithbot still running; stop first"}
        subprocess.Popen(["bash", "-lc", f"cd {BOT_DIR} && {startcmd}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
    return {"ok": True, "target": target, "action": action, "pids": _pids(pat)}


def _perf() -> dict:
    out = {"today": None, "equity": [], "by_coin": {}, "lifetime": None}
    # today real (chain)
    try:
        import sys
        sys.path.insert(0, str(BOT_DIR))
        import daily_reconcile as dr
        rec = dr.realized_pnl_today()
        if rec:
            wp, lc, net = rec
            out["today"] = {"win_profit": wp, "loss_cost": lc, "net": net}
    except Exception as e:
        out["today_err"] = str(e)
    # equity curve + by-coin from poly_reconciled.csv
    try:
        rows = []
        with open(BOT_DIR / "data" / "poly_reconciled.csv") as f:
            for r in csv.DictReader(f):
                try:
                    r["pnl"] = float(r["pnl"])
                except Exception:
                    continue
                rows.append(r)
        days = defaultdict(float)
        coins = defaultdict(lambda: [0, 0, 0.0])
        bands = defaultdict(lambda: [0, 0, 0.0])
        n = w = 0
        net = 0.0
        wins_amt = []
        loss_amt = []

        def band_of(p):
            try:
                p = float(p)
            except Exception:
                return "?"
            for lo in (30, 40, 50, 60, 70, 80):
                if p < (lo + 10) / 100.0:
                    return f"{lo}-{lo+10}c"
            return "80c+"
        for r in rows:
            days[r["day"]] += r["pnl"]
            won = r["result"] == "WIN"
            for tbl, key in ((coins, r["coin"]), (bands, band_of(r.get("avg")))):
                t = tbl[key]
                t[0] += 1
                t[1] += 1 if won else 0
                t[2] += r["pnl"]
            n += 1
            w += 1 if won else 0
            net += r["pnl"]
            (wins_amt if won else loss_amt).append(r["pnl"])
        cum = 0.0
        eq = []
        peak = 0.0
        max_dd = 0.0
        for d in sorted(days):
            cum += days[d]
            peak = max(peak, cum)
            max_dd = min(max_dd, cum - peak)
            eq.append({"day": d, "net": round(days[d], 2), "cum": round(cum, 2)})
        # current win/loss streak (most recent trades)
        streak = 0
        for r in reversed(rows):
            s = 1 if r["result"] == "WIN" else -1
            if streak == 0 or (streak > 0) == (s > 0):
                streak += s
            else:
                break
        daysort = sorted(days.items(), key=lambda x: x[1])
        out["equity"] = eq[-90:]
        out["daily"] = [{"day": d, "net": round(v, 2)} for d, v in sorted(days.items())][-30:]
        out["by_coin"] = {k: {"n": v[0], "wr": round(100 * v[1] / v[0], 1) if v[0] else 0,
                              "net": round(v[2], 2)} for k, v in coins.items()}
        out["by_band"] = {k: {"n": v[0], "wr": round(100 * v[1] / v[0], 1) if v[0] else 0,
                              "net": round(v[2], 2)} for k, v in sorted(bands.items())}
        out["lifetime"] = {"n": n, "wr": round(100 * w / n, 1) if n else 0, "net": round(net, 2)}
        out["stats"] = {
            "avg_win": round(sum(wins_amt) / len(wins_amt), 2) if wins_amt else 0,
            "avg_loss": round(sum(loss_amt) / len(loss_amt), 2) if loss_amt else 0,
            "best_day": {"day": daysort[-1][0], "net": round(daysort[-1][1], 2)} if daysort else None,
            "worst_day": {"day": daysort[0][0], "net": round(daysort[0][1], 2)} if daysort else None,
            "max_drawdown": round(max_dd, 2),
            "streak": streak,
            "n_days": len(days),
        }
    except Exception as e:
        out["equity_err"] = str(e)
    return out


def _sniper() -> dict:
    log = BOT_DIR / "logs" / "sniper_dryrun.log"
    running = bool(_pids(r"sniper_dryrun\.py"))
    res = {"running": running, "snipes": [], "near": 0, "snipe_count": 0,
           "settled": {"w": 0, "l": 0, "net": 0.0}, "recent": []}
    if not log.exists():
        return res
    try:
        lines = log.read_text(encoding="utf-8", errors="ignore").splitlines()[-400:]
        for ln in lines:
            if "[SNIPE]" in ln:
                res["snipe_count"] += 1
                res["snipes"].append(ln.split("|", 0)[0][:11] + ln.split(" - ")[-1][:120])
            elif "[NEAR]" in ln:
                res["near"] += 1
            elif "[SETTLE]" in ln:
                if "WIN" in ln:
                    res["settled"]["w"] += 1
                elif "LOSS" in ln:
                    res["settled"]["l"] += 1
                m = re.search(r"net=\$([-+\d.]+)", ln)
                if m:
                    res["settled"]["net"] = float(m.group(1))
        res["recent"] = [l.split(" - ")[-1] for l in lines if any(t in l for t in ("[SNIPE]", "[NEAR]", "[SETTLE]", "[STATUS]"))][-25:]
    except Exception as e:
        res["err"] = str(e)
    return res


def _scout():
    """Daily-threshold scout (Option 2): BS vol-model vs market on daily
    'above $X' markets. Parses logs/daily_scout.log."""
    log = BOT_DIR / "logs" / "daily_scout.log"
    res = {"running": bool(_pids(r"daily_scout")), "atm": {}, "flags": [],
           "results": [], "recent": [], "brier": None}
    if not log.exists():
        return res
    try:
        lines = log.read_text(encoding="utf-8", errors="ignore").splitlines()[-600:]
        for ln in lines:
            if "[ATM]" in ln:
                m = re.search(r"\[ATM\] (\w+) hv=([\d.]+)%/h .*?\$([\d,]+): model (\d+)% vs mkt (\d+)% \(edge ([-+]\d+)%\)", ln)
                if m:
                    res["atm"][m.group(1)] = {"hv": float(m.group(2)), "strike": m.group(3),
                                              "model": int(m.group(4)), "market": int(m.group(5)),
                                              "edge": int(m.group(6))}
            elif "[SCOUT]" in ln:
                res["flags"].append(ln.split(" - ")[-1])
            elif "[RESULT]" in ln:
                res["results"].append(ln.split(" - ")[-1])
                mb = re.search(r"model_brier=([\d.]+) mkt_brier=([\d.]+) \((\d+)/(\d+)", ln)
                if mb:
                    res["brier"] = {"model": float(mb.group(1)), "market": float(mb.group(2)),
                                    "better": int(mb.group(3)), "n": int(mb.group(4))}
        res["recent"] = [l.split(" - ")[-1] for l in lines
                         if any(t in l for t in ("[SCOUT]", "[ATM]", "[RESULT]", "[STATUS]"))][-30:]
        res["flags"] = res["flags"][-15:]
    except Exception as e:
        res["err"] = str(e)
    return res


_lm = {"ts": 0.0, "data": []}


def _livemarket():
    """Live current-window quotes per coin: strike, spot, distance, UP/DOWN
    best asks, time-left. CLOB + Binance.us are directly reachable from the
    EC2 (only binance.com is geo-blocked). Cached 4s."""
    if time.time() - _lm["ts"] < 4 and _lm["data"]:
        return _lm["data"]
    import sys
    if str(BOT_DIR) not in sys.path:
        sys.path.insert(0, str(BOT_DIR))
    import httpx
    try:
        import market_data
        import poly_resolution as pr
    except Exception as e:
        return [{"coin": "?", "err": str(e)}]
    SYM = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}
    bcli = httpx.Client(timeout=5)

    def bpx(c):
        for host in ("https://api.binance.us", "https://api.binance.com"):
            try:
                jj = bcli.get(host + "/api/v3/ticker/price", params={"symbol": SYM[c]}).json()
                if "price" in jj:
                    return float(jj["price"])
            except Exception:
                pass
        return None

    def ask(tok):
        try:
            r = httpx.get("https://clob.polymarket.com/book", params={"token_id": tok}, timeout=8)
            a = [float(x["price"]) for x in (r.json().get("asks") or []) if float(x.get("size", 0)) > 0]
            return round(min(a) * 100) if a else None
        except Exception:
            return None

    now = int(time.time())
    ws = (now // 900) * 900
    tleft = ws + 900 - now
    out = []
    for c in ("BTC", "ETH", "SOL", "XRP"):
        row = {"coin": c, "time_left": tleft, "window_age": now - ws}
        try:
            strike = market_data.get_threshold_from_binance(c, ws, "15m")
        except Exception:
            strike = None
        px = bpx(c)
        row["strike"] = round(strike, 2) if strike else None
        row["price"] = round(px, 2) if px else None
        if strike and px:
            row["dist_pct"] = round((px - strike) / strike * 100, 3)
            row["leader"] = "UP" if px >= strike else "DOWN"
        try:
            m = pr.fetch_market_by_slug(f"{c.lower()}-updown-15m-{ws}")
            toks = json.loads(m.get("clobTokenIds") or "[]") if m else []
            if len(toks) >= 2:
                row["up_ask"] = ask(toks[0])
                row["down_ask"] = ask(toks[1])
        except Exception:
            pass
        out.append(row)
    _lm.update(ts=time.time(), data=out)
    return out


def register(app):
    @app.route("/control")
    def control_page():
        return render_template("control.html")

    @app.route("/api/v3/livemarket")
    def livemarket():
        return jsonify({"coins": _livemarket(), "ts": time.time()})

    @app.route("/api/v3/config")
    def cfg_get():
        env = _read_env()
        items = [{"key": k, "label": lbl, "type": t, "group": g, "value": env.get(k, "")}
                 for k, lbl, t, g in KNOBS]
        return jsonify({"knobs": items})

    @app.route("/api/v3/config", methods=["POST"])
    def cfg_set():
        if not _auth_ok():
            return jsonify({"ok": False, "msg": "unauthorized"}), 401
        data = request.get_json(force=True, silent=True) or {}
        changed, errors = [], []
        for k, v in data.items():
            try:
                _write_env_key(k, v)
                changed.append(k)
            except Exception as e:
                errors.append(f"{k}: {e}")
        return jsonify({"ok": not errors, "changed": changed, "errors": errors,
                        "note": "restart the bot to apply"})

    @app.route("/api/v3/control/<target>/<action>", methods=["POST"])
    def control(target, action):
        if not _auth_ok():
            return jsonify({"ok": False, "msg": "unauthorized"}), 401
        if action not in ("start", "stop", "restart"):
            return jsonify({"ok": False, "msg": "bad action"}), 400
        return jsonify(_control(target, action))

    @app.route("/api/v3/perf")
    def perf():
        return jsonify(_perf())

    @app.route("/api/v3/sniper")
    def sniper():
        return jsonify(_sniper())

    @app.route("/api/v3/scout")
    def scout():
        return jsonify(_scout())

    return app

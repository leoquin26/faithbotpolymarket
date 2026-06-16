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
        n = w = 0
        net = 0.0
        for r in rows:
            days[r["day"]] += r["pnl"]
            c = coins[r["coin"]]
            c[0] += 1
            c[1] += 1 if r["result"] == "WIN" else 0
            c[2] += r["pnl"]
            n += 1
            w += 1 if r["result"] == "WIN" else 0
            net += r["pnl"]
        cum = 0.0
        eq = []
        for d in sorted(days):
            cum += days[d]
            eq.append({"day": d, "net": round(days[d], 2), "cum": round(cum, 2)})
        out["equity"] = eq[-60:]
        out["by_coin"] = {k: {"n": v[0], "wr": round(100 * v[1] / v[0], 1) if v[0] else 0,
                              "net": round(v[2], 2)} for k, v in coins.items()}
        out["lifetime"] = {"n": n, "wr": round(100 * w / n, 1) if n else 0, "net": round(net, 2)}
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


def register(app):
    @app.route("/control")
    def control_page():
        return render_template("control.html")

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

    return app

#!/usr/bin/env python3
"""QUANTUM DATA CONTROL — read-only dashboard over every dataset the project
collects. FastAPI on localhost:8097 (cloudflared quick-tunnel in front, same
HTTP Basic credentials as the Quantum Desk). Places no orders, writes nothing
to bot state — a pure observer over the data product.

Endpoints:
  /                    control room UI (quantum_ui/data.html)
  /api/inventory       per-dataset rows, size, span, growth
  /api/hourly/metrics  calibration + seat ROI by t_left/band/week (validated suite)
  /api/ledger          hour_bot cycles, pooled stats, equity curve
  /api/health          liveness
Analytics recompute in a background thread every 15 min; endpoints serve cache.
"""
import csv, json, math, os, secrets, threading, time
from collections import defaultdict

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:
    pass

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import JSONResponse, PlainTextResponse
import uvicorn

V3 = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(V3, "quantum_ui", "data.html")
STATS = os.path.join(V3, "data_control_stats.json")   # growth snapshots

DASH_USER = os.getenv("QUANTUM_DASH_USER", "leo")
DASH_PASS = os.getenv("QUANTUM_DASH_PASS", "")

DATASETS = {
    "hourly_research.csv":   {"kind": "csv",   "ts": "ts",  "desc": "30s bid/ask snapshots, 1h windows + winners"},
    "late_book.jsonl":       {"kind": "jsonl", "ts": "ts",  "desc": "1Hz top-2 book depth, 15m windows"},
    "clean_bot_research.csv": {"kind": "csv",  "ts": None,  "desc": "15m per-window features + outcomes (closed era)"},
    "wallet_trades.csv":     {"kind": "csv",   "ts": None,  "desc": "participant census, 6,754 wallets, cash-flow P&L"},
    "hour_bot_state.json":   {"kind": "json",  "ts": None,  "desc": "audited 1H maker ledger, 4 cycles, 158 settles"},
}

app = FastAPI()
security = HTTPBasic(auto_error=True)
_cache = {"inventory": None, "hourly": None, "ledger": None, "built": 0}
_lock = threading.Lock()


def _check(cred: HTTPBasicCredentials = Depends(security)):
    if not DASH_PASS:
        return True
    ok = (secrets.compare_digest(cred.username, DASH_USER)
          and secrets.compare_digest(cred.password, DASH_PASS))
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad credentials",
                            {"WWW-Authenticate": "Basic"})
    return True


# ---------- inventory ----------

def _count_lines(path):
    n = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            n += chunk.count(b"\n")
    return n


def _first_last_ts(path, kind):
    try:
        with open(path, "rb") as f:
            head = f.readline()
            first = f.readline().decode(errors="replace")
            f.seek(max(0, os.path.getsize(path) - 4096))
            last = f.read().decode(errors="replace").strip().splitlines()[-1]
        if kind == "jsonl":
            first = head.decode(errors="replace")  # jsonl has no header
            return (json.loads(first).get("ts"), json.loads(last).get("ts"))
        return (first.split(",")[0] or None, last.split(",")[0] or None)
    except Exception:
        return (None, None)


def _inventory():
    hist = {}
    try:
        hist = json.load(open(STATS))
    except Exception:
        pass
    now = time.time()
    out = []
    for name, meta in DATASETS.items():
        p = os.path.join(V3, name)
        if not os.path.exists(p):
            out.append({"name": name, "missing": True, "desc": meta["desc"]})
            continue
        size = os.path.getsize(p)
        rows = _count_lines(p) if meta["kind"] != "json" else None
        f_ts = l_ts = None
        if meta["ts"]:
            f_ts, l_ts = _first_last_ts(p, meta["kind"])
        prev = hist.get(name)
        growth = None
        if prev and rows is not None and now > prev["t"] + 3600:
            growth = round((rows - prev["rows"]) / (now - prev["t"]) * 86400)
        hist[name] = {"t": now, "rows": rows or 0}
        fresh = (now - os.path.getmtime(p)) < 600
        out.append({"name": name, "desc": meta["desc"], "rows": rows,
                    "mb": round(size / 1048576, 1), "first": f_ts, "last": l_ts,
                    "growth_day": growth, "fresh": fresh})
    try:
        json.dump(hist, open(STATS, "w"))
    except Exception:
        pass
    return out


# ---------- hourly metrics (the validated suite) ----------

COINS = {"BTC", "ETH", "SOL"}

def _hourly_metrics():
    winners, quotes = {}, defaultdict(list)
    path = os.path.join(V3, "hourly_research.csv")
    for r in csv.DictReader(open(path)):
        if r["coin"] not in COINS:
            continue
        hs = int(r["hour_start"])
        if r["winner"]:
            winners[(hs, r["coin"])] = r["winner"]
            continue
        try:
            t = int(r["t_left"])
            ua, ub = float(r["up_ask"]), float(r["up_bid"])
            da, db = float(r["down_ask"]), float(r["down_bid"])
        except ValueError:
            continue
        quotes[(hs, r["coin"])].append((t, ua, ub, da, db))

    calib = defaultdict(lambda: [0, 0])          # ask bucket -> [n, wins]
    tleft_roi = defaultdict(list)                # t bucket -> [(px, won)]
    band_roi = defaultdict(list)                 # band -> [(px, won)]
    weekly = defaultdict(list)                   # weeks ago -> [(px, won)]
    now = time.time()

    for (hs, coin), rows in quotes.items():
        wn = winners.get((hs, coin))
        if not wn:
            continue
        rows.sort(key=lambda x: -x[0])
        entered_seat = False
        seen_tbucket = set()
        for t, ua, ub, da, db in rows:
            if ua <= 0 or da <= 0:
                continue
            if ua > da:
                fav, ask, bid = "UP", ua, ub
            elif da > ua:
                fav, ask, bid = "DOWN", da, db
            else:
                continue
            # calibration: favourite ask vs outcome, once per (window, 5c bucket)
            cb = int(ask * 20) / 20
            key = (cb, id(None))
            calib[cb][0] += 1
            calib[cb][1] += 1 if wn == fav else 0
            # seat sims need band + mid guard
            if not (0.55 <= ask <= 0.85) or (bid + ask) / 2 < 0.55:
                continue
            px = min(bid + 0.01, ask - 0.01)
            tb = min(5, max(0, int(t // 600)))   # 0-600..3000-3600 buckets
            if tb not in seen_tbucket:
                seen_tbucket.add(tb)
                tleft_roi[tb].append((px, wn == fav))
            if not entered_seat and 1800 <= t <= 3300:
                entered_seat = True
                band = "55-65c" if ask < 0.65 else ("65-75c" if ask < 0.75 else "75-85c")
                band_roi[band].append((px, wn == fav))
                weekly[int((now - hs) // (7 * 86400))].append((px, wn == fav))

    def roi(pairs):
        if not pairs:
            return None
        risked = sum(p for p, _ in pairs)
        pnl = sum((1 - p) if w else -p for p, w in pairs)
        return {"n": len(pairs), "roi": round(pnl / risked, 4),
                "win": round(sum(1 for _, w in pairs if w) / len(pairs), 3)}

    return {
        "calibration": [{"ask": round(k, 2), "n": v[0],
                         "actual": round(v[1] / v[0], 3)}
                        for k, v in sorted(calib.items()) if v[0] >= 30],
        "roi_by_tleft": {f"{b*10}-{b*10+10}min": roi(v)
                         for b, v in sorted(tleft_roi.items())},
        "roi_by_band": {k: roi(v) for k, v in sorted(band_roi.items())},
        "weekly_seat": [{"weeks_ago": k, **(roi(v) or {})}
                        for k, v in sorted(weekly.items()) if v][:8],
    }


# ---------- ledger ----------

def _ledger():
    try:
        s = json.load(open(os.path.join(V3, "hour_bot_state.json")))
    except Exception:
        return None
    def real(bets):
        return [x for x in bets if x.get("px", 0) > 0 and x.get("sh", 0) > 0]
    cycles, allb = [], []
    for i, cn in enumerate(("cycle1", "cycle2", "cycle3", "cycle4"), 1):
        c = s.get(cn)
        if not c:
            continue
        b = real(c["bets"])
        allb += b
        w = sum(1 for x in b if x["won"])
        cycles.append({"cycle": i, "n": len(b), "w": w, "l": len(b) - w,
                       "net": round(c.get("net", 0), 2)})
    cur = real(s["bets"])
    if cur:
        allb += cur
        w = sum(1 for x in cur if x["won"])
        cycles.append({"cycle": len(cycles) + 1, "n": len(cur), "w": w,
                       "l": len(cur) - w, "net": round(s.get("net", 0), 2),
                       "live": not s.get("done")})
    allb.sort(key=lambda x: x["hs"])
    eq, run = [], 0.0
    for b in allb:
        run += b["pnl"]
        eq.append({"hs": b["hs"], "eq": round(77.84 + run, 2)})
    W = sum(1 for x in allb if x["won"])
    risked = sum(x["px"] * x["sh"] for x in allb)
    net = sum(x["pnl"] for x in allb)
    var = sum(x["px"] * (1 - x["px"]) for x in allb)
    z = (W - sum(x["px"] for x in allb)) / math.sqrt(var) if var else 0
    return {"cycles": cycles, "done": s.get("done") or "",
            "pooled": {"n": len(allb), "w": W, "net": round(net, 2),
                       "ev": round(net / risked, 4) if risked else None,
                       "z": round(z, 2)},
            "equity": eq, "launch": 77.84}


# ---------- refresh loop + endpoints ----------

def _refresh():
    while True:
        try:
            inv = _inventory()
            hm = _hourly_metrics()
            led = _ledger()
            with _lock:
                _cache.update(inventory=inv, hourly=hm, ledger=led,
                              built=time.time())
        except Exception as e:
            print("refresh error:", e, flush=True)
        time.sleep(900)


@app.get("/api/inventory")
def api_inventory(_=Depends(_check)):
    with _lock:
        return JSONResponse({"built": _cache["built"], "datasets": _cache["inventory"]})


@app.get("/api/hourly/metrics")
def api_hourly(_=Depends(_check)):
    with _lock:
        return JSONResponse({"built": _cache["built"], **(_cache["hourly"] or {})})


@app.get("/api/ledger")
def api_ledger(_=Depends(_check)):
    with _lock:
        return JSONResponse({"built": _cache["built"], **(_cache["ledger"] or {})})


@app.get("/api/health")
def api_health():
    with _lock:
        age = time.time() - _cache["built"] if _cache["built"] else None
    return {"ok": True, "cache_age_s": round(age) if age else None}


@app.get("/", response_class=PlainTextResponse)
def index(_=Depends(_check)):
    try:
        return PlainTextResponse(open(UI, encoding="utf-8").read(),
                                 media_type="text/html")
    except Exception:
        return PlainTextResponse("data control UI missing", status_code=500)


@app.on_event("startup")
async def _startup():
    threading.Thread(target=_refresh, daemon=True).start()


if __name__ == "__main__":
    print("[data-control] localhost:8097 (tunnel mode)", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=8097, log_level="warning")

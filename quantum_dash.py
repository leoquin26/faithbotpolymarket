#!/usr/bin/env python3
"""Quantum Desk — real-time CleanBot dashboard backend (read-only).
FastAPI + WebSocket. Streams: raw log tail, engine/state snapshots (2s), live
prices + window clock (1s). REST: /api/history, /api/health. Serves quantum_ui/.

SECURITY: public exposure is gated by HTTP Basic auth (QUANTUM_DASH_PASS in .env)
on EVERY route, a per-session token on the websocket, and TLS. Binds publicly on
8443 with TLS only when both cert files exist; otherwise localhost:8096 plaintext
(tunnel mode). Places NO orders, writes NOTHING to bot state — a pure observer."""
import asyncio, json, os, re, secrets, subprocess, threading, time
from collections import deque

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:
    pass

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, PlainTextResponse
import uvicorn

import binance_ws

V3 = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(V3, "clean_bot.log")
STATE = os.path.join(V3, "clean_bot_state.json")
HOUR_LOG = os.path.join(V3, "hour_bot.log")
HOUR_STATE = os.path.join(V3, "hour_bot_state.json")
HOUR_BRAIN = os.path.join(V3, "hour_bot_brain.json")
MICRO_STATE = os.path.join(V3, "micro_bot_state.json")
MICRO_BRAIN = os.path.join(V3, "micro_bot_brain.json")
MICRO_LOG = os.path.join(V3, "micro_bot.log")
BALANCE = os.path.join(V3, "balance.json")
UI = os.path.join(V3, "quantum_ui")
CERT = os.path.join(V3, "quantum_cert.pem")
KEY = os.path.join(V3, "quantum_key.pem")
COINS = ("BTC", "ETH", "SOL")
ENGINES = ("fav", "late", "hiband", "early", "voldiv")

DASH_USER = os.getenv("QUANTUM_DASH_USER", "leo")
DASH_PASS = os.getenv("QUANTUM_DASH_PASS", "")     # empty ⇒ auth disabled (tunnel/localhost only)

app = FastAPI()
security = HTTPBasic(auto_error=True)
_clients: set = set()
_loop: asyncio.AbstractEventLoop = None
_lock = threading.Lock()
_price_hist = {c: deque(maxlen=900) for c in COINS}
_wstokens = deque(maxlen=64)                        # valid websocket session tokens


def _check(cred: HTTPBasicCredentials = Depends(security)):
    """Constant-time Basic-auth guard. No-op when DASH_PASS is unset (tunnel mode)."""
    if not DASH_PASS:
        return True
    ok = (secrets.compare_digest(cred.username, DASH_USER)
          and secrets.compare_digest(cred.password, DASH_PASS))
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad credentials",
                            {"WWW-Authenticate": "Basic"})
    return True


def _broadcast(msg: dict):
    if _loop is None:
        return
    data = json.dumps(msg)
    with _lock:
        targets = list(_clients)
    for ws in targets:
        try:
            asyncio.run_coroutine_threadsafe(ws.send_text(data), _loop)
        except Exception:
            pass


LOG_CLASS = (
    (re.compile(r"\[(WIN|LOSS)\]"), "result"),
    (re.compile(r"\[LATE ENTER\]|\[FAV ENTER\]|\[FILLED"), "enter"),
    (re.compile(r"\[VERDICT|\[TRACK"), "verdict"),
    (re.compile(r"\[LATE SKIP\]|SKIP:"), "skip"),
    (re.compile(r"\[SIZE->BOOK\]|\[LATE MISS\]|\[LATE EVAL MISSED\]"), "exec"),
    # v1.61.0 maker era: resting-order lifecycle gets its own class in the console
    (re.compile(r"\[GTC\]|\[CANCEL\]|\[LATE ORDER"), "maker"),
    # hour-bot audition lifecycle (separate process, same console)
    (re.compile(r"\[REST\]|\[HOUR VERDICT\]|\[FILLED-RACE\]"), "maker"),
)


def _classify(line: str) -> str:
    for rx, cls in LOG_CLASS:
        if rx.search(line):
            return cls
    return "sys"


def _tail_file(path, prefix=""):
    pos = os.path.getsize(path) if os.path.exists(path) else 0
    while True:
        try:
            size = os.path.getsize(path) if os.path.exists(path) else 0
            if size < pos:
                pos = 0
            if size > pos:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(pos)
                    chunk = f.read(min(size - pos, 262144))
                    pos = f.tell()
                for ln in chunk.splitlines():
                    ln = ln.strip()
                    # keep only full "YYYY-MM-DD …" lines (skips the short-ts stdout echo)
                    if ln and ln[:2] == "20" and ln[4:5] == "-":
                        _broadcast({"t": "log", "cls": _classify(ln),
                                    "line": (prefix + ln)[:500]})
        except Exception:
            pass
        time.sleep(0.4)


def _tail_log():
    _tail_file(LOG)


def _tail_hour_log():
    _tail_file(HOUR_LOG, prefix="[1H] ")


def _tail_micro_log():
    _tail_file(MICRO_LOG, prefix="[MICRO] ")


def _engines_from_state(s: dict) -> list:
    ev = s.get("recent_ev") or []
    mult = s.get("engine_mult") or {}
    off = s.get("engine_off") or {}
    out = []
    for tag in ENGINES:
        rows = [x for x in ev if len(x) > 3 and x[3] == tag]
        n = len(rows)
        w = sum(x[0] for x in rows)
        stk = sum(x[2] for x in rows)
        net = sum(x[1] for x in rows)
        m = float(mult.get(tag, 1.0))
        status = ("RETIRED" if off.get(tag)
                  else f"SCALED x{m:.0f}" if m > 1 else "MEASURING")
        out.append({"engine": tag, "n": n, "wins": w,
                    "wr": round(100 * w / n, 1) if n else None,
                    "net": round(net, 2),
                    "ev": round(net / stk, 3) if stk else None,
                    "mult": m, "off": bool(off.get(tag)), "status": status})
    return out


def _poll_state():
    while True:
        try:
            s = json.load(open(STATE, encoding="utf-8"))
            opens = []
            for p in (s.get("positions") or {}).values():
                if p.get("status") == "filled":
                    opens.append({"coin": p.get("coin"), "dir": p.get("dir"),
                                  "entry": p.get("entry"), "shares": p.get("shares"),
                                  "ws": p.get("ws"), "hiband": bool(p.get("hiband"))})
            # v1.61.0 maker era: resting GTC orders, so the dashboard shows the full
            # post -> fill/cancel lifecycle per window (cancel is at t_rem<90s)
            resting = []
            for o in (s.get("open_orders") or {}).values():
                resting.append({"coin": o.get("coin"), "dir": o.get("dir"),
                                "px": o.get("price"), "shares": o.get("shares"),
                                "ws": o.get("ws"), "hiband": bool(o.get("hiband"))})
            # HOURLY AUDIT (hour_bot.py — separate process, own state file)
            hour = None
            try:
                h = json.load(open(HOUR_STATE, encoding="utf-8"))
                cycles = []
                for cn in ("cycle1", "cycle2", "cycle3", "cycle4"):
                    c = h.get(cn)
                    if c:
                        cb = [x for x in c.get("bets", []) if x.get("sh", 0) > 0]
                        cw = sum(1 for x in cb if x.get("won"))
                        cycles.append({"name": cn, "n": len(cb), "w": cw,
                                       "net": round(c.get("net", 0.0), 2)})
                # hour_bot's own final run is cycle 4's ledger (retired Aug 12)
                hb = [x for x in (h.get("bets") or []) if x.get("sh", 0) > 0]
                if hb:
                    cycles.append({"name": f"cycle{len(cycles)+1}",
                                   "n": len(hb),
                                   "w": sum(1 for x in hb if x.get("won")),
                                   "net": round(h.get("net", 0.0), 2)})
                # live meter = micro_bot (cycle 5, the 75-85c seat) when present
                live = {"bets": [], "net": 0.0, "open": None, "order": None,
                        "done": ""}
                try:
                    live = json.load(open(MICRO_STATE, encoding="utf-8"))
                except Exception:
                    pass
                bets = [x for x in (live.get("bets") or []) if x.get("sh", 0) > 0]
                hn = len(bets)
                hw = sum(1 for b in bets if b.get("won"))
                stk = sum(b.get("px", 0) * b.get("sh", 0) for b in bets)
                wallet = None
                try:
                    wallet = json.load(open(BALANCE, encoding="utf-8"))
                except Exception:
                    pass
                hour = {"n": hn, "w": hw, "l": hn - hw,
                        "net": round(live.get("net", 0.0), 2),
                        "ev": round(live.get("net", 0.0) / stk, 3) if stk else None,
                        "open": live.get("open"), "order": live.get("order"),
                        "cycles": cycles, "wallet": wallet, "micro": True,
                        "done": live.get("done") or ""}
            except Exception:
                pass
            brain = None
            try:
                brain = json.load(open(MICRO_BRAIN, encoding="utf-8"))
            except Exception:
                try:
                    brain = json.load(open(HOUR_BRAIN, encoding="utf-8"))
                except Exception:
                    pass
            _broadcast({"t": "state",
                        "brain": brain,
                        "bankroll": s.get("bankroll"),
                        "day_net": round((s.get("wins") or 0) - (s.get("losses") or 0), 2),
                        "killed": bool(s.get("killed")),
                        "open_positions": len(opens), "open": opens,
                        "resting": resting,
                        "hour": hour,
                        "engines": _engines_from_state(s)})
        except Exception:
            pass
        time.sleep(2.0)


def _poll_prices():
    while True:
        now = time.time()
        coins = {}
        for c in COINS:
            try:
                px = binance_ws.get_price(c)
            except Exception:
                px = None
            if px:
                _price_hist[c].append((now, float(px)))
            h = _price_hist[c]
            def chg(sec):
                if len(h) < 2:
                    return None
                base = None
                for ts, p in h:
                    if now - ts <= sec:
                        base = p
                        break
                return round((h[-1][1] - base) / base * 1e4, 1) if base else None
            spark = [round(p, 6) for _, p in list(h)[-240::3]]
            coins[c] = {"px": h[-1][1] if h else None,
                        "chg60": chg(60), "chg300": chg(300), "spark": spark}
        ws_epoch = int(now // 3600) * 3600            # 1H markets are MAIN now
        t_rem = int(ws_epoch + 3600 - now)
        _broadcast({"t": "prices", "coins": coins,
                    "window": {"start": ws_epoch, "t_rem": t_rem, "wlen": 3600,
                               "in_slot": 1200 <= t_rem <= 3000}})
        time.sleep(1.0)


RX_RESULT = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| \[(WIN|LOSS)\] (\w+) (UP|DOWN) @ (\d+)c"
    r" -> \w+ \| ([+-][\d.]+) \| audit net ([+-][\d.]+)")
RX_TRACK = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| \[TRACK:(late|hiband|fav)\] last (\d+): "
    r"(\d+)/\d+=(\d+)%WR \| net ([+-][\d.]+) \| EV/\$ ([+-][\d.]+)")


@app.get("/api/history")
def history(_=Depends(_check)):
    equity, trades, ev_trend, days = [], [], [], {}
    n = w = 0
    net = best = worst = 0.0
    cur = mx = 0                                     # win-streak tracking
    try:
        size = os.path.getsize(HOUR_LOG)
        with open(HOUR_LOG, "r", encoding="utf-8", errors="replace") as f:
            if size > 8_000_000:
                f.seek(size - 8_000_000)
                f.readline()
            for ln in f:
                m = RX_RESULT.match(ln)
                if m:
                    ts, res, coin, d, px, pnl, bk = m.groups()
                    equity.append({"ts": ts, "bk": float(bk)})   # bk = cumulative audit net
                    _st = int(px) / 100.0 * 5
                    _cumstk = getattr(history, "_stk", 0.0) + _st
                    history._stk = _cumstk
                    ev_trend.append({"ts": ts, "tag": "hour", "n": n + 1,
                                     "ev": round(float(bk) / _cumstk, 4)})
                    trades.append({"ts": ts, "res": res, "coin": coin, "dir": d,
                                   "px": int(px), "pnl": float(pnl)})
                    p = float(pnl)
                    n += 1
                    w += 1 if res == "WIN" else 0
                    net += p
                    best = max(best, p)
                    worst = min(worst, p)
                    cur = cur + 1 if res == "WIN" else 0
                    mx = max(mx, cur)
                    day = ts[:10]
                    agg = days.setdefault(day, {"net": 0.0, "n": 0, "w": 0})
                    agg["net"] = round(agg["net"] + float(pnl), 2)
                    agg["n"] += 1
                    agg["w"] += 1 if res == "WIN" else 0
                    continue
                m = RX_TRACK.match(ln)
                if m:
                    tts, tag, tn, tw, twr, tnet, ev = m.groups()  # distinct names — must NOT clobber n/w/net stats
                    ev_trend.append({"ts": tts, "tag": tag, "n": int(tn), "ev": float(ev)})
    except Exception:
        pass
    stats = {"n": n, "w": w, "l": n - w,
             "wr": round(100 * w / n, 1) if n else 0.0,
             "net": round(net, 2), "best": round(best, 2), "worst": round(worst, 2),
             "streak": mx}
    return JSONResponse({"equity": equity[-500:], "trades": trades[-120:],
                         "ev_trend": ev_trend[-200:], "stats": stats,
                         "days": [{"date": k, **v} for k, v in sorted(days.items())[-8:]]})


@app.get("/api/health")
def health(_=Depends(_check)):
    def procs(pat):
        try:
            r = subprocess.run(["pgrep", "-fc", pat], capture_output=True, text=True)
            return int((r.stdout or "0").strip() or 0)
        except Exception:
            return -1
    age = None
    try:
        age = round(time.time() - os.path.getmtime(LOG), 1)
    except Exception:
        pass
    return {"bot": procs("^python3 -u clean_bot.py$"),
            "capture": procs("^python3 -u hourly_capture.py$"),
            "log_age_s": age, "ws_clients": len(_clients)}


@app.get("/api/logtail")
def logtail(n: int = 250, _=Depends(_check)):
    """Recent history for the console — last n full-timestamp lines, classified."""
    n = max(1, min(n, 1000))
    out = []
    try:
        size = os.path.getsize(LOG)
        with open(LOG, "r", encoding="utf-8", errors="replace") as f:
            f.seek(max(0, size - 600_000))      # ~last 600KB is plenty for 250 lines
            f.readline()
            for ln in f:
                ln = ln.strip()
                if ln and ln[:2] == "20" and ln[4:5] == "-":
                    out.append({"cls": _classify(ln), "line": ln[:500]})
    except Exception:
        pass
    return JSONResponse({"lines": out[-n:]})


@app.get("/api/wstoken")
def wstoken(_=Depends(_check)):
    tok = secrets.token_urlsafe(24)
    _wstokens.append(tok)
    return {"token": tok}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    tok = ws.query_params.get("token", "")
    if DASH_PASS and tok not in _wstokens:
        await ws.close(code=1008)
        return
    await ws.accept()
    with _lock:
        _clients.add(ws)
    try:
        await ws.send_text(json.dumps({"t": "hello", "server_time": time.time()}))
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        with _lock:
            _clients.discard(ws)


# Static UI — index.html itself is guarded so the browser prompts for the password.
@app.get("/", response_class=PlainTextResponse)
def index(_=Depends(_check)):
    try:
        return PlainTextResponse(open(os.path.join(UI, "index.html"), encoding="utf-8").read(),
                                 media_type="text/html")
    except Exception:
        return PlainTextResponse("dashboard UI missing", status_code=500)


@app.on_event("startup")
async def _startup():
    global _loop
    _loop = asyncio.get_running_loop()
    binance_ws.start()
    for fn in (_tail_hour_log, _tail_micro_log, _poll_state, _poll_prices):
        threading.Thread(target=fn, daemon=True).start()


if __name__ == "__main__":
    public = os.path.exists(CERT) and os.path.exists(KEY)
    if public and not DASH_PASS:
        raise SystemExit("refusing public bind without QUANTUM_DASH_PASS set in .env")
    kw = dict(host="0.0.0.0", port=8443, ssl_certfile=CERT, ssl_keyfile=KEY) if public \
        else dict(host="127.0.0.1", port=8096)
    print(f"[quantum] {'PUBLIC https :8443 (auth on)' if public else 'localhost :8096'}", flush=True)
    uvicorn.run(app, log_level="warning", **kw)

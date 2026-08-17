#!/usr/bin/env python3
"""SHADOW BOT — the high-band seat (75-85c favourites), live decisions, $0 risk.

Runs the exact micro-audition logic found by seat_scan (2026-08-13) in shadow:
every 60s scan BTC/ETH/SOL/XRP; when the favourite's ask sits in [0.75, 0.85]
with spread <= 3c and 20-50min left in the window, record the bet it WOULD
place (maker px = min(bid+1c, ask-1c), 3 shares nominal) and settle it against
the chain. Feeds the confirmation gate. Places NO orders, touches NO keys.
"""
import json, os, sys, time

V3 = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, V3)
STATE = os.path.join(V3, "shadow_state.json")

from hour_bot import http_json, hour_slug, winner_for   # read-only helpers
import telegram_notifier as tg

NAMES = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "xrp"}
MIN_ASK, MAX_ASK = 0.55, 0.65  # Sunday sweep candidate: n=1148, +5.7% z=2.2, all splits green
MAX_SPREAD = 0.03
T_MIN, T_MAX = 2400, 3000          # 40-50min left (the candidate cell)
SHARES = 3                          # nominal, mirrors the pre-registered audition


def market_for(coin):
    for off in (4, 5):
        try:
            ev = http_json("https://gamma-api.polymarket.com/events?slug="
                           + hour_slug(NAMES[coin], off))
        except Exception:
            continue
        if not ev:
            continue
        mk = (ev[0].get("markets") or [None])[0]
        if not mk:
            continue
        ids, outs = mk.get("clobTokenIds"), mk.get("outcomes")
        if isinstance(ids, str):
            ids = json.loads(ids)
        if isinstance(outs, str):
            outs = json.loads(outs)
        if ids and outs and len(ids) == 2:
            return dict(zip([o.upper() for o in outs], ids)), \
                mk.get("slug") or hour_slug(NAMES[coin], off)
    return None


def book_top(token):
    try:
        bk = http_json("https://clob.polymarket.com/book?token_id=" + token)
        asks = [float(a["price"]) for a in bk.get("asks", []) if 0.01 < float(a["price"]) < 0.99]
        bids = [float(b["price"]) for b in bk.get("bids", []) if 0.01 < float(b["price"]) < 0.99]
        return (max(bids) if bids else None), (min(asks) if asks else None)
    except Exception:
        return None, None


def latest_drift(coin, hs):
    """freshest drift_pct for this coin's CURRENT window, from the collector's
    CSV tail (written every 30s). None if not found/stale."""
    try:
        path = os.path.join(V3, "hourly_research.csv")
        with open(path, "rb") as f:
            f.seek(max(0, os.path.getsize(path) - 16384))
            lines = f.read().decode(errors="replace").strip().splitlines()
        for ln in reversed(lines):
            p = ln.split(",")
            if len(p) >= 6 and p[2] == coin and p[1] == str(hs):
                return float(p[5])
    except Exception:
        pass
    return None


def load():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"entries": []}


def save(s):
    json.dump(s, open(STATE, "w"))


def log(m):
    print(time.strftime("%Y-%m-%d %H:%M:%S") + " | " + m, flush=True)


def step(s):
    now = time.time()
    hs = int(now // 3600) * 3600
    t_left = hs + 3600 - now

    # settle finished shadows
    for e in s["entries"]:
        if "won" in e or now < e["hs"] + 3600 + 90:
            continue
        wn = winner_for(e["slug"])
        if not wn:
            continue
        e["won"] = (wn == e["dir"])
        e["pnl"] = round(((1 - e["px"]) if e["won"] else -e["px"]) * SHARES, 2)
        done = [x for x in s["entries"] if "won" in x]
        net = sum(x["pnl"] for x in done)
        w = sum(1 for x in done if x["won"])
        log(f"[SHADOW {'WIN' if e['won'] else 'LOSS'}] {e['coin']} {e['dir']} @ "
            f"{e['px']*100:.0f}c -> {wn} | {e['pnl']:+.2f} | shadow net {net:+.2f} "
            f"(n={len(done)} {w}W)")
        tg._send(f"🕶 <b>SHADOW {'WIN' if e['won'] else 'LOSS'}</b> {e['coin']} "
                 f"{e['dir']} @ {e['px']*100:.0f}c | {e['pnl']:+.2f} | "
                 f"net {net:+.2f} (n={len(done)}, {w}W) — $0 real",
                 dedup_key=f"shs-{e['coin']}-{e['hs']}")
        save(s)

    if not (T_MIN <= t_left <= T_MAX):
        return
    for coin in NAMES:
        if any(e["hs"] == hs and e["coin"] == coin for e in s["entries"]):
            continue
        mk = market_for(coin)
        if not mk:
            continue
        toks, slug = mk
        ub, ua = book_top(toks.get("UP", ""))
        db, da = book_top(toks.get("DOWN", ""))
        if not (ua and da and ub and db):
            continue
        if ua > da:
            d, ask, bid = "UP", ua, ub
        elif da > ua:
            d, ask, bid = "DOWN", da, db
        else:
            continue
        if not (MIN_ASK <= ask <= MAX_ASK) or (ask - bid) > MAX_SPREAD:
            continue
        drift = latest_drift(coin, hs)
        if drift is None or (d == "UP") != (drift > 0):
            log(f"[SKIP] {coin} {d} @ {ask*100:.0f}c — drift "
                f"{'unknown' if drift is None else f'{drift:+.3f}% opposed'} "
                f"(direction engine)")
            continue
        px = round(min(bid + 0.01, ask - 0.01), 2)
        s["entries"].append({"ts": now, "hs": hs, "coin": coin, "dir": d,
                             "px": px, "ask": ask, "bid": bid, "slug": slug})
        log(f"[SHADOW REST] {coin} {d} @ {px*100:.0f}c x{SHARES} "
            f"(bid {bid*100:.0f}/ask {ask*100:.0f}) T={t_left/60:.0f}min")
        tg._send(f"🕶 <b>SHADOW SIGNAL</b> {coin} favourite <b>{d}</b> @ ask "
                 f"{ask*100:.0f}c — would rest {px*100:.0f}c ×{SHARES}. "
                 f"55-65c seat audition, $0 real until the gate arms.",
                 dedup_key=f"shr-{coin}-{hs}")
        save(s)


if __name__ == "__main__":
    s = load()
    done = [x for x in s["entries"] if "won" in x]
    log(f"=== shadow-bot start | seat: fav {MIN_ASK}-{MAX_ASK} spread<={MAX_SPREAD} "
        f"T {T_MIN}-{T_MAX}s | shadows so far {len(done)} ===")
    while True:
        try:
            step(s)
        except Exception as e:
            log(f"loop error: {e}")
        time.sleep(60)

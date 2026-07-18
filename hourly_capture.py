#!/usr/bin/env python3
"""READ-ONLY shadow capture for Polymarket HOURLY Up/Down markets (BTC/ETH/SOL/XRP).
Purpose: measure whether the fixed-time favorite edge exists on 1h windows before any
trading. Settlement = Binance 1h candle (close vs open, tie -> UP) per market rules, so
strike AND spot both come from Binance here — no cross-feed basis.
Writes hourly_research.csv: snapshot rows (winner empty) + one resolution row per
(coin, hour) with winner filled (t_left=-1). NO ORDERS. ~30s poll."""
import json, os, sys, time, urllib.request, urllib.parse

CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hourly_research.csv")
LOG = lambda m: print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {m}", flush=True)

COINS = {  # coin -> (binance symbol, gamma slug fragments to match)
    "BTC": ("BTCUSDT", ("btc",)),
    "ETH": ("ETHUSDT", ("eth",)),
    "SOL": ("SOLUSDT", ("sol",)),
    "XRP": ("XRPUSDT", ("xrp",)),
}
HDR = ("ts,hour_start,coin,spot,candle_open,drift_pct,up_ask,up_bid,down_ask,down_bid,"
       "t_left,winner\n")

def http_json(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "cleanbot-hourly-shadow"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def binance_kline_1h(sym, start_ms):
    u = (f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=1h"
         f"&startTime={start_ms}&limit=1")
    k = http_json(u)
    if not k:
        return None
    o, c = float(k[0][1]), float(k[0][4])
    return o, c

def binance_spot(sym):
    return float(http_json(f"https://api.binance.com/api/v3/ticker/price?symbol={sym}")["price"])

# ── market discovery (gamma) ──────────────────────────────────────────
_tokens = {}      # coin -> {"end": epoch, "up": token, "down": token, "slug": str}

def _parse_tokens(m):
    raw = m.get("clobTokenIds") or m.get("clob_token_ids")
    if isinstance(raw, str):
        raw = json.loads(raw)
    outs = m.get("outcomes")
    if isinstance(outs, str):
        outs = json.loads(outs)
    if not raw or not outs or len(raw) != len(outs):
        return None
    up = dn = None
    for tok, out in zip(raw, outs):
        o = str(out).lower()
        if o in ("up", "yes"):
            up = tok
        elif o in ("down", "no"):
            dn = tok
    return (up, dn) if up and dn else None

SLUG_NAMES = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "xrp"}

def _hour_slug(name, offset_h):
    """Deterministic event slug for the market covering the CURRENT hour, with the ET
    clock at UTC-offset_h (4 = EDT, 5 = EST). Pattern verified live 2026-07-18:
    bitcoin-up-or-down-july-18-2026-2am-et"""
    et = time.gmtime(time.time() - offset_h * 3600)
    h = et.tm_hour % 12 or 12
    ap = "am" if et.tm_hour < 12 else "pm"
    month = time.strftime("%B", et).lower()
    return f"{name}-up-or-down-{month}-{et.tm_mday}-{et.tm_year}-{h}{ap}-et"

def discover():
    """Resolve the current hour's market per coin by constructing the event slug
    (gamma listings are polluted with stale rows; direct slug fetch is exact)."""
    now = time.time()
    hour_end = (int(now // 3600) + 1) * 3600
    for coin in COINS:
        cur = _tokens.get(coin)
        if cur and cur["end"] >= hour_end - 5:
            continue                                  # already have this hour
        name = SLUG_NAMES[coin]
        for off in (4, 5):                            # EDT first; EST fallback
            slug = _hour_slug(name, off)
            try:
                ev = http_json("https://gamma-api.polymarket.com/events?slug=" + slug)
            except Exception as e:
                LOG(f"[DISCOVER ERR] {coin} {e}")
                break
            if isinstance(ev, list) and ev and (ev[0].get("markets") or []):
                m = ev[0]["markets"][0]
                tk = _parse_tokens(m)
                if tk:
                    _tokens[coin] = {"end": hour_end, "up": tk[0], "down": tk[1],
                                     "slug": slug}
                    LOG(f"[MARKET] {coin} {slug}")
                break

def book_top(token):
    try:
        b = http_json(f"https://clob.polymarket.com/book?token_id={token}")
        asks = b.get("asks") or []
        bids = b.get("bids") or []
        # CLOB returns asks descending; best ask = min price, best bid = max
        a = min((float(x["price"]) for x in asks), default=None)
        bd = max((float(x["price"]) for x in bids), default=None)
        return a, bd
    except Exception:
        return None, None

def main():
    if not os.path.exists(CSV):
        open(CSV, "w").write(HDR)
    LOG(f"=== HOURLY SHADOW CAPTURE start | {','.join(COINS)} | 30s poll | NO ORDERS ===")
    candle_open = {}          # (coin, hour) -> open price
    resolved = set()
    last_disc = 0.0
    while True:
        now = time.time()
        hour = int(now // 3600) * 3600
        t_left = hour + 3600 - now
        if now - last_disc > 600 or any(c not in _tokens or _tokens[c]["end"] < now
                                        for c in COINS):
            discover(); last_disc = now
        # resolve the PREVIOUS hour once its candle is final (15s grace)
        prev = hour - 3600
        for coin, (sym, _) in COINS.items():
            key = (coin, prev)
            if key in resolved or now - hour < 15:
                continue
            try:
                k = binance_kline_1h(sym, prev * 1000)
                if k:
                    o, c = k
                    w = "UP" if c >= o else "DOWN"   # tie -> UP per market rules
                    with open(CSV, "a") as f:
                        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')},{prev},{coin},"
                                f"{c},{o},{(c - o) / o * 100:.4f},,,,,-1,{w}\n")
                    resolved.add(key)
                    LOG(f"[RESOLVE] {coin} {time.strftime('%H:00', time.gmtime(prev))} "
                        f"open={o} close={c} -> {w}")
            except Exception as e:
                LOG(f"[RESOLVE ERR] {coin} {e}")
        # snapshots
        for coin, (sym, _) in COINS.items():
            try:
                ck = (coin, hour)
                if ck not in candle_open:
                    k = binance_kline_1h(sym, hour * 1000)
                    if not k:
                        continue
                    candle_open[ck] = k[0]
                    for old in [x for x in candle_open if x[1] < hour - 7200]:
                        candle_open.pop(old, None)
                o = candle_open[ck]
                spot = binance_spot(sym)
                drift = (spot - o) / o * 100
                tk = _tokens.get(coin)
                ua = ub = da = db = ""
                if tk and tk["end"] >= now:
                    a1, b1 = book_top(tk["up"]); a2, b2 = book_top(tk["down"])
                    ua, ub = (a1 or ""), (b1 or "")
                    da, db = (a2 or ""), (b2 or "")
                with open(CSV, "a") as f:
                    f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')},{hour},{coin},{spot},"
                            f"{o},{drift:.4f},{ua},{ub},{da},{db},{int(t_left)},\n")
            except Exception as e:
                LOG(f"[SNAP ERR] {coin} {str(e)[:80]}")
        time.sleep(30)

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build a clean training set for the CleanBot early-drift edge from 1m klines.
For every 15m window (4 coins, ~120 days): features at minute 5 + true 15m outcome.
No API key needed. Caches klines to disk. CPU-only."""
import urllib.request, json, time, os, math, csv, datetime as dt

COINS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}
DAYS = 120
HOST = "https://api.binance.com"
CACHE = "klines_cache"
os.makedirs(CACHE, exist_ok=True)


def fetch_1m(sym, start_ms, end_ms):
    cache = os.path.join(CACHE, f"{sym}_{start_ms}_{end_ms}.json")
    if os.path.exists(cache):
        return json.load(open(cache))
    out, cur = [], start_ms
    while cur < end_ms:
        u = f"{HOST}/api/v3/klines?symbol={sym}&interval=1m&startTime={cur}&endTime={end_ms}&limit=1000"
        for attempt in range(5):
            try:
                d = json.load(urllib.request.urlopen(u, timeout=20)); break
            except Exception as e:
                if attempt == 4:
                    raise
                time.sleep(1.5)
        if not d:
            break
        out += d
        cur = d[-1][0] + 60_000
        time.sleep(0.12)
    json.dump(out, open(cache, "w"))
    return out


def series(sym, start_ms, end_ms):
    """ts(sec) -> (open, close) for each 1m bar."""
    op, cl = {}, {}
    for k in fetch_1m(sym, start_ms, end_ms):
        t = k[0] // 1000
        op[t] = float(k[1]); cl[t] = float(k[4])
    return op, cl


def main():
    end_ms = int(time.time() // 60 * 60) * 1000
    start_ms = end_ms - DAYS * 86400 * 1000
    print(f"fetching {DAYS}d 1m klines for {list(COINS)} ...")
    OP, CL = {}, {}
    for c, sym in COINS.items():
        OP[c], CL[c] = series(sym, start_ms, end_ms)
        print(f"  {c}: {len(CL[c])} bars")

    def drift_at(coin, t0, mins):
        """drift of `coin` at `mins` into the window starting t0."""
        strike = OP[coin].get(t0)
        p = CL[coin].get(t0 + (mins - 1) * 60)
        if not strike or not p:
            return None
        return (p - strike) / strike

    rows = []
    t0 = (start_ms // 1000 // 900 + 1) * 900
    last = end_ms // 1000 - 900
    while t0 <= last:
        btc_d5 = drift_at("BTC", t0, 5)
        for coin in COINS:
            strike = OP[coin].get(t0)
            p5 = CL[coin].get(t0 + 240)          # ~5 min in
            close = CL[coin].get(t0 + 840)       # window close (~15 min)
            if not strike or not p5 or not close:
                continue
            drift = (p5 - strike) / strike
            # 1m returns over minutes 0..5 -> sigma + last-minute roc
            rets = []
            for m in range(1, 6):
                a = CL[coin].get(t0 + (m - 1) * 60); b = CL[coin].get(t0 + m * 60)
                if a and b:
                    rets.append((b - a) / a)
            if len(rets) < 4:
                continue
            mean = sum(rets) / len(rets)
            sigma = (sum((r - mean) ** 2 for r in rets) / len(rets)) ** 0.5
            roc_last = rets[-1]
            z = drift / (sigma * math.sqrt(5) + 1e-9)
            winner = 1 if close > strike else 0      # 1=UP
            bet_up = 1 if drift > 0 else 0
            drift_correct = int(bet_up == winner)
            hour = dt.datetime.utcfromtimestamp(t0).hour
            agree_btc = int((drift > 0) == (btc_d5 > 0)) if btc_d5 is not None else -1
            rows.append({
                "ws": t0, "coin": coin, "hour": hour,
                "drift_bps": round(drift * 1e4, 2), "abs_drift_bps": round(abs(drift) * 1e4, 2),
                "roc_last_bps": round(roc_last * 1e4, 2), "sigma_bps": round(sigma * 1e4, 3),
                "z": round(z, 3), "abs_z": round(abs(z), 3),
                "btc_drift_bps": round(btc_d5 * 1e4, 2) if btc_d5 is not None else 0,
                "agree_btc": agree_btc, "drift_correct": drift_correct})
        t0 += 900

    cols = ["ws", "coin", "hour", "drift_bps", "abs_drift_bps", "roc_last_bps",
            "sigma_bps", "z", "abs_z", "btc_drift_bps", "agree_btc", "drift_correct"]
    with open("dataset.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    base = sum(r["drift_correct"] for r in rows) / len(rows)
    print(f"\nDATASET: {len(rows)} windows -> dataset.csv | base drift-correct {base*100:.1f}%")


if __name__ == "__main__":
    main()

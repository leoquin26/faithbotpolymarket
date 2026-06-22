#!/usr/bin/env python3
"""Test the owner's thesis: enter on a CLEARER signal (bigger/confirmed drift) instead
of the first 10bps hair-trigger. Higher WR = lower variance = survives a small account.
Tests entry-drift thresholds + momentum confirmation. Uses cached 1m klines."""
import json, os, datetime as dt

COINS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}
CACHE = "klines_cache"
OP, CL = {}, {}
for c, sym in COINS.items():
    op, cl = {}, {}
    for fn in os.listdir(CACHE):
        if fn.startswith(sym + "_"):
            for k in json.load(open(os.path.join(CACHE, fn))):
                t = k[0] // 1000; op[t] = float(k[1]); cl[t] = float(k[4])
    OP[c], CL[c] = op, cl


def windows():
    allt = sorted(CL["BTC"]); t0 = (allt[0] // 900 + 1) * 900; last = allt[-1] - 900
    while t0 <= last:
        yield t0; t0 += 900


def test(thresh_bps, need_confirm):
    n = w = 0
    for t0 in windows():
        for coin in COINS:
            strike = OP[coin].get(t0); close = CL[coin].get(t0 + 840)
            if not strike or close is None:
                continue
            # walk minutes 2..5, enter at FIRST minute drift crosses threshold
            entered = None
            for k in range(2, 6):
                c = CL[coin].get(t0 + k * 60); cp = CL[coin].get(t0 + (k - 1) * 60)
                if c is None or cp is None:
                    break
                drift = (c - strike) / strike
                if abs(drift) * 1e4 >= thresh_bps:
                    roc = (c - cp) / cp                     # last-minute momentum
                    if need_confirm and (roc * drift <= 0):  # momentum must agree
                        continue
                    entered = (1 if drift > 0 else 0); break
            if entered is None:
                continue
            winner = 1 if close > strike else 0
            n += 1; w += (entered == winner)
    return w, n


print("entry-drift threshold -> directional WR (bigger/clearer signal = higher WR?):")
for T in (10, 12, 15, 18, 20, 25):
    w, n = test(T, False)
    print(f"  drift>={T:>2}bps          WR {100*w/n:4.1f}%  trades {n:6d}  (loss-rate {100*(1-w/n):.0f}%)")
print("\n+ require momentum CONFIRMATION (last-minute roc agrees with drift):")
for T in (10, 15, 20):
    w, n = test(T, True)
    print(f"  drift>={T:>2}bps +confirm WR {100*w/n:4.1f}%  trades {n:6d}")

#!/usr/bin/env python3
"""Backtest the owner's approach: predict P(close vs threshold) continuously through
the window and ENTER when the calculation is confident — bet a lot, on the math.
P(close>strike) = Phi( distance / (sigma_min * sqrt(minutes_left)) ).  Uses time-left,
so it rewards clear signals late in the window. Cached 1m klines, CPU."""
import json, os, math

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
Phi = lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2)))


def windows():
    allt = sorted(CL["BTC"]); t0 = (allt[0] // 900 + 1) * 900; last = allt[-1] - 900
    while t0 <= last:
        yield t0; t0 += 900


def run(conf, max_min):
    """Enter at first minute (2..max_min) P(close in our dir) >= conf. Bet that dir."""
    n = w = 0; ent_min = 0; pcal = []
    for t0 in windows():
        for coin in COINS:
            strike = OP[coin].get(t0); close = CL[coin].get(t0 + 840)
            if not strike or close is None:
                continue
            entered = None
            for m in range(2, max_min + 1):
                price = CL[coin].get(t0 + m * 60)
                if price is None:
                    break
                rets = []
                for j in range(1, m + 1):
                    a = CL[coin].get(t0 + (j - 1) * 60); b = CL[coin].get(t0 + j * 60)
                    if a and b:
                        rets.append((b - a) / a)
                if len(rets) < 2:
                    continue
                mean = sum(rets) / len(rets)
                sig = (sum((r - mean) ** 2 for r in rets) / len(rets)) ** 0.5 or 1e-9
                dist = (price - strike) / strike
                z = dist / (sig * math.sqrt(15 - m))
                p_up = Phi(z)
                if p_up >= conf:
                    entered = (1, p_up); break
                if (1 - p_up) >= conf:
                    entered = (0, 1 - p_up); break
            if entered is None:
                continue
            direction, pconf = entered
            winner = 1 if close > strike else 0
            hit = (direction == winner)
            n += 1; w += hit; ent_min += m; pcal.append((pconf, hit))
    wr = 100 * w / n if n else 0
    # calibration: mean predicted vs actual
    mp = sum(p for p, _ in pcal) / len(pcal) if pcal else 0
    return wr, n, ent_min / n if n else 0, mp * 100


print("close-probability engine: enter when P(close in our dir) >= conf, minutes 2..10")
print(f"{'conf':>5} {'WR':>6} {'trades':>8} {'avg_entry_min':>14} {'mean_pred':>10}")
for conf in (0.58, 0.62, 0.66, 0.70, 0.75):
    wr, n, am, mp = run(conf, 10)
    print(f"{conf:>5} {wr:5.1f}% {n:8d} {am:14.1f} {mp:9.1f}%  (calib: pred {mp:.0f}% vs actual {wr:.0f}%)")
print("\n(volume vs WR tradeoff; calib close = the math is honest. compare to raw drift>=10 @78%)")

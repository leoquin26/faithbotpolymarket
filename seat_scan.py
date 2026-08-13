#!/usr/bin/env python3
"""SEAT SCAN — the active edge hunt. Grid over every seat the hourly data
can express: entry-time bucket x price band x side (favourite/longshot) x
coin, maker entry, hold to settle.

Honesty bars (multiple-comparisons aware — ~200 cells means z>=2 alone
produces ~5 false positives by chance):
  CANDIDATE  = pooled z >= 2, both 70/30 splits same sign, recent-14d ROI > 0
  STRONG     = pooled z >= 3 with the same split+recency agreement
A CANDIDATE needs one more independent week green before any micro-audition."""
import csv, math, time
from collections import defaultdict

PATH = "/home/ubuntu/v3-bot/hourly_research.csv"
COINS = ("BTC", "ETH", "SOL", "XRP")
DAY = 86400
now = time.time()

winners, quotes = {}, defaultdict(list)
for r in csv.DictReader(open(PATH)):
    coin = r["coin"]
    if coin not in COINS:
        continue
    hs = int(r["hour_start"])
    if r["winner"]:
        winners[(hs, coin)] = r["winner"]
        continue
    try:
        t = int(r["t_left"])
        ua, ub = float(r["up_ask"]), float(r["up_bid"])
        da, db = float(r["down_ask"]), float(r["down_bid"])
    except ValueError:
        continue
    quotes[(hs, coin)].append((t, ua, ub, da, db))

# opportunities: one per (window, coin, t_bucket): first quote in bucket
# entry = maker px on chosen side; sides evaluated: FAV and DOG (longshot)
opps = []   # (hs, coin, tb, side, ask, px, spread, won)
for (hs, coin), rows in quotes.items():
    wn = winners.get((hs, coin))
    if not wn:
        continue
    rows.sort(key=lambda x: -x[0])
    seen = set()
    for t, ua, ub, da, db in rows:
        tb = min(5, t // 600)
        if tb in seen or ua <= 0 or da <= 0 or ub <= 0 or db <= 0:
            continue
        seen.add(tb)
        if ua == da:
            continue
        fav_up = ua > da
        for side in ("FAV", "DOG"):
            up = fav_up if side == "FAV" else not fav_up
            ask, bid = (ua, ub) if up else (da, db)
            px = min(bid + 0.01, ask - 0.01)
            if px <= 0.01:
                continue
            won = (wn == "UP") == up
            opps.append((hs, coin, tb, side, ask, px, ask - bid, won))

cut = sorted(o[0] for o in opps)[int(len(opps) * 0.7)]
recent = now - 14 * DAY

def stats(sel):
    if len(sel) < 30:
        return None
    W = sum(1 for o in sel if o[7])
    risked = sum(o[5] for o in sel)
    pnl = sum((1 - o[5]) if o[7] else -o[5] for o in sel)
    var = sum(o[5] * (1 - o[5]) for o in sel)
    z = (W - sum(o[5] for o in sel)) / math.sqrt(var) if var else 0
    return {"n": len(sel), "roi": pnl / risked, "z": z}

BANDS = [(0.50, 0.55), (0.55, 0.65), (0.65, 0.75), (0.75, 0.85), (0.85, 0.92)]
TB_NAME = {0: "0-10m", 1: "10-20m", 2: "20-30m", 3: "30-40m", 4: "40-50m", 5: "50-60m"}

cands = []
tested = 0
for tb in range(6):
    for blo, bhi in BANDS:
        for side in ("FAV", "DOG"):
            for coin in ("ALL",) + COINS:
                sel = [o for o in opps if o[2] == tb and o[3] == side
                       and blo <= o[4] < bhi and (coin == "ALL" or o[1] == coin)]
                s = stats(sel)
                if not s:
                    continue
                tested += 1
                if s["z"] < 2:
                    continue
                a = stats([o for o in sel if o[0] < cut])
                b = stats([o for o in sel if o[0] >= cut])
                rec = stats([o for o in sel if o[0] >= recent])
                split_ok = a and b and (a["roi"] > 0) == (b["roi"] > 0)
                rec_ok = rec and rec["roi"] > 0
                grade = ("STRONG" if s["z"] >= 3 and split_ok and rec_ok else
                         "CANDIDATE" if split_ok and rec_ok else "MIRAGE")
                cands.append((grade, s["z"], tb, (blo, bhi), side, coin, s, a, b, rec))

print(f"cells tested (n>=30): {tested} | z>=2 hits: {len(cands)} "
      f"(~{tested*0.023:.0f} expected by chance at z>=2)")
for grade, z, tb, band, side, coin, s, a, b, rec in sorted(cands, key=lambda x: -x[1]):
    print(f"\n[{grade}] {side} {coin} {TB_NAME[tb]} ask {band[0]:.2f}-{band[1]:.2f}"
          f"  n={s['n']} ROI {s['roi']*100:+.1f}% z={z:+.1f}")
    print(f"   train {a['roi']*100:+.1f}% (n={a['n']}) | test {b['roi']*100:+.1f}% "
          f"(n={b['n']}) | last14d {rec['roi']*100:+.1f}% (n={rec['n']})" if a and b and rec
          else "   insufficient split/recency data")
if not cands:
    print("\nno cell reaches z>=2 — nothing to bet this week; sweep re-runs on fresh data")

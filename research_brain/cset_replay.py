#!/usr/bin/env python3
"""ASYNC COMPLETE-SET ACCUMULATION replay on the 1h tape (hourly_research.csv,
30s snapshots, full hour). The mechanism behind the most profitable 1h/4h
bots per the RetroValix analysis (Aug 2026): buy one side cheap now, buy the
other side cheap later in the hour's swings; the matched pair costs < $1 and
pays $1 regardless of outcome. We test the ALMACH/0xb55 variant:

  1) rest a bid on BOTH outcomes at best bid from the first snapshot
  2) when side A fills (price-through proxy: later ask <= our bid), REPRICE
     the other side's bid to min(its best bid, TARGET - pxA) so that a second
     fill LOCKS a pair cost <= TARGET
  3) unmatched inventory at settlement -> directional pnl; matched -> 1-cost
  4) stop quoting at T-CUT seconds (avoid the settlement sweep)
Reported per TARGET: pair-lock rate, avg pair cost, leftover single EV,
total EV/$ per window, 70/30 chronological split. Run LOCALLY:
  TAPE=<dir with hourly_research.csv> python cset_replay.py
"""
import csv, os, sys, time
from collections import defaultdict

TAPE = os.environ.get("TAPE", "/home/ubuntu/v3-bot")
COINS = {"BTC", "ETH", "SOL", "XRP"}
T_CUT = int(sys.argv[1]) if len(sys.argv) > 1 else 300      # stop quoting at T-300s
K = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0        # rest K below best bid (almach levels)
TARGETS = (0.99, 0.97, 0.95, 0.93)

winners, rows = {}, defaultdict(list)
for r in csv.DictReader(open(os.path.join(TAPE, "hourly_research.csv"))):
    if r["coin"] not in COINS:
        continue
    hs = int(r["hour_start"])
    if r["winner"]:
        winners[(hs, r["coin"])] = r["winner"].upper()
        continue
    try:
        t = int(float(r["t_left"]))
        ua, ub = float(r["up_ask"]), float(r["up_bid"])
        da, db = float(r["down_ask"]), float(r["down_bid"])
    except ValueError:
        continue
    if min(ua, ub, da, db) <= 0:
        continue
    rows[(hs, r["coin"])].append((t, ua, ub, da, db))
print(f"windows with book+winner: {sum(1 for k in rows if k in winners)}", flush=True)

def simulate(target):
    out = []   # (hs, coin, pair_locked, pair_cost, single_pnl, risked, matched_pnl)
    for (hs, coin), snaps in rows.items():
        wn = winners.get((hs, coin))
        if not wn or len(snaps) < 10:
            continue
        snaps.sort(key=lambda x: -x[0])          # chronological
        q = {"UP": None, "DOWN": None}           # our bid px
        fill = {"UP": None, "DOWN": None}
        for t, ua, ub, da, db in snaps:
            if t < T_CUT:
                break
            book = {"UP": (ub, ua), "DOWN": (db, da)}
            for o in ("UP", "DOWN"):
                bb, ba = book[o]
                if fill[o] is not None:
                    continue
                other = "DOWN" if o == "UP" else "UP"
                if fill[other] is not None:
                    # second side: a RESTING bid at the locking level (<= target - pxA),
                    # never above ask-1c (stay maker), never chased down with the touch
                    cap = round(target - fill[other], 2)
                    if cap < 0.02:
                        q[o] = None
                    else:
                        lvl = min(cap, round(ba - 0.01, 2))
                        q[o] = lvl if q[o] is None else min(cap, max(q[o], lvl))
                elif q[o] is None:
                    q[o] = round(max(0.02, bb - K), 2)   # passive level K below the touch
                elif K == 0 and bb > q[o]:
                    q[o] = bb                    # touch-joining variant only
                if q[o] is not None and ba <= q[o]:
                    fill[o] = q[o]
        # settle
        fu, fd = fill["UP"], fill["DOWN"]
        if fu is not None and fd is not None:
            cost = fu + fd
            out.append((hs, coin, 1, cost, 0.0, cost, 1.0 - cost))
        elif fu is not None or fd is not None:
            o, px = ("UP", fu) if fu is not None else ("DOWN", fd)
            pnl = (1 - px) if wn == o else -px
            out.append((hs, coin, 0, None, pnl, px, 0.0))
        else:
            out.append((hs, coin, 0, None, 0.0, 0.0, 0.0))
    return out

def summarize(label, res):
    n = len(res)
    if not n:
        print(f"{label}: n=0"); return
    pairs = [x for x in res if x[2]]
    singles = [x for x in res if not x[2] and x[5] > 0]
    risked = sum(x[5] for x in res)
    pnl = sum(x[4] + x[6] for x in res)
    print(f"{label:22s} windows={n:5d} pair-lock {len(pairs)/n*100:4.0f}% "
          f"(avg cost {sum(x[3] for x in pairs)/len(pairs) if pairs else 0:.3f}) "
          f"single {len(singles)/n*100:4.0f}% (EV/$ {sum(x[4] for x in singles)/sum(x[5] for x in singles) if singles else 0:+.3f}) "
          f"none {100-(len(pairs)+len(singles))/n*100:4.0f}% | EV/$ {pnl/risked if risked else 0:+.4f} "
          f"| pnl/window ${pnl/n:+.4f}/sh")

for target in TARGETS:
    res = simulate(target)
    res.sort(key=lambda x: x[0])
    cut = res[int(len(res) * 0.7)][0] if res else 0
    print(f"\n== TARGET pair cost <= {target:.2f}, quote until T-{T_CUT}s ==")
    summarize("ALL", res)
    summarize("  train 70%", [x for x in res if x[0] < cut])
    summarize("  test 30%", [x for x in res if x[0] >= cut])
    for c in sorted(COINS):
        summarize(f"  {c}", [x for x in res if x[1] == c])

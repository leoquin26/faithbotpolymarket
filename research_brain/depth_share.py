#!/usr/bin/env python3
"""REWARD-FARM YIELD ESTIMATE from late_book.jsonl (top-2 depth both sides).
Question: if we quote MIN_SIZE shares on both sides within MAX_SPREAD of mid,
what share of the per-sample Q pool would we hold, and what does the market's
daily pool pay per 15m window?  Uses the docs' quadratic score S=((v-s)/v)^2,
c=3 single-side penalty, min-of-two-sides for two-sided quoting.
Competition depth = visible top-2 size within v cents of mid (a LOWER bound
on real competition — deeper levels beyond top-2 are invisible to us)."""
import json, sys, time
from collections import defaultdict

V3 = "/home/ubuntu/v3-bot"
MIN_SIZE = float(sys.argv[1]) if len(sys.argv) > 1 else 50
V = float(sys.argv[2]) if len(sys.argv) > 2 else 1.5      # max spread cents
POOL_PER_DAY = float(sys.argv[3]) if len(sys.argv) > 3 else 7258   # BTC 15m: $225k/31d
WINDOWS_PER_DAY = 96

def S(s_cents):
    return max(0.0, (V - s_cents) / V) ** 2

# per (ws, coin, tr-bucket): our Q share samples
acc = defaultdict(lambda: [0.0, 0])   # (coin, phase) -> [sum share, n]
books = {}   # (ws,c) -> {o: (bids, asks, tr)}
n = 0
with open(V3 + "/late_book.jsonl") as f:
    for ln in f:
        n += 1
        if n % 5:
            continue                       # sample 1/5 of seconds
        try:
            r = json.loads(ln)
        except Exception:
            continue
        k = (r["ws"], r["c"])
        bk = books.setdefault(k, {})
        bk[r["o"]] = (r["b"], r["a"], r["tr"])
        if "UP" not in bk or "DOWN" not in bk:
            continue
        ub, ua, tr = bk["UP"]
        if not ub or not ua:
            continue
        mid = (ub[0][0] + ua[0][0]) / 2
        if not (0.10 <= mid <= 0.90):
            continue                       # single-sided cannot score there; skip
        # competition Q (one side, "first side" = bids on UP + asks on DOWN ~ same thing)
        comp = 0.0
        for px, sz in ub:
            s = (mid - px) * 100
            if 0 <= s <= V: comp += S(s) * sz
        db, da, _ = bk["DOWN"]
        for px, sz in db:
            s = ((1 - mid) - px) * 100
            if 0 <= s <= V: comp += S(s) * sz
        # our quote: MIN_SIZE at best bid on both UP and DOWN (join the top)
        s_up = (mid - ub[0][0]) * 100
        s_dn = ((1 - mid) - db[0][0]) * 100 if db else V
        ours = min(S(s_up), S(s_dn)) * MIN_SIZE
        share = ours / (ours + comp) if (ours + comp) > 0 else 0
        phase = "early>600s" if tr > 600 else ("mid 180-600" if tr > 180 else "late<180s")
        a = acc[(r["c"], phase)]
        a[0] += share; a[1] += 1

per_window_pool = POOL_PER_DAY / WINDOWS_PER_DAY
print(f"quote {MIN_SIZE:.0f}sh both sides within {V}c | pool ${POOL_PER_DAY:.0f}/day => ${per_window_pool:.2f} per 15m window (BTC-15m assumption)")
for (c, phase), (ssum, cnt) in sorted(acc.items()):
    sh = ssum / cnt if cnt else 0
    print(f"  {c} {phase:12s} avg Q-share {sh*100:5.1f}%  => ~${sh*per_window_pool:.2f}/window if held all window  (samples {cnt})")
